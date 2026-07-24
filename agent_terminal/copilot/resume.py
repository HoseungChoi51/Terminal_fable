"""Episode-scoped resume: turn a stored session's episode into a shell-
history seed so a restored pane recalls exactly that stretch of work.

A session is one pane's whole history; an *episode* is a time/cwd-coherent
slice of it (copilot.episode). Resuming an episode should surface only that
episode's commands for up-arrow recall — not the whole session, not sibling
episodes — landed in the pane's cwd.

Pure/stdlib-only (ADR 0005). Everything here consumes already-redacted
commands (the journal redacts before storing, ADR 0007/0008) and merely
selects/reshapes them, so a redacted secret can never be un-redacted here.
"""

from __future__ import annotations

from agent_terminal.copilot import episode as episode_mod
from agent_terminal.copilot import titles

# Cap the seed so a marathon episode can't write a huge history file; the
# most recent unique commands are the ones worth recalling.
_MAX_SEED_LINES = 200


def episodes_of(session, *, idle_gap_s=episode_mod.DEFAULT_IDLE_GAP_S):
    """Segment a stored SessionRecord's commands into episodes (newest last)."""
    records = getattr(session, "commands", None) or ()
    return episode_mod.segment(records, idle_gap_s=idle_gap_s)


def _one_line(cmd: str) -> str:
    """Collapse a multi-line command (heredoc) to a single history entry.

    A history file is one entry per line; a raw multi-line command would be
    read back as several bogus entries. Join its lines with "; " so it stays
    one recallable entry with its intent intact (the user edits before run).
    """
    parts = [line for line in cmd.splitlines() if line.strip()]
    return "; ".join(parts)


def seed_history_lines(episode, *, max_lines=_MAX_SEED_LINES) -> tuple:
    """The episode's commands as history-seed lines: non-trivial, de-duped
    (erasedups-style — keep each command's latest position), order preserved,
    one entry per line, capped."""
    if episode is None:
        return ()
    cleaned = []
    for record in getattr(episode, "records", ()) or ():
        cmd = (getattr(record, "cmd", None) or "").strip()
        if not cmd or titles.is_trivial(cmd):
            continue
        line = _one_line(cmd)
        if line:
            cleaned.append(line)
    # erasedups: keep the last occurrence of each command, preserve order.
    seen, kept = set(), []
    for line in reversed(cleaned):
        if line in seen:
            continue
        seen.add(line)
        kept.append(line)
    kept.reverse()
    return tuple(kept[-max_lines:])


def seed_file_content(episode, *, max_lines=_MAX_SEED_LINES) -> str:
    """The seed lines rendered as a bash history file (trailing newline)."""
    lines = seed_history_lines(episode, max_lines=max_lines)
    return "".join(f"{line}\n" for line in lines)


def resume_cwd(episode) -> str | None:
    """The directory to reopen the pane in: the episode's last known cwd."""
    if episode is None:
        return None
    for record in reversed(getattr(episode, "records", ()) or ()):
        cwd = getattr(record, "cwd", None)
        if cwd:
            return cwd
    return None
