"""Shared pure core for the terminal's TUI subprocesses.

GTK-free and curses-free: the directory-entry model, directory listing,
and the control-socket client. Consumed by the file picker
(tui_navigation) and importable headlessly for tests.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PickerEntry:
    name: str
    path: str
    is_dir: bool


def list_directory(path, *, show_hidden=False, extensions=(), query=""):
    """Picker entries: parent first, then directories, then files.

    Files are filtered by the extension list and the case-insensitive
    query substring; directories are only filtered by the query.
    """
    base = Path(path).resolve()
    entries: list[PickerEntry] = []
    if base.parent != base:
        entries.append(PickerEntry("..", str(base.parent), True))
    try:
        children = list(base.iterdir())
    except OSError:
        children = []
    needle = query.casefold()
    directories = []
    files = []
    for child in children:
        name = child.name
        if not show_hidden and name.startswith("."):
            continue
        if needle and needle not in name.casefold():
            continue
        if child.is_dir():
            directories.append(PickerEntry(name + "/", str(child), True))
        else:
            if extensions and not name.lower().endswith(extensions):
                continue
            files.append(PickerEntry(name, str(child), False))
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
