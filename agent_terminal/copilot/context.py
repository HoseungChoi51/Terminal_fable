"""Context heuristics: project, README, and argument-aware completion.

All local, no LLM. Directory reads go through the injected scanner
(``tui_core.scan_directory``) so this stays headless-testable, and the
README parser is handed pre-parsed blocks (the caller owns
``parse_markdown_blocks``) to keep the one-way import rule — nothing
here imports ``native_terminal``.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass

from agent_terminal.copilot import fuzzy
from agent_terminal.copilot.suggest import Suggestion, make_suggestion
from agent_terminal.tui_core import scan_directory

# -- project detection --------------------------------------------------

# Ordered by priority; first match wins.
_PROJECT_MARKERS = (
    ("python", ("pyproject.toml", "setup.py", "setup.cfg",
                "requirements.txt", "Pipfile", "uv.lock")),
    ("node", ("package.json",)),
    ("rust", ("Cargo.toml",)),
    ("go", ("go.mod",)),
    ("make", ("Makefile", "makefile", "GNUmakefile")),
    ("just", ("justfile", "Justfile", ".justfile")),
)


@dataclass(frozen=True)
class Project:
    kind: str
    name: str
    files: frozenset


def detect_project(cwd, *, scandir=scan_directory) -> Project | None:
    if not cwd:
        return None
    names = frozenset(e.name for e in scandir(cwd, show_hidden=True))
    for kind, markers in _PROJECT_MARKERS:
        if any(marker in names for marker in markers):
            return Project(kind=kind, name=os.path.basename(cwd.rstrip("/")),
                           files=names)
    if ".git" in names:
        return Project(kind="git",
                       name=os.path.basename(cwd.rstrip("/")), files=names)
    return None


# -- README run-command extraction --------------------------------------

_SHELL_LANGS = frozenset({"", "bash", "sh", "shell", "shellsession",
                          "console", "zsh", "text"})
# Commands that commonly appear in README "how to run" blocks.
_RUNNERS = frozenset({
    "python", "python3", "uv", "poetry", "pip", "pip3", "pytest", "tox",
    "npm", "pnpm", "yarn", "node", "cargo", "go", "make", "just", "bash",
    "sh", "docker", "flask", "django-admin", "streamlit", "gunicorn",
    "uvicorn", "rails", "rake", "./run.sh", "./manage.py",
})


def readme_run_commands(blocks) -> list[str]:
    """Pull likely run commands out of pre-parsed markdown code blocks."""
    commands = []
    seen = set()
    for block in blocks:
        if getattr(block, "kind", "") != "code":
            continue
        if (getattr(block, "language", "") or "").lower() not in _SHELL_LANGS:
            continue
        for raw in getattr(block, "text", "").splitlines():
            line = raw.strip()
            if line.startswith(("$ ", "# ")):
                line = line[2:].strip()
            elif line.startswith(("$", "#")):
                line = line[1:].strip()
            if not line or line.startswith("#"):
                continue
            head = line.split()[0]
            if head in _RUNNERS and line not in seen:
                seen.add(line)
                commands.append(line)
    return commands


def project_run_commands(project, *, cwd=None,
                         scandir=scan_directory) -> list[str]:
    """Built-in run/test commands implied by the project type."""
    if project is None:
        return []
    files = project.files
    out = []
    if project.kind == "python":
        for entry in ("run.py", "main.py", "app.py", "manage.py"):
            if entry in files:
                out.append(f"python {entry}")
        if "uv.lock" in files:
            out += ["uv run pytest", "uv sync"]
        if "Pipfile" in files or "Pipfile.lock" in files:
            out.append("pipenv run pytest")
        if any(m in files for m in ("pyproject.toml", "poetry.lock")):
            out.append("poetry run pytest")
        out += ["pytest", "python -m pytest"]
        if "requirements.txt" in files:
            out.append("pip install -r requirements.txt")
    elif project.kind == "node":
        out += _npm_scripts(cwd, scandir)
        out += ["npm install", "npm test", "npm run build"]
    elif project.kind == "rust":
        out += ["cargo run", "cargo build", "cargo test"]
    elif project.kind == "go":
        out += ["go run .", "go build ./...", "go test ./..."]
    elif project.kind == "make":
        out += ["make", "make build", "make test"]
    elif project.kind == "just":
        out += ["just", "just --list"]
    # de-dupe, preserve order
    result, seen = [], set()
    for cmd in out:
        if cmd not in seen:
            seen.add(cmd)
            result.append(cmd)
    return result


def _npm_scripts(cwd, scandir):
    if not cwd:
        return []
    try:
        with open(os.path.join(cwd, "package.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return []
    return [f"npm run {name}" for name in list(scripts)[:6]]


# -- argument-aware completion ------------------------------------------

DIRS = "dirs"
FILES = "files"
ARCHIVES = "archives"
MEDIA = "media"
DEB = "deb"
BRANCHES = "branches"
CHANGED_FILES = "changed-files"
SSH_HOSTS = "ssh-hosts"
REPO_URL = "repo-url"
NONE = "none"

_ARCHIVE_EXT = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2",
                ".tar.xz", ".txz", ".zip", ".gz", ".bz2", ".xz", ".7z",
                ".rar")
_MEDIA_EXT = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v",
              ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".gif")

_GUIDES = {
    REPO_URL: "Enter a repository URL, e.g. https://github.com/org/repo.git",
    BRANCHES: "Enter a branch name",
    SSH_HOSTS: "Enter a host (user@host)",
    DEB: "No .deb files here — download one first",
    ARCHIVES: "No archive files in this directory",
    MEDIA: "No media files in this directory",
}

_DIR_CMDS = frozenset({"cd", "pushd", "rmdir"})
_FILE_CMDS = frozenset({"cat", "less", "more", "head", "tail", "vim",
                        "nvim", "vi", "nano", "emacs", "code", "bat",
                        "wc", "source", "."})


@dataclass(frozen=True)
class ArgSpec:
    kind: str
    partial: str        # the token being completed ("" after a space)
    prefix: str         # command line up to (not including) the partial
    guide: str = ""


def _strip_leading(tokens):
    """Skip sudo/doas and VAR=val prefixes; return index of the command."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("sudo", "doas", "pkexec", "command", "time", "env"):
            i += 1
        elif "=" in tok and not tok.startswith(("-", "/", ".")):
            i += 1
        else:
            break
    return i


