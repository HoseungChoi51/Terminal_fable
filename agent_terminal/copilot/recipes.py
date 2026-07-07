"""Built-in command recipes and recipe search.

Each recipe pairs a command template with a description, search
keywords, and any <placeholder> slots the user must fill. Risk is
derived from the command by the classifier so labels stay consistent
with everything else. Search ranks by fuzzy match over the description,
command, and keywords.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent_terminal.copilot import fuzzy
from agent_terminal.copilot import risk as risk_mod

_PLACEHOLDER = re.compile(r"<[a-z0-9_]+>")


@dataclass(frozen=True)
class Recipe:
    command: str
    description: str
    keywords: tuple[str, ...] = ()
    source: str = "builtin"
    placeholders: tuple[str, ...] = field(default=())
    risk: object = None

    def haystack(self) -> str:
        return " ".join((self.description, self.command,
                         " ".join(self.keywords)))


def _recipe(command, description, keywords=()):
    placeholders = tuple(dict.fromkeys(_PLACEHOLDER.findall(command)))
    return Recipe(command=command, description=description,
                  keywords=tuple(keywords), placeholders=placeholders,
                  risk=risk_mod.classify(command))


# ~45 built-in recipes covering the design-doc examples plus common needs.
BUILTIN_RECIPES = (
    _recipe("du -ah . | sort -rh | head -20",
            "Show the largest files and directories, biggest first",
            ("sort by size", "disk usage", "largest", "big files")),
    _recipe("find . -type f -printf '%s %p\\n' | sort -nr | head -20",
            "Find the largest files under the current directory",
            ("find large files", "biggest files", "size")),
    _recipe("find . -type f -name '*.py' -print0 | xargs -0 wc -l",
            "Count lines across all Python files",
            ("number of lines", "line count", "loc", "wc")),
    _recipe("find . -name '*.zip' -exec unzip {} \\;",
            "Unzip every .zip archive in the current directory",
            ("unzip all files", "extract archives", "unzip")),
    _recipe("ffmpeg -i <input_video> <output_dir>/frame_%06d.png",
            "Extract every frame of a video as numbered PNG images",
            ("split video into frames", "video frames", "ffmpeg extract")),
    _recipe("ffmpeg -i <input_video> -vf fps=1 <output_dir>/frame_%06d.png",
            "Extract one frame per second from a video",
            ("video frames per second", "ffmpeg fps", "sample video")),
    _recipe("lsof -ti tcp:<port> | xargs -r kill",
            "Kill the process listening on a TCP port",
            ("kill process on port", "free port", "release port")),
    _recipe("ss -tulpn",
            "List listening TCP/UDP ports and their processes",
            ("list ports", "open ports", "listening sockets", "netstat")),
    _recipe("grep -rn '<pattern>' .",
            "Recursively search files for a pattern with line numbers",
            ("search text", "grep recursive", "find in files")),
    _recipe("find . -type f -name '<glob>'",
            "Find files by name pattern",
            ("find file by name", "locate file", "search filename")),
    _recipe("find . -type f -mtime -1",
            "Find files modified in the last day",
            ("recently modified files", "recent changes", "new files")),
    _recipe("tar -czvf <archive>.tar.gz <path>",
            "Create a gzipped tar archive",
            ("create tar", "compress folder", "make archive", "tar gz")),
    _recipe("tar -xzvf <archive>.tar.gz",
            "Extract a gzipped tar archive",
            ("extract tar", "untar", "decompress")),
    _recipe("chmod +x <file>",
            "Make a file executable",
            ("make executable", "chmod x", "run permission")),
    _recipe("df -h",
            "Show free disk space per filesystem, human readable",
            ("disk free", "free space", "df")),
    _recipe("free -h",
            "Show memory usage, human readable",
            ("memory usage", "ram", "free memory")),
    _recipe("ps aux --sort=-%mem | head -20",
            "Show the top processes by memory use",
            ("top memory processes", "high memory", "ps memory")),
    _recipe("ps aux --sort=-%cpu | head -20",
            "Show the top processes by CPU use",
            ("top cpu processes", "high cpu", "ps cpu")),
    _recipe("git status -sb",
            "Show a short git status with branch info",
            ("git status", "what changed", "working tree")),
    _recipe("git log --oneline --graph --decorate -20",
            "Show a compact commit graph",
            ("git log", "commit history", "git graph")),
    _recipe("git diff --stat",
            "Summarize changed files and line counts",
            ("git diff summary", "what changed", "diff stat")),
    _recipe("git commit -am '<message>'",
            "Stage tracked changes and commit with a message",
            ("git commit", "commit all", "save changes")),
    _recipe("git checkout -b <branch>",
            "Create and switch to a new branch",
            ("new branch", "git branch", "create branch")),
    _recipe("git restore <file>",
            "Discard unstaged changes to a file",
            ("discard changes", "undo file", "revert file")),
    _recipe("git reset --soft HEAD~1",
            "Undo the last commit but keep the changes staged",
            ("undo last commit", "uncommit", "git reset")),
    _recipe("git stash",
            "Stash uncommitted changes for later",
            ("stash changes", "git stash", "set aside")),
    _recipe("rsync -avh --progress <src> <dest>",
            "Copy files preserving attributes, with progress",
            ("copy files", "rsync", "sync directories")),
    _recipe("scp <file> <user>@<host>:<path>",
            "Copy a file to a remote host over SSH",
            ("copy to remote", "scp", "upload file")),
    _recipe("ssh <user>@<host>",
            "Open an SSH session to a host",
            ("ssh connect", "remote shell", "log in")),
    _recipe("curl -fsSL <url> -o <file>",
            "Download a URL to a file",
            ("download file", "curl download", "fetch url")),
    _recipe("sudo apt update && sudo apt upgrade",
            "Update the package lists and upgrade installed packages",
            ("update system", "apt upgrade", "update packages")),
    _recipe("sudo apt install ./<package>.deb",
            "Install a local Debian package",
            ("install deb", "local package", "apt install file")),
    _recipe("docker ps -a",
            "List all Docker containers",
            ("list containers", "docker ps", "running containers")),
    _recipe("docker logs -f <container>",
            "Follow the logs of a container",
            ("docker logs", "container logs", "tail logs")),
    _recipe("docker compose up -d",
            "Start services in the background with Docker Compose",
            ("docker compose up", "start services", "compose")),
    _recipe("docker exec -it <container> bash",
            "Open a shell inside a running container",
            ("docker shell", "exec container", "enter container")),
    _recipe("kubectl get pods -A",
            "List pods across all namespaces",
            ("kubectl pods", "list pods", "k8s pods")),
    _recipe("kubectl logs -f <pod>",
            "Follow the logs of a pod",
            ("kubectl logs", "pod logs", "k8s logs")),
    _recipe("journalctl -u <service> -f",
            "Follow the logs of a systemd service",
            ("service logs", "journalctl", "systemd logs")),
    _recipe("systemctl status <service>",
            "Show the status of a systemd service",
            ("service status", "systemctl", "is running")),
    _recipe("python3 -m http.server <port>",
            "Serve the current directory over HTTP",
            ("http server", "serve files", "quick server")),
    _recipe("chmod -R u+rwX <path>",
            "Recursively give the owner read/write access",
            ("fix permissions", "chmod recursive", "own files")),
    _recipe("awk '{print $<n>}' <file>",
            "Print a specific whitespace-delimited column",
            ("print column", "awk column", "extract field")),
    _recipe("sed -n '<start>,<end>p' <file>",
            "Print a range of lines from a file",
            ("print line range", "sed lines", "show lines")),
    _recipe("watch -n 2 '<command>'",
            "Re-run a command every two seconds",
            ("watch command", "repeat command", "monitor")),
)


def search(query, recipes=BUILTIN_RECIPES, limit=None):
    """Return recipes matching `query`, best first."""
    scored = []
    for recipe in recipes:
        value = fuzzy.score(query, recipe.haystack())
        if value is not None:
            scored.append((value, recipe))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    result = [recipe for _, recipe in scored]
    return result if limit is None else result[:limit]
