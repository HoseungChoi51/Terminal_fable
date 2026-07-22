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
        if name == "env":
            return READ_ONLY   # bare `env` just prints the environment

    if name in ("rm", "rmdir"):
        return DESTRUCTIVE
    if name == "dd" and any(a.startswith("of=") for a in args):
        return DESTRUCTIVE
    if name in _DESTRUCTIVE_COMMANDS:
        return DESTRUCTIVE
    if name == "find":
        return _classify_find(args)
    if name == "sed" and any(a == "-i" or a.startswith("-i")
                             or a == "--in-place" for a in args):
        return DESTRUCTIVE   # in-place edit rewrites the target file
    if name in ("chmod", "chown") and any(
            a in ("-R", "--recursive")
            or (a.startswith("-") and not a.startswith("--") and "R" in a)
            for a in args):
        return DESTRUCTIVE   # recursive perms/ownership can brick a tree
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


# find primitives that open a named file for writing (O_TRUNC) up front.
_FIND_WRITE = ("-fprint", "-fprintf", "-fprint0", "-fls")
_FIND_EXEC = ("-exec", "-execdir", "-ok", "-okdir")
_FIND_EXEC_TERM = frozenset({";", "\\;", "+"})


def _classify_find(args) -> str:
    """find is read-only unless it deletes, writes a file, or runs a command.

    An -exec/-ok clause is graded by its inner command, so `find … -exec
    grep …` stays read-only while `find … -exec rm …` is destructive.
    """
    if "-delete" in args:
        return DESTRUCTIVE
    for flag in _FIND_EXEC:
        if flag in args:
            inner, placeholder = [], False
            for tok in args[args.index(flag) + 1:]:
                if tok in _FIND_EXEC_TERM:
                    break
                if tok == "{}":
                    placeholder = True
                else:
                    inner.append(tok)
            if not inner:
                return DESTRUCTIVE
            grade = _classify_segment(inner)
            # A mutating inner applied to {} rewrites every match, an
            # unbounded fileset — scale it up from local-change.
            if placeholder and grade == LOCAL_CHANGE:
                return DESTRUCTIVE
            return grade
    if any(a.startswith(_FIND_WRITE) for a in args):
        return DESTRUCTIVE
    return READ_ONLY


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


_GIT_FORCE = frozenset({"-f", "--force", "--discard-changes"})


def _git_is_destructive(args) -> bool:
    subs = [a for a in args if not a.startswith("-")]
    first = subs[0] if subs else ""
    force = any(f in args for f in _GIT_FORCE)
    if "reset" in args and "--hard" in args:
        return True
    if "clean" in args and any("f" in a for a in args if a.startswith("-")):
        return True
    if "branch" in args and "-D" in args:
        return True
    if "push" in args and force:
        return True
    # Working-tree / history discards that grade "local-change" otherwise.
    if first == "reflog" and ("expire" in subs or "delete" in subs):
        return True
    if first == "restore":                       # discards worktree changes
        return True
    if first == "switch" and force:              # -f/--discard-changes wipes
        return True
    if first == "checkout" and ("." in args or "--" in args or force):
        return True                              # pathspec / forced overwrite
    if first == "stash" and ("drop" in subs or "clear" in subs):
        return True
    if first == "worktree" and "remove" in subs and force:
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


# -- auto-run allowlist (the robust floor under auto-pilot) --------------
#
# classify() is a denylist and will always have gaps (a destructive command
# it has not learned grades low). For *unattended execution* that is not
# good enough, so auto-run is gated by an allowlist as well: only commands
# whose every segment is a known-safe program run without a keystroke. A
# classifier miss then costs at most a wrong badge, never an auto-run.
#
# Curated (not derived from _READ_ONLY): `find` is excluded — its flags
# (-delete/-exec/-fprint) make it a footgun whose safety would again depend
# on the classifier — and pagers / interactive programs (less, more, man,
# top, htop) are excluded because they would block an unattended run. The
# set is deliberately small; broaden it only with evidence a program is
# safe to run unattended.
_AUTORUN_ALLOWLIST = frozenset({
    "ls", "ll", "la", "cat", "head", "tail", "grep", "egrep", "rg", "ag",
    "fd", "du", "df", "ps", "pwd", "echo", "which", "type", "printenv",
    "env", "wc", "sort", "uniq", "cut", "tr", "stat", "file", "tree",
    "date", "whoami", "id", "uname", "hostname", "history", "jobs", "free",
    "uptime", "lsblk", "lsusb", "lspci", "dmesg", "who", "w", "column",
    "mkdir", "touch",   # the only mutating programs, and both are safe
})
_AUTORUN_METACHARS = re.compile(r"\$\(|`|[<>]")


def _segment_program(tokens) -> str:
    """Leading program of a segment after peeling passthrough wrappers.

    Privilege wrappers (sudo/…) are NOT peeled: their name is returned so
    they fail the allowlist and never auto-run.
    """
    while tokens:
        name = tokens[0].rsplit("/", 1)[-1]
        if name in _PASSTHROUGH_WRAPPERS:
            inner = _peel_wrapper(name, tokens[1:])
            if not inner:
                return name
            tokens = inner
            continue
        return name
    return ""


def auto_run_safe(command) -> bool:
    """True only if `command` is safe to run unattended (auto-pilot).

    Robust by construction: every pipeline segment must lead with an
    allowlisted program and the command must contain no substitution or
    redirection. Anything unrecognized — a new program, a wrapped or
    mis-graded command, a metacharacter the classifier can't see into — is
    refused, so a classifier gap can never let a destructive command run.
    """
    if not isinstance(command, str) or not command.strip():
        return False
    if _AUTORUN_METACHARS.search(command):
        return False
    segments = _segments(command)
    if not segments:
        return False
    return all(_segment_program(_tokenize(seg)) in _AUTORUN_ALLOWLIST
               for seg in segments)
