"""Curses file picker used by the native terminal for file opening.

The native app runs this module inside a transient VTE-backed picker
window:

    python -m agent_terminal.tui_navigation select-file --socket PATH ...

When the user selects a file, the picker sends one JSON line over the
native app's Unix control socket and exits. Without a socket it prints
the selected path to stdout instead.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_terminal.native_terminal import CONTROL_SOCKET_ENV

PAGE_SIZE = 12


@dataclass(frozen=True)
class PickerEntry:
    name: str
    path: str
    is_dir: bool


def parse_extensions(raw) -> tuple[str, ...]:
    """Parse a comma-separated extension filter like "md,markdown,png"."""
    if not raw:
        return ()
    out = []
    for piece in str(raw).split(","):
        piece = piece.strip().lower().lstrip(".")
        if piece:
            out.append("." + piece)
    return tuple(out)


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


class PickerState:
    """Pure navigation state for the curses picker."""

    def __init__(self, start, extensions=(), show_hidden=False):
        self.directory = str(Path(start or os.getcwd()).resolve())
        self.extensions = tuple(extensions)
        self.show_hidden = show_hidden
        self.query = ""
        self.cursor = 0
        self.offset = 0
        self.entries = []
        self.refresh()

    def refresh(self):
        self.entries = list_directory(self.directory,
                                      show_hidden=self.show_hidden,
                                      extensions=self.extensions,
                                      query=self.query)
        self.cursor = min(self.cursor, max(len(self.entries) - 1, 0))
        self.offset = min(self.offset, self.cursor)

    def selected(self):
        if 0 <= self.cursor < len(self.entries):
            return self.entries[self.cursor]
        return None

    def move(self, delta):
        if not self.entries:
            return
        self.cursor = min(max(self.cursor + delta, 0), len(self.entries) - 1)

    def page(self, delta):
        self.move(delta * PAGE_SIZE)

    def enter_directory(self, path):
        self.directory = str(Path(path).resolve())
        self.query = ""
        self.cursor = 0
        self.offset = 0
        self.refresh()

    def go_parent(self):
        parent = Path(self.directory).parent
        self.enter_directory(parent)

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.refresh()

    def append_query(self, char):
        self.query += char
        self.cursor = 0
        self.refresh()

    def backspace(self):
        if self.query:
            self.query = self.query[:-1]
            self.refresh()
        else:
            self.go_parent()


def _draw(stdscr, state):
    import curses

    stdscr.erase()
    height, width = stdscr.getmaxyx()
    header = f" {state.directory}"
    if state.query:
        header += f"  filter: {state.query}"
    if state.show_hidden:
        header += "  [hidden shown]"
    stdscr.addnstr(0, 0, header.ljust(width - 1), width - 1,
                   curses.A_REVERSE)
    visible_rows = max(height - 3, 1)
    if state.cursor < state.offset:
        state.offset = state.cursor
    if state.cursor >= state.offset + visible_rows:
        state.offset = state.cursor - visible_rows + 1
    for row in range(visible_rows):
        index = state.offset + row
        if index >= len(state.entries):
            break
        entry = state.entries[index]
        attribute = curses.A_REVERSE if index == state.cursor else 0
        if entry.is_dir:
            attribute |= curses.A_BOLD
        stdscr.addnstr(row + 1, 1, entry.name, width - 2, attribute)
    footer = (" Enter select · Backspace filter/up · Tab hidden · "
              "PgUp/PgDn page · type to filter · Esc cancel")
    stdscr.addnstr(height - 1, 0, footer.ljust(width - 1), width - 1,
                   curses.A_DIM)
    stdscr.refresh()


def run_picker(stdscr, state):
    """Curses event loop; returns the selected file path or None."""
    import curses

    curses.curs_set(0)
    stdscr.keypad(True)
    while True:
        _draw(stdscr, state)
        key = stdscr.getch()
        if key in (27,):  # Escape cancels
            return None
        if key in (curses.KEY_UP,):
            state.move(-1)
        elif key in (curses.KEY_DOWN,):
            state.move(1)
        elif key == curses.KEY_PPAGE:
            state.page(-1)
        elif key == curses.KEY_NPAGE:
            state.page(1)
        elif key in (curses.KEY_LEFT,):
            state.go_parent()
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            state.backspace()
        elif key == 9:  # Tab toggles hidden files
            state.toggle_hidden()
        elif key in (curses.KEY_ENTER, 10, 13, curses.KEY_RIGHT):
            entry = state.selected()
            if entry is None:
                continue
            if entry.is_dir:
                state.enter_directory(entry.path)
            elif key != curses.KEY_RIGHT:
                return entry.path
        elif 32 <= key < 127:
            state.append_query(chr(key))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-terminal-tui")
    subparsers = parser.add_subparsers(dest="command", required=True)
    select = subparsers.add_parser("select-file",
                                   help="Interactively pick a file.")
    select.add_argument("--start", default=None,
                        help="Starting directory (default: cwd).")
    select.add_argument("--extensions", default="",
                        help="Comma-separated extension filter, e.g. md,png.")
    select.add_argument("--socket", default=None,
                        help="Native control socket path "
                             f"(default: ${CONTROL_SOCKET_ENV}).")
    select.add_argument("--show-hidden", action="store_true")
    select.add_argument("--action", default="open-file",
                        help="Control message action name.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.command != "select-file":
        return 2
    state = PickerState(args.start, parse_extensions(args.extensions),
                        show_hidden=args.show_hidden)
    import curses

    selected = curses.wrapper(run_picker, state)
    if selected is None:
        return 1
    socket_path = args.socket or os.environ.get(CONTROL_SOCKET_ENV)
    if socket_path:
        if send_control_message(socket_path,
                                control_message(args.action, selected)):
            return 0
        print(selected)
        return 0
    print(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
