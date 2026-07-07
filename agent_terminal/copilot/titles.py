"""Tab-title inference from the command journal, plus command analysis.

A title reads "project: command" — the working directory's basename
and a short summary of the most recent meaningful command — and is
damped so it never flaps (see TitlePolicy). Program-set titles (vim,
ssh, htop) are handled in the GTK layer and win while they run; this
module only produces the at-the-prompt title.
"""

from __future__ import annotations

import os

# Commands with no lasting effect and near-zero intent. A session made
# only of these is not worth a title or a stored session.
TRIVIAL_COMMANDS = frozenset({
    "ls", "l", "ll", "la", "cd", "clear", "pwd", "exit", "logout",
    "top", "htop", "echo", "history", "jobs", "fg", "bg", "reset",
    "true", "false", ":", "whoami", "date", "uptime", "w", "id",
    "hostname", "tput", "env", "printenv", "which", "type",
})

# Tools whose first argument names the real action worth showing.
_SUBCOMMAND_TOOLS = frozenset({
    "git", "docker", "kubectl", "npm", "pnpm", "yarn", "cargo", "uv",
    "pip", "apt", "systemctl", "make", "just", "go", "poetry", "podman",
})


def command_base(command) -> str | None:
    """First meaningful token of a command line, skipping VAR=val prefixes."""
    if not isinstance(command, str):
        return None
    for token in command.split():
        if "=" in token and not token.startswith(("-", "/", ".")):
            # leading environment assignment like FOO=bar cmd
            head = token.split("=", 1)[0]
            if head and all(c.isalnum() or c == "_" for c in head):
                continue
        return token
    return None


def is_trivial(command) -> bool:
    base = command_base(command)
    return base is None or base in TRIVIAL_COMMANDS


def command_summary(command) -> str | None:
    """Short label for a command: "git commit", "pytest", "docker logs"."""
    base = command_base(command)
    if base is None:
        return None
    name = os.path.basename(base)
    if name in _SUBCOMMAND_TOOLS:
        for token in command.split()[1:]:
            if token.startswith("-"):
                continue
            return f"{name} {token}"
    return name


def _last_meaningful(records):
    for record in reversed(records):
        if record.cmd and not is_trivial(record.cmd):
            return record
    return None


def infer_title(records, cwd) -> str | None:
    """Infer a "project: command" tab title, or None to leave it alone."""
    project = os.path.basename(cwd.rstrip("/")) if cwd else None
    project = project or None
    record = _last_meaningful(records)
    command = command_summary(record.cmd) if record is not None else None
    if project and command:
        return f"{project}: {command}"
    return project or command or None


class TitlePolicy:
    """Damps title changes so a tab title never flaps.

    A new title is accepted only when it differs from the current one
    and at least min_interval_s has elapsed since the last change; the
    first title is always accepted.
    """

    def __init__(self, min_interval_s: float):
        self.min_interval_s = max(float(min_interval_s), 0.0)
        self.current: str | None = None
        self.last_change: float | None = None

    def propose(self, candidate, now) -> str | None:
        if candidate is None or candidate == self.current:
            return None
        if (self.last_change is not None
                and now - self.last_change < self.min_interval_s):
            return None
        self.current = candidate
        self.last_change = now
        return candidate
