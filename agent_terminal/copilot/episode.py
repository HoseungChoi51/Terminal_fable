"""Segment a journal into "episodes" — the task you're working on now.

Commands cluster in time within one directory while you're on a task; a
long idle gap, or a `cd` into another project, starts a new one. Ask mode
describes the *current* episode (its commands + digested output) rather
than a flat "last N commands", so the model understands what you're doing.

Pure, GTK-free, stdlib-only (ADR 0005): the caller supplies journal records
(and, for the optional branch signal, a per-record branch), so the core
never shells out to git or reads the clock itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent_terminal.copilot import titles

# 8 minutes: a gap wider than this reads as "a different sitting / task".
# Tunable; the sensitivity is exercised in the tests.
DEFAULT_IDLE_GAP_S = 480.0


@dataclass(frozen=True)
class Episode:
    records: tuple                 # CommandRecord slice, in order
    title: str                     # "project: command" (via titles.infer_title)
    project: str | None
    started_at: float | None
    ended_at: float | None
    command_count: int             # non-trivial commands
    failure_count: int

    @property
    def span_s(self) -> float | None:
        if self.started_at is None or self.ended_at is None:
            return None
        return max(self.ended_at - self.started_at, 0.0)

    def headline(self) -> str:
        parts = [self.title]
        span = self.span_s
        if span is not None and span >= 1:
            parts.append(_human_span(span))
        parts.append(f"{self.command_count} cmd"
                     + ("" if self.command_count == 1 else "s"))
        if self.failure_count:
            parts.append(f"{self.failure_count} failure"
                         + ("" if self.failure_count == 1 else "s"))
        return " · ".join(parts)


def _human_span(seconds) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} min"
    return f"{minutes // 60}h {minutes % 60}min"


def _branch_of(record):
    return getattr(record, "branch", None)


def _is_boundary(prev, cur, idle_gap_s, cwd_break) -> bool:
    """Does `cur` start a new episode after `prev`?"""
    if prev is None:
        return False
    if cwd_break and prev.cwd and cur.cwd and prev.cwd != cur.cwd:
        return True
    prev_branch, cur_branch = _branch_of(prev), _branch_of(cur)
    if prev_branch and cur_branch and prev_branch != cur_branch:
        return True
    if (prev.started_at is not None and cur.started_at is not None
            and cur.started_at - prev.started_at > idle_gap_s):
        return True
    return False


def _make_episode(records) -> Episode:
    records = tuple(records)
    cwd_last = next((r.cwd for r in reversed(records) if r.cwd), None)
    project = os.path.basename(cwd_last.rstrip("/")) if cwd_last else None
    title = titles.infer_title(records, cwd_last) or project or "session"
    starts = [r.started_at for r in records if r.started_at is not None]
    ends = [r.started_at + (r.duration_s or 0.0)
            for r in records if r.started_at is not None]
    nontrivial = sum(1 for r in records
                     if r.cmd and not titles.is_trivial(r.cmd))
    failures = sum(1 for r in records if r.exit_code not in (0, None))
    return Episode(
        records=records, title=title, project=project or None,
        started_at=min(starts) if starts else None,
        ended_at=max(ends) if ends else None,
        command_count=nontrivial, failure_count=failures)


def segment(records, *, idle_gap_s=DEFAULT_IDLE_GAP_S,
            cwd_break=True) -> list[Episode]:
    """Split records into episodes at idle gaps / cwd (or branch) changes."""
    episodes: list[Episode] = []
    current: list = []
    prev = None
    for record in records:
        if current and _is_boundary(prev, record, idle_gap_s, cwd_break):
            episodes.append(_make_episode(current))
            current = []
        current.append(record)
        prev = record
    if current:
        episodes.append(_make_episode(current))
    return episodes


def current_episode(records, *, idle_gap_s=DEFAULT_IDLE_GAP_S,
                    cwd_break=True) -> Episode | None:
    """The most recent episode — the task the user is on right now."""
    episodes = segment(records, idle_gap_s=idle_gap_s, cwd_break=cwd_break)
    return episodes[-1] if episodes else None
