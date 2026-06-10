"""Native GTK4/VTE terminal application.

This module is the single source of truth for the native terminal MVP:
CLI parsing, launch options, the pure split-layout engine, Markdown and
image helpers, the action/shortcut surface, the control socket protocol,
and the GTK/VTE user interface.

Everything above the GTK section is importable without PyGObject so the
unit tests can exercise the pure logic on headless machines. GTK modules
are loaded lazily by :func:`load_gtk` and the widget classes are built by
:func:`build_native_classes`.
"""

from __future__ import annotations

import argparse
import html
import itertools
import json
import os
import re
import shlex
import socket
import sys
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

VERSION = "0.1.0"
APP_ID = "dev.agent.TerminalNative"
APP_TITLE = "Agent Terminal"
CONTROL_SOCKET_ENV = "AGENT_TERMINAL_NATIVE_CONTROL_SOCKET"
CONFIG_PATH = "~/.config/agent-terminal/native.json"

DEPENDENCY_PACKAGES = (
    "python3-gi",
    "gir1.2-gtk-4.0",
    "gir1.2-vte-3.91",
    "gir1.2-adw-1",
)
DEPENDENCY_HINT = (
    "The native terminal needs PyGObject with GTK 4 and VTE 3.91.\n"
    "On Debian/Ubuntu install the system bindings with:\n"
    "  sudo apt install " + " ".join(DEPENDENCY_PACKAGES)
)


class NativeDependencyError(RuntimeError):
    """Raised when the GTK/VTE introspection bindings are unavailable."""


# ---------------------------------------------------------------------------
# Settings, configuration, and launch options
# ---------------------------------------------------------------------------

MIN_FONT_SIZE = 6.0
MAX_FONT_SIZE = 72.0
DEFAULT_FONT_FAMILY = "Monospace"
DEFAULT_FONT_SIZE = 11.0
DEFAULT_SCROLLBACK_LINES = 10_000
DEFAULT_CURSOR_STYLE = "block"
CURSOR_STYLES = ("block", "ibeam", "underline")

DEFAULT_PALETTE = "agent-dark"
PALETTES = {
    "agent-dark": {
        "foreground": "#d8dee9",
        "background": "#11151c",
        "colors": (
            "#1c2128", "#e05561", "#8cc265", "#d18f52",
            "#4aa5f0", "#c162de", "#42b3c2", "#d7dae0",
            "#475061", "#ff616e", "#a5e075", "#f0a45d",
            "#4dc4ff", "#de73ff", "#4cd1e0", "#e6e6e6",
        ),
    },
    "agent-light": {
        "foreground": "#2a2f38",
        "background": "#fafafa",
        "colors": (
            "#101216", "#cd3131", "#13803c", "#a05a00",
            "#0451a5", "#bc05bc", "#0598bc", "#777f8b",
            "#5d646f", "#e05561", "#3e8a52", "#c08a26",
            "#2a7ab0", "#c75ae8", "#3994a8", "#2a2f38",
        ),
    },
    "solarized-dark": {
        "foreground": "#93a1a1",
        "background": "#002b36",
        "colors": (
            "#073642", "#dc322f", "#859900", "#b58900",
            "#268bd2", "#d33682", "#2aa198", "#eee8d5",
            "#002b36", "#cb4b16", "#586e75", "#657b83",
            "#839496", "#6c71c4", "#93a1a1", "#fdf6e3",
        ),
    },
}

DEFAULT_CLOSE_POLICY = "adjacent_expand"
CLOSE_POLICIES = ("adjacent_expand", "same_axis_reflow")

# PCRE2 option bits used with Vte.Regex. VTE requires multiline regexes.
PCRE2_CASELESS = 0x0000_0008
PCRE2_MULTILINE = 0x0000_0400
URL_REGEX_PATTERN = r"(?:https?|ftp)://[\w\-.~:/?#\[\]@!$&'()*+,;=%]+"
URL_REGEX_FLAGS = PCRE2_MULTILINE
SEARCH_REGEX_FLAGS = PCRE2_MULTILINE | PCRE2_CASELESS


def clamp_font_size(size: float) -> float:
    """Clamp a font size into the supported point range."""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return DEFAULT_FONT_SIZE
    return min(max(value, MIN_FONT_SIZE), MAX_FONT_SIZE)


def normalize_palette(name: object) -> str:
    """Return a known palette name, falling back to the default."""
    if isinstance(name, str) and name in PALETTES:
        return name
    return DEFAULT_PALETTE


def normalize_cursor_style(style: object) -> str:
    if isinstance(style, str) and style in CURSOR_STYLES:
        return style
    return DEFAULT_CURSOR_STYLE


@dataclass(frozen=True)
class TerminalSettings:
    font_family: str = DEFAULT_FONT_FAMILY
    font_size: float = DEFAULT_FONT_SIZE
    scrollback_lines: int = DEFAULT_SCROLLBACK_LINES
    cursor_blink: bool = True
    cursor_style: str = DEFAULT_CURSOR_STYLE
    palette: str = DEFAULT_PALETTE


@dataclass(frozen=True)
class NativeConfig:
    pane_close_policy: str = DEFAULT_CLOSE_POLICY
    palette: str = DEFAULT_PALETTE


@dataclass(frozen=True)
class LaunchOptions:
    working_directory: str | None = None
    command: str | None = None
    title: str | None = None
    hold_on_exit: bool = False
    settings: TerminalSettings = TerminalSettings()
    native_config: NativeConfig = NativeConfig()
    markdown_paths: tuple[str, ...] = ()
    image_paths: tuple[str, ...] = ()
    control_socket_path: str | None = None


def load_native_config(path: str | os.PathLike | None = None) -> NativeConfig:
    """Load the user config, tolerating missing or malformed files."""
    config_path = Path(path) if path else Path(os.path.expanduser(CONFIG_PATH))
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    policy = data.get("pane_close_policy", DEFAULT_CLOSE_POLICY)
    if policy not in CLOSE_POLICIES:
        policy = DEFAULT_CLOSE_POLICY
    return NativeConfig(
        pane_close_policy=policy,
        palette=normalize_palette(data.get("palette", DEFAULT_PALETTE)),
    )


def default_control_socket_path(pid: int | None = None) -> str:
    """Process-local Unix socket path for picker handoff."""
    pid = os.getpid() if pid is None else pid
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(runtime_dir, f"agent-terminal-native-{pid}.sock")


def default_shell() -> str:
    return os.environ.get("SHELL") or "/bin/bash"


def command_argv(options: LaunchOptions) -> list[str]:
    """The argv to spawn: the explicit command or the default shell."""
    if options.command:
        return shlex.split(options.command)
    return [default_shell()]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-terminal-native",
        description="Native GTK4/VTE terminal with split panes and viewers.",
    )
    parser.add_argument("--working-directory", metavar="DIR", default=None)
    parser.add_argument("--command", metavar="CMD", default=None,
                        help="Command line to run instead of the default shell.")
    parser.add_argument("--title", default=None)
    parser.add_argument("--hold-on-exit", action="store_true",
                        help="Keep the pane open after the child exits.")
    parser.add_argument("--font-family", default=None)
    parser.add_argument("--font-size", type=float, default=None)
    parser.add_argument("--scrollback-lines", type=int, default=None)
    parser.add_argument("--cursor-style", choices=CURSOR_STYLES, default=None)
    parser.add_argument("--no-cursor-blink", action="store_true")
    parser.add_argument("--palette", choices=sorted(PALETTES), default=None)
    parser.add_argument("--markdown", action="append", default=[], metavar="PATH",
                        help="Open a Markdown file in a viewer tab.")
    parser.add_argument("--image", action="append", default=[], metavar="PATH",
                        help="Open an image file in a viewer tab.")
    parser.add_argument("--control-socket", default=None, metavar="PATH")
    parser.add_argument("--config", default=None, metavar="PATH",
                        help="Override the native config file path.")
    parser.add_argument("--version", action="version",
                        version=f"agent-terminal-native {VERSION}")
    return parser


def parse_args(argv: list[str] | None = None) -> LaunchOptions:
    args = build_arg_parser().parse_args(argv)
    config = load_native_config(args.config)
    settings = TerminalSettings(
        font_family=args.font_family or DEFAULT_FONT_FAMILY,
        font_size=clamp_font_size(
            args.font_size if args.font_size is not None else DEFAULT_FONT_SIZE),
        scrollback_lines=(args.scrollback_lines
                          if args.scrollback_lines is not None and args.scrollback_lines >= 0
                          else DEFAULT_SCROLLBACK_LINES),
        cursor_blink=not args.no_cursor_blink,
        cursor_style=normalize_cursor_style(args.cursor_style),
        palette=normalize_palette(args.palette or config.palette),
    )
    return LaunchOptions(
        working_directory=args.working_directory,
        command=args.command,
        title=args.title,
        hold_on_exit=args.hold_on_exit,
        settings=settings,
        native_config=config,
        markdown_paths=tuple(args.markdown),
        image_paths=tuple(args.image),
        control_socket_path=args.control_socket,
    )


# ---------------------------------------------------------------------------
# Pure layout engine
# ---------------------------------------------------------------------------

HORIZONTAL = "horizontal"
VERTICAL = "vertical"
DIRECTIONS = ("left", "right", "up", "down")

MIN_PANE_WIDTH = 72
MIN_PANE_HEIGHT = 48
PANE_GAP = 4
MIN_SPLIT_WEIGHT = 0.05
FIT_FOCUSED_SHARE = 0.62
ZOOM_PANE_SHARE = 0.9
RESIZE_STEP = 0.04
MAX_LAYOUT_HISTORY = 100

_split_ids = itertools.count(1)


def next_split_id() -> str:
    return f"split-{next(_split_ids)}"


@dataclass(frozen=True)
class LayoutNode:
    kind: str
    pane_id: str | None = None
    orientation: str | None = None
    children: tuple["LayoutNode", ...] = ()
    weights: tuple[float, ...] = ()
    split_id: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class PaneRect:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class PixelRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class SplitBoundary:
    split_id: str
    index: int
    orientation: str
    x: int
    y: int
    width: int
    height: int
    axis_start: int
    axis_length: int


@dataclass(frozen=True)
class LayoutSnapshot:
    root: LayoutNode | None
    active_pane_id: str | None


def _normalize_weights(weights) -> tuple[float, ...]:
    cleaned = [max(float(w), MIN_SPLIT_WEIGHT) for w in weights]
    total = sum(cleaned)
    if total <= 0:
        count = len(cleaned) or 1
        return tuple(1.0 / count for _ in range(count))
    return tuple(w / total for w in cleaned)


def layout_leaf(pane_id: str, role: str | None = None) -> LayoutNode:
    return LayoutNode(kind="leaf", pane_id=pane_id, role=role)


def layout_split_group(orientation, children, weights=None, split_id=None,
                       role=None):
    """Build a normalized split: same-axis children are flattened."""
    items = list(children)
    raw = list(weights) if weights else [1.0] * len(items)
    flat_children: list[LayoutNode] = []
    flat_weights: list[float] = []
    for child, weight in zip(items, raw):
        if child is None:
            continue
        if child.kind == "split" and child.orientation == orientation:
            for sub, sub_weight in zip(child.children, child.weights):
                flat_children.append(sub)
                flat_weights.append(weight * sub_weight)
        else:
            flat_children.append(child)
            flat_weights.append(weight)
    if not flat_children:
        return None
    if len(flat_children) == 1:
        return flat_children[0]
    return LayoutNode(
        kind="split",
        orientation=orientation,
        children=tuple(flat_children),
        weights=_normalize_weights(flat_weights),
        split_id=split_id or next_split_id(),
        role=role,
    )


