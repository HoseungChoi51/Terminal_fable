"""Session persistence: build, store, list, and restore terminal sessions.

A session is one terminal pane's worth of command history. Meaningful
sessions are written to $XDG_DATA_HOME/agent-terminal/sessions/<id>/ as
session.json plus a human summary.md (a real markdown file so restore
can reuse the Markdown viewer pane unchanged). This module does disk
I/O but no GTK, so it stays headless-testable. Everything it stores is
already redacted by the journal (ADR 0007).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from agent_terminal.copilot.config import SessionsConfig
from agent_terminal.copilot.journal import CommandRecord
from agent_terminal.copilot.titles import command_summary, is_trivial

SCHEMA_VERSION = 1
_SESSIONS_SUBDIR = "sessions"
_MEANINGFUL_MIN_COMMANDS = 3
_MEANINGFUL_MIN_DURATION_S = 5.0


@dataclass(frozen=True)
class SessionRecord:
    id: str
    title: str
    cwd_first: str | None
    cwd_last: str | None
    project: str | None
    started_at: float
    ended_at: float
    commands: tuple[CommandRecord, ...]
    summary: str
    meaningful: bool = True
    summary_kind: str = "heuristic"


@dataclass(frozen=True)
class SessionSummary:
    id: str
    title: str
    cwd_last: str | None
    project: str | None
    ended_at: float
    command_count: int
    last_command: str | None = None
    started_at: float = 0.0        # for job clustering by co-activity


def default_base_dir(env=None) -> str:
    env = os.environ if env is None else env
    base = (env.get("XDG_DATA_HOME")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(base, "agent-terminal")


def make_session_id(started_at, localtime=time.localtime) -> str:
    """Sortable id: YYYYmmdd-HHMMSS-mmm, millisecond-unique."""
    stamp = time.strftime("%Y%m%d-%H%M%S", localtime(started_at))
    millis = int((started_at - int(started_at)) * 1000)
    return f"{stamp}-{millis:03d}"


# -- serialization -------------------------------------------------------

def _record_to_dict(record: CommandRecord) -> dict:
    return {
        "seq": record.seq, "cmd": record.cmd, "cwd": record.cwd,
        "started_at": record.started_at, "duration_s": record.duration_s,
        "exit_code": record.exit_code,
        "output_tail": list(record.output_tail)
        if record.output_tail is not None else None,
        "capture": record.capture, "redacted": record.redacted,
    }


def _record_from_dict(data: dict) -> CommandRecord:
    tail = data.get("output_tail")
    return CommandRecord(
        seq=data.get("seq", 0), cmd=data.get("cmd"), cwd=data.get("cwd"),
        started_at=data.get("started_at"),
        duration_s=data.get("duration_s"), exit_code=data.get("exit_code"),
        output_tail=tuple(tail) if isinstance(tail, list) else None,
        capture=data.get("capture", "none"),
        redacted=bool(data.get("redacted", False)))


def session_to_json(session: SessionRecord) -> str:
    return json.dumps({
        "version": SCHEMA_VERSION,
        "id": session.id, "title": session.title,
        "cwd_first": session.cwd_first, "cwd_last": session.cwd_last,
        "project": session.project,
        "started_at": session.started_at, "ended_at": session.ended_at,
        "commands": [_record_to_dict(r) for r in session.commands],
        "meaningful": session.meaningful,
        "summary_kind": session.summary_kind,
    }, indent=2)


def session_from_json(text) -> SessionRecord | None:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or "id" not in data:
        return None
    commands = tuple(_record_from_dict(d)
                     for d in data.get("commands", [])
                     if isinstance(d, dict))
    return SessionRecord(
        id=str(data["id"]), title=data.get("title", "Session"),
        cwd_first=data.get("cwd_first"), cwd_last=data.get("cwd_last"),
        project=data.get("project"),
        started_at=float(data.get("started_at", 0.0)),
        ended_at=float(data.get("ended_at", 0.0)),
        commands=commands, summary="",
        meaningful=bool(data.get("meaningful", True)),
        summary_kind=data.get("summary_kind", "heuristic"))


# -- building a session from a journal -----------------------------------

def _dir_excluded(cwd, exclude_dirs) -> bool:
    if not cwd:
        return False
    resolved = os.path.normpath(cwd)
    for pattern in exclude_dirs:
        prefix = os.path.normpath(os.path.expanduser(pattern))
        if resolved == prefix or resolved.startswith(prefix + os.sep):
            return True
    return False


def _command_excluded(cmd, exclude_commands) -> bool:
    if not cmd:
        return False
    return any(fnmatch(cmd, pattern) for pattern in exclude_commands)


def is_meaningful(records) -> bool:
    """Worth remembering: 3+ non-trivial commands, or 1 that ran >= 5 s."""
    nontrivial = [r for r in records if r.cmd and not is_trivial(r.cmd)]
    if len(nontrivial) >= _MEANINGFUL_MIN_COMMANDS:
        return True
    return any((r.duration_s or 0.0) >= _MEANINGFUL_MIN_DURATION_S
               for r in nontrivial)


def summarize(records, cwd) -> str:
    """Heuristic markdown session summary (design doc 5.6)."""
    lines = ["# Session summary", ""]
    if cwd:
        lines += [f"Working in `{cwd}`.", ""]
    nontrivial = [r for r in records if r.cmd and not is_trivial(r.cmd)]
    if nontrivial:
        lines += ["Recent commands:", ""]
        for record in nontrivial[-8:]:
            mark = ("" if record.exit_code in (0, None)
                    else f" — exit {record.exit_code}")
            lines.append(f"- `{record.cmd}`{mark}")
        lines.append("")
    last_fail = next((r for r in reversed(records)
                      if r.exit_code not in (0, None) and r.cmd), None)
    if last_fail is not None:
        lines += [f"The last failing command was `{last_fail.cmd}` "
                  f"(exit {last_fail.exit_code}).", ""]
    return "\n".join(lines)


def _infer_project_and_title(records, cwd_last):
    project = os.path.basename(cwd_last.rstrip("/")) if cwd_last else None
    project = project or None
    last = next((r for r in reversed(records)
                 if r.cmd and not is_trivial(r.cmd)), None)
    command = command_summary(last.cmd) if last is not None else None
    if project and command:
        title = f"{project}: {command}"
    else:
        title = project or command or "Session"
    return project, title


def build_session(records, *, config: SessionsConfig, now,
                  localtime=time.localtime) -> SessionRecord | None:
    """Turn a journal snapshot into a storable session, or None to skip."""
    records = tuple(r for r in records
                    if not _command_excluded(r.cmd, config.exclude_commands))
    if not records:
        return None
    cwds = [r.cwd for r in records if r.cwd]
    cwd_first = cwds[0] if cwds else None
    cwd_last = cwds[-1] if cwds else None
    if any(_dir_excluded(r.cwd, config.exclude_dirs) for r in records):
        return None
    if not is_meaningful(records):
        return None
    if not config.store_output:
        records = tuple(_strip_output(r) for r in records)
    started_at = next((r.started_at for r in records
                       if r.started_at is not None), now)
    project, title = _infer_project_and_title(records, cwd_last)
    return SessionRecord(
        id=make_session_id(started_at, localtime),
        title=title, cwd_first=cwd_first, cwd_last=cwd_last, project=project,
        started_at=started_at, ended_at=now, commands=records,
        summary=summarize(records, cwd_last), meaningful=True)


def _strip_output(record: CommandRecord) -> CommandRecord:
    if record.output_tail is None:
        return record
    return CommandRecord(
        seq=record.seq, cmd=record.cmd, cwd=record.cwd,
        started_at=record.started_at, duration_s=record.duration_s,
        exit_code=record.exit_code, output_tail=None,
        capture=record.capture, redacted=record.redacted)


# -- the on-disk store ---------------------------------------------------

class SessionStore:
    """Directory of per-session subdirectories under a base data dir."""

    def __init__(self, base_dir):
        self.root = Path(base_dir) / _SESSIONS_SUBDIR

    def dir_for(self, session_id) -> Path:
        return self.root / session_id

    def save(self, session: SessionRecord) -> bool:
        target = self.dir_for(session.id)
        try:
            target.mkdir(parents=True, exist_ok=True)
            (target / "session.json").write_text(
                session_to_json(session), encoding="utf-8")
            (target / "summary.md").write_text(
                session.summary, encoding="utf-8")
            return True
        except OSError:
            return False

    def summary_path(self, session_id) -> str:
        return str(self.dir_for(session_id) / "summary.md")

    def list(self) -> list[SessionSummary]:
        summaries = []
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return []
        for entry in entries:
            summary = self._read_summary(entry)
            if summary is not None:
                summaries.append(summary)
        summaries.sort(key=lambda s: s.ended_at, reverse=True)
        return summaries

    def _read_summary(self, entry: Path) -> SessionSummary | None:
        try:
            data = json.loads((entry / "session.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or "id" not in data:
            return None
        commands = [c for c in data.get("commands", []) if isinstance(c, dict)]
        last_cmd = next((c.get("cmd") for c in reversed(commands)
                         if c.get("cmd")), None)
        return SessionSummary(
            id=str(data["id"]), title=data.get("title", "Session"),
            cwd_last=data.get("cwd_last"), project=data.get("project"),
            ended_at=float(data.get("ended_at", 0.0)),
            command_count=len(commands), last_command=last_cmd,
            started_at=float(data.get("started_at", 0.0)))

    def load(self, session_id) -> SessionRecord | None:
        try:
            text = (self.dir_for(session_id) / "session.json").read_text(
                encoding="utf-8")
        except OSError:
            return None
        return session_from_json(text)

    def sweep(self, retention_days, now) -> int:
        """Delete sessions older than retention_days; returns count removed."""
        if retention_days <= 0:
            return 0
        cutoff = now - retention_days * 86400
        removed = 0
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return 0
        for entry in entries:
            summary = self._read_summary(entry)
            ended = summary.ended_at if summary is not None else None
            if ended is None:
                try:
                    ended = entry.stat().st_mtime
                except OSError:
                    continue
            if ended < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        return removed
