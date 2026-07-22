"""Per-pane command journal fed by VTE shell-integration termprops.

Event protocol (verified against VTE 0.84; see docs/copilot.md):
the shell snippet emits OSC 666 termprops — "vte.shell.preexec!" from
PS0 when a command starts, and, from PROMPT_COMMAND when it finishes,
"vte.ext.agentterm.cmd=<base64 of `history 1`>" (only when a new
history entry appeared), "vte.shell.postexec=<exit>", and
"vte.shell.precmd!". VTE coalesces one output burst into one signal
batch whose within-batch order is NOT the emission order, so the GTK
layer collects a burst and hands it to apply_batch(), which applies
canonical ordering: preexec, cmd, postexec, precmd.
"""

from __future__ import annotations

import base64
import binascii
import itertools
from collections import deque
from dataclasses import dataclass

from agent_terminal.copilot.config import JournalConfig
from agent_terminal.copilot.redact import redact_lines

# Termprop names double as event names in apply_batch input.
PRECMD = "vte.shell.precmd"
PREEXEC = "vte.shell.preexec"
POSTEXEC = "vte.shell.postexec"
COMMAND_TERMPROP = "vte.ext.agentterm.cmd"

# Canonical order inside one signal batch.
_EVENT_ORDER = {PREEXEC: 0, COMMAND_TERMPROP: 1, POSTEXEC: 2, PRECMD: 3}

CAPTURE_TERMPROP = "termprop"
CAPTURE_SCREEN = "screen"   # reserved for later phases
CAPTURE_NONE = "none"

# Lines to read above the output tail so a PEM key block whose BEGIN marker
# sits just above the window is still recognized and dropped whole. Larger
# than any real private-key block; bounds the read so it stays cheap.
_PEM_MARGIN_LINES = 120

INTEGRATION_NONE = "cwd-only"
INTEGRATION_TERMPROPS = "integrated"

@dataclass(frozen=True)
class CommandRecord:
    seq: int
    cmd: str | None
    cwd: str | None
    started_at: float | None      # wall-clock, unix seconds
    duration_s: float | None
    exit_code: int | None
    output_tail: tuple[str, ...] | None
    capture: str = CAPTURE_NONE
    redacted: bool = False


