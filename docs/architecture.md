# Architecture

This document explains how the native GTK4/VTE terminal in `agent_terminal/native_terminal.py`
is put together: the layers, the boundary that keeps most of the code testable
without a display, the layout engine, the pane model, and the invariants the rest
of the codebase relies on.

It is a companion to
[`native-terminal-reproducible-development-plan.md`](../native-terminal-reproducible-development-plan.md),
which is the *rebuild* plan (how to recreate the app from scratch). This document
describes the app *as it is* and is the entry point for anyone changing it.

> Scope: only the native terminal launched by `bin/agent-terminal-native` is
> covered here. There is no browser/server component in this repository.

## The one big idea: a pure core under a thin GTK shell

The single most important property of this codebase is a hard split between:

- **Pure core** — plain Python with **no GTK/VTE imports**: configuration, the
  layout engine, the Markdown parser, image math, the action surface, and the
  control-socket protocol. All of it is unit-tested headlessly.
- **GTK shell** — the widgets that turn that core into a running app: VTE
  terminals, the custom pane container, tabs, the window, the application, and
  the modal pane-control window.

In the source, the pure core is everything *above* `build_native_classes()`
(roughly lines 54–1321). The GTK shell is built *inside* `build_native_classes()`
and the classes it defines (roughly lines 1326–3052). GTK is imported lazily by
`load_gtk()` and only when an actual GUI is created, so importing the module —
which is what the test suite does — never needs a display or PyGObject.

```
                       ┌─────────────────────────────────────────┐
   bin/agent-terminal- │  main() → create_application()           │
   native  ──────────► │     load_gtk()  ── lazy, GUI-only        │
                       │     build_native_classes()               │
                       └───────────────┬─────────────────────────┘
                                       │ builds, once per process
   ┌───────────────────────────────────┴──────────────────────────┐
   │ GTK SHELL  (imports Gtk/Vte/Gdk/GdkPixbuf/Pango/Gsk)          │
   │   NativeTerminalApplication                                   │
   │     NativeTerminalWindow  (HeaderBar, SearchBar, Notebook)    │
   │       TerminalTab         (one per notebook page)             │
   │         PaneLayoutContainer / AgentPaneLayoutWidget           │
   │           PaneBase: TerminalPane | MarkdownPane | ImagePane   │
   │       PaneLeaderWindow    (modal pane-control modes)          │
   │     ControlSocketServer   (daemon thread, Unix socket)        │
   └───────────────────────────────────┬──────────────────────────┘
                                        │ calls into
   ┌────────────────────────────────────┴─────────────────────────┐
   │ PURE CORE  (no GTK imports — unit-tested in tests/)           │
   │   Config:   TerminalSettings, NativeConfig, LaunchOptions,    │
   │             PALETTES, parse_args, load_native_config          │
   │   Layout:   LayoutNode + layout_* functions (the engine)      │
   │   Markdown: parse_markdown_blocks, markdown_inline_to_pango   │
   │   Image:    fit_image_scale, clamp_image_scale                │
   │   Actions:  ACTION_NAMES, ACCELERATORS                        │
   │   Control:  encode/parse_control_message                      │
   └──────────────────────────────────────────────────────────────┘
```

Why this matters: almost every behavior worth testing (splitting, closing,
focusing, resizing, balancing, Markdown parsing, config parsing, the control
protocol) lives in the pure core and is verified by `tests/test_native_terminal.py`
without launching a window. See
[ADR 0003: Keep layout and app logic in a pure, GTK-free core](decisions/0003-pure-gtk-free-core.md).

## Module layout

Everything ships in one module, `agent_terminal/native_terminal.py`, organized
top-to-bottom from pure core to GTK shell. The approximate map:

| Region | Lines | Responsibility |
| --- | --- | --- |
| Constants, palettes, color math | 54–172 | `PALETTES`, hex/RGB blending, `pane_tint_background` |
| Config & CLI | 175–295 | `TerminalSettings`, `NativeConfig`, `LaunchOptions`, `load_native_config`, `parse_args` |
| Layout engine | 299–889 | `LayoutNode` + all `layout_*` functions (pure tree ops, pixel math, navigation, balancing) |
| Markdown helpers | 892–1132 | `MarkdownBlock`, `parse_markdown_blocks`, `markdown_inline_to_pango`, link safety |
| Image helpers | 1138–1159 | `clamp_image_scale`, `fit_image_scale` |
| Action surface | 1162–1227 | `ACTION_NAMES`, `TAB_ACTION_NAMES`, `APP_LEVEL_ACTIONS`, `ACCELERATORS` |
| Control protocol & server | 1230–1321 | `encode_control_message`, `parse_control_message`, `ControlSocketServer` |
| GTK class factory | 1326–3052 | `load_gtk`, `build_native_classes`, all widget classes |
| Entry point | 3056–3078 | `create_application`, `main` |

`tui_navigation.py` is a separate, self-contained curses file picker plus the
client side of the control protocol (`send_control_message`). It is run as a
child process, not imported by the GTK shell.

## The layout engine (pure)

Pane geometry is modeled as an **immutable n-ary split tree** and all operations
on it are pure functions that return a *new* tree. This is the heart of the app.

### Data model

- `LayoutNode` (frozen dataclass) — either a `leaf` (carries a `pane_id`) or a
  `split` (carries `orientation`, a tuple of `children`, and a tuple of
  normalized `weights` summing to 1.0, plus a `split_id`).
- `PaneRect` / `PixelRect` — relative `[0,1]` and absolute pixel rectangles.
- `SplitBoundary` — a divider's pixel rectangle, used for hit-testing and drag.
- `LayoutSnapshot` — `(root, active_pane_id)`, the unit of undo/redo.

### Invariants

These hold everywhere and code depends on them:

1. **Immutable** — mutating helpers return a new tree; old trees stay valid (this
   is what makes undo/redo a simple stack of `LayoutSnapshot`s).
2. **Normalized** — consecutive same-axis splits are flattened into one n-ary
   group by `layout_split_group()`; you never get a right-leaning chain of binary
   splits for three side-by-side panes.
3. **Weights are intent, not pixels** — weights sum to 1.0 and respect
   `MIN_SPLIT_WEIGHT` (0.05). Pixels are recomputed from weights on every
   allocation by `layout_pixel_rects()`, honoring `MIN_PANE_WIDTH`/`MIN_PANE_HEIGHT`
   and `PANE_GAP` when the container is large enough.
4. **Focused-fit is a view, not a mutation** — `layout_fit_focused()` returns an
   ephemeral enlarged tree for "zoom this pane" display; it never touches the base
   `root` and never enters undo history.
5. **Structure changes undo; focus changes don't** — splitting/closing/moving push
   a snapshot; moving focus does not.

### Operation families

- **Create/normalize:** `layout_leaf`, `layout_split_group`, `layout_split_node`,
  `layout_split_leaf`.
- **Inspect:** `layout_leaf_ids`, `layout_pane_count`, `layout_contains`.
- **Allocate:** `layout_rects`, `layout_pixel_rects`, `layout_split_boundaries`,
  `layout_boundary_at` (pointer hit-test).
- **Resize:** `layout_update_split_ratio`, `layout_update_split_boundary_ratio`
  (drag), `layout_resize_nearest_split_result`, `layout_grow`, `layout_resize_centered`.
- **Navigate:** `layout_focus_target` (nearest pane in a direction).
- **Restructure:** `layout_remove_leaf` (with `adjacent_expand` or
  `same_axis_reflow` policy), `layout_swap_panes`, `layout_move_pane_near`.
- **Balance/fit:** `layout_balance_local`, `layout_balance_axis`,
  `layout_balance_subtree`, `layout_balance_splits`, `layout_balance_weighted`,
  `layout_balance_tidy`, `layout_fit_focused`.

## The GTK shell

### Custom pane container (not `Gtk.Paned`)