def layout_split_leaf(leaf: LayoutNode, orientation: str, new_pane_id: str,
                      after: bool = True) -> LayoutNode:
    new_leaf = layout_leaf(new_pane_id)
    children = [leaf, new_leaf] if after else [new_leaf, leaf]
    return layout_split_group(orientation, children, [0.5, 0.5])


def layout_split_node(root: LayoutNode, target_pane_id: str, orientation: str,
                      new_pane_id: str, after: bool = True) -> LayoutNode:
    """Split the target leaf, flattening same-axis nesting."""
    if root.kind == "leaf":
        if root.pane_id == target_pane_id:
            return layout_split_leaf(root, orientation, new_pane_id, after)
        return root
    children = [layout_split_node(child, target_pane_id, orientation,
                                  new_pane_id, after)
                for child in root.children]
    return layout_split_group(root.orientation, children, root.weights,
                              root.split_id, root.role)


def layout_leaf_ids(root: LayoutNode | None) -> tuple[str, ...]:
    if root is None:
        return ()
    if root.kind == "leaf":
        return (root.pane_id,)
    out: list[str] = []
    for child in root.children:
        out.extend(layout_leaf_ids(child))
    return tuple(out)


def layout_pane_count(root: LayoutNode | None) -> int:
    return len(layout_leaf_ids(root))


def layout_contains(root: LayoutNode | None, pane_id: str) -> bool:
    return pane_id in layout_leaf_ids(root)


def _layout_path(node: LayoutNode, pane_id: str, trail=()):
    """Path of (split, child_index) pairs from root to the leaf."""
    if node.kind == "leaf":
        return trail if node.pane_id == pane_id else None
    for index, child in enumerate(node.children):
        found = _layout_path(child, pane_id, trail + ((node, index),))
        if found is not None:
            return found
    return None


def layout_rects(root: LayoutNode | None, rect: PaneRect | None = None):
    """Relative pane rectangles in [0, 1] coordinates."""
    out: dict[str, PaneRect] = {}
    if root is None:
        return out
    _collect_rects(root, rect or PaneRect(0.0, 0.0, 1.0, 1.0), out)
    return out


def _collect_rects(node, rect, out):
    if node.kind == "leaf":
        out[node.pane_id] = rect
        return
    offset = 0.0
    for child, weight in zip(node.children, node.weights):
        if node.orientation == HORIZONTAL:
            child_rect = PaneRect(rect.x + rect.width * offset, rect.y,
                                  rect.width * weight, rect.height)
        else:
            child_rect = PaneRect(rect.x, rect.y + rect.height * offset,
                                  rect.width, rect.height * weight)
        _collect_rects(child, child_rect, out)
        offset += weight


def _weighted_sizes(avail: int, weights, minimum: int | None) -> list[int]:
    count = len(weights)
    if avail <= 0:
        return [0] * count
    sizes = [avail * w for w in weights]
    if minimum is not None and avail >= minimum * count:
        for _ in range(count):
            short = [i for i, s in enumerate(sizes) if s < minimum]
            if not short:
                break
            flexible = [i for i in range(count) if i not in short]
            shortfall = sum(minimum - sizes[i] for i in short)
            for i in short:
                sizes[i] = float(minimum)
            slack = sum(sizes[i] - minimum for i in flexible)
            if slack <= 0:
                break
            for i in flexible:
                sizes[i] -= shortfall * (sizes[i] - minimum) / slack
    floors = [int(s) for s in sizes]
    remainder = avail - sum(floors)
    order = sorted(range(count), key=lambda i: sizes[i] - floors[i],
                   reverse=True)
    for i in order[:max(remainder, 0)]:
        floors[i] += 1
    return floors


def _allocate_pixels(node, x, y, width, height, min_w, min_h, gap, out,
                     boundaries):
    if node.kind == "leaf":
        out[node.pane_id] = PixelRect(x, y, max(width, 1), max(height, 1))
        return
    count = len(node.children)
    horizontal = node.orientation == HORIZONTAL
    axis_size = width if horizontal else height
    avail = max(axis_size - gap * (count - 1), 0)
    minimum = min_w if horizontal else min_h
    sizes = _weighted_sizes(avail, node.weights, minimum)
    cursor = x if horizontal else y
    for index, (child, size) in enumerate(zip(node.children, sizes)):
        if horizontal:
            _allocate_pixels(child, cursor, y, size, height, min_w, min_h,
                             gap, out, boundaries)
        else:
            _allocate_pixels(child, x, cursor, width, size, min_w, min_h,
                             gap, out, boundaries)
        cursor += size
        if index < count - 1:
            if boundaries is not None:
                if horizontal:
                    boundaries.append(SplitBoundary(
                        node.split_id, index, node.orientation,
                        cursor, y, gap, height, x, axis_size))
                else:
                    boundaries.append(SplitBoundary(
                        node.split_id, index, node.orientation,
                        x, cursor, width, gap, y, axis_size))
            cursor += gap


def layout_pixel_rects(root, width, height, min_width=MIN_PANE_WIDTH,
                       min_height=MIN_PANE_HEIGHT, gap=PANE_GAP):
    """Pixel pane rectangles, respecting hard minimums when possible."""
    out: dict[str, PixelRect] = {}
    if root is None or width <= 0 or height <= 0:
        return out
    _allocate_pixels(root, 0, 0, int(width), int(height), min_width,
                     min_height, gap, out, None)
    return out


def layout_split_boundaries(root, width, height, min_width=MIN_PANE_WIDTH,
                            min_height=MIN_PANE_HEIGHT, gap=PANE_GAP):
    boundaries: list[SplitBoundary] = []
    if root is None or width <= 0 or height <= 0:
        return ()
    _allocate_pixels(root, 0, 0, int(width), int(height), min_width,
                     min_height, gap, {}, boundaries)
    return tuple(boundaries)


def layout_boundary_at(boundaries, px, py, tolerance=3):
    """Hit-test a pointer position against divider rectangles."""
    for boundary in boundaries:
        if (boundary.x - tolerance <= px <= boundary.x + boundary.width + tolerance
                and boundary.y - tolerance <= py <= boundary.y + boundary.height + tolerance):
            return boundary
    return None


def layout_update_split_ratio(root, split_id, index, ratio):
    """Move one divider of a split; unrelated weights are preserved."""
    if root is None or root.kind == "leaf":
        return root
    if root.split_id == split_id:
        weights = list(root.weights)
        if not 0 <= index < len(weights) - 1:
            return root
        before = sum(weights[:index + 1])
        low = sum(weights[:index]) + MIN_SPLIT_WEIGHT
        high = before + weights[index + 1] - MIN_SPLIT_WEIGHT
        target = min(max(float(ratio), low), max(low, high))
        delta = target - before
        weights[index] += delta
        weights[index + 1] -= delta
        return replace(root, weights=tuple(weights))
    children = tuple(layout_update_split_ratio(child, split_id, index, ratio)
                     for child in root.children)
    return replace(root, children=children)


def layout_update_split_boundary_ratio(root, boundary, position):
    """Drag a divider to an absolute pixel position along its axis."""
    if boundary.axis_length <= 0:
        return root
    ratio = (position - boundary.axis_start) / boundary.axis_length
    return layout_update_split_ratio(root, boundary.split_id, boundary.index,
                                     ratio)


def _direction_axis(direction: str) -> str:
    return HORIZONTAL if direction in ("left", "right") else VERTICAL


def layout_resize_nearest_split_result(root, pane_id, direction,
                                       amount=RESIZE_STEP):
    """Move the nearest matching divider; returns (root, changed)."""
    path = _layout_path(root, pane_id) if root is not None else None
    if not path:
        return root, False
    axis = _direction_axis(direction)
    towards_start = direction in ("left", "up")
    for node, index in reversed(path):
        if node.kind != "split" or node.orientation != axis:
            continue
        count = len(node.children)
        if towards_start:
            boundary_index = index - 1 if index > 0 else index
        else:
            boundary_index = index if index < count - 1 else index - 1
        if not 0 <= boundary_index < count - 1:
            continue
        before = sum(node.weights[:boundary_index + 1])
        delta = -amount if towards_start else amount
        new_root = layout_update_split_ratio(root, node.split_id,
                                             boundary_index, before + delta)
        return new_root, new_root != root
    return root, False


def layout_grow(root, pane_id, side, amount=RESIZE_STEP):
    """Grow (or shrink, with negative amount) the pane from one side."""
    path = _layout_path(root, pane_id) if root is not None else None
    if not path:
        return root
    axis = _direction_axis(side)
    towards_start = side in ("left", "up")
    for node, index in reversed(path):
        if node.kind != "split" or node.orientation != axis:
            continue
        if towards_start and index > 0:
            boundary_index = index - 1
            before = sum(node.weights[:boundary_index + 1])
            return layout_update_split_ratio(root, node.split_id,
                                             boundary_index, before - amount)
        if not towards_start and index < len(node.children) - 1:
            before = sum(node.weights[:index + 1])
            return layout_update_split_ratio(root, node.split_id, index,
                                             before + amount)
    return root


def layout_resize_centered(root, pane_id, axis, amount=RESIZE_STEP):
    """Resize the pane around its center along one axis."""
    sides = ("left", "right") if axis == HORIZONTAL else ("up", "down")
    half = amount / 2.0
    out = layout_grow(root, pane_id, sides[0], half)
    out = layout_grow(out, pane_id, sides[1], half)
    if out == root:
        out = layout_grow(root, pane_id, sides[0], amount)
    if out == root:
        out = layout_grow(root, pane_id, sides[1], amount)
    return out


def layout_focus_target(root, pane_id, direction):
    """Geometrically nearest pane in a direction, or None."""
    rects = layout_rects(root)
    active = rects.get(pane_id)
    if active is None:
        return None
    eps = 1e-6
    best = None
    for other_id, rect in rects.items():
        if other_id == pane_id:
            continue
        if direction == "left":
            distance = active.x - (rect.x + rect.width)
            overlap = (min(active.y + active.height, rect.y + rect.height)
                       - max(active.y, rect.y))
        elif direction == "right":
            distance = rect.x - (active.x + active.width)
            overlap = (min(active.y + active.height, rect.y + rect.height)
                       - max(active.y, rect.y))
        elif direction == "up":
            distance = active.y - (rect.y + rect.height)
            overlap = (min(active.x + active.width, rect.x + rect.width)
                       - max(active.x, rect.x))
        else:
            distance = rect.y - (active.y + active.height)
            overlap = (min(active.x + active.width, rect.x + rect.width)
                       - max(active.x, rect.x))
        if distance < -eps or overlap <= eps:
            continue
        key = (max(distance, 0.0), -overlap)
        if best is None or key < best[0]:
            best = (key, other_id)
    return best[1] if best else None


def layout_remove_leaf(root, pane_id, policy=DEFAULT_CLOSE_POLICY):
    """Remove a leaf. Returns the new root, or None when empty."""
    if root is None:
        return None
    if root.kind == "leaf":
        return None if root.pane_id == pane_id else root
    target_index = None
    for index, child in enumerate(root.children):
        if layout_contains(child, pane_id):
            target_index = index
            break
    if target_index is None:
        return root
    new_child = layout_remove_leaf(root.children[target_index], pane_id,
                                   policy)
    if new_child is not None:
        children = list(root.children)
        children[target_index] = new_child
        return layout_split_group(root.orientation, children, root.weights,
                                  root.split_id, root.role)
    children = [c for i, c in enumerate(root.children) if i != target_index]
    weights = [w for i, w in enumerate(root.weights) if i != target_index]
    if not children:
        return None
    removed_weight = root.weights[target_index]
    if policy == "adjacent_expand" and weights:
        adjacent = target_index - 1 if target_index > 0 else 0
        weights[adjacent] += removed_weight
    return layout_split_group(root.orientation, children, weights,
                              root.split_id, root.role)