def decode_command_payload(value) -> str | None:
    """Decode the base64 command payload from the ext termprop.

    The shell snippet already stripped the `history` number/marker and
    dropped leading-space (hidden) commands; this decodes the command
    text, with a leading-space check as a second line of defense.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        decoded = base64.b64decode(value, validate=True).decode(
            "utf-8", "replace")
    except (binascii.Error, ValueError):
        return None
    if decoded.startswith(" "):
        return None
    return decoded if decoded.strip() else None


class _Pending:
    __slots__ = ("started_at", "monotonic", "cwd", "exec_row",
                 "cmd", "exit_code", "duration_s")

    def __init__(self, started_at, monotonic, cwd, exec_row):
        self.started_at = started_at
        self.monotonic = monotonic
        self.cwd = cwd
        self.exec_row = exec_row
        self.cmd = None
        self.exit_code = None
        self.duration_s = None


class PaneJournal:
    """Pure ring of finished commands plus live capture state."""

    def __init__(self, config: JournalConfig | None = None):
        self.config = config or JournalConfig()
        self.records = deque(maxlen=max(self.config.max_commands, 1))
        self.integration = INTEGRATION_NONE
        self.state = "idle"           # idle | prompt | executing
        self.paused = False
        self.last_activity = None     # monotonic time of the last batch
        self._seq = itertools.count(1)
        self._pending: _Pending | None = None

    def idle_seconds(self, now):
        """Seconds since the last command activity, or None if never active."""
        if self.last_activity is None:
            return None
        return max(now - self.last_activity, 0.0)

    # -- event intake ----------------------------------------------------

    def apply_batch(self, events, *, timestamp, wall_time, cwd=None,
                    cursor_row=None, read_rows=None):
        """Apply one coalesced signal batch.

        events: iterable of (termprop_name, value). timestamp is a
        monotonic clock, wall_time unix seconds. read_rows(start, end)
        returns screen text lines for the half-open row range, or None.
        """
        if self.paused:
            return
        self.last_activity = timestamp
        ordered = sorted(events, key=lambda e: _EVENT_ORDER.get(e[0], 99))
        for name, value in ordered:
            if name == PREEXEC:
                self._on_preexec(timestamp, wall_time, cwd, cursor_row)
            elif name == COMMAND_TERMPROP:
                self._on_command(value)
            elif name == POSTEXEC:
                self._on_postexec(value, timestamp)
            elif name == PRECMD:
                self._on_precmd(cursor_row, read_rows)

    def _on_preexec(self, timestamp, wall_time, cwd, cursor_row):
        self.integration = INTEGRATION_TERMPROPS
        if self._pending is not None:
            # Missed the closing burst (e.g. scrollback trimmed it away):
            # finalize what we have rather than leaking the record.
            self._finalize(None, None)
        self._pending = _Pending(wall_time, timestamp, cwd, cursor_row)
        self.state = "executing"

    def _on_command(self, value):
        if self._pending is None:
            return
        self._pending.cmd = decode_command_payload(value)

    def _on_postexec(self, value, timestamp):
        self.integration = INTEGRATION_TERMPROPS
        if self._pending is None:
            return
        try:
            self._pending.exit_code = int(value)
        except (TypeError, ValueError):
            self._pending.exit_code = None
        self._pending.duration_s = max(timestamp - self._pending.monotonic,
                                       0.0)

    def _on_precmd(self, cursor_row, read_rows):
        self.integration = INTEGRATION_TERMPROPS
        if self._pending is not None:
            self._finalize(cursor_row, read_rows)
        self.state = "prompt"

    def _finalize(self, cursor_row, read_rows):
        pending = self._pending
        self._pending = None
        tail = None
        redacted = False
        if (self.config.store_output and read_rows is not None
                and cursor_row is not None and pending.exec_row is not None
                and cursor_row > pending.exec_row):
            keep = self.config.output_tail_lines
            # Read a PEM-sized margin above the tail, redact the block, THEN
            # slice: otherwise a key block whose BEGIN sits just above the
            # window is never recognized and its base64 body is kept.
            start = max(pending.exec_row + 1,
                        cursor_row - keep - _PEM_MARGIN_LINES)
            lines = read_rows(start, cursor_row)
            if lines:
                red, redacted = redact_lines(lines)
                tail = tuple(red[-keep:]) if keep else ()
        cmd = pending.cmd
        cmd_redacted = False
        if cmd is not None:
            # A command can be multi-line (heredoc); redact as a block so a
            # PEM private-key body is dropped whole, not sent line-by-line.
            parts, cmd_redacted = redact_lines(cmd.split("\n"))
            cmd = "\n".join(parts)
        self.records.append(CommandRecord(
            seq=next(self._seq),
            cmd=cmd,
            cwd=pending.cwd,
            started_at=pending.started_at,
            duration_s=pending.duration_s,
            exit_code=pending.exit_code,
            output_tail=tail,
            capture=CAPTURE_TERMPROP if cmd is not None else CAPTURE_NONE,
            redacted=redacted or cmd_redacted,
        ))

    # -- queries ----------------------------------------------------------

    def snapshot(self) -> tuple[CommandRecord, ...]:
        return tuple(self.records)

    def last_record(self) -> CommandRecord | None:
        return self.records[-1] if self.records else None

    def to_markdown(self) -> str:
        """Debug rendering of the journal as a markdown document."""
        lines = ["# Copilot journal (debug)", "",
                 f"- state: `{self.state}`",
                 f"- integration: `{self.integration}`",
                 f"- records: {len(self.records)}", ""]
        for record in reversed(self.records):
            exit_part = ("?" if record.exit_code is None
                         else str(record.exit_code))
            duration = ("" if record.duration_s is None
                        else f" · {record.duration_s:.1f}s")
            cmd = record.cmd if record.cmd is not None else "(not captured)"
            lines.append(f"## #{record.seq} — exit {exit_part}{duration}")
            lines.append("")
            if record.cwd:
                lines.append(f"`{record.cwd}`")
                lines.append("")
            lines.append("```")
            lines.append(cmd)
            lines.append("```")
            if record.output_tail:
                lines.append("")
                lines.extend(f"> {line}" for line in record.output_tail)
            lines.append("")
        return "\n".join(lines)