Panes are placed by a **custom widget**, not `Gtk.Paned` or `Gtk.Fixed`:

- `AgentPaneLayoutWidget` subclasses `Gtk.Widget` and implements `do_measure`
  (minimum/natural size) and `do_size_allocate` (delegates to its owner).
- `PaneLayoutContainer` owns the pane widgets and the divider widgets. On every
  allocation it calls `layout_pixel_rects()` to convert the current split tree
  into pixel rectangles, then positions each child with `child.allocate(...)` and
  a `Gsk.Transform` translation. Dividers are real child widgets (CSS-styled
  boxes) that are also drag targets; a drag maps the pointer position through
  `layout_update_split_boundary_ratio()`.

This is a deliberate choice — see
[ADR 0002: Custom pane-layout widget instead of `Gtk.Paned`](decisions/0002-custom-layout-widget.md).
The test suite *guards* it: `tests/test_native_terminal.py` asserts the source
contains no `Gtk.Paned`/`Gtk.Fixed` and does use `do_size_allocate`,
`child.allocate`, and `Gsk.Transform`.

### Panes are polymorphic

Every pane exposes the same small behavior surface (`focus`, `copy`, `paste`,
`select_all`, `reset`, `clear_scrollback`, `reload`, `set_search`, `find_next`,
`find_previous`, `zoom_in/out/reset`, plus `kind`, `pane_id`, `widget`, `title`).
Three kinds implement it:

- **`TerminalPane`** — a `Vte.Terminal` in a `Gtk.ScrolledWindow`. Terminal
  emulation is delegated entirely to VTE (spawn, scrollback, true color, copy,
  paste, search, URL matching). See
  [ADR 0001: Delegate terminal emulation to VTE](decisions/0001-delegate-terminal-emulation-to-vte.md).
- **`MarkdownPane`** — a *passive* viewer built from GTK labels/boxes/grids and
  Pango markup produced by the pure Markdown helpers. Widgets are kept
  non-focusable/non-selectable where needed to avoid focus-chain crashes; links
  open safe local files as new panes or safe external URIs in the browser.
- **`ImagePane`** — `GdkPixbuf` + `Gtk.Picture`, with fit/zoom/pan and
  copy-path; search and paste are no-ops.

Because the surface is uniform, the window's action dispatcher routes commands to
"the active pane" via a single `_route()` helper without caring which kind it is.

### Tabs, window, application

- `TerminalTab` — one per `Gtk.Notebook` page. Owns a `PaneLayoutContainer`, the
  `panes` dict (`pane_id → pane`), the `root` `LayoutNode`, `active_pane_id`,
  the ephemeral `fit_focused_root`, and the undo/redo stacks.
- `NativeTerminalWindow` — `Gtk.ApplicationWindow` with `HeaderBar`, `SearchBar`,
  and the `Notebook`; installs window-level actions; owns transient picker
  windows and the pane-leader dialog.
- `NativeTerminalApplication` — `Gtk.Application`; installs app-level actions
  (`new-window`, `quit`) and the full accelerator map; owns windows and the
  optional control socket.

### Actions and shortcuts

Commands are declared as data first and bound second:

- `ACTION_NAMES` lists the ~45 window actions; `APP_LEVEL_ACTIONS` is the small
  set installed on the application instead of the window; `TAB_ACTION_NAMES`
  covers `tab-1..tab-9`.
- `ACCELERATORS` maps each action to a tuple of GTK accelerator strings (an
  action may have several). `RESERVED_PLAIN_ACCELERATORS` documents the plain
  control keys that must stay with the terminal and never be stolen.
- At startup the application installs `Gio.SimpleAction`s and calls
  `set_accels_for_action("win."/"app." + name, …)`. Dispatch lands in
  `_on_action(...)`, which calls tab methods (split/focus/resize/balance/undo)
  or routes pane methods via `_route()`.

Convention: **`Alt+Shift` = pane management**, **`Ctrl+Shift` = terminal-window
commands**, and plain terminal keys are left untouched.