def layout_swap_panes(root, first, second):
    if (root is None or not layout_contains(root, first)
            or not layout_contains(root, second)):
        return root

    def walk(node):
        if node.kind == "leaf":
            if node.pane_id == first:
                return replace(node, pane_id=second)
            if node.pane_id == second:
                return replace(node, pane_id=first)
            return node
        return replace(node, children=tuple(walk(c) for c in node.children))

    return walk(root)


def layout_move_pane_near(root, pane_id, direction):
    """Move the pane next to its geometric neighbor in a direction."""
    target = layout_focus_target(root, pane_id, direction)
    if target is None:
        return root
    removed = layout_remove_leaf(root, pane_id, "adjacent_expand")
    if removed is None:
        return root
    axis = _direction_axis(direction)
    after = direction in ("right", "down")
    return layout_split_node(removed, target, axis, pane_id, after=after)


def _rebuild_with_weights(node, weight_fn):
    if node.kind == "leaf":
        return node
    children = tuple(_rebuild_with_weights(child, weight_fn)
                     for child in node.children)
    rebuilt = replace(node, children=children)
    weights = weight_fn(rebuilt)
    if weights is None:
        return rebuilt
    return replace(rebuilt, weights=_normalize_weights(weights))


def layout_balance_splits(root):
    """Equalize every split in the tree."""
    if root is None:
        return None
    return _rebuild_with_weights(root, lambda n: [1.0] * len(n.children))


def layout_balance_axis(root, orientation):
    """Equalize every split with the given orientation."""
    if root is None:
        return None
    return _rebuild_with_weights(
        root,
        lambda n: [1.0] * len(n.children) if n.orientation == orientation else None,
    )


def layout_balance_local(root, pane_id):
    """Equalize only the pane's parent split."""
    path = _layout_path(root, pane_id) if root is not None else None
    if not path:
        return root
    parent, _ = path[-1]
    return _rebuild_with_weights(
        root,
        lambda n: [1.0] * len(n.children) if n.split_id == parent.split_id else None,
    )


def _replace_subtree(node, split_id, replacement):
    if node.kind == "leaf":
        return node
    if node.split_id == split_id:
        return replacement
    return replace(node, children=tuple(
        _replace_subtree(child, split_id, replacement)
        for child in node.children))


def layout_balance_subtree(root, pane_id):
    """Equalize all splits inside the pane's parent split."""
    path = _layout_path(root, pane_id) if root is not None else None
    if not path:
        return root
    parent, _ = path[-1]
    balanced = layout_balance_splits(parent)
    return _replace_subtree(root, parent.split_id, balanced)


def layout_balance_weighted(root, pane_id, factor=2.0):
    """Give the pane a weighted share of its parent split."""
    path = _layout_path(root, pane_id) if root is not None else None
    if not path:
        return root
    parent, index = path[-1]

    def weight_fn(node):
        if node.split_id != parent.split_id:
            return None
        return [factor if i == index else 1.0
                for i in range(len(node.children))]

    return _rebuild_with_weights(root, weight_fn)


def layout_balance_tidy(root):
    """Give every leaf an equal share of the whole area."""
    if root is None or root.kind == "leaf":
        return root
    children = tuple(layout_balance_tidy(child) for child in root.children)
    weights = [float(layout_pane_count(child)) for child in children]
    return replace(root, children=children,
                   weights=_normalize_weights(weights))


def layout_fit_focused(root, pane_id, share=FIT_FOCUSED_SHARE):
    """A temporary root that enlarges the focused pane along every ancestor.

    The base root is never mutated; callers keep this result separate from
    undo history.
    """
    if root is None or not layout_contains(root, pane_id):
        return root

    def adjust(node):
        if node.kind == "leaf":
            return node
        target_index = None
        new_children = []
        for index, child in enumerate(node.children):
            if target_index is None and layout_contains(child, pane_id):
                target_index = index
                new_children.append(adjust(child))
            else:
                new_children.append(child)
        if target_index is None:
            return node
        weights = list(node.weights)
        if weights[target_index] < share:
            others = sum(w for i, w in enumerate(weights) if i != target_index)
            scale = (1.0 - share) / others if others > 0 else 0.0
            weights = [share if i == target_index else w * scale
                       for i, w in enumerate(weights)]
        return replace(node, children=tuple(new_children),
                       weights=_normalize_weights(weights))

    return adjust(root)


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

MARKDOWN_EXTENSIONS = (".md", ".markdown", ".mkd")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
SAFE_URI_SCHEMES = ("http", "https", "mailto", "file")

HEADING_POINT_SIZES = {1: 22.0, 2: 18.0, 3: 15.0, 4: 13.0, 5: 12.0, 6: 11.0}
BASE_TEXT_POINT_SIZE = 10.5


def is_markdown_path(path) -> bool:
    return str(path).lower().endswith(MARKDOWN_EXTENSIONS)


def is_image_path(path) -> bool:
    return str(path).lower().endswith(IMAGE_EXTENSIONS)


def heading_point_size(level: int, zoom: float = 1.0) -> float:
    size = HEADING_POINT_SIZES.get(int(level), BASE_TEXT_POINT_SIZE)
    return size * max(zoom, 0.1)


def list_item_prefix(index: int, ordered: bool) -> str:
    return f"{index}. " if ordered else "• "


@dataclass(frozen=True)
class MarkdownBlock:
    kind: str
    text: str = ""
    level: int = 0
    language: str = ""
    items: tuple = ()
    rows: tuple = ()


