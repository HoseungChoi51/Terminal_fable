"""Fuzzy scoring for suggestion ranking.

Two modes, chosen by the query: a multi-word query is scored by
requiring every word to appear as a substring (good for recipe search
like "sort by size"); a single-token query is scored as a subsequence
match with consecutive/word-boundary bonuses (good for command
completion like "gco"). Higher is better; None means no match.
"""

from __future__ import annotations

_BOUNDARY = " -_/.:=@"


def _boundary_bonus(text, index) -> float:
    return 1.5 if index == 0 or text[index - 1] in _BOUNDARY else 0.0


def _subsequence(query, text) -> float | None:
    pos = 0
    prev = -2
    first = None
    score = 0.0
    for char in query:
        index = text.find(char, pos)
        if index < 0:
            return None
        if first is None:
            first = index
        score += 2.0 if index == prev + 1 else 1.0
        score += _boundary_bonus(text, index)
        prev = index
        pos = index + 1
    if text.startswith(query):
        score += 3.0
    score -= (first or 0) * 0.05
    score -= max(len(text) - len(query), 0) * 0.01
    return score


def _tokens(query, text) -> float | None:
    total = 0.0
    for token in query.split():
        index = text.find(token)
        if index < 0:
            return None
        total += 3.0 + _boundary_bonus(text, index)
    total -= max(len(text) - len(query), 0) * 0.01
    return total


def score(query, text) -> float | None:
    """Score `text` against `query`; None if it does not match."""
    query = (query or "").strip().casefold()
    text = (text or "").casefold()
    if not query:
        return 0.0
    if not text:
        return None
    if len(query.split()) > 1:
        return _tokens(query, text)
    return _subsequence(query, text)
