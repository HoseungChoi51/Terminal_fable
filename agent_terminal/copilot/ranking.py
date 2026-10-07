"""Frecency ranking over the persistent command corpus.

Replaces the additive, unnormalized bonuses in ``copilot.suggest`` with one
scoring model shared by both surfaces (ghost text and the completion menu),
so scores from different sources are finally comparable.

Three ideas carry the whole module:

1. **Frecency.** A command's weight is its frequency damped by recency:
   ``log1p(count) * (0.3 + 0.7 * 0.5**(age/half_life))``. The log keeps a
   command run 500 times from permanently burying everything else; the
   floor of 0.3 keeps an old-but-heavily-used command in play.

2. **Context scoping.** The same command is worth more where you actually
   run it. An exact cwd match beats a same-project match beats neither —
   this is what stops ``npm test`` ranking inside a Python repo.

3. **Dominance confidence.** Ghost text needs a real number to gate on, not
   the hardcoded 0.9/0.6 constants it used before. Confidence is how far
   the winner beats the best *materially different* runner-up:
   ``s1 / (s1 + s2)``. One clear candidate scores 1.0; a coin-flip between
   two equals scores 0.5, which correctly fails the default 0.7 gate.

Pure and GTK-free (ADR 0003); the clock is injectable.
"""

from __future__ import annotations

import math
import os
import time

from agent_terminal.copilot import fuzzy
from agent_terminal.copilot.config import CompletionConfig
from agent_terminal.copilot.corpus import Corpus

# Context multipliers.
CWD_EXACT_BOOST = 1.6
PROJECT_BOOST = 1.25
# Exit-status multipliers. "Never seen succeed" is damped hard but not to
# zero: seeded bash_history entries have no exit data and must still rank.
EXIT_OK = 1.0
EXIT_MIXED = 0.5
EXIT_UNKNOWN = 0.7
EXIT_ONLY_FAILED = 0.15
# Weight of the bigram edge in "chain" mode, relative to base frecency.
CHAIN_WEIGHT = 1.5

FRECENCY = "frecency"
CHAIN = "chain"
TOKEN = "token"


class Scored:
    """A corpus entry with its score, for ranking and confidence."""

    __slots__ = ("entry", "score", "command")

    def __init__(self, entry, score):
        self.entry = entry
        self.score = score
        self.command = entry.cmd

    def __repr__(self):
        return f"Scored({self.command!r}, {self.score:.4f})"


# Markers that mark a directory as the root of a project. `context
# .detect_project` answers "is *this* directory a project?"; ranking needs
# "which project am I inside?", so it walks up instead.
_ROOT_MARKERS = (".git", ".hg", ".svn", "package.json", "pyproject.toml",
                 "Cargo.toml", "go.mod", "Makefile", "justfile")


def find_project_root(cwd, *, exists=os.path.exists, max_depth=24):
    """Nearest ancestor of `cwd` holding a project marker, or None."""
    if not cwd:
        return None
    current = os.path.normpath(cwd)
    for _ in range(max_depth):
        for marker in _ROOT_MARKERS:
            if exists(os.path.join(current, marker)):
                return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def squash(value, scale) -> float:
    """Map an unbounded non-negative score into (0, 1).

    `scale` is the value that maps to 0.5, so it should be roughly the
    source's typical magnitude. This is what lets scores from different
    sources be compared at all.
    """
    value = max(float(value), 0.0)
    return value / (value + max(float(scale), 1e-9))


def recency_factor(last_used, now, half_life_days) -> float:
    """Exponential decay in [0, 1]; 1.0 for something used right now."""
    half_life = max(float(half_life_days), 0.5)
    age_days = max(now - last_used, 0.0) / 86400.0
    return 0.5 ** (age_days / half_life)


def base_weight(entry, now, half_life_days) -> float:
    return (math.log1p(entry.count)
            * (0.3 + 0.7 * recency_factor(entry.last_used, now,
                                          half_life_days)))


def exit_factor(entry) -> float:
    if entry.ok and not entry.fail:
        return EXIT_OK
    if entry.ok and entry.fail:
        return EXIT_MIXED
    if entry.fail:
        return EXIT_ONLY_FAILED
    return EXIT_UNKNOWN          # never observed either way (e.g. seeded)


def _same_project(entry_cwds, cwd, project_root) -> bool:
    if not project_root:
        return False
    root = os.path.normpath(project_root)
    for other in entry_cwds:
        norm = os.path.normpath(other)
        if norm == root or norm.startswith(root + os.sep):
            return True
    return False