_FENCE_RE = re.compile(r"^(```+|~~~+)\s*(\S*)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TASK_RE = re.compile(r"^[-*+]\s+\[( |x|X)\]\s+(.*)$")
_ULIST_RE = re.compile(r"^[-*+]\s+(.*)$")
_OLIST_RE = re.compile(r"^\d+[.)]\s+(.*)$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def _is_rule(stripped: str) -> bool:
    chars = set(stripped.replace(" ", ""))
    return (len(chars) == 1 and chars <= {"-", "*", "_"}
            and len(stripped.replace(" ", "")) >= 3)


def _is_table_start(lines, i) -> bool:
    if "|" not in lines[i] or i + 1 >= len(lines):
        return False
    separator = lines[i + 1]
    return ("|" in separator and "-" in separator
            and _TABLE_SEPARATOR_RE.match(separator) is not None)


def _split_table_row(line: str) -> tuple:
    cells = line.strip().strip("|").split("|")
    return tuple(cell.strip() for cell in cells)


def parse_markdown_blocks(text: str) -> tuple:
    """Parse a practical Markdown subset into renderable blocks."""
    lines = text.splitlines()
    blocks: list[MarkdownBlock] = []
    i = 0
    count = len(lines)
    while i < count:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        fence = _FENCE_RE.match(stripped)
        if fence:
            close = fence.group(1)[0] * 3
            language = fence.group(2)
            i += 1
            body = []
            while i < count and not lines[i].strip().startswith(close):
                body.append(lines[i])
                i += 1
            if i < count:
                i += 1
            blocks.append(MarkdownBlock(kind="code", text="\n".join(body),
                                        language=language))
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            blocks.append(MarkdownBlock(kind="heading",
                                        text=heading.group(2).strip(),
                                        level=len(heading.group(1))))
            i += 1
            continue
        if _is_rule(stripped):
            blocks.append(MarkdownBlock(kind="rule"))
            i += 1
            continue
        if stripped.startswith(">"):
            quote = []
            while i < count and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(MarkdownBlock(kind="blockquote",
                                        text="\n".join(quote)))
            continue
        if _TASK_RE.match(stripped):
            items = []
            while i < count and _TASK_RE.match(lines[i].strip()):
                match = _TASK_RE.match(lines[i].strip())
                items.append((match.group(1).lower() == "x",
                              match.group(2).strip()))
                i += 1
            blocks.append(MarkdownBlock(kind="tasklist", items=tuple(items)))
            continue
        if _is_table_start(lines, i):
            rows = [_split_table_row(lines[i])]
            i += 2
            while i < count and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            blocks.append(MarkdownBlock(kind="table", rows=tuple(rows)))
            continue
        if _ULIST_RE.match(stripped):
            items = []
            while i < count and _ULIST_RE.match(lines[i].strip()) \
                    and not _TASK_RE.match(lines[i].strip()):
                items.append(_ULIST_RE.match(lines[i].strip()).group(1).strip())
                i += 1
            blocks.append(MarkdownBlock(kind="ulist", items=tuple(items)))
            continue
        if _OLIST_RE.match(stripped):
            items = []
            while i < count and _OLIST_RE.match(lines[i].strip()):
                items.append(_OLIST_RE.match(lines[i].strip()).group(1).strip())
                i += 1
            blocks.append(MarkdownBlock(kind="olist", items=tuple(items)))
            continue
        if lines[i].startswith(("    ", "\t")):
            body = []
            while i < count and (lines[i].startswith(("    ", "\t"))
                                 or not lines[i].strip()):
                if not lines[i].strip() and (i + 1 >= count
                                             or not lines[i + 1].startswith(("    ", "\t"))):
                    break
                body.append(lines[i][4:] if lines[i].startswith("    ")
                            else lines[i].lstrip("\t"))
                i += 1
            blocks.append(MarkdownBlock(kind="code", text="\n".join(body)))
            continue
        paragraph = []
        while i < count:
            current = lines[i].strip()
            if not current or _HEADING_RE.match(current) \
                    or _FENCE_RE.match(current) or current.startswith(">") \
                    or _ULIST_RE.match(current) or _OLIST_RE.match(current) \
                    or _is_rule(current) or _is_table_start(lines, i):
                break
            paragraph.append(current)
            i += 1
        blocks.append(MarkdownBlock(kind="paragraph",
                                    text=" ".join(paragraph)))
    return tuple(blocks)


_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_INLINE_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_INLINE_ITALIC_RE = re.compile(r"\*([^*\n]+)\*|\b_([^_\n]+)_\b")
HIGHLIGHT_SPAN = '<span background="#a98e00" foreground="#11151c">'


def _escape_markup(text: str) -> str:
    return html.escape(text, quote=False)


def _escape_attr(text: str) -> str:
    return html.escape(text, quote=True)


def markdown_inline_to_pango(text: str, highlight: str | None = None) -> str:
    """Convert inline Markdown to escaped Pango markup."""
    tokens: list[str] = []

    def stash(markup: str) -> str:
        tokens.append(markup)
        return f"\x00{len(tokens) - 1}\x00"

    work = _INLINE_CODE_RE.sub(
        lambda m: stash(f"<tt>{_escape_markup(m.group(1))}</tt>"), text)
    work = _INLINE_LINK_RE.sub(
        lambda m: stash('<a href="%s">%s</a>'
                        % (_escape_attr(m.group(2)),
                           _escape_markup(m.group(1)))), work)
    out = _escape_markup(work)
    if highlight:
        needle = re.escape(_escape_markup(highlight))
        out = re.sub(needle,
                     lambda m: f"{HIGHLIGHT_SPAN}{m.group(0)}</span>",
                     out, flags=re.IGNORECASE)
    out = _INLINE_BOLD_RE.sub(
        lambda m: f"<b>{m.group(1) or m.group(2)}</b>", out)
    out = _INLINE_ITALIC_RE.sub(
        lambda m: f"<i>{m.group(1) or m.group(2)}</i>", out)
    for index, markup in enumerate(tokens):
        out = out.replace(f"\x00{index}\x00", markup)
    return out


def resolve_local_link(base_path, target) -> str | None:
    """Resolve a Markdown link target to a safe local viewer path."""
    if not isinstance(target, str) or not target:
        return None
    if "://" in target or target.startswith(("#", "mailto:", "tel:")):
        return None
    raw = target.split("#", 1)[0].strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(base_path).resolve().parent / candidate
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    if not candidate.is_file():
        return None
    if is_markdown_path(candidate) or is_image_path(candidate):
        return str(candidate)
    return None


def is_safe_external_uri(uri) -> bool:
    if not isinstance(uri, str) or "://" not in uri and ":" not in uri:
        return False
    scheme = uri.split(":", 1)[0].lower()
    return scheme in SAFE_URI_SCHEMES


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

MIN_IMAGE_SCALE = 0.1
MAX_IMAGE_SCALE = 16.0
IMAGE_ZOOM_STEP = 1.2


def clamp_image_scale(scale) -> float:
    try:
        value = float(scale)
    except (TypeError, ValueError):
        return 1.0
    return min(max(value, MIN_IMAGE_SCALE), MAX_IMAGE_SCALE)


def fit_image_scale(image_width, image_height, viewport_width,
                    viewport_height) -> float:
    """Scale that fits the image inside the viewport, clamped."""
    if min(image_width, image_height, viewport_width, viewport_height) <= 0:
        return 1.0
    return clamp_image_scale(min(viewport_width / image_width,
                                 viewport_height / image_height))


# ---------------------------------------------------------------------------
# Actions and shortcuts
# ---------------------------------------------------------------------------

ACTION_NAMES = (
    "new-window", "new-tab", "open-file", "open-markdown", "open-image",
    "close-active", "close-pane", "next-tab", "previous-tab",
    "split-horizontal", "split-vertical",
    "focus-left", "focus-right", "focus-up", "focus-down",
    "fit-focused", "zoom-pane", "pane-leader",
    "move-border-left", "move-border-right", "move-border-up",
    "move-border-down",
    "grow-left", "grow-right", "grow-up", "grow-down",
    "increase-width", "decrease-width", "increase-height", "decrease-height",
    "undo-layout", "redo-layout",
    "copy", "paste", "select-all", "find", "find-next", "find-previous",
    "reset", "reload-pane", "clear-scrollback",
    "zoom-in", "zoom-out", "zoom-reset",
    "shortcuts", "preferences", "quit",
)

TAB_ACTION_NAMES = tuple(f"tab-{n}" for n in range(1, 10))

APP_LEVEL_ACTIONS = frozenset({"new-window", "quit"})

ACCELERATORS = {
    "new-window": ("<Ctrl><Shift>n",),
    "new-tab": ("<Ctrl><Shift>t",),
    "open-file": ("<Ctrl><Shift>o",),
    "close-active": ("<Ctrl><Shift>w",),
    "next-tab": ("<Ctrl>Page_Down",),
    "previous-tab": ("<Ctrl>Page_Up",),
    "split-horizontal": ("<Alt><Shift>h",),
    "split-vertical": ("<Alt><Shift>v",),
    "focus-left": ("<Alt><Shift>Left",),
    "focus-right": ("<Alt><Shift>Right",),
    "focus-up": ("<Alt><Shift>Up",),
    "focus-down": ("<Alt><Shift>Down",),
    "fit-focused": ("<Alt><Shift>f",),
    "zoom-pane": ("<Alt><Shift>Return",),
    "pane-leader": ("<Alt><Shift>space",),
    "undo-layout": ("<Alt><Shift>z",),
    "redo-layout": ("<Alt><Shift>y",),
    "copy": ("<Ctrl><Shift>c",),
    "paste": ("<Ctrl><Shift>v",),
    "select-all": ("<Ctrl><Shift>a",),
    "find": ("<Ctrl><Shift>f",),
    "find-next": ("<Ctrl><Shift>g",),
    "find-previous": ("<Ctrl><Shift>b",),
    "reset": ("<Ctrl><Shift>r",),
    "reload-pane": ("F5",),
    "clear-scrollback": ("<Ctrl><Shift>k",),
    "zoom-in": ("<Ctrl>plus", "<Ctrl>equal"),
    "zoom-out": ("<Ctrl>minus",),
    "zoom-reset": ("<Ctrl>0",),
    "shortcuts": ("<Ctrl><Shift>h", "F1", "<Ctrl>question"),
    "preferences": ("<Ctrl>comma",),
    "quit": ("<Ctrl><Shift>q",),
}
ACCELERATORS.update({name: (f"<Alt>{name[4:]}",) for name in TAB_ACTION_NAMES})

# Keys that ordinary terminal applications need must never be stolen.
RESERVED_PLAIN_ACCELERATORS = (
    "<Ctrl>h", "<Ctrl>c", "<Ctrl>d", "<Ctrl>w", "<Ctrl>a", "<Ctrl>e",
    "<Ctrl>k", "<Ctrl>l", "<Ctrl>r", "<Ctrl>t", "<Ctrl>u",
)


# ---------------------------------------------------------------------------
# Control socket protocol
# ---------------------------------------------------------------------------

def encode_control_message(message: dict) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"


def parse_control_message(line) -> dict | None:
    """Parse one JSON control line; returns None for invalid payloads."""
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(line, str) or not line.strip():
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    if not isinstance(action, str) or not action:
        return None
    path = data.get("path")
    if path is not None and not isinstance(path, str):
        return None
    return data


class ControlSocketServer:
    """Accept JSON-line control messages on a Unix socket.

    Connections are handled on a daemon thread; messages are forwarded to
    the dispatch callable, which is responsible for hopping onto the GTK
    main loop.
    """

    def __init__(self, path: str, dispatch):
        self.path = path
        self._dispatch = dispatch
        self._closed = False
        try:
            os.unlink(path)
        except OSError:
            pass
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(path)
        self._socket.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="agent-terminal-control")
        self._thread.start()

    def _serve(self):
        while not self._closed:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                break
            try:
                connection.settimeout(2.0)
                data = b""
                while b"\n" not in data and len(data) < 65536:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                for line in data.splitlines():
                    message = parse_control_message(line)
                    if message is not None:
                        self._dispatch(message)
            except OSError:
                pass
            finally:
                try:
                    connection.close()
                except OSError:
                    pass

    def close(self):
        self._closed = True
        try:
            self._socket.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# GTK loading and native classes
# ---------------------------------------------------------------------------

# Behavior surface every pane kind must expose.
PANE_BEHAVIOR_METHODS = (
    "focus", "copy", "paste", "select_all", "reset", "clear_scrollback",
    "reload", "set_search", "find_next", "find_previous",
    "zoom_in", "zoom_out", "zoom_reset",
)

APP_CSS = """
.pane-separator { background-color: alpha(#8a93a5, 0.45); border-radius: 2px; }
.pane-separator:hover { background-color: #6ea3ff; }
.markdown-view { padding: 14px; }
.markdown-code { background-color: alpha(#000000, 0.35); padding: 8px;
                 border-radius: 6px; }
.markdown-quote { border-left: 3px solid #6ea3ff; padding-left: 10px; }
.image-status { padding: 4px 10px; }
.pane-leader { background-color: #1c2128; color: #d8dee9; padding: 18px;
               border-radius: 10px; }
"""

_pane_ids = itertools.count(1)


def next_pane_id() -> str:
    return f"pane-{next(_pane_ids)}"


def load_gtk():
    """Import PyGObject plus GTK/VTE, failing with a concrete package hint."""
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Vte", "3.91")
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import (Gdk, GdkPixbuf, Gio, GLib, GObject,
                                   Graphene, Gsk, Gtk, Pango, Vte)
    except (ImportError, ValueError) as exc:
        raise NativeDependencyError(f"{exc}\n{DEPENDENCY_HINT}") from exc
    return SimpleNamespace(Gdk=Gdk, GdkPixbuf=GdkPixbuf, Gio=Gio, GLib=GLib,
                           GObject=GObject, Graphene=Graphene, Gsk=Gsk,
                           Gtk=Gtk, Pango=Pango, Vte=Vte)


_NATIVE_CLASSES = None


def build_native_classes(g):
    """Build the GTK-backed classes once per process."""
    global _NATIVE_CLASSES
    if _NATIVE_CLASSES is not None:
        return _NATIVE_CLASSES

    Gdk, GdkPixbuf, Gio, GLib = g.Gdk, g.GdkPixbuf, g.Gio, g.GLib
    Graphene, Gsk = g.Graphene, g.Gsk
    Gtk, Pango, Vte = g.Gtk, g.Pango, g.Vte

    def rgba(value):
        color = Gdk.RGBA()
        color.parse(value)
        return color

    def install_css():
        display = Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(APP_CSS.encode("utf-8"))
        except TypeError:
            provider.load_from_data(APP_CSS, -1)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def copy_text_to_clipboard(text):
        display = Gdk.Display.get_default()
        if display is None:
            return
        try:
            display.get_clipboard().set(text)
        except Exception:
            pass

    def child_transform(x, y):
        point = Graphene.Point()
        point.init(float(x), float(y))
        return Gsk.Transform().translate(point)

    class PaneBase:
        """Common behavior surface shared by every pane kind."""

        kind = "pane"

        def __init__(self, pane_id, title):
            self.pane_id = pane_id
            self.title = title
            self.widget = None
            self.on_title_changed = None

        def _notify_title(self):
            if self.on_title_changed:
                self.on_title_changed(self)

        def focus(self):
            if self.widget is not None:
                self.widget.grab_focus()

        def copy(self):
            pass

        def paste(self):
            pass

        def select_all(self):
            pass

        def reset(self):
            pass

        def clear_scrollback(self):
            pass

        def reload(self):
            pass

        def set_search(self, text):
            pass

        def find_next(self):
            pass

        def find_previous(self):
            pass

        def zoom_in(self):
            pass

        def zoom_out(self):
            pass

        def zoom_reset(self):
            pass

    class TerminalPane(PaneBase):
        kind = "terminal"

        def __init__(self, settings, *, command=None, working_directory=None,
                     hold_on_exit=False, control_socket_path=None,
                     extra_env=None, on_exited=None, title=None):
            super().__init__(next_pane_id(), title or "Terminal")
            self.hold_on_exit = hold_on_exit
            self.on_exited = on_exited
            self.terminal = Vte.Terminal()
            self._configure(settings)
            self.terminal.connect("child-exited", self._on_child_exited)
            try:
                self.terminal.connect("window-title-changed",
                                      self._on_title_signal)
            except TypeError:
                pass
            scroller = Gtk.ScrolledWindow()
            scroller.set_child(self.terminal)
            scroller.set_hexpand(True)
            scroller.set_vexpand(True)
            self.widget = scroller
            self._spawn(command, working_directory, control_socket_path,
                        extra_env)

        def _configure(self, settings):
            terminal = self.terminal
            terminal.set_scrollback_lines(int(settings.scrollback_lines))
            terminal.set_mouse_autohide(True)
            try:
                terminal.set_allow_hyperlink(True)
            except AttributeError:
                pass
            font = Pango.FontDescription()
            font.set_family(settings.font_family)
            font.set_size(int(clamp_font_size(settings.font_size)
                              * Pango.SCALE))
            terminal.set_font(font)
            shapes = {
                "block": Vte.CursorShape.BLOCK,
                "ibeam": Vte.CursorShape.IBEAM,
                "underline": Vte.CursorShape.UNDERLINE,
            }
            terminal.set_cursor_shape(shapes.get(settings.cursor_style,
                                                 Vte.CursorShape.BLOCK))
            terminal.set_cursor_blink_mode(
                Vte.CursorBlinkMode.ON if settings.cursor_blink
                else Vte.CursorBlinkMode.OFF)
            palette = PALETTES[normalize_palette(settings.palette)]
            terminal.set_colors(rgba(palette["foreground"]),
                                rgba(palette["background"]),
                                [rgba(value) for value in palette["colors"]])
            try:
                regex = Vte.Regex.new_for_match(URL_REGEX_PATTERN, -1,
                                                URL_REGEX_FLAGS)
                tag = terminal.match_add_regex(regex, 0)
                terminal.match_set_cursor_name(tag, "pointer")
            except Exception:
                pass

        def _spawn(self, command, working_directory, control_socket_path,
                   extra_env):
            argv = list(command) if command else [default_shell()]
            env = dict(os.environ)
            env.setdefault("TERM", "xterm-256color")
            if control_socket_path:
                env[CONTROL_SOCKET_ENV] = control_socket_path
            if extra_env:
                env.update(extra_env)
            envv = [f"{key}={value}" for key, value in env.items()]
            cwd = working_directory or os.getcwd()
            try:
                self.terminal.spawn_async(
                    Vte.PtyFlags.DEFAULT, cwd, argv, envv,
                    GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None,
                    self._on_spawned)
            except TypeError:
                self.terminal.spawn_async(
                    Vte.PtyFlags.DEFAULT, cwd, argv, envv,
                    GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None,
                    self._on_spawned, None)

        def _on_spawned(self, terminal, pid, error, *user_data):
            if error is not None:
                self._feed_message(f"spawn failed: {error}")

        def _feed_message(self, message):
            data = f"\r\n[{message}]\r\n".encode("utf-8")
            try:
                self.terminal.feed(data)
            except TypeError:
                self.terminal.feed(data, len(data))

        def _on_child_exited(self, terminal, status):
            if self.hold_on_exit:
                self._feed_message(f"process exited with status {int(status)}")
                return
            if self.on_exited:
                self.on_exited(self)

        def _on_title_signal(self, terminal):
            self.title = terminal.get_window_title() or "Terminal"
            self._notify_title()

        def focus(self):
            self.terminal.grab_focus()

        def copy(self):
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)

        def paste(self):
            self.terminal.paste_clipboard()

        def select_all(self):
            self.terminal.select_all()

        def reset(self):
            self.terminal.reset(True, False)

        def clear_scrollback(self):
            self.terminal.reset(False, True)

        def reload(self):
            self.reset()

        def set_search(self, text):
            if not text:
                try:
                    self.terminal.search_set_regex(None, 0)
                except Exception:
                    pass
                return
            try:
                regex = Vte.Regex.new_for_search(re.escape(text), -1,
                                                 SEARCH_REGEX_FLAGS)
            except Exception:
                return
            self.terminal.search_set_regex(regex, 0)
            self.terminal.search_set_wrap_around(True)

        def find_next(self):
            self.terminal.search_find_next()

        def find_previous(self):
            self.terminal.search_find_previous()

        def zoom_in(self):
            self.terminal.set_font_scale(
                min(self.terminal.get_font_scale() * 1.1, 4.0))

        def zoom_out(self):
            self.terminal.set_font_scale(
                max(self.terminal.get_font_scale() / 1.1, 0.3))

        def zoom_reset(self):
            self.terminal.set_font_scale(1.0)

    class MarkdownPane(PaneBase):
        """Passive native Markdown viewer rendered with GTK labels."""

        kind = "markdown"

        def __init__(self, path, *, on_open_path=None, on_activated=None):
            super().__init__(next_pane_id(),
                             os.path.basename(str(path)) or "Markdown")
            self.path = str(path)
            self.on_open_path = on_open_path
            self.on_activated = on_activated
            self._zoom = 1.0
            self._search = ""
            self._match_widgets = []
            self._match_index = -1
            self._scroller = Gtk.ScrolledWindow()
            self._scroller.set_hexpand(True)
            self._scroller.set_vexpand(True)
            self.widget = self._scroller
            click = Gtk.GestureClick()
            click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            click.connect("pressed", self._on_pressed)
            self._scroller.add_controller(click)
            self.reload()

        def _on_pressed(self, *args):
            if self.on_activated:
                self.on_activated(self)

        def reload(self):
            try:
                text = Path(self.path).read_text(encoding="utf-8",
                                                 errors="replace")
            except OSError as exc:
                text = f"# Unable to read file\n\n`{self.path}`\n\n{exc}"
            self._render(parse_markdown_blocks(text))

        def _render(self, blocks):
            self._match_widgets = []
            self._match_index = -1
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            box.add_css_class("markdown-view")
            for block in blocks:
                widget = self._build_block(block)
                if widget is not None:
                    box.append(widget)
            self._scroller.set_child(box)

        def _label(self, markup, *, wrap=True):
            label = Gtk.Label()
            label.set_xalign(0.0)
            label.set_wrap(wrap)
            if wrap:
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            # Passive viewer: focusable or selectable labels would steal
            # keyboard focus from terminal panes and crash focus chains.
            label.set_selectable(False)
            label.set_focusable(False)
            label.set_use_markup(True)
            try:
                label.set_markup(markup)
            except Exception:
                label.set_text(markup)
            label.connect("activate-link", self._on_link)
            if self._search and HIGHLIGHT_SPAN in markup:
                self._match_widgets.append(label)
            return label

        def _inline(self, text):
            size = int(BASE_TEXT_POINT_SIZE * self._zoom * 1024)
            return (f'<span size="{size}">'
                    + markdown_inline_to_pango(text, self._search or None)
                    + "</span>")

        def _highlighted_code(self, text):
            escaped = _escape_markup(text)
            if self._search:
                needle = re.escape(_escape_markup(self._search))
                escaped = re.sub(
                    needle,
                    lambda m: f"{HIGHLIGHT_SPAN}{m.group(0)}</span>",
                    escaped, flags=re.IGNORECASE)
            return escaped

        def _build_block(self, block):
            if block.kind == "heading":
                size = int(heading_point_size(block.level, self._zoom) * 1024)
                markup = (f'<span size="{size}" weight="bold">'
                          + markdown_inline_to_pango(block.text,
                                                     self._search or None)
                          + "</span>")
                return self._label(markup)
            if block.kind == "paragraph":
                return self._label(self._inline(block.text))
            if block.kind == "blockquote":
                quote = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=4)
                quote.add_css_class("markdown-quote")
                for line in block.text.splitlines() or [""]:
                    quote.append(self._label(self._inline(line)))
                return quote
            if block.kind == "code":
                wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                wrapper.add_css_class("markdown-code")
                wrapper.append(self._label(
                    f"<tt>{self._highlighted_code(block.text)}</tt>",
                    wrap=False))
                return wrapper
            if block.kind == "rule":
                return Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            if block.kind in ("ulist", "olist"):
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                ordered = block.kind == "olist"
                for index, item in enumerate(block.items, start=1):
                    box.append(self._label(self._inline(
                        list_item_prefix(index, ordered) + item)))
                return box
            if block.kind == "tasklist":
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                for checked, item in block.items:
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                  spacing=6)
                    check = Gtk.CheckButton()
                    check.set_active(bool(checked))
                    check.set_sensitive(False)
                    check.set_focusable(False)
                    check.set_can_focus(False)
                    row.append(check)
                    row.append(self._label(self._inline(item)))
                    box.append(row)
                return box
            if block.kind == "table":
                grid = Gtk.Grid()
                grid.set_column_spacing(14)
                grid.set_row_spacing(4)
                for row_index, row in enumerate(block.rows):
                    for col_index, cell in enumerate(row):
                        markup = self._inline(cell)
                        if row_index == 0:
                            markup = f"<b>{markup}</b>"
                        grid.attach(self._label(markup, wrap=False),
                                    col_index, row_index, 1, 1)
                return grid
            return None

        def _on_link(self, label, uri):
            local = resolve_local_link(self.path, uri)
            if local and self.on_open_path:
                self.on_open_path(local)
                return True
            if is_safe_external_uri(uri):
                try:
                    Gtk.show_uri(None, uri, Gdk.CURRENT_TIME)
                except Exception:
                    pass
                return True
            return True

        def copy(self):
            copy_text_to_clipboard(self.path)

        def set_search(self, text):
            self._search = text or ""
            self.reload()
            if self._match_widgets:
                self._match_index = 0
                self._scroll_to(self._match_widgets[0])

        def find_next(self):
            if not self._match_widgets:
                return
            self._match_index = (self._match_index + 1) % len(self._match_widgets)
            self._scroll_to(self._match_widgets[self._match_index])

        def find_previous(self):
            if not self._match_widgets:
                return
            self._match_index = (self._match_index - 1) % len(self._match_widgets)
            self._scroll_to(self._match_widgets[self._match_index])

        def _scroll_to(self, widget):
            content = self._scroller.get_child()
            if content is None:
                return
            try:
                ok, bounds = widget.compute_bounds(content)
            except Exception:
                return
            if ok:
                adjustment = self._scroller.get_vadjustment()
                adjustment.set_value(max(bounds.get_y() - 40.0, 0.0))

        def zoom_in(self):
            self._zoom = min(self._zoom * 1.1, 3.0)
            self.reload()

        def zoom_out(self):
            self._zoom = max(self._zoom / 1.1, 0.5)
            self.reload()

        def zoom_reset(self):
            self._zoom = 1.0
            self.reload()

    class ImagePane(PaneBase):
        """Native image viewer: GdkPixbuf loading with Gtk.Picture output."""

        kind = "image"

        def __init__(self, path, *, on_activated=None):
            super().__init__(next_pane_id(),
                             os.path.basename(str(path)) or "Image")
            self.path = str(path)
            self.on_activated = on_activated
            self._scale = 1.0
            self._fit_mode = False
            self._pixbuf = None
            self._pan_origin = (0.0, 0.0)
            self.picture = Gtk.Picture()
            self.picture.set_can_shrink(False)
            self._scroller = Gtk.ScrolledWindow()
            self._scroller.set_hexpand(True)
            self._scroller.set_vexpand(True)
            self._scroller.set_child(self.picture)
            self._status = Gtk.Label()
            self._status.set_xalign(0.0)
            self._status.add_css_class("image-status")
            self._status.set_selectable(False)
            self._status.set_focusable(False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.append(self._scroller)
            box.append(self._status)
            self.widget = box
            click = Gtk.GestureClick()
            click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            click.connect("pressed", self._on_pressed)
            box.add_controller(click)
            scroll = Gtk.EventControllerScroll.new(
                Gtk.EventControllerScrollFlags.BOTH_AXES)
            scroll.connect("scroll", self._on_scroll)
            self._scroller.add_controller(scroll)
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self._on_drag_begin)
            drag.connect("drag-update", self._on_drag_update)
            self._scroller.add_controller(drag)
            key = Gtk.EventControllerKey()
            key.connect("key-pressed", self._on_key)
            box.add_controller(key)
            self.reload()

        def _on_pressed(self, *args):
            if self.on_activated:
                self.on_activated(self)

        def reload(self):
            try:
                self._pixbuf = GdkPixbuf.Pixbuf.new_from_file(self.path)
            except Exception as exc:
                self._pixbuf = None
                self._status.set_text(f"Unable to load {self.path}: {exc}")
                return
            try:
                texture = Gdk.Texture.new_for_pixbuf(self._pixbuf)
                self.picture.set_paintable(texture)
            except Exception:
                pass
            if self._fit_mode:
                self.fit_to_pane()
            else:
                self._apply_scale()

        def _apply_scale(self):
            if self._pixbuf is None:
                return
            width = max(int(self._pixbuf.get_width() * self._scale), 1)
            height = max(int(self._pixbuf.get_height() * self._scale), 1)
            self.picture.set_size_request(width, height)
            self._status.set_text(
                f"{os.path.basename(self.path)} — "
                f"{self._pixbuf.get_width()}x{self._pixbuf.get_height()} — "
                f"{int(self._scale * 100)}%")

        def actual_size(self):
            self._fit_mode = False
            self._scale = 1.0
            self._apply_scale()

        def fit_to_pane(self):
            if self._pixbuf is None:
                return
            self._fit_mode = True
            viewport_width = self._scroller.get_width() or 1
            viewport_height = self._scroller.get_height() or 1
            self._scale = fit_image_scale(self._pixbuf.get_width(),
                                          self._pixbuf.get_height(),
                                          viewport_width, viewport_height)
            self._apply_scale()

        def zoom_in(self):
            self._fit_mode = False
            self._scale = clamp_image_scale(self._scale * IMAGE_ZOOM_STEP)
            self._apply_scale()

        def zoom_out(self):
            self._fit_mode = False
            self._scale = clamp_image_scale(self._scale / IMAGE_ZOOM_STEP)
            self._apply_scale()

        def zoom_reset(self):
            self.actual_size()

        def copy(self):
            copy_text_to_clipboard(self.path)

        def _on_scroll(self, controller, dx, dy):
            try:
                state = controller.get_current_event_state()
            except Exception:
                return False
            if not state & Gdk.ModifierType.CONTROL_MASK:
                return False
            if dy < 0:
                self.zoom_in()
            else:
                self.zoom_out()
            return True

        def _on_drag_begin(self, gesture, x, y):
            self._pan_origin = (self._scroller.get_hadjustment().get_value(),
                                self._scroller.get_vadjustment().get_value())

        def _on_drag_update(self, gesture, dx, dy):
            self._scroller.get_hadjustment().set_value(
                self._pan_origin[0] - dx)
            self._scroller.get_vadjustment().set_value(
                self._pan_origin[1] - dy)

        def _on_key(self, controller, keyval, keycode, state):
            name = (Gdk.keyval_name(keyval) or "").lower()
            if name == "f":
                self.fit_to_pane()
                return True
            if name == "1":
                self.actual_size()
                return True
            return False

    class AgentPaneLayoutWidget(Gtk.Widget):
        """Custom container that hands its allocation to the layout owner."""

        __gtype_name__ = "AgentPaneLayoutWidget"

        def __init__(self, owner):
            super().__init__()
            self._owner = owner
            self.set_hexpand(True)
            self.set_vexpand(True)

        def do_measure(self, orientation, for_size):
            if orientation == Gtk.Orientation.HORIZONTAL:
                return (MIN_PANE_WIDTH, 640, -1, -1)
            return (MIN_PANE_HEIGHT, 400, -1, -1)

        def do_size_allocate(self, width, height, baseline):
            self._owner.allocate_children(width, height, baseline)

    class PaneLayoutContainer:
        """Tracks pane and divider widgets and performs child allocation."""

        def __init__(self, tab):
            self._tab = tab
            self.widget = AgentPaneLayoutWidget(self)
            self._pane_widgets = {}
            self._separators = []
            self._boundaries = ()
            self._drag_boundary = None
            self._drag_origin = 0.0

        def set_panes(self, widgets):
            for pane_id, child in widgets.items():
                if pane_id not in self._pane_widgets:
                    child.set_parent(self.widget)
                    self._pane_widgets[pane_id] = child
            for pane_id in list(self._pane_widgets):
                if pane_id not in widgets:
                    self._pane_widgets.pop(pane_id).unparent()
            root = self._tab.display_root()
            self._ensure_separators(
                len(layout_split_boundaries(root, 1024, 768)))
            self.widget.queue_allocate()

        def remove_pane(self, pane_id):
            child = self._pane_widgets.pop(pane_id, None)
            if child is not None:
                child.unparent()

        def allocate_children(self, width, height, baseline):
            root = self._tab.display_root()
            if root is None:
                return
            rects = layout_pixel_rects(root, width, height)
            for pane_id, child in self._pane_widgets.items():
                rect = rects.get(pane_id)
                if rect is None:
                    child.set_child_visible(False)
                    continue
                child.set_child_visible(True)
                # Pane widgets must not keep stale size requests.
                transform = child_transform(rect.x, rect.y)
                child.allocate(rect.width, rect.height, -1, transform)
            boundaries = layout_split_boundaries(root, width, height)
            self._ensure_separators(len(boundaries))
            for separator, boundary in zip(self._separators, boundaries):
                cursor = ("col-resize" if boundary.orientation == HORIZONTAL
                          else "row-resize")
                try:
                    separator.set_cursor(Gdk.Cursor.new_from_name(cursor))
                except Exception:
                    pass
                separator.set_child_visible(True)
                transform = child_transform(boundary.x, boundary.y)
                separator.allocate(max(boundary.width, 1),
                                   max(boundary.height, 1), -1, transform)
            self._boundaries = boundaries

        def _ensure_separators(self, count):
            while len(self._separators) < count:
                index = len(self._separators)
                separator = Gtk.Box()
                separator.add_css_class("pane-separator")
                separator.set_parent(self.widget)
                drag = Gtk.GestureDrag()
                drag.connect("drag-begin", self._on_drag_begin, index)
                drag.connect("drag-update", self._on_drag_update)
                drag.connect("drag-end", self._on_drag_end)
                separator.add_controller(drag)
                self._separators.append(separator)
            while len(self._separators) > count:
                self._separators.pop().unparent()

        def _on_drag_begin(self, gesture, x, y, index):
            if index >= len(self._boundaries):
                return
            boundary = self._boundaries[index]
            self._drag_boundary = boundary
            if boundary.orientation == HORIZONTAL:
                self._drag_origin = boundary.x + boundary.width / 2.0
            else:
                self._drag_origin = boundary.y + boundary.height / 2.0
            self._tab.begin_boundary_drag()

        def _on_drag_update(self, gesture, dx, dy):
            boundary = self._drag_boundary
            if boundary is None:
                return
            delta = dx if boundary.orientation == HORIZONTAL else dy
            self._tab.update_boundary(boundary, self._drag_origin + delta)

        def _on_drag_end(self, gesture, dx, dy):
            self._drag_boundary = None

    class TerminalTab:
        """One notebook page: a pane dictionary plus a split-layout tree."""

        def __init__(self, window, first_pane):
            self.window = window
            self.panes = {}
            self.root = None
            self.active_pane_id = None
            self.fit_focused_root = None
            self.undo_stack = []
            self.redo_stack = []
            self.container = PaneLayoutContainer(self)
            self.widget = self.container.widget
            self.title = first_pane.title
            self._attach_pane(first_pane)
            self.root = layout_leaf(first_pane.pane_id)
            self.active_pane_id = first_pane.pane_id
            self._sync()

        def display_root(self):
            if self.fit_focused_root is not None:
                return self.fit_focused_root
            return self.root

        def active_pane(self):
            return self.panes.get(self.active_pane_id)

        def pane_count(self):
            return len(self.panes)

        def _attach_pane(self, pane):
            self.panes[pane.pane_id] = pane
            pane.on_title_changed = self._on_pane_title
            click = Gtk.GestureClick()
            click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            click.connect("pressed", self._on_pane_pressed, pane.pane_id)
            pane.widget.add_controller(click)

        def _on_pane_pressed(self, gesture, n_press, x, y, pane_id):
            self.set_active(pane_id)

        def _on_pane_title(self, pane):
            if pane.pane_id == self.active_pane_id:
                self.title = pane.title
                self.window.update_tab_title(self)

        def set_active(self, pane_id, focus=False):
            if pane_id not in self.panes:
                return
            self.active_pane_id = pane_id
            self.title = self.panes[pane_id].title
            self.window.update_tab_title(self)
            if focus:
                self.panes[pane_id].focus()

        def _sync(self):
            self.container.set_panes(
                {pane_id: pane.widget for pane_id, pane in self.panes.items()})

        def push_history(self):
            self.undo_stack.append(LayoutSnapshot(self.root,
                                                  self.active_pane_id))
            del self.undo_stack[:-MAX_LAYOUT_HISTORY]
            self.redo_stack.clear()

        def _prune_history(self):
            valid = set(self.panes)
            self.undo_stack = [snap for snap in self.undo_stack
                               if set(layout_leaf_ids(snap.root)) <= valid]
            self.redo_stack = [snap for snap in self.redo_stack
                               if set(layout_leaf_ids(snap.root)) <= valid]

        def add_pane(self, pane, orientation=HORIZONTAL):
            self.push_history()
            self.fit_focused_root = None
            self._attach_pane(pane)
            if self.root is None:
                self.root = layout_leaf(pane.pane_id)
            else:
                self.root = layout_split_node(self.root, self.active_pane_id,
                                              orientation, pane.pane_id,
                                              after=True)
            self.set_active(pane.pane_id)
            self._sync()
            pane.focus()

        def split(self, orientation):
            self.add_pane(self.window.create_terminal_pane(), orientation)

        def close_pane(self, pane_id=None):
            pane_id = pane_id or self.active_pane_id
            pane = self.panes.pop(pane_id, None)
            if pane is None:
                return
            self.push_history()
            self.fit_focused_root = None
            self.root = layout_remove_leaf(self.root, pane_id,
                                           self.window.close_policy)
            self.container.remove_pane(pane_id)
            self._prune_history()
            if self.root is None:
                self.window.remove_tab(self)
                return
            if self.active_pane_id not in self.panes:
                ids = layout_leaf_ids(self.root)
                if ids:
                    self.set_active(ids[0])
            self._sync()
            active = self.active_pane()
            if active:
                active.focus()

        def focus_direction(self, direction):
            target = layout_focus_target(self.display_root(),
                                         self.active_pane_id, direction)
            if self.fit_focused_root is not None:
                self.fit_focused_root = None
                self.widget.queue_allocate()
            if target:
                self.set_active(target, focus=True)

        def toggle_fit_focused(self, share=FIT_FOCUSED_SHARE):
            if self.fit_focused_root is not None:
                self.fit_focused_root = None
            elif (self.active_pane_id and self.root is not None
                  and self.root.kind == "split"):
                self.fit_focused_root = layout_fit_focused(
                    self.root, self.active_pane_id, share)
            self.widget.queue_allocate()

        def _commit(self, new_root):
            if new_root is None or new_root == self.root:
                return False
            self.push_history()
            self.fit_focused_root = None
            self.root = new_root
            self.widget.queue_allocate()
            return True

        def move_border(self, direction):
            new_root, changed = layout_resize_nearest_split_result(
                self.root, self.active_pane_id, direction)
            if changed:
                self._commit(new_root)

        def grow(self, side, amount=RESIZE_STEP):
            self._commit(layout_grow(self.root, self.active_pane_id, side,
                                     amount))

        def resize_centered(self, axis, amount):
            self._commit(layout_resize_centered(self.root,
                                                self.active_pane_id, axis,
                                                amount))

        def move_pane(self, direction):
            self._commit(layout_move_pane_near(self.root,
                                               self.active_pane_id,
                                               direction))

        def swap_with(self, direction):
            target = layout_focus_target(self.root, self.active_pane_id,
                                         direction)
            if target:
                self._commit(layout_swap_panes(self.root,
                                               self.active_pane_id, target))

        def balance(self, kind):
            pane_id = self.active_pane_id
            if kind == "local":
                result = layout_balance_local(self.root, pane_id)
            elif kind == "axis":
                path = _layout_path(self.root, pane_id) if self.root else None
                orientation = path[-1][0].orientation if path else HORIZONTAL
                result = layout_balance_axis(self.root, orientation)
            elif kind == "subtree":
                result = layout_balance_subtree(self.root, pane_id)
            elif kind == "weighted":
                result = layout_balance_weighted(self.root, pane_id)
            elif kind == "spotlight":
                result = layout_fit_focused(self.root, pane_id,
                                            ZOOM_PANE_SHARE)
            elif kind == "tidy":
                result = layout_balance_tidy(self.root)
            else:
                result = layout_balance_splits(self.root)
            self._commit(result)

        def undo(self):
            if not self.undo_stack:
                return
            self.redo_stack.append(LayoutSnapshot(self.root,
                                                  self.active_pane_id))
            self._restore(self.undo_stack.pop())

        def redo(self):
            if not self.redo_stack:
                return
            self.undo_stack.append(LayoutSnapshot(self.root,
                                                  self.active_pane_id))
            self._restore(self.redo_stack.pop())

        def _restore(self, snapshot):
            self.fit_focused_root = None
            self.root = snapshot.root
            ids = layout_leaf_ids(self.root)
            active = snapshot.active_pane_id
            if active not in self.panes or (ids and active not in ids):
                active = ids[0] if ids else None
            if active:
                self.set_active(active, focus=True)
            self.widget.queue_allocate()

        def begin_boundary_drag(self):
            if self.fit_focused_root is None:
                self.push_history()

        def update_boundary(self, boundary, position):
            if self.fit_focused_root is not None:
                return
            new_root = layout_update_split_boundary_ratio(self.root, boundary,
                                                          position)
            if new_root != self.root:
                self.root = new_root
                self.widget.queue_allocate()

        def dispose(self):
            for pane_id in list(self.panes):
                self.container.remove_pane(pane_id)
            self.panes.clear()

    LEADER_HINTS = {
        "command": ("arrows focus · h/v split · x close · f fit · z undo · "
                    "y redo\nr resize · g grow · m move · b balance · "
                    "Esc close"),
        "resize": "arrows move the nearest divider · Esc back",
        "grow": ("arrows grow from a side · Shift+arrows resize around "
                 "center · Esc back"),
        "move": ("arrows move the pane near a neighbor · Shift+arrows swap · "
                 "Esc back"),
        "balance": ("l local · a axis · s subtree · t tab · w weighted · "
                    "o spotlight · d tidy · Esc back"),
    }

    class PaneLeaderWindow(Gtk.Window):
        """Transient modal pane-control window (command/resize/grow/move)."""

        def __init__(self, parent):
            super().__init__()
            self.set_transient_for(parent)
            self.set_modal(True)
            self.set_decorated(False)
            self.set_resizable(False)
            self._window = parent
            self.mode = "command"
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.add_css_class("pane-leader")
            self._title = Gtk.Label()
            self._title.set_xalign(0.0)
            self._hint = Gtk.Label()
            self._hint.set_xalign(0.0)
            box.append(self._title)
            box.append(self._hint)
            self.set_child(box)
            key = Gtk.EventControllerKey()
            key.connect("key-pressed", self._on_key)
            self.add_controller(key)
            self._refresh()

        def _refresh(self):
            self._title.set_markup(
                f"<b>Pane control — {self.mode} mode</b>")
            self._hint.set_text(LEADER_HINTS[self.mode])

        def _set_mode(self, mode):
            self.mode = mode
            self._refresh()

        def _on_key(self, controller, keyval, keycode, state):
            name = Gdk.keyval_name(keyval) or ""
            lower = name.lower()
            shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
            direction = {"Left": "left", "Right": "right",
                         "Up": "up", "Down": "down"}.get(name)
            if name == "Escape":
                if self.mode == "command":
                    self.close()
                else:
                    self._set_mode("command")
                return True
            tab = self._window.active_tab()
            if tab is None:
                return True
            if self.mode == "command":
                if direction:
                    tab.focus_direction(direction)
                elif lower == "h":
                    tab.split(HORIZONTAL)
                    self.close()
                elif lower == "v":
                    tab.split(VERTICAL)
                    self.close()
                elif lower in ("x", "w"):
                    self._window.close_active_pane()
                    self.close()
                elif lower == "f":
                    tab.toggle_fit_focused()
                elif lower == "z":
                    tab.undo()
                elif lower == "y":
                    tab.redo()
                elif lower == "r":
                    self._set_mode("resize")
                elif lower == "g":
                    self._set_mode("grow")
                elif lower == "m":
                    self._set_mode("move")
                elif lower == "b":
                    self._set_mode("balance")
                elif lower == "q":
                    self.close()
                return True
            if self.mode == "resize":
                if direction:
                    tab.move_border(direction)
                return True
            if self.mode == "grow":
                if direction and shift:
                    axis = (HORIZONTAL if direction in ("left", "right")
                            else VERTICAL)
                    amount = (RESIZE_STEP if direction in ("right", "down")
                              else -RESIZE_STEP)
                    tab.resize_centered(axis, amount)
                elif direction:
                    tab.grow(direction)
                return True
            if self.mode == "move":
                if direction and shift:
                    tab.swap_with(direction)
                elif direction:
                    tab.move_pane(direction)
                return True
            if self.mode == "balance":
                kind = {"l": "local", "a": "axis", "s": "subtree", "t": "tab",
                        "w": "weighted", "o": "spotlight",
                        "d": "tidy"}.get(lower)
                if kind:
                    tab.balance(kind)
                    self._set_mode("command")
                return True
            return True

    class NativeTerminalWindow(Gtk.ApplicationWindow):
        def __init__(self, app, options):
            super().__init__(application=app,
                             title=options.title or APP_TITLE)
            self.set_default_size(1100, 700)
            self._app = app
            self.options = options
            self.close_policy = options.native_config.pane_close_policy
            self.tabs = []
            self._picker_windows = []
            self._leader = None
            self._build_header()
            self._build_body()
            self._install_actions()
            self._open_initial_tabs()

        def _build_header(self):
            header = Gtk.HeaderBar()
            new_tab = Gtk.Button.new_from_icon_name("tab-new-symbolic")
            new_tab.set_action_name("win.new-tab")
            new_tab.set_tooltip_text("New tab (Ctrl+Shift+T)")
            header.pack_start(new_tab)
            menu = Gio.Menu()
            files = Gio.Menu()
            files.append("New Window", "app.new-window")
            files.append("New Tab", "win.new-tab")
            files.append("Open File…", "win.open-file")
            menu.append_section(None, files)
            edit = Gio.Menu()
            edit.append("Copy", "win.copy")
            edit.append("Paste", "win.paste")
            edit.append("Select All", "win.select-all")
            edit.append("Find", "win.find")
            menu.append_section(None, edit)
            view = Gio.Menu()
            view.append("Reset", "win.reset")
            view.append("Clear Scrollback", "win.clear-scrollback")
            view.append("Reload Pane", "win.reload-pane")
            menu.append_section(None, view)
            meta = Gio.Menu()
            meta.append("Keyboard Shortcuts", "win.shortcuts")
            meta.append("Preferences", "win.preferences")
            meta.append("Quit", "app.quit")
            menu.append_section(None, meta)
            button = Gtk.MenuButton()
            button.set_icon_name("open-menu-symbolic")
            button.set_menu_model(menu)
            header.pack_end(button)
            self.set_titlebar(header)

        def _build_body(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.search_bar = Gtk.SearchBar()
            self.search_entry = Gtk.SearchEntry()
            self.search_entry.connect("search-changed",
                                      self._on_search_changed)
            self.search_entry.connect(
                "activate", lambda *_: self._route("find_next"))
            self.search_bar.set_child(self.search_entry)
            self.search_bar.connect_entry(self.search_entry)
            self.notebook = Gtk.Notebook()
            self.notebook.set_scrollable(True)
            self.notebook.connect("switch-page", self._on_switch_page)
            box.append(self.search_bar)
            box.append(self.notebook)
            self.set_child(box)

        def _install_actions(self):
            for name in ACTION_NAMES + TAB_ACTION_NAMES:
                if name in APP_LEVEL_ACTIONS:
                    continue
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", self._on_action, name)
                self.add_action(action)

        def _open_initial_tabs(self):
            options = self.options
            command = command_argv(options) if options.command else None
            self.add_terminal_tab(command=command,
                                  working_directory=options.working_directory,
                                  hold_on_exit=options.hold_on_exit,
                                  title=options.title)
            for path in options.markdown_paths:
                self.open_path_in_new_tab(path)
            for path in options.image_paths:
                self.open_path_in_new_tab(path)
            self.notebook.set_current_page(0)

        # -- pane factories -------------------------------------------------

        def create_terminal_pane(self, command=None, working_directory=None,
                                 hold_on_exit=False, title=None):
            return TerminalPane(
                self.options.settings, command=command,
                working_directory=(working_directory
                                   or self.options.working_directory),
                hold_on_exit=hold_on_exit,
                control_socket_path=self._app.control_socket_path,
                on_exited=self._on_pane_exited, title=title)

        def create_viewer_pane(self, path):
            if is_image_path(path):
                return ImagePane(path, on_activated=self._on_viewer_activated)
            return MarkdownPane(path, on_open_path=self.open_path,
                                on_activated=self._on_viewer_activated)

        def _on_viewer_activated(self, pane):
            tab = self.tab_for_pane(pane.pane_id)
            if tab:
                tab.set_active(pane.pane_id)

        def tab_for_pane(self, pane_id):
            for tab in self.tabs:
                if pane_id in tab.panes:
                    return tab
            return None

        def _on_pane_exited(self, pane):
            tab = self.tab_for_pane(pane.pane_id)
            if tab:
                tab.close_pane(pane.pane_id)

        # -- tabs -----------------------------------------------------------

        def add_terminal_tab(self, command=None, working_directory=None,
                             hold_on_exit=False, title=None):
            pane = self.create_terminal_pane(command, working_directory,
                                             hold_on_exit, title)
            self._append_tab(TerminalTab(self, pane))

        def open_path_in_new_tab(self, path):
            self._append_tab(TerminalTab(self, self.create_viewer_pane(path)))

        def _append_tab(self, tab):
            self.tabs.append(tab)
            label = Gtk.Label(label=tab.title or "Terminal")
            page = self.notebook.append_page(tab.widget, label)
            self.notebook.set_tab_reorderable(tab.widget, True)
            self.notebook.set_current_page(page)
            pane = tab.active_pane()
            if pane:
                GLib.idle_add(pane.focus)

        def update_tab_title(self, tab):
            label = self.notebook.get_tab_label(tab.widget)
            if isinstance(label, Gtk.Label):
                label.set_text(tab.title or "Terminal")
            if tab is self.active_tab():
                self.set_title(self.options.title or tab.title or APP_TITLE)

        def active_tab(self):
            page = self.notebook.get_current_page()
            if page < 0:
                return None
            child = self.notebook.get_nth_page(page)
            for tab in self.tabs:
                if tab.widget is child:
                    return tab
            return None

        def remove_tab(self, tab):
            if tab not in self.tabs:
                return
            self.tabs.remove(tab)
            page = self.notebook.page_num(tab.widget)
            if page >= 0:
                self.notebook.remove_page(page)
            tab.dispose()
            if not self.tabs:
                self.close()

        def _on_switch_page(self, notebook, page_widget, page_index):
            for tab in self.tabs:
                if tab.widget is page_widget:
                    self.set_title(self.options.title or tab.title
                                   or APP_TITLE)
                    pane = tab.active_pane()
                    if pane:
                        GLib.idle_add(pane.focus)
                    break

        # -- opening files --------------------------------------------------

        def open_path(self, path):
            if not (is_markdown_path(path) or is_image_path(path)):
                return
            pane = self.create_viewer_pane(path)
            tab = self.active_tab()
            if tab is None:
                self._append_tab(TerminalTab(self, pane))
            else:
                tab.add_pane(pane, HORIZONTAL)

        def open_file_picker(self, extensions=None):
            socket_path = self._app.control_socket_path
            extensions = extensions or ",".join(
                ext.lstrip(".")
                for ext in MARKDOWN_EXTENSIONS + IMAGE_EXTENSIONS)
            package_root = str(Path(__file__).resolve().parent.parent)
            python_path = package_root
            if os.environ.get("PYTHONPATH"):
                python_path += os.pathsep + os.environ["PYTHONPATH"]
            argv = [sys.executable or "python3", "-m",
                    "agent_terminal.tui_navigation", "select-file",
                    "--start", self.options.working_directory or os.getcwd(),
                    "--extensions", extensions]
            if socket_path:
                argv += ["--socket", socket_path]
            picker = Gtk.Window()
            picker.set_transient_for(self)
            picker.set_modal(True)
            picker.set_title("Open File")
            picker.set_default_size(720, 480)
            pane = TerminalPane(
                self.options.settings, command=argv,
                working_directory=self.options.working_directory,
                control_socket_path=socket_path,
                extra_env={"PYTHONPATH": python_path},
                on_exited=lambda _pane: picker.close())
            picker.set_child(pane.widget)
            picker.connect("close-request", self._on_picker_closed)
            self._picker_windows.append(picker)
            picker.present()
            GLib.idle_add(pane.focus)

        def _on_picker_closed(self, picker):
            if picker in self._picker_windows:
                self._picker_windows.remove(picker)
            return False

        def close_picker_windows(self):
            for picker in list(self._picker_windows):
                picker.close()
            self._picker_windows.clear()

        # -- closing --------------------------------------------------------

        def close_active_pane(self):
            tab = self.active_tab()
            if tab is None:
                self.close()
                return
            tab.close_pane()

        def close_active(self):
            tab = self.active_tab()
            if tab is None:
                self.close()
                return
            if tab.pane_count() > 1:
                tab.close_pane()
            else:
                self.remove_tab(tab)

        # -- search and routing ----------------------------------------------

        def _active_pane(self):
            tab = self.active_tab()
            return tab.active_pane() if tab else None

        def _route(self, method):
            pane = self._active_pane()
            if pane is not None:
                getattr(pane, method, lambda: None)()

        def _on_search_changed(self, entry):
            pane = self._active_pane()
            if pane:
                pane.set_search(entry.get_text())

        # -- dialogs ----------------------------------------------------------

        def show_shortcuts(self):
            dialog = Gtk.Window()
            dialog.set_transient_for(self)
            dialog.set_modal(True)
            dialog.set_title("Keyboard Shortcuts")
            dialog.set_default_size(540, 620)
            scroller = Gtk.ScrolledWindow()
            scroller.set_vexpand(True)
            grid = Gtk.Grid()
            grid.set_column_spacing(24)
            grid.set_row_spacing(6)
            grid.set_margin_top(12)
            grid.set_margin_bottom(12)
            grid.set_margin_start(12)
            grid.set_margin_end(12)
            row = 0
            for action in sorted(ACCELERATORS):
                accels = ACCELERATORS[action]
                if not accels:
                    continue
                name_label = Gtk.Label(label=action)
                name_label.set_xalign(0.0)
                accel_label = Gtk.Label(label="  ".join(accels))
                accel_label.set_xalign(0.0)
                grid.attach(name_label, 0, row, 1, 1)
                grid.attach(accel_label, 1, row, 1, 1)
                row += 1
            scroller.set_child(grid)
            close_button = Gtk.Button(label="Close")
            close_button.connect("clicked", lambda *_: dialog.close())
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.append(scroller)
            box.append(close_button)
            dialog.set_child(box)
            dialog.present()

        def show_preferences(self):
            dialog = Gtk.Window()
            dialog.set_transient_for(self)
            dialog.set_modal(True)
            dialog.set_title("Preferences")
            dialog.set_default_size(460, 240)
            label = Gtk.Label()
            label.set_wrap(True)
            label.set_xalign(0.0)
            label.set_margin_top(16)
            label.set_margin_bottom(16)
            label.set_margin_start(16)
            label.set_margin_end(16)
            label.set_text(
                "Settings are configured from the command line "
                "(--font-family, --font-size, --scrollback-lines, "
                "--cursor-style, --no-cursor-blink, --palette) and from "
                f"{CONFIG_PATH} with keys pane_close_policy "
                "(adjacent_expand or same_axis_reflow) and palette.")
            close_button = Gtk.Button(label="Close")
            close_button.connect("clicked", lambda *_: dialog.close())
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.append(label)
            box.append(close_button)
            dialog.set_child(box)
            dialog.present()

        def show_pane_leader(self):
            if self._leader is not None:
                return
            leader = PaneLeaderWindow(self)
            leader.connect("close-request", self._on_leader_closed)
            self._leader = leader
            leader.present()

        def _on_leader_closed(self, leader):
            self._leader = None
            pane = self._active_pane()
            if pane:
                GLib.idle_add(pane.focus)
            return False

        # -- action dispatch --------------------------------------------------

        def _on_action(self, action, parameter, name):
            tab = self.active_tab()
            if name.startswith("tab-"):
                index = int(name.split("-", 1)[1]) - 1
                if 0 <= index < self.notebook.get_n_pages():
                    self.notebook.set_current_page(index)
                return
            if name == "new-tab":
                self.add_terminal_tab()
            elif name == "open-file":
                self.open_file_picker()
            elif name == "open-markdown":
                self.open_file_picker(",".join(
                    ext.lstrip(".") for ext in MARKDOWN_EXTENSIONS))
            elif name == "open-image":
                self.open_file_picker(",".join(
                    ext.lstrip(".") for ext in IMAGE_EXTENSIONS))
            elif name == "close-active":
                self.close_active()
            elif name == "close-pane":
                self.close_active_pane()
            elif name == "next-tab":
                pages = self.notebook.get_n_pages()
                if pages:
                    self.notebook.set_current_page(
                        (self.notebook.get_current_page() + 1) % pages)
            elif name == "previous-tab":
                pages = self.notebook.get_n_pages()
                if pages:
                    self.notebook.set_current_page(
                        (self.notebook.get_current_page() - 1) % pages)
            elif name == "split-horizontal" and tab:
                tab.split(HORIZONTAL)
            elif name == "split-vertical" and tab:
                tab.split(VERTICAL)
            elif name.startswith("focus-") and tab:
                tab.focus_direction(name[len("focus-"):])
            elif name == "fit-focused" and tab:
                tab.toggle_fit_focused()
            elif name == "zoom-pane" and tab:
                tab.toggle_fit_focused(ZOOM_PANE_SHARE)
            elif name == "pane-leader":
                self.show_pane_leader()
            elif name.startswith("move-border-") and tab:
                tab.move_border(name[len("move-border-"):])
            elif name.startswith("grow-") and tab:
                tab.grow(name[len("grow-"):])
            elif name == "increase-width" and tab:
                tab.resize_centered(HORIZONTAL, RESIZE_STEP)
            elif name == "decrease-width" and tab:
                tab.resize_centered(HORIZONTAL, -RESIZE_STEP)
            elif name == "increase-height" and tab:
                tab.resize_centered(VERTICAL, RESIZE_STEP)
            elif name == "decrease-height" and tab:
                tab.resize_centered(VERTICAL, -RESIZE_STEP)
            elif name == "undo-layout" and tab:
                tab.undo()
            elif name == "redo-layout" and tab:
                tab.redo()
            elif name in ("copy", "paste", "reset"):
                self._route(name)
            elif name == "select-all":
                self._route("select_all")
            elif name == "find":
                self.search_bar.set_search_mode(True)
                self.search_entry.grab_focus()
            elif name == "find-next":
                self._route("find_next")
            elif name == "find-previous":
                self._route("find_previous")
            elif name == "reload-pane":
                self._route("reload")
            elif name == "clear-scrollback":
                self._route("clear_scrollback")
            elif name == "zoom-in":
                self._route("zoom_in")
            elif name == "zoom-out":
                self._route("zoom_out")
            elif name == "zoom-reset":
                self._route("zoom_reset")
            elif name == "shortcuts":
                self.show_shortcuts()
            elif name == "preferences":
                self.show_preferences()

    class NativeTerminalApplication(Gtk.Application):
        def __init__(self, options):
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.NON_UNIQUE)
            self.options = options
            self.control_socket_path = (options.control_socket_path
                                        or default_control_socket_path())
            self._socket_server = None

        def do_startup(self):
            Gtk.Application.do_startup(self)
            install_css()
            for name in sorted(APP_LEVEL_ACTIONS):
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", self._on_app_action, name)
                self.add_action(action)
            for name, accels in ACCELERATORS.items():
                prefix = "app." if name in APP_LEVEL_ACTIONS else "win."
                self.set_accels_for_action(prefix + name, list(accels))
            try:
                self._socket_server = ControlSocketServer(
                    self.control_socket_path, self._dispatch_control)
            except OSError:
                self._socket_server = None

        def do_activate(self):
            window = NativeTerminalWindow(self, self.options)
            window.present()

        def do_shutdown(self):
            if self._socket_server is not None:
                self._socket_server.close()
                self._socket_server = None
            Gtk.Application.do_shutdown(self)

        def _on_app_action(self, action, parameter, name):
            if name == "quit":
                self.quit()
            elif name == "new-window":
                window = NativeTerminalWindow(self, self.options)
                window.present()

        def _dispatch_control(self, message):
            # Socket messages arrive on a daemon thread; hop to GTK.
            GLib.idle_add(self._handle_control, message)

        def _handle_control(self, message):
            window = self.get_active_window()
            if not isinstance(window, NativeTerminalWindow):
                window = next((w for w in (self.get_windows() or [])
                               if isinstance(w, NativeTerminalWindow)), None)
            if window is not None:
                window.close_picker_windows()
                action = message.get("action")
                path = message.get("path")
                if action in ("open-file", "open-markdown",
                              "open-image") and path:
                    window.open_path(path)
            return GLib.SOURCE_REMOVE

    _NATIVE_CLASSES = SimpleNamespace(
        rgba=rgba,
        install_css=install_css,
        PaneBase=PaneBase,
        TerminalPane=TerminalPane,
        MarkdownPane=MarkdownPane,
        ImagePane=ImagePane,
        AgentPaneLayoutWidget=AgentPaneLayoutWidget,
        PaneLayoutContainer=PaneLayoutContainer,
        TerminalTab=TerminalTab,
        PaneLeaderWindow=PaneLeaderWindow,
        NativeTerminalWindow=NativeTerminalWindow,
        NativeTerminalApplication=NativeTerminalApplication,
    )
    return _NATIVE_CLASSES


def create_application(options: LaunchOptions):
    g = load_gtk()
    classes = build_native_classes(g)
    return classes.NativeTerminalApplication(options)


def main(argv: list[str] | None = None) -> int:
    try:
        options = parse_args(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    try:
        app = create_application(options)
    except NativeDependencyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return app.run([sys.argv[0] if sys.argv else "agent-terminal-native"])


if __name__ == "__main__":
    raise SystemExit(main())
