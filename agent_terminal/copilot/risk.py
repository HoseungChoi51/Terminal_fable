"""Command risk classification (design doc 8.3).

classify() splits a command into pipeline segments, classifies each by
its leading program and arguments, and reports the highest-severity
label plus the full set. sudo/doas/pkexec wrap an inner command and add
the privileged label. Anything unrecognized is `unknown` and treated
conservatively downstream (never ghost-texted in P4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

READ_ONLY = "read-only"
LOCAL_CHANGE = "local-change"
INSTALL = "install"
REMOTE = "remote"
PRIVILEGED = "privileged"
DESTRUCTIVE = "destructive"
UNKNOWN = "unknown"

SEVERITY = {
    UNKNOWN: 0,
    READ_ONLY: 1,
    LOCAL_CHANGE: 2,
    INSTALL: 3,
    REMOTE: 4,
    PRIVILEGED: 5,
    DESTRUCTIVE: 6,
}

_PRIVILEGE_WRAPPERS = frozenset({"sudo", "doas", "pkexec"})
_READ_ONLY = frozenset({
    "ls", "ll", "la", "cat", "less", "more", "head", "tail", "grep",
    "egrep", "rg", "ag", "find", "fd", "du", "df", "ps", "top", "htop",
    "pwd", "echo", "which", "type", "printenv", "wc", "sort",
    "uniq", "cut", "tr", "stat", "file", "tree", "date", "whoami", "id",
    "uname", "hostname", "man", "history", "jobs", "free", "uptime",
    "lsblk", "lsusb", "lspci", "dmesg", "who", "w", "tldr", "column",
})
# Programs that run another command passed as their arguments. They hide
# the real command from a first-token classifier, so peel the wrapper (and
# its own options/args) and classify the inner command instead — as the
# privilege wrappers are peeled in classify().
_PASSTHROUGH_WRAPPERS = frozenset({
    "env", "xargs", "nohup", "timeout", "watch", "nice", "ionice",
    "stdbuf", "time", "setsid", "chrt",
})
# Of those, the ones whose first positional token is their OWN argument
# (a duration / priority) rather than the start of the inner command.
_WRAPPER_TAKES_VALUE = frozenset({"timeout", "chrt"})
_LOCAL_CHANGE = frozenset({
    "mkdir", "touch", "cp", "mv", "ln", "chmod", "chown", "tar", "unzip",
    "zip", "gzip", "gunzip", "make", "cmake", "sed", "tee", "install",
    "patch", "rename",
})
_INSTALL_TOOLS = frozenset({
    "apt", "apt-get", "aptitude", "dpkg", "dnf", "yum", "pacman", "zypper",
    "snap", "flatpak", "brew", "port", "pip", "pip3", "pipx", "npm", "pnpm",
    "yarn", "gem", "cargo", "go", "poetry", "uv", "conda",
})
_REMOTE_TOOLS = frozenset({
    "ssh", "scp", "sftp", "rsync", "curl", "wget", "ping", "nc", "ncat",
    "telnet", "kubectl", "helm", "aws", "gcloud", "az", "doctl", "terraform",
    "ansible", "ftp", "http", "https",
})
_DESTRUCTIVE_COMMANDS = frozenset({
    "shred", "mkfs", "fdisk", "parted", "wipefs", "sgdisk", "blkdiscard",
})
_INSTALL_SUBCOMMANDS = frozenset({"install", "add", "upgrade", "get"})


def _segments(command):
    return [seg.strip() for seg in re.split(r"\|\||&&|[|;&]", command)
            if seg.strip()]


def _tokenize(segment):
    return [tok for tok in segment.split() if tok]


def _peel_wrapper(name, args):
    """Drop a passthrough wrapper's own options/args; return the inner argv."""
    i, took_value = 0, False
    while i < len(args):
        tok = args[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("-"):
            i += 1
        elif name == "env" and "=" in tok:
            i += 1
        elif name in _WRAPPER_TAKES_VALUE and not took_value:
            took_value = True
            i += 1
        else:
            break
    return args[i:]


def _classify_segment(tokens) -> str:
    if not tokens:
        return UNKNOWN
    name = tokens[0].rsplit("/", 1)[-1]
    args = tokens[1:]

    if name in _PASSTHROUGH_WRAPPERS:
        inner = _peel_wrapper(name, args)
        if inner:
            return _classify_segment(inner)

    if name in ("rm", "rmdir"):
        return DESTRUCTIVE
    if name == "dd" and any(a.startswith("of=") for a in args):
        return DESTRUCTIVE
    if name in _DESTRUCTIVE_COMMANDS:
        return DESTRUCTIVE
    if name == "find" and ("-delete" in args
                           or any(a.startswith(("-exec", "-ok"))
                                  for a in args)):
        # find … -delete / -exec rm … runs destructive work per match.
        return DESTRUCTIVE
    if name in ("kubectl", "helm") and "delete" in args:
        return DESTRUCTIVE
    if name == "terraform" and any(a in ("destroy", "apply") for a in args):
        return DESTRUCTIVE
    if name == "docker" and any(a in ("rm", "rmi", "prune") for a in args):
        return DESTRUCTIVE
    if name in ("killall", "pkill"):
        return DESTRUCTIVE
    if name == "git" and _git_is_destructive(args):
        return DESTRUCTIVE

    if name == "docker":
        return _classify_docker(args)

    if name in _INSTALL_TOOLS:
        if name in ("go", "cargo", "npm", "pnpm", "yarn", "pip", "pip3",
                    "uv", "poetry", "gem", "conda"):
            if not any(a in _INSTALL_SUBCOMMANDS for a in args):
                # e.g. `npm test`, `cargo build` are not installs
                return _tool_default(name)
        return INSTALL

    if name in _REMOTE_TOOLS:
        return REMOTE
    if name == "git":
        return _classify_git(args)

    if name in _READ_ONLY:
        return READ_ONLY
    if name in _LOCAL_CHANGE:
        return LOCAL_CHANGE
    return UNKNOWN


_GIT_READ_ONLY = frozenset({
    "status", "log", "diff", "show", "blame", "describe", "rev-parse",
    "ls-files", "ls-remote", "shortlog", "reflog", "cat-file",
})
_DOCKER_READ_ONLY = frozenset({"ps", "logs", "images", "inspect", "version",
                               "info", "stats", "top", "port"})


def _classify_git(args) -> str:
    subcommands = [a for a in args if not a.startswith("-")]
    first = subcommands[0] if subcommands else ""
    if _git_is_remote(args):
        return REMOTE
    if first in _GIT_READ_ONLY:
        return READ_ONLY
    return LOCAL_CHANGE


def _classify_docker(args) -> str:
    subcommands = [a for a in args if not a.startswith("-")]
    first = subcommands[0] if subcommands else ""
    if first in ("pull", "push"):
        return REMOTE
    if first in _DOCKER_READ_ONLY:
        return READ_ONLY
    return LOCAL_CHANGE


def _tool_default(name) -> str:
    if name in ("go", "cargo", "make"):
        return LOCAL_CHANGE
    return UNKNOWN


def _git_is_destructive(args) -> bool:
    joined = " ".join(args)
    if "reset" in args and "--hard" in args:
        return True
    if "clean" in args and any("f" in a for a in args if a.startswith("-")):
        return True
    if "branch" in args and "-D" in args:
        return True
    if "push" in args and ("--force" in args or "-f" in args):
        return True
    return False


def _git_is_remote(args) -> bool:
    return any(a in ("push", "pull", "fetch", "clone", "remote")
               for a in args)


@dataclass(frozen=True)
class RiskResult:
    labels: frozenset
    display: str
    severity: int


def classify(command) -> RiskResult:
    if not isinstance(command, str) or not command.strip():
        return RiskResult(frozenset({UNKNOWN}), UNKNOWN, SEVERITY[UNKNOWN])
    labels = set()
    segments = _segments(command)
    piped_to_shell = bool(re.search(r"\|\s*(sudo\s+)?(sh|bash|zsh)\b",
                                    command))
    for segment in segments:
        tokens = _tokenize(segment)
        if tokens and tokens[0].rsplit("/", 1)[-1] in _PRIVILEGE_WRAPPERS:
            labels.add(PRIVILEGED)
            tokens = tokens[1:]
        labels.add(_classify_segment(tokens))
    if piped_to_shell:
        # `curl ... | sh` fetches and runs unknown code.
        labels.add(INSTALL)
        labels.add(DESTRUCTIVE)
    if len(labels) > 1:
        labels.discard(UNKNOWN)   # a known label beats "unknown"
    if not labels:
        labels = {UNKNOWN}
    display = max(labels, key=lambda label: SEVERITY[label])
    return RiskResult(frozenset(labels), display, SEVERITY[display])