def context_factor(entry, cwd, project_root) -> float:
    if cwd and cwd in entry.cwds:
        return CWD_EXACT_BOOST
    if _same_project(entry.cwds, cwd, project_root):
        return PROJECT_BOOST
    return 1.0


def chain_factor(entry, prev_command) -> float:
    """Bigram boost: how often `entry` followed `prev_command` before.

    Scaled by the share of this entry's runs that followed it, so a command
    that *always* follows `git add` beats one that merely follows it once.
    """
    if not prev_command or not entry.prev:
        return 1.0
    edge = entry.prev.get(prev_command, 0)
    if not edge:
        return 1.0
    share = edge / max(entry.count, 1)
    return 1.0 + CHAIN_WEIGHT * min(share, 1.0)


def score_entry(entry, *, typed="", cwd=None, project_root=None, now=None,
                half_life_days=14, prev_command=None, mode=FRECENCY,
                match=None) -> float | None:
    """Score one entry, or None when it does not match `typed` at all."""
    now = time.time() if now is None else now
    quality = _match_quality(typed, entry.cmd) if match is None else match
    if quality is None:
        return None
    value = (base_weight(entry, now, half_life_days)
             * context_factor(entry, cwd, project_root)
             * exit_factor(entry)
             * quality)
    if mode in (CHAIN, TOKEN):
        value *= chain_factor(entry, prev_command)
    return value


def _match_quality(typed, command) -> float | None:
    """How well `command` answers `typed`. None means "not a candidate".

    A literal prefix extension is the strongest signal (that is what ghost
    text can render); a fuzzy hit is weaker and normalized into (0, 1] so
    it can never outrank a true prefix match on match quality alone.
    """
    if not typed:
        return 1.0
    if command == typed:
        return None                     # nothing left to complete
    if command.startswith(typed):
        # Longer shared prefixes are better, with diminishing returns.
        return 1.0 + 0.5 * (len(typed) / max(len(command), 1))
    raw = fuzzy.score(typed, command)
    if raw is None:
        return None
    # fuzzy.score is unbounded and length-dependent; squash to (0, 1).
    return 1.0 - 1.0 / (1.0 + max(raw, 0.0) / 8.0)


def rank(corpus: Corpus, *, typed="", cwd=None, project_root=None, now=None,
         config: CompletionConfig | None = None, prev_command=None,
         limit=None) -> list[Scored]:
    """All matching corpus entries, best first."""
    config = config or CompletionConfig()
    now = time.time() if now is None else now
    out = []
    for entry in corpus.all():
        value = score_entry(
            entry, typed=typed, cwd=cwd, project_root=project_root, now=now,
            half_life_days=config.half_life_days, prev_command=prev_command,
            mode=config.ranking)
        if value is not None:
            out.append(Scored(entry, value))
    out.sort(key=lambda s: (-s.score, s.command))
    return out[:limit] if limit else out


def confidence(ranked, typed="") -> float:
    """Dominance of the winner over the best materially different rival.

    "Materially different" means proposing a different completion suffix —
    two entries that would insert the same text are not competitors, so
    they must not depress confidence.
    """
    if not ranked:
        return 0.0
    top = ranked[0]
    if top.score <= 0:
        return 0.0
    winner_suffix = top.command[len(typed):] if typed else top.command
    for other in ranked[1:]:
        suffix = other.command[len(typed):] if typed else other.command
        if suffix == winner_suffix:
            continue
        rival = max(other.score, 0.0)
        return top.score / (top.score + rival)
    return 1.0                      # no rival proposes anything different


def best_completion(corpus: Corpus, typed, *, cwd=None, project_root=None,
                    now=None, config: CompletionConfig | None = None,
                    prev_command=None, min_confidence=0.7):
    """The winning prefix-extension for ghost text, with its confidence.

    Returns ``(command, confidence)`` or ``(None, confidence)``. Only true
    prefix extensions are eligible — ghost text renders a suffix at the
    cursor, so a fuzzy match has nothing to draw. Risk gating stays with
    the caller (``suggest.ghost_completion``).
    """
    ranked = rank(corpus, typed=typed, cwd=cwd, project_root=project_root,
                  now=now, config=config, prev_command=prev_command)
    extensions = [s for s in ranked if s.command.startswith(typed)
                  and s.command != typed]
    if not extensions:
        return None, 0.0
    conf = confidence(extensions, typed)
    if conf < min_confidence:
        return None, conf
    return extensions[0].command, conf
