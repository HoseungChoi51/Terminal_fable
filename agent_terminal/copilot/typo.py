"""High-confidence typo correction (design doc 5.5).

Fixes only what it is confident about: a mistyped known command, a
broken URL prefix, and a couple of path-shorthand slips. Corrections
are always visible and never auto-run. The risk-escalation guard means
a correction that turns a harmless typo into a more dangerous command
(``kubectl detele`` → ``kubectl delete``) is reported so the caller can
keep it out of one-keystroke paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent_terminal.copilot import risk as risk_mod
from agent_terminal.copilot.titles import command_base
from agent_terminal.tui_core import scan_directory

_SHELL_BUILTINS = frozenset({
    "cd", "echo", "export", "alias", "unalias", "set", "unset", "source",
    "pushd", "popd", "exit", "kill", "jobs", "fg", "bg", "type", "which",
    "history", "test", "read", "wait", "trap", "eval", "exec",
})
_MAX_EDITS = 2
_MIN_LEN = 3

# Subcommands worth correcting for a few common tools.
_TOOL_SUBCOMMANDS = {
    "git": frozenset({
        "add", "commit", "checkout", "switch", "status", "log", "diff",
        "push", "pull", "clone", "fetch", "merge", "rebase", "reset",
        "restore", "stash", "branch", "tag", "remote", "show", "init",
        "rm", "mv", "cherry-pick", "revert", "bisect", "worktree"}),
    "kubectl": frozenset({
        "get", "describe", "delete", "apply", "create", "logs", "exec",
        "edit", "scale", "rollout", "expose", "port-forward", "top",
        "config", "run", "set", "label", "annotate", "patch", "cordon",
        "drain"}),
    "docker": frozenset({
        "run", "build", "pull", "push", "ps", "images", "exec", "logs",
        "stop", "start", "rm", "rmi", "compose", "inspect", "tag",
        "commit", "volume", "network", "system", "stats", "restart"}),
    "cargo": frozenset({
        "build", "run", "test", "check", "new", "init", "add", "remove",
        "update", "publish", "install", "clean", "doc", "bench", "clippy"}),
    "systemctl": frozenset({
        "start", "stop", "restart", "status", "enable", "disable",
        "reload", "mask", "unmask", "daemon-reload", "is-active"}),
    "npm": frozenset({
        "install", "run", "test", "start", "build", "init", "publish",
        "update", "uninstall", "ci", "audit", "exec"}),
}
_DANGEROUS = frozenset({risk_mod.DESTRUCTIVE, risk_mod.PRIVILEGED})


@dataclass(frozen=True)
class Correction:
    original: str
    corrected: str
    reason: str          # "command" | "url" | "path"
    escalates_risk: bool  # True if the fix raises the risk class


def path_commands(path_env=None, *, scandir=scan_directory) -> frozenset:
    """Executable names on PATH, plus common shell builtins."""
    env = path_env if path_env is not None else os.environ.get("PATH", "")
    names = set(_SHELL_BUILTINS)
    for directory in env.split(os.pathsep):
        if not directory:
            continue
        for entry in scandir(directory):
            if not entry.is_dir:
                names.add(entry.name)
    return frozenset(names)


def _edit_distance(a, b, cap=_MAX_EDITS):
    """Damerau OSA distance (transposition = 1 edit), capped for speed."""
    la, lb = len(a), len(b)
    if abs(la - lb) > cap:
        return cap + 1
    prev2 = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        current = [i] + [0] * lb
        best = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            value = min(prev[j] + 1, current[j - 1] + 1, prev[j - 1] + cost)
            if (i > 1 and j > 1 and a[i - 1] == b[j - 2]
                    and a[i - 2] == b[j - 1]):
                value = min(value, prev2[j - 2] + 1)
            current[j] = value
            best = min(best, value)
        if best > cap:
            return cap + 1
        prev2, prev = prev, current
    return prev[lb]


def nearest_command(word, known):
    """The single closest known command within the edit cap, or None."""
    if len(word) < _MIN_LEN or word in known:
        return None
    best, best_dist, ties = None, _MAX_EDITS + 1, 0
    for candidate in known:
        if abs(len(candidate) - len(word)) > _MAX_EDITS:
            continue
        dist = _edit_distance(word, candidate)
        if dist < best_dist:
            best, best_dist, ties = candidate, dist, 1
        elif dist == best_dist:
            ties += 1
    if best is None or best_dist > _MAX_EDITS or ties > 1:
        return None
    return best


_URL_FIXES = (
    ("ttps://", "https://"), ("htps://", "https://"), ("htp://", "http://"),
    ("ttp://", "http://"), ("https:/", "https://"), ("http:/", "http://"),
    ("hhttp", "http"),
)


def _fix_url(token):
    for bad, good in _URL_FIXES:
        if token.startswith(bad) and not token.startswith(good):
            return good + token[len(bad):]
    return token


def _fix_path(token):
    if token.startswith(".../"):
        return "../" + token[4:]
    if token == "...":
        return ".."
    return token


def correct_command(line, *, known=frozenset(), history=()) -> Correction | None:
    """Return a visible correction for `line`, or None."""
    if not line or not line.strip():
        return None
    tokens = line.split()
    changed = False
    reason = ""

    # First token: mistyped command name (skip if it's a path).
    head = tokens[0]
    if "/" not in head and head not in known:
        # The just-failed command is often already in history; exclude it
        # so it is not mistaken for a valid target.
        candidates = set(known)
        candidates.update(b for b in (command_base(h) for h in history)
                          if b and b != head)
        fixed = nearest_command(head, candidates)
        if fixed and fixed != head:
            tokens[0] = fixed
            changed = True
            reason = "command"

    # Subcommand of a known tool (git/kubectl/docker/...).
    subcommands = _TOOL_SUBCOMMANDS.get(tokens[0])
    if subcommands is not None:
        for i in range(1, len(tokens)):
            if tokens[i].startswith("-"):
                continue
            fixed = nearest_command(tokens[i], subcommands)
            if fixed and fixed != tokens[i]:
                tokens[i] = fixed
                changed = True
                reason = "command"
            break

    # URL and path repairs on every token.
    for i, tok in enumerate(tokens):
        new = _fix_path(_fix_url(tok))
        if new != tok:
            tokens[i] = new
            changed = True
            reason = reason or ("url" if "://" in new else "path")

    if not changed:
        return None
    corrected = " ".join(tokens)
    corrected_risk = risk_mod.classify(corrected)
    original_risk = risk_mod.classify(line)
    # Only flag a fix that introduces real danger (design doc 5.5), not one
    # that merely resolves an unknown command into a normal one.
    escalates = (corrected_risk.display in _DANGEROUS
                 and corrected_risk.severity > original_risk.severity)
    return Correction(original=line, corrected=corrected, reason=reason,
                      escalates_risk=escalates)
