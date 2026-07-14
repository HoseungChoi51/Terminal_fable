"""Merge and rank suggestions from recipes and command history.

The completion menu asks build_suggestions() for a ranked list given a
filter query, the builtin recipes, and this pane's recent commands.
Suggestions are deduplicated by command text, risk-labeled, and sorted
by score. insert_plan() decides what to feed the terminal when one is
accepted — always without a trailing newline, so nothing runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_terminal.copilot import fuzzy
from agent_terminal.copilot import recipes as recipes_mod
from agent_terminal.copilot import risk as risk_mod
from agent_terminal.copilot.titles import is_trivial

SOURCE_RECIPE = "recipe"
SOURCE_HISTORY = "history"

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


def build_suggestions(query, *, recipes=recipes_mod.BUILTIN_RECIPES,
                      history=(), limit=8):
    """Ranked, deduplicated suggestions for the completion menu."""
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


def ghost_completion(typed, *, recipes=recipes_mod.BUILTIN_RECIPES,
                     history=(), min_confidence=0.7):
    """Suffix to show as inline ghost text after `typed`, or None.

    Prefers the most recent history command that extends the prefix,
    then a recipe. Never ghosts a destructive/privileged/unknown command
    (design doc 8.4), a multi-line command, or below min_confidence.
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
