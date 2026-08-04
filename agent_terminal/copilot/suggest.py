"""Merge and rank suggestions from recipes and command history.

The completion menu asks build_suggestions() for a ranked list given a
filter query, the builtin recipes, and this pane's recent commands.
Suggestions are deduplicated by command text, risk-labeled, and sorted
by score. insert_plan() decides what to feed the terminal when one is
accepted — always without a trailing newline, so nothing runs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from agent_terminal.copilot import fuzzy
from agent_terminal.copilot import ranking as ranking_mod
from agent_terminal.copilot import recipes as recipes_mod
from agent_terminal.copilot import risk as risk_mod
from agent_terminal.copilot.titles import is_trivial

SOURCE_RECIPE = "recipe"
SOURCE_HISTORY = "history"

# -- normalized score bands ----------------------------------------------
#
# Sources produce scores on wildly different natural scales (frecency is
# roughly 0-8, fuzzy roughly 3-15), so comparing them raw is meaningless.
# Each source is squashed into (0, 1) and multiplied by its band ceiling,
# which makes one ranked list out of all of them.
CORPUS_CEILING = 10.0     # your own usage, frecency-ranked
RECIPE_CEILING = 7.0      # generic builtins: below your habits, above noise
_CORPUS_SCALE = 2.0       # typical frecency magnitude
_RECIPE_SCALE = 8.0       # typical fuzzy magnitude

# Merge bands (see merge_suggestions). Higher bands win outright; within a
# band, the normalized score decides.
BAND_CORRECTION = 300.0   # "did you mean" — a typo fix always leads
BAND_ARGUMENT = 200.0     # a concrete path/branch/host for the token typed
BAND_PRIMARY = 100.0      # habits, project/README commands, recipes

# History outranks an identical recipe (it is what this user actually runs),
# and, all else equal, ranks slightly above recipes on ties.
_HISTORY_BONUS = 0.5
# Small per-step recency boost so recent history wins near-ties without
# overriding a genuinely stronger fuzzy match.
_RECENCY_STEP = 0.15
_RECENCY_MAX = 1.5


@dataclass(frozen=True)
class Suggestion:
    command: str
    label: str
    source: str
    risk: risk_mod.RiskResult
    score: float
    description: str = ""


def make_suggestion(command, label, *, source="context", score=5.0,
                    description=""):
    """Build a risk-classified Suggestion (for context/typo sources)."""
    return Suggestion(command=command, label=label, source=source,
                      risk=risk_mod.classify(command), score=score,
                      description=description)


def _dedupe_history(commands):
    """Most-recent-first unique commands, dropping trivial ones."""
    seen = set()
    ordered = []
    for command in reversed(list(commands)):
        text = (command or "").strip()
        if not text or text in seen or is_trivial(text):
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def build_corpus_suggestions(query, corpus, *, recipes=(), cwd=None,
                             project_root=None, now=None, config=None,
                             prev_command=None, limit=8):
    """Menu suggestions ranked against the persistent corpus.

    The corpus-backed counterpart to build_suggestions(): same output
    shape, but scored by frecency + context instead of raw fuzzy bonuses,
    and normalized so corpus and recipe scores are directly comparable.
    """
    query = query or ""
    ranked = ranking_mod.rank(
        corpus, typed=query, cwd=cwd, project_root=project_root, now=now,
        config=config, prev_command=prev_command)
    by_command = {}
    for scored in ranked:
        if is_trivial(scored.command):
            continue
        by_command[scored.command] = Suggestion(
            command=scored.command, label="history", source=SOURCE_HISTORY,
            risk=risk_mod.classify(scored.command),
            score=CORPUS_CEILING * ranking_mod.squash(scored.score,
                                                     _CORPUS_SCALE))
    for recipe in recipes:
        if recipe.command in by_command:
            continue           # your own version of it already ranked
        value = fuzzy.score(query, recipe.haystack())
        if value is None:
            continue
        by_command[recipe.command] = Suggestion(
            command=recipe.command, label="recipe", source=SOURCE_RECIPE,
            risk=recipe.risk or risk_mod.classify(recipe.command),
            score=RECIPE_CEILING * ranking_mod.squash(value, _RECIPE_SCALE),
            description=recipe.description)
    out = sorted(by_command.values(), key=lambda s: (-s.score, s.command))
    return out[:limit] if limit is not None else out


def normalized(suggestions, *, ceiling=CORPUS_CEILING, scale=_RECIPE_SCALE):
    """Re-scale raw build_suggestions() scores into the normalized band.

    The corpus-free path still produces unbounded fuzzy scores; squashing
    them keeps the banded merge comparable whichever path produced them.
    """
    return [replace(s, score=ceiling * ranking_mod.squash(s.score, scale))
            for s in suggestions]


def merge_suggestions(*groups, limit=12):
    """Merge banded suggestion groups into one ranked, deduplicated list.

    Each group is ``(band, suggestions)``. Ordering is by band first, then
    by normalized score — so a strong habit can outrank a weak README
    command, while a concrete argument completion still leads them both.
    Previously the menu ordered purely by which source ran first, which
    made scores decorative.
    """
    ordered = []
    for band, suggestions in groups:
        for suggestion in suggestions or ():
            ordered.append((band, suggestion))
    ordered.sort(key=lambda pair: (-pair[0], -pair[1].score,
                                   pair[1].command))
    out, seen = [], set()
    for _, suggestion in ordered:
        if suggestion.command in seen:
            continue
        seen.add(suggestion.command)
        out.append(suggestion)
    return out[:limit] if limit is not None else out


def build_suggestions(query, *, recipes=recipes_mod.BUILTIN_RECIPES,
                      history=(), limit=8):
    """Ranked, deduplicated suggestions for the completion menu.

    The corpus-free path, used when `completion.corpus` is disabled; see
    build_corpus_suggestions() for the frecency-ranked one.
    """
    query = query or ""
    by_command = {}

    for index, command in enumerate(_dedupe_history(history)):
        value = fuzzy.score(query, command)
        if value is None:
            continue
        recency = max(_RECENCY_MAX - index * _RECENCY_STEP, 0.0)
        by_command[command] = Suggestion(
            command=command, label="history", source=SOURCE_HISTORY,
            risk=risk_mod.classify(command),
            score=value + _HISTORY_BONUS + recency)

    for recipe in recipes:
        value = fuzzy.score(query, recipe.haystack())
        if value is None:
            continue
        existing = by_command.get(recipe.command)
        if existing is not None and existing.source == SOURCE_HISTORY:
            continue   # keep the history version of an identical command
        candidate = Suggestion(
            command=recipe.command, label="recipe", source=SOURCE_RECIPE,
            risk=recipe.risk or risk_mod.classify(recipe.command),
            score=value, description=recipe.description)
        if existing is None or candidate.score > existing.score:
            by_command[recipe.command] = candidate

    ranked = sorted(by_command.values(),
                    key=lambda s: s.score, reverse=True)
    return ranked[:limit] if limit is not None else ranked


# Ghost text is deliberately conservative: it completes from your own
# history readily, and from generic recipes only if you lower the bar.
_GHOST_HISTORY_CONFIDENCE = 0.9
_GHOST_RECIPE_CONFIDENCE = 0.6
_GHOST_MIN_PREFIX = 2
_GHOST_SAFE_RISK = frozenset({risk_mod.READ_ONLY, risk_mod.LOCAL_CHANGE})


def ghost_suffix(command, typed):
    """The renderable suffix of `command` after `typed`, or None."""
    if not command or not command.startswith(typed) or command == typed:
        return None
    if risk_mod.classify(command).display not in _GHOST_SAFE_RISK:
        return None
    suffix = command[len(typed):]
    if "\n" in suffix or not suffix:
        return None
    return suffix


def corpus_ghost_completion(typed, corpus, *, cwd=None, project_root=None,
                            now=None, config=None, prev_command=None,
                            min_confidence=0.7):
    """Ghost suffix ranked against the persistent corpus, or None.

    Unlike the history path below, the confidence gate here is a real
    measurement — how far the winner dominates the best rival proposing a
    different suffix — so min_confidence genuinely controls how eagerly
    ghost text appears.
    """
    if len(typed) < _GHOST_MIN_PREFIX:
        return None
    command, _ = ranking_mod.best_completion(
        corpus, typed, cwd=cwd, project_root=project_root, now=now,
        config=config, prev_command=prev_command,
        min_confidence=min_confidence)
    return ghost_suffix(command, typed)


def ghost_completion(typed, *, recipes=recipes_mod.BUILTIN_RECIPES,
                     history=(), min_confidence=0.7):
    """Suffix to show as inline ghost text after `typed`, or None.

    The corpus-free path: prefers the most recent history command that
    extends the prefix, then a recipe. Never ghosts a
    destructive/privileged/unknown command (design doc 8.4), a multi-line
    command, or below min_confidence.
    """
    if len(typed) < _GHOST_MIN_PREFIX:
        return None
    command = None
    confidence = 0.0
    for candidate in _dedupe_history(history):
        if candidate.startswith(typed) and candidate != typed:
            command, confidence = candidate, _GHOST_HISTORY_CONFIDENCE
            break
    if command is None:
        for recipe in recipes:
            if (recipe.command.startswith(typed)
                    and recipe.command != typed):
                command, confidence = recipe.command, _GHOST_RECIPE_CONFIDENCE
                break
    if command is None or confidence < min_confidence:
        return None
    if risk_mod.classify(command).display not in _GHOST_SAFE_RISK:
        return None
    suffix = command[len(typed):]
    if "\n" in suffix or not suffix:
        return None
    return suffix


def insert_plan(typed, command):
    """How to insert `command` given what is already typed.

    Returns (clear_line, text). When the command simply extends the
    typed prefix, only the remainder is fed (clear_line False). Otherwise
    the line is cleared first and the whole command is fed. Never a
    newline — the user presses Enter themselves.
    """
    typed = typed or ""
    if typed and command.startswith(typed):
        return False, command[len(typed):]
    return True, command