def argument_expectation(line) -> ArgSpec:
    """What does the argument currently being typed expect?"""
    if not line or not line.strip():
        return ArgSpec(NONE, "", line)
    trailing_space = line[-1:].isspace()
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    if not tokens:
        return ArgSpec(NONE, "", line)
    partial = "" if trailing_space else tokens[-1]
    prefix = line[:len(line) - len(partial)]
    idx = _strip_leading(tokens)
    if idx >= len(tokens):
        return ArgSpec(NONE, partial, prefix)
    # Still typing the command itself (no argument yet): not an argument.
    if not trailing_space and idx == len(tokens) - 1:
        return ArgSpec(NONE, partial, prefix)
    cmd = os.path.basename(tokens[idx])
    args = tokens[idx + 1:]
    sub = next((a for a in args if not a.startswith("-")), "")
    kind = _expectation_kind(cmd, sub, args, partial)
    return ArgSpec(kind, partial, prefix, _GUIDES.get(kind, ""))


def _expectation_kind(cmd, sub, args, partial):
    if cmd in _DIR_CMDS:
        return DIRS
    if cmd in _FILE_CMDS:
        return FILES
    if cmd == "tar":
        return ARCHIVES if any("x" in a for a in args
                               if a.startswith("-")) else NONE
    if cmd == "unzip":
        return ARCHIVES
    if cmd == "ffmpeg":
        return MEDIA if "-i" in args else NONE
    if cmd in ("apt", "apt-get") and "install" in args:
        if partial.startswith("./") or partial.endswith(".deb"):
            return DEB
        return NONE
    if cmd == "dpkg" and ("-i" in args or "--install" in args):
        return DEB
    if cmd == "git":
        if sub in ("checkout", "switch", "merge", "rebase"):
            return BRANCHES
        if sub in ("add", "restore", "diff"):
            return CHANGED_FILES
        if sub == "clone":
            return REPO_URL
    if cmd in ("ssh", "scp", "sftp"):
        return SSH_HOSTS
    return NONE


