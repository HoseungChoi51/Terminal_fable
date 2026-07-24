"""Shared pure core for the terminal's TUI subprocesses.

GTK-free and curses-free: the directory-entry model, directory listing,
and the control-socket client. Consumed by the file picker
(tui_navigation) and importable headlessly for tests.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path

# The control-socket env var and the viewer file-type predicates live here
# (the shared, GTK-free core) so the standalone TUI subprocesses — smart-ls
# and the file picker — depend only on this module, never on the GTK terminal
# shell. `native_terminal` re-exports them for its own use and back-compat.
CONTROL_SOCKET_ENV = "AGENT_TERMINAL_NATIVE_CONTROL_SOCKET"
MARKDOWN_EXTENSIONS = (".md", ".markdown", ".mkd")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def is_markdown_path(path) -> bool:
    return str(path).lower().endswith(MARKDOWN_EXTENSIONS)


def is_image_path(path) -> bool:
    return str(path).lower().endswith(IMAGE_EXTENSIONS)


@dataclass(frozen=True)
class PickerEntry:
    name: str
    path: str
    is_dir: bool
    size: int | None = None
    mtime: float | None = None
    mode: int | None = None


def scan_directory(path, *, show_hidden=False, with_stat=False):
    """Raw directory entries: names verbatim, unsorted, no ".." entry.

    With with_stat, entries carry size/mtime/mode; a broken symlink
    falls back to its own lstat, and stat failure leaves them None.
    An unreadable directory yields an empty list.
    """
    try:
        with os.scandir(str(path)) as children:
            listed = list(children)
    except OSError:
        return []
    entries: list[PickerEntry] = []
    for child in listed:
        name = child.name
        if not show_hidden and name.startswith("."):
            continue
        try:
            is_dir = child.is_dir(follow_symlinks=True)
        except OSError:
            is_dir = False
        size = mtime = mode = None
        if with_stat:
            try:
                result = child.stat(follow_symlinks=True)
            except OSError:
                try:
                    result = child.stat(follow_symlinks=False)
                except OSError:
                    result = None
            if result is not None:
                size = result.st_size
                mtime = result.st_mtime
                mode = result.st_mode
        entries.append(PickerEntry(name, child.path, is_dir,
                                   size=size, mtime=mtime, mode=mode))
    return entries


def list_directory(path, *, show_hidden=False, extensions=(), query=""):
    """Picker entries: parent first, then directories, then files.

    Files are filtered by the extension list and the case-insensitive
    query substring; directories are only filtered by the query.
    """
    base = Path(path).resolve()
    entries: list[PickerEntry] = []
    if base.parent != base:
        entries.append(PickerEntry("..", str(base.parent), True))
    needle = query.casefold()
    directories = []
    files = []
    for child in scan_directory(base, show_hidden=show_hidden):
        name = child.name
        if needle and needle not in name.casefold():
            continue
        if child.is_dir:
            directories.append(PickerEntry(name + "/", child.path, True))
        else:
            if extensions and not name.lower().endswith(extensions):
                continue
            files.append(child)
    directories.sort(key=lambda entry: entry.name.casefold())
    files.sort(key=lambda entry: entry.name.casefold())
    entries.extend(directories)
    entries.extend(files)
    return entries


def control_message(action: str, path) -> dict:
    """The JSON payload sent to the native app's control socket."""
    return {"action": action, "path": str(Path(path).resolve())}


def encode_message(message: dict) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"


def send_control_message(socket_path: str, message: dict) -> bool:
    """Send one JSON line over the native Unix socket."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5.0)
            connection.connect(socket_path)
            connection.sendall(encode_message(message))
        return True
    except OSError:
        return False