### Pane-leader (modal pane control)

`PaneLeaderWindow` is a transient, undecorated modal window (opened with
`Alt+Shift+Space`) that exposes the larger pane-management surface without
binding dozens of global shortcuts that would collide with terminal programs. It
has sub-modes — command, resize, grow, move, balance — each interpreting arrow
keys differently. Escape steps back out.

## Control socket and the file picker

Opening a Markdown/image file uses a **transient picker process plus a Unix
socket**, not typed commands into the user's shell:

1. A window action spawns a `TerminalPane` in a transient `Gtk.Window` running
   `python -m agent_terminal.tui_navigation select-file …`.
2. The curses picker writes one JSON line —
   `{"action": "open-file", "path": "/abs/path"}` — to the per-process socket at
   `$XDG_RUNTIME_DIR/agent-terminal-native-<pid>.sock` (path also passed via the
   `AGENT_TERMINAL_NATIVE_CONTROL_SOCKET` env var).
3. `ControlSocketServer` (a daemon thread) accepts the connection, parses and
   validates the message with `parse_control_message`, and hops the result onto
   the GTK main loop with `GLib.idle_add`.
4. The app opens the selected file as a Markdown or image pane in the current
   window.

The socket is optional: if the bind fails the app still runs, just without IPC
file handoff.

## Theming

- **Palettes** (`PALETTES`): `agent-dark`, `agent-light`, `solarized-dark`. Each
  is a foreground, a background, and 16 ANSI colors. Applied to VTE via
  `set_colors(...)`.
- **Per-pane tints**: each new terminal pane gets a subtle background blended
  from an accent palette color (`PANE_TINT_SLOTS`, blended by `PANE_TINT_AMOUNT`
  = 0.08 in `pane_tint_background()`), so adjacent panes are visually
  distinguishable. The first/main pane stays on the base background. Controlled
  by the `pane_tints` config key (default on); the active tint can be cycled
  live.
- **CSS** (`APP_CSS`): compact header bar, tab styling, divider color, and the
  Markdown/image view styles; installed once for the display.

## Configuration

Two layers, kept immutable and explicit:

- `TerminalSettings` — per-launch (CLI flags): font family/size, scrollback,
  cursor style/blink, palette.
- `NativeConfig` — persistent, loaded from `~/.config/agent-terminal/native.json`
  by `load_native_config()`: `pane_close_policy` (`adjacent_expand` |
  `same_axis_reflow`), `palette`, `pane_tints`. Unknown/invalid values fall back
  to defaults; a missing or malformed file is tolerated.

CLI flags override the config file (e.g. `--palette` wins over the file's
`palette`). `parse_args()` produces a single immutable `LaunchOptions`.

## Application lifecycle

1. `bin/agent-terminal-native` puts the repo on `PYTHONPATH` and runs
   `python3 -m agent_terminal.native_terminal` (system Python, so the distro's
   PyGObject/VTE bindings are visible; `AGENT_TERMINAL_NATIVE_USE_UV=1` switches
   to `uv` for debugging).
2. `main(argv)` → `parse_args` → `create_application` → `load_gtk()` (version-checks
   GTK 4.0 / VTE 3.91 / GdkPixbuf, raising a `NativeDependencyError` with the exact
   apt packages on failure) → `build_native_classes()` → `app.run(...)`.
3. `do_startup`: install CSS, app actions + accelerators, try to bind the control
   socket.
4. `do_activate`: create and present a `NativeTerminalWindow`; open the initial
   terminal tab (and any `--markdown`/`--image` tabs).
5. `do_shutdown`: close the socket server.

## Where to go next

- Set up and run the app, run tests, and follow the commit/release workflow:
  [`developer-guide.md`](developer-guide.md).
- Add a feature (action, pane type, palette, config key, socket command, layout
  op): [`extending.md`](extending.md).
- Understand *why* the big choices were made:
  [`decisions/`](decisions/README.md).
