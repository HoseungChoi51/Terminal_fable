"""Full-screen curses directory browser ("smart ls").

Runs standalone in any terminal:

    python -m agent_terminal.smart_ls [DIR]

Single keys mirror ls flags (a/l/h/t/S/r); names much longer than the
rest of the listing are truncated toward the mode of the name lengths
so one outlier does not blow up the column layout. Enter opens
markdown/images in a running native terminal via its control socket
(falling back to xdg-open), and quitting with "q" writes the browsed
directory to --cwd-file so a shell wrapper can cd there.
"""

from __future__ import annotations

import argparse
import math
import os
import stat
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from agent_terminal.native_terminal import (CONTROL_SOCKET_ENV,
                                            is_image_path, is_markdown_path)
from agent_terminal.tui_core import (control_message, scan_directory,
                                     send_control_message)

ELLIPSIS = "…"
SLACK = 4
MIN_NAME_WIDTH = 8
MIN_GAIN = 3
GAP = 2

SORT_KEYS = ("name", "mtime", "size")
TRUNCATION_MODES = ("end", "middle", "start")


def display_name(entry) -> str:
    return entry.name + "/" if entry.is_dir else entry.name


def compute_target_width(lengths) -> int | None:
    """Column width that outlier-long names get truncated to, or None.

    Centered on the mode of the name lengths (the largest length wins
    a tie; with no repeated length the mode is meaningless and the
    upper median takes over). The median also guards the mode from a
    cluster of very short names. Truncation only kicks in when it
    saves at least MIN_GAIN columns on the longest name, so listings
    of uniformly long names just get fewer columns instead.
    """
    if not lengths:
        return None
    longest = max(lengths)
    median = sorted(lengths)[len(lengths) // 2]
    counts = Counter(lengths)
    top = max(counts.values())
    if top > 1:
        mode = max(v for v, c in counts.items() if c == top)
        center = max(mode, median)
    else:
        center = median
    target = max(center + SLACK, MIN_NAME_WIDTH)
    if longest < target + MIN_GAIN:
        return None
    return target


def truncate_name(name: str, width: int, mode: str = "end") -> str:
    """Ellipsize `name` to exactly `width` characters.

    Modes: "end" (head…), "middle" (head…tail, keeping a short
    extension), "start" (…tail). Below width 5 middle/start are
    unreadable and degrade to end.
    """
    if len(name) <= width:
        return name
    if width < 5:
        mode = "end"
    keep = width - 1
    if mode == "start":
        return ELLIPSIS + name[-keep:]
    if mode == "middle":
        dot = name.rfind(".")
        if 0 < dot < len(name) - 1 and len(name) - dot <= width // 2:
            extension = name[dot:]
        else:
            extension = ""
        tail = max(len(extension), keep // 3)
        return name[:keep - tail] + ELLIPSIS + name[-tail:]
    return name[:keep] + ELLIPSIS


def grid_geometry(count: int, avail_width: int, cell_width: int):
    """(columns, rows) for a column-major grid, ls style.

    Rebalanced so the trailing column is never empty.
    """
    if count <= 0:
        return 1, 0
    columns = max(1, (avail_width + GAP) // (cell_width + GAP))
    columns = min(columns, count)
    rows = math.ceil(count / columns)
    columns = math.ceil(count / rows)
    return columns, rows


def grid_position(index: int, rows: int):
    """Column-major cell of entry `index`: (row, column)."""
    return index % rows, index // rows


def grid_index(row: int, column: int, rows: int) -> int:
    return column * rows + row


def format_size(size, human: bool = False) -> str:
    """ls-style size: raw bytes, or -h-style 1024-power units."""
    if size is None:
        return "?"
    if not human:
        return str(size)
    value = float(size)
    for unit in ("B", "K", "M", "G", "T", "P"):
        if value < 1024 or unit == "P":
            break
        value /= 1024.0
    if unit == "B":
        return str(size)
    if value < 10:
        return f"{value:.1f}{unit}"
    return f"{value:.0f}{unit}"


def format_mtime(mtime, localtime=time.localtime) -> str:
    if mtime is None:
        return "?"
    return time.strftime("%Y-%m-%d %H:%M", localtime(mtime))


def format_mode(mode) -> str:
    if mode is None:
        return "?" * 10
    return stat.filemode(mode)


def sort_entries(entries, key: str = "name", reverse: bool = False):
    """Directories first, then files; each group ordered by `key`.

    name: casefold ascending; mtime: newest first (ls -t); size:
    largest first (ls -S). Entries with missing metadata stay at the
    end of their group in both directions; reverse flips the rest.
    """
    if key not in SORT_KEYS:
        raise ValueError(f"unknown sort key: {key}")

    def order(group):
        ranked = sorted(group, key=lambda e: e.name.casefold())
        if key == "name":
            missing = []
        else:
            field = "mtime" if key == "mtime" else "size"
            missing = [e for e in ranked
                       if getattr(e, field) is None]
            ranked = [e for e in ranked if getattr(e, field) is not None]
            ranked.sort(key=lambda e: getattr(e, field), reverse=True)
        if reverse:
            ranked.reverse()
        return ranked + missing

    return (order([e for e in entries if e.is_dir])
            + order([e for e in entries if not e.is_dir]))


def open_plan(path, socket_path):
    """How Enter opens a file, as data.

    ("control", message) sends the native terminal's open-file action
    over its control socket (markdown/images only); everything else is
    ("spawn", argv) for a detached opener.
    """
    resolved = str(Path(path).resolve())
    if socket_path and (is_markdown_path(resolved)
                        or is_image_path(resolved)):
        return "control", control_message("open-file", resolved)
    return "spawn", ["xdg-open", resolved]


def write_cwd_file(path, directory) -> None:
    Path(path).write_text(str(Path(directory).resolve()) + "\n",
                          encoding="utf-8")


class SmartLsState:
    """Pure browsing state for the smart-ls screen."""

    def __init__(self, start=None, *, show_hidden=False, long_format=False):
        self.directory = str(Path(start or os.getcwd()).resolve())
        self.show_hidden = show_hidden
        self.long_format = long_format
        self.sort_key = "name"
        self.reverse = False
        self.human_units = False
        self.trunc_mode = TRUNCATION_MODES[0]
        self.cursor = 0
        self.row_offset = 0
        self.entries = []
        self.refresh()

    def refresh(self):
        self.entries = sort_entries(
            scan_directory(self.directory, show_hidden=self.show_hidden,
                           with_stat=True),
            self.sort_key, self.reverse)
        self.cursor = min(self.cursor, max(len(self.entries) - 1, 0))

    def selected(self):
        if 0 <= self.cursor < len(self.entries):
            return self.entries[self.cursor]
        return None

    def move(self, delta):
        if not self.entries:
            return
        self.cursor = min(max(self.cursor + delta, 0),
                          len(self.entries) - 1)

    def move_horizontal(self, delta, rows):
        self.move(delta * max(rows, 1))

    def page(self, delta, visible_rows):
        self.move(delta * max(visible_rows, 1))

    def first(self):
        self.cursor = 0

    def last(self):
        self.cursor = max(len(self.entries) - 1, 0)

    def enter_directory(self, path):
        self.directory = str(Path(path).resolve())
        self.cursor = 0
        self.row_offset = 0
        self.refresh()

    def go_parent(self):
        previous = Path(self.directory)
        if previous.parent == previous:
            return
        self.enter_directory(previous.parent)
        for index, entry in enumerate(self.entries):
            if entry.is_dir and entry.name == previous.name:
                self.cursor = index
                break

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.refresh()

    def toggle_long(self):
        self.long_format = not self.long_format

    def toggle_human(self):
        self.human_units = not self.human_units

    def toggle_reverse(self):
        self.reverse = not self.reverse
        self.refresh()

    def set_sort(self, key):
        self.sort_key = "name" if self.sort_key == key else key
        self.refresh()

    def cycle_truncation(self):
        index = TRUNCATION_MODES.index(self.trunc_mode)
        self.trunc_mode = TRUNCATION_MODES[(index + 1)
                                           % len(TRUNCATION_MODES)]


def _open_selected(path, socket_path, spawned):
    """Execute the open plan; a failed control send falls back to spawn."""
    kind, payload = open_plan(path, socket_path)
    if kind == "control":
        if send_control_message(socket_path, payload):
            return
        payload = ["xdg-open", str(Path(path).resolve())]
    try:
        spawned.append(subprocess.Popen(payload,
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,
                                        start_new_session=True))
    except OSError:
        pass


def _reap(spawned):
    spawned[:] = [process for process in spawned
                  if process.poll() is None]


def _header_text(state) -> str:
    home = str(Path.home())
    directory = state.directory
    if directory == home or directory.startswith(home + os.sep):
        directory = "~" + directory[len(home):]
    parts = [f" {directory}"]
    if state.sort_key != "name":
        parts.append(f"[{state.sort_key}{'↑' if state.reverse else '↓'}]")
    elif state.reverse:
        parts.append("[reverse]")
    if state.show_hidden:
        parts.append("[hidden]")
    if state.human_units:
        parts.append("[human]")
    if state.trunc_mode != "end":
        parts.append(f"[trunc:{state.trunc_mode}]")
    return "  ".join(parts)


def _long_line(entry, state) -> str:
    size = format_size(entry.size, state.human_units).rjust(8)
    when = format_mtime(entry.mtime).rjust(16)
    return f"{format_mode(entry.mode)}  {size}  {when}  {display_name(entry)}"


FOOTER = (" Enter open · Bksp up · a hidden · l long · h units · "
          "t/S/r sort · e trunc · q quit+cd · Q quit")


def _draw(stdscr, state):
    """Render one frame; returns (rows, visible_rows) for movement."""
    import curses

    stdscr.erase()
    height, width = stdscr.getmaxyx()
    stdscr.addnstr(0, 0, _header_text(state).ljust(width - 1), width - 1,
                   curses.A_REVERSE)
    visible_rows = max(height - 3, 1)

    names = [display_name(entry) for entry in state.entries]
    count = len(names)
    if state.long_format:
        columns, rows = 1, max(count, 1)
        cell_width = width - 2
    else:
        target = compute_target_width([len(name) for name in names])
        natural = max((len(name) for name in names), default=1)
        cell_width = min(target if target is not None else natural,
                         width - 2)
        columns, rows = grid_geometry(count, width - 2, cell_width)

    cursor_row = grid_position(state.cursor, rows)[0] if count else 0
    if cursor_row < state.row_offset:
        state.row_offset = cursor_row
    if cursor_row >= state.row_offset + visible_rows:
        state.row_offset = cursor_row - visible_rows + 1
    state.row_offset = max(0, min(state.row_offset,
                                  max(rows - visible_rows, 0)))

    if not count:
        stdscr.addnstr(1, 1, "(empty)", max(width - 2, 1), curses.A_DIM)
    for row in range(state.row_offset,
                     min(rows, state.row_offset + visible_rows)):
        y = 1 + row - state.row_offset
        for column in range(columns):
            index = grid_index(row, column, rows)
            if index >= count:
                break
            entry = state.entries[index]
            attribute = curses.A_REVERSE if index == state.cursor else 0
            if entry.is_dir:
                attribute |= curses.A_BOLD
            if state.long_format:
                x = 1
                text = _long_line(entry, state).ljust(width - 2)
            else:
                x = 1 + column * (cell_width + GAP)
                text = truncate_name(names[index], cell_width,
                                     state.trunc_mode).ljust(cell_width)
            try:
                stdscr.addnstr(y, x, text, max(width - 1 - x, 1),
                               attribute)
            except curses.error:
                pass
    try:
        stdscr.addnstr(height - 1, 0, FOOTER.ljust(width - 1), width - 1,
                       curses.A_DIM)
    except curses.error:
        pass
    stdscr.refresh()
    return rows, visible_rows


def run_browser(stdscr, state, socket_path) -> bool:
    """Curses event loop; True means the user quit with cd (q)."""
    import curses

    curses.curs_set(0)
    stdscr.keypad(True)
    spawned = []
    while True:
        rows, visible_rows = _draw(stdscr, state)
        _reap(spawned)
        key = stdscr.getch()
        if key == ord("q"):
            return True
        if key in (ord("Q"), 27):  # Escape quits without cd
            return False
        if key == curses.KEY_UP:
            state.move(-1)
        elif key == curses.KEY_DOWN:
            state.move(1)
        elif key == curses.KEY_LEFT:
            state.move_horizontal(-1, 1 if state.long_format else rows)
        elif key == curses.KEY_RIGHT:
            state.move_horizontal(1, 1 if state.long_format else rows)
        elif key == curses.KEY_PPAGE:
            state.page(-1, visible_rows)
        elif key == curses.KEY_NPAGE:
            state.page(1, visible_rows)
        elif key in (curses.KEY_HOME, ord("g")):
            state.first()
        elif key in (curses.KEY_END, ord("G")):
            state.last()
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            state.go_parent()
        elif key in (curses.KEY_ENTER, 10, 13):
            entry = state.selected()
            if entry is None:
                continue
            if entry.is_dir:
                state.enter_directory(entry.path)
            else:
                _open_selected(entry.path, socket_path, spawned)
        elif key == ord("a"):
            state.toggle_hidden()
        elif key == ord("l"):
            state.toggle_long()
        elif key == ord("h"):
            state.toggle_human()
        elif key == ord("t"):
            state.set_sort("mtime")
        elif key == ord("S"):
            state.set_sort("size")
        elif key == ord("r"):
            state.toggle_reverse()
        elif key == ord("e"):
            state.cycle_truncation()
        # KEY_RESIZE just falls through: every frame recomputes geometry


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sls",
        description="Full-screen directory browser with ls-style keys.")
    parser.add_argument("directory", nargs="?", default=None,
                        help="Starting directory (default: cwd).")
    parser.add_argument("--cwd-file", default=None,
                        help="Write the final directory here when quitting "
                             "with q, for a shell cd wrapper.")
    parser.add_argument("--show-hidden", action="store_true",
                        help="Start with hidden files shown.")
    parser.add_argument("--long", action="store_true",
                        help="Start in long format.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.directory is not None and not os.path.isdir(args.directory):
        print(f"sls: not a directory: {args.directory}", file=sys.stderr)
        return 2
    state = SmartLsState(args.directory, show_hidden=args.show_hidden,
                         long_format=args.long)
    socket_path = os.environ.get(CONTROL_SOCKET_ENV)
    import curses

    cd_requested = curses.wrapper(run_browser, state, socket_path)
    if cd_requested and args.cwd_file:
        write_cwd_file(args.cwd_file, state.directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
