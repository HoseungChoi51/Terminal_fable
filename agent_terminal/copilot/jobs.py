"""Group sessions into "jobs" and pack a job into bounded split-pane windows.

One job routinely spans several sessions at once — a server in one pane, a
client in another, an editor in a third. The strongest signal a terminal has
that sessions belong together is **co-activity in time**: their active
intervals overlap or nearly touch. Same-project sessions get a wider gap
tolerance (a build can idle a pane for minutes without ending the job).

Packing answers "how do I lay a job out without tiny illegible panes?" — never
sub-divide below a legibility floor (min cols×rows measured against the target
window/monitor). A job with more members than fit spills into more windows,
balanced, rather than one over-divided window.

Pure/stdlib-only (ADR 0005): the caller supplies session metadata and the
runtime pixel/cell sizes; this module never touches GTK or the clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 5 minutes: sessions whose active spans are within this of each other read as
# concurrent work. Widened for same-project members (a build/test can idle).
DEFAULT_JOB_GAP_S = 300.0
_PROJECT_GAP_FACTOR = 3.0

# Legibility floor + soft ceiling (the user's stated preference is ~6).
DEFAULT_MIN_COLS = 80
DEFAULT_MIN_ROWS = 24
DEFAULT_SOFT_MAX = 6


@dataclass(frozen=True)
class Job:
    members: tuple                 # sessions, ordered by started_at
    started_at: float
    ended_at: float
    title: str
    project: str | None

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class WindowPlan:
    members: tuple                 # sessions for one window
    cols: int                      # balanced grid that respects the floor
    rows: int

    @property
    def size(self) -> int:
        return len(self.members)


# -- clustering ---------------------------------------------------------

def _interval(session):
    start = getattr(session, "started_at", 0.0) or 0.0
    end = getattr(session, "ended_at", 0.0) or 0.0
    return (start, max(end, start))


def _same_project(a, b) -> bool:
    pa, pb = getattr(a, "project", None), getattr(b, "project", None)
    return bool(pa) and pa == pb


def _linked(a, b, gap_s) -> bool:
    """Do two sessions' active intervals overlap or nearly touch?"""
    (lo1, hi1), (lo2, hi2) = _interval(a), _interval(b)
    gap = max(lo1, lo2) - min(hi1, hi2)     # <= 0 when they overlap
    tolerance = gap_s * (_PROJECT_GAP_FACTOR if _same_project(a, b) else 1.0)
    return gap <= tolerance


def _title_for(members) -> tuple:
    projects = {getattr(m, "project", None) for m in members}
    projects.discard(None)
    if len(projects) == 1:
        project = next(iter(projects))
        return project, project
    # mixed projects: name the job after its busiest member
    busiest = max(members, key=lambda m: getattr(m, "command_count", 0))
    return getattr(busiest, "title", "job"), (
        next(iter(projects)) if len(projects) == 1 else None)


def cluster(sessions, *, gap_s=DEFAULT_JOB_GAP_S, min_size=2) -> list:
    """Group co-active sessions into jobs (size >= min_size). Deterministic:
    input order is preserved within each job; jobs are returned newest-first."""
    items = [s for s in sessions if _interval(s)[1] > 0]
    n = len(items)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _linked(items[i], items[j], gap_s):
                parent[find(i)] = find(j)

    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(items[i])

    jobs = []
    for members in groups.values():
        if len(members) < min_size:
            continue
        members = tuple(sorted(members, key=lambda m: _interval(m)[0]))
        title, project = _title_for(members)
        jobs.append(Job(
            members=members,
            started_at=min(_interval(m)[0] for m in members),
            ended_at=max(_interval(m)[1] for m in members),
            title=title, project=project))
    jobs.sort(key=lambda job: job.ended_at, reverse=True)
    return jobs


# -- packing (the legibility floor) -------------------------------------

def _fit_counts(avail_px, cell_px, min_cols, min_rows):
    (width, height), (char_w, char_h) = avail_px, cell_px
    max_c = max(1, int(width // max(min_cols * char_w, 1)))
    max_r = max(1, int(height // max(min_rows * char_h, 1)))
    return max_c, max_r


def capacity(avail_px, cell_px, *, min_cols=DEFAULT_MIN_COLS,
             min_rows=DEFAULT_MIN_ROWS, soft_max=DEFAULT_SOFT_MAX) -> int:
    """How many panes fit in one window at or above the legibility floor,
    capped by the soft ceiling."""
    max_c, max_r = _fit_counts(avail_px, cell_px, min_cols, min_rows)
    return max(1, min(max_c * max_r, soft_max))


def _even_split(n, windows):
    """Distribute n panes across `windows` windows as evenly as possible
    (sizes differ by at most one), largest windows first."""
    base, rem = divmod(n, windows)
    return [base + 1 if i < rem else base for i in range(windows)]


def _grid(k, avail_px, cell_px, min_cols, min_rows):
    """A balanced cols×rows for k panes that never exceeds what fits."""
    max_c, max_r = _fit_counts(avail_px, cell_px, min_cols, min_rows)
    cols = min(max_c, max(1, math.ceil(math.sqrt(k))))
    rows = min(max_r, max(1, math.ceil(k / cols)))
    # ensure the grid can actually hold k (widen columns if rows capped)
    while cols * rows < k and cols < max_c:
        cols += 1
        rows = min(max_r, max(1, math.ceil(k / cols)))
    return cols, max(1, math.ceil(k / cols))


def pack(members, *, avail_px, cell_px, min_cols=DEFAULT_MIN_COLS,
         min_rows=DEFAULT_MIN_ROWS, soft_max=DEFAULT_SOFT_MAX) -> list:
    """Pack members into the fewest balanced windows that keep every pane at
    or above the legibility floor. Same members, bigger monitor → fewer
    windows. Never produces an over-divided window."""
    members = tuple(members)
    n = len(members)
    if n == 0:
        return []
    cap = capacity(avail_px, cell_px, min_cols=min_cols,
                   min_rows=min_rows, soft_max=soft_max)
    windows = max(1, math.ceil(n / cap))
    plans, index = [], 0
    for size in _even_split(n, windows):
        chunk = members[index:index + size]
        index += size
        cols, rows = _grid(size, avail_px, cell_px, min_cols, min_rows)
        plans.append(WindowPlan(members=chunk, cols=cols, rows=rows))
    return plans