def _file_kind_filter(kind):
    if kind == DIRS:
        return lambda e: e.is_dir
    if kind == DEB:
        return lambda e: not e.is_dir and e.name.endswith(".deb")
    if kind == ARCHIVES:
        return lambda e: not e.is_dir and e.name.lower().endswith(_ARCHIVE_EXT)
    if kind == MEDIA:
        return lambda e: not e.is_dir and e.name.lower().endswith(_MEDIA_EXT)
    if kind == FILES:
        return lambda e: not e.is_dir
    return None


def file_completions(cwd, kind, partial, *, scandir=scan_directory,
                     limit=8):
    """Completed tokens for a filesystem-argument expectation, newest first."""
    keep = _file_kind_filter(kind)
    if keep is None or not cwd:
        return []
    base, _, frag = partial.rpartition("/")
    directory = os.path.join(cwd, base) if base else cwd
    entries = [e for e in scandir(directory, with_stat=True) if keep(e)]
    if frag:
        entries = [e for e in entries if e.name.startswith(frag)]
    entries.sort(key=lambda e: (e.mtime or 0.0), reverse=True)
    prefix = (base + "/") if base else ("./" if partial.startswith("./")
                                        else "")
    out = []
    for entry in entries[:limit]:
        token = prefix + entry.name + ("/" if entry.is_dir else "")
        out.append(token)
    return out


# -- ssh hosts ----------------------------------------------------------

def ssh_hosts(config_path=None):
    """Host aliases from ~/.ssh/config (no wildcards)."""
    path = config_path or os.path.expanduser("~/.ssh/config")
    hosts = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.lower().startswith("host "):
                    for name in line.split()[1:]:
                        if "*" not in name and "?" not in name:
                            hosts.append(name)
    except OSError:
        return []
    seen, out = set(), []
    for host in hosts:
        if host not in seen:
            seen.add(host)
            out.append(host)
    return out


# -- assembling menu suggestions ----------------------------------------

def menu_suggestions(line, cwd, *, project=None, readme_blocks=(),
                     scandir=scan_directory, providers=None,
                     limit=8) -> list[Suggestion]:
    """Context-aware suggestions for the completion menu, best first.

    `providers` may supply {BRANCHES: [...], CHANGED_FILES: [...],
    SSH_HOSTS: [...]} for the git/ssh expectations this module cannot
    resolve without a subprocess; missing ones fall back to the guide.
    """
    providers = providers or {}
    spec = argument_expectation(line)
    out, seen = [], set()

    def add(command, label, score):
        if command and command not in seen:
            seen.add(command)
            out.append(make_suggestion(command, label, score=score))

    # Argument-aware completions.
    if spec.kind in (DIRS, FILES, ARCHIVES, MEDIA, DEB):
        label = {DIRS: "directory", DEB: "recent .deb",
                 ARCHIVES: "archive", MEDIA: "media"}.get(spec.kind,
                                                          "recent file")
        for token in file_completions(cwd, spec.kind, spec.partial,
                                      scandir=scandir, limit=limit):
            add(spec.prefix + token, label, 6.0)
    elif spec.kind in (BRANCHES, CHANGED_FILES, SSH_HOSTS):
        label = {BRANCHES: "branch", CHANGED_FILES: "changed file",
                 SSH_HOSTS: "host"}[spec.kind]
        for value in providers.get(spec.kind, ()):
            if spec.partial and not value.startswith(spec.partial):
                continue
            add(spec.prefix + value, label, 6.0)

    # Project run commands (README preferred), shown only while typing a
    # bare command (not while completing an argument), filtered by it.
    if spec.kind == NONE:
        query = spec.partial

        def matches(cmd):
            return not query or fuzzy.score(query, cmd) is not None

        for cmd in readme_run_commands(readme_blocks):
            if matches(cmd):
                add(cmd, "from README", 5.5)
        for cmd in project_run_commands(project, cwd=cwd, scandir=scandir):
            if matches(cmd):
                add(cmd, "project", 5.0)

    return out[:limit]
