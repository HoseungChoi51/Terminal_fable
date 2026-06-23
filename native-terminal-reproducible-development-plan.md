# Native Terminal Reproducible Development Plan

This document is the rebuild plan for the current native terminal MVP. It is
intended to be detailed enough that a developer can reproduce a similar
terminal from scratch while keeping the same engineering boundaries as this
repository.

The existing `docs/native-terminal-mvp.md` is a usage and comparison guide. This
document is the implementation plan and architecture map.

## Scope

The reproducible target is the native GTK/VTE terminal launched by:

```bash
bin/agent-terminal-native
```

The browser prototype in `agent_terminal/server.py` and `web/` is a broader
workspace experiment with a custom web terminal renderer. Do not use that as the
baseline for rebuilding the native terminal. For the native terminal, terminal
emulation is delegated to VTE.

## Source Of Truth

- `agent_terminal/native_terminal.py`
  - Native app entry point, CLI parser, GTK/VTE loading, layout model, pane
    widgets, tabs, window actions, accelerators, control socket, and main loop.
- `agent_terminal/tui_navigation.py`
  - Curses file picker used by the native app for Markdown and image opening.
- `bin/agent-terminal-native`
  - Launcher that defaults to system Python so PyGObject and VTE system
    bindings are visible.
- `docs/native-terminal-mvp.md`
  - User-facing guide, shortcuts, GNOME Terminal comparison checklist, and
    troubleshooting.
- `tests/test_native_terminal.py`
  - Unit-level contracts for options, layout operations, Markdown helpers,
    image helpers, action names, shortcuts, and implementation guardrails.
- `tests/test_tui_navigation.py`
  - Unit-level contracts for the TUI picker and control socket payloads.

## Current Documentation Status

The current terminal is partially documented:

- Runtime, launcher, options, shortcuts, tutorial, and manual validation are in
  `README.md` and `docs/native-terminal-mvp.md`.
- The keyboard pane-control migration rationale is in
  `docs/GTK_VTE_MIGRATION_PLAN.md`.
- Tests document many behavioral contracts.

Before this file, the project did not have a single reproducible build plan
that explained how to recreate the current native terminal architecture from
scratch.

## Target Architecture

### Runtime

- Language: Python 3.14-compatible source.
- Native UI stack: PyGObject with GTK 4, VTE 3.91, GDK, GdkPixbuf, Pango,
  Graphene, and GSK.
- Launcher default: system `python3`, not `uv`, because Ubuntu installs
  `python3-gi` and the VTE introspection bindings into system Python.
- Optional launcher path: `AGENT_TERMINAL_NATIVE_USE_UV=1` for debugging `uv`.

Install baseline runtime packages on Debian/Ubuntu-like systems:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-vte-3.91 gir1.2-adw-1
```

### Entry Point

Implement the app as a normal Python module:

```text
agent_terminal.native_terminal:main(argv) -> int
```

The `main` function should:

1. Parse CLI flags.
2. Normalize launch options.
3. Load GTK/VTE modules and fail with a concrete package hint if imports fail.
4. Create `NativeTerminalApplication`.
5. Run the GTK application and return its exit status.

### Configuration Objects

Keep launch state immutable and explicit:

```text
TerminalSettings
  font_family
  font_size
  scrollback_lines
  cursor_blink
  cursor_style
  palette

NativeConfig
  pane_close_policy
  palette
  pane_tints

LaunchOptions
  working_directory
  command
  title
  hold_on_exit
  settings
  native_config
  markdown_paths
  image_paths
  control_socket_path
```

Load user config from:

```text
~/.config/agent-terminal/native.json
```

The current config keys are:

```json
{
  "pane_close_policy": "adjacent_expand",
  "palette": "agent-dark",
  "pane_tints": true
}
```

Supported close policies are `adjacent_expand` and `same_axis_reflow`.
`pane_tints` (default `true`) gives each pane a subtle per-pane
background tint blended from the active palette; set it to `false` to
keep every pane on the palette's base background.

### Layout Model

Keep layout logic pure and testable. The current model is an n-ary split tree:

```text
LayoutNode
  kind: "leaf" | "split"
  pane_id
  orientation: "horizontal" | "vertical"
  children
  weights
  split_id
  role
```

Core invariants:

- Same-axis nested splits are flattened into one n-ary group.
- Split weights represent persistent intent, not pixels.
- Pixel rectangles are recomputed from the current allocation.
- Hard minimums are respected when the container is large enough.
- Temporary focused-pane fit is separate from the base root and does not enter
  undo history.
- Persistent structural changes push undo history; focus movement does not.

Required pure functions:

- Create and normalize: `layout_leaf`, `layout_split_group`,
  `layout_split_node`, `layout_split_leaf`.
- Inspect: `layout_leaf_ids`, `layout_pane_count`, `layout_contains`.
- Allocate: `layout_rects`, `layout_pixel_rects`, `layout_split_boundaries`.
- Resize: `layout_update_split_ratio`,
  `layout_update_split_boundary_ratio`, `layout_resize_nearest_split_result`.
- Navigate: `layout_focus_target`.
- Mutate structure: `layout_remove_leaf`, `layout_swap_panes`,
  `layout_move_pane_near`.
- Balance and fit: `layout_balance_local`, `layout_balance_axis`,
  `layout_balance_subtree`, `layout_balance_splits`,
  `layout_balance_weighted`, `layout_balance_tidy`,
  `layout_fit_focused`.

### GTK Allocation

Do not use `Gtk.Paned` for the current behavior. Reproduce the custom allocation
container:

- Subclass `Gtk.Widget`.
- Implement `do_measure` with useful minimum and natural sizes.
- Implement `do_size_allocate` and delegate to a layout owner.
- Use `Gsk.Transform().translate(Graphene.Point)` for child placement.
- Keep visible divider overlays as child widgets with CSS classes.
- Convert the split tree to pixel rectangles on every allocation.

The ownership split is:

```text
AgentPaneLayoutWidget
  GTK custom widget that receives size allocation.

PaneLayoutContainer
  Tracks pane widgets, separator widgets, rectangles, and performs child
  allocation.
```

### Pane Types

All panes must expose a small common behavior surface:

```text
kind
pane_id
widget
title
focus()
copy()
paste()
select_all()
reset()
clear_scrollback()
reload()
set_search(text)
find_next()
find_previous()
zoom_in()
zoom_out()
zoom_reset()
```

#### TerminalPane

Use `Vte.Terminal` inside `Gtk.ScrolledWindow`.

Responsibilities:

- Spawn the default shell or explicit command with `spawn_async`.
- Set working directory and environment.
- Inject `AGENT_TERMINAL_NATIVE_CONTROL_SOCKET` when available.
- Apply scrollback, hyperlink support, mouse autohide, font, cursor style,
  cursor blink, and palette.
- Install URL matching with `Vte.Regex`.
- Forward title changes to the owning tab/window.
- Close the pane on child exit unless `hold_on_exit` is enabled.
- Delegate copy, paste, selection, search, reset, scrollback clear, and font
  zoom to VTE.

#### MarkdownPane

Use a passive GTK viewer, not a web view.

Responsibilities:

- Read UTF-8 Markdown from disk, using replacement on decode errors.
- Parse a practical subset: headings, paragraphs, blockquotes, fenced and
  indented code blocks, horizontal rules, task lists, ordered and unordered
  lists, basic tables, links, inline code, and emphasis.
- Render with GTK labels, boxes, grids, frames, check buttons, and Pango markup.
- Keep widgets non-focusable/selectable where needed to avoid focus crashes.
- Use pointer click to mark the pane active.
- Resolve safe local Markdown links and local image links into new native panes.
- Use external URI opening only for safe schemes.
- Implement find by re-rendering with highlighted text and scrolling to matched
  widgets.

#### ImagePane

Use native GTK image primitives.

Responsibilities:

- Load PNG, JPEG, GIF, and WebP through `GdkPixbuf.Pixbuf.new_from_file`.
- Render through `Gtk.Picture`.
- Open at actual size by default.
- Support fit-to-pane, actual-size reset, zoom in/out/reset, Ctrl+wheel zoom,
  and drag-to-pan.
- Clamp zoom scale.
- Copy the image path as text.
- Treat search and paste as no-ops.

### Tabs And Window

Use one `Gtk.Notebook` per window. Each tab owns:

```text
TerminalTab
  PaneLayoutContainer
  panes: dict[pane_id, pane]
  root: LayoutNode
  active_pane_id
  fit_focused_root
  undo/redo stacks
```

The window owns:

```text
NativeTerminalWindow
  Gtk.ApplicationWindow
  Gtk.HeaderBar
  Gtk.SearchBar
  Gtk.Notebook
  window actions
  transient picker windows
  pane leader dialog
```

The app owns:

```text
NativeTerminalApplication
  Gtk.Application
  app actions
  accelerator map
  windows
  Unix control socket
```

### Actions And Shortcuts

Expose commands as GTK actions first, then map accelerators separately. The
current action surface includes:

```text
new-window, new-tab, open-file, open-markdown, open-image,
close-active, close-pane, next-tab, previous-tab,
split-horizontal, split-vertical,
focus-left, focus-right, focus-up, focus-down,
fit-focused, zoom-pane, pane-leader,
move-border-left, move-border-right, move-border-up, move-border-down,
grow-left, grow-right, grow-up, grow-down,
increase-width, decrease-width, increase-height, decrease-height,
undo-layout, redo-layout,
copy, paste, select-all, find, find-next, find-previous,
reset, reload-pane, clear-scrollback,
zoom-in, zoom-out, zoom-reset,
shortcuts, preferences, quit
```

Default shortcuts should preserve ordinary terminal input. Use `Alt+Shift`
for pane management and `Ctrl+Shift` for common terminal-window commands:

```text
Ctrl+Shift+T      new tab
Ctrl+Shift+O      open file picker
Ctrl+Shift+W      close pane or tab
Alt+Shift+H/V     split left-right / top-bottom
Alt+Shift+Arrows  focus panes
Alt+Shift+F       temporary focus fit
Alt+Shift+Space   pane control mode
Ctrl+Shift+C/V    copy/paste
Ctrl+Shift+F      find
F5                reload viewer
F / 1             image fit / actual size
```

### Pane Control Mode

Implement pane control mode as a transient modal GTK window. It exists so the
app can expose more pane commands without stealing normal terminal keystrokes.

Modes:

- Command mode: focus, split, close, fit, undo/redo, balance menu, resize mode,
  grow mode, move mode.
- Resize mode: arrow keys move nearest divider.
- Grow mode: arrow keys grow from a side; shifted arrows resize around center.
- Move mode: arrows move active pane near a neighbor; shifted arrows swap.
- Balance mode: local, axis, subtree, whole tab, weighted, spotlight, tidy.

### File Picker And Control Socket

Use a transient VTE-backed picker window rather than typing commands into the
user's active shell.

The flow is:

1. Window action creates a `TerminalPane` in a transient `Gtk.Window`.
2. The pane runs:

   ```text
   python -m agent_terminal.tui_navigation select-file ...
   ```

3. The curses picker sends a single JSON message over the native Unix socket:

   ```json
   {"action":"open-file","path":"/absolute/path"}
   ```

4. The app accepts the socket message on a daemon thread.
5. The GTK main loop opens the selected Markdown or image as a pane in the most
   recent native window.

Socket paths should be process-local:

```text
$XDG_RUNTIME_DIR/agent-terminal-native-<pid>.sock
```

## Rebuild Phases

### Phase 1: Minimal Native Shell

Deliver:

- Python package/module with `main(argv)`.
- CLI parser with `--working-directory`, `--command`, `--title`,
  `--hold-on-exit`, `--version`.
- GTK module loader with concrete dependency error.
- `Gtk.Application`, `Gtk.ApplicationWindow`, `Gtk.HeaderBar`.
- One `Vte.Terminal` spawning the default shell or explicit command.

Acceptance:

- `bin/agent-terminal-native` opens an interactive shell.
- Typing, Enter, Backspace, arrows, Ctrl+C, Ctrl+D, and paste work.
- `--command "bash -lc 'echo ok; exec bash'"` starts correctly.
- Child exit closes the window by default.
- `--hold-on-exit` keeps the window open after command exit.

Tests:

- Parser creates `LaunchOptions`.
- `command_argv` chooses explicit command or default shell.
- Dependency hint names required system packages.

### Phase 2: Terminal Settings

Deliver:

- `TerminalSettings`.
- Font family and font size.
- Scrollback line count.
- Cursor style and blink.
- Palette presets with foreground, background, and 16-color palette.
- URL matching and hyperlink cursor.

Acceptance:

- `--font-family`, `--font-size`, `--scrollback-lines`,
  `--cursor-style`, `--no-cursor-blink`, and `--palette` work.
- Font zoom changes only the active terminal pane.
- True-color terminal output is handled by VTE.

Tests:

- Font size clamping.
- Palette normalization.
- URL regex flags include multiline behavior required by VTE.

### Phase 3: Tabs And Window Actions

Deliver:

- `NativeTerminalWindow`.
- `Gtk.Notebook` tabs.
- New tab, close, next/previous tab, numbered tab actions.
- Header menu with copy, paste, select all, reset, clear scrollback, reload,
  shortcuts, preferences, and quit.
- Search bar routed to the active pane.

Acceptance:

- Multiple terminal tabs work.
- Active tab title follows active terminal title.
- Search, find next, and find previous work in VTE scrollback.
- Copy/paste and reset actions route to the active pane.

Tests:

- `ACTION_NAMES` contains the public actions.
- Shortcut contract avoids stealing plain `Ctrl+H` and GNOME workspace
  defaults.

### Phase 4: Pure Layout Engine

Deliver:

- `LayoutNode`, `PaneRect`, `SplitBoundary`, `LayoutSnapshot`.
- N-ary split creation and normalization.
- Leaf removal with both close policies.
- Rectangle computation in relative coordinates and pixels.
- Split boundary hit testing and drag ratio calculation.
- Geometric focus target selection.
- Pure resize, grow, move, swap, balance, tidy, and focused fit helpers.

Acceptance:

- Repeated same-axis splits flatten into one group.
- Closing a pane preserves unrelated weights.
- Focus movement picks the nearest geometric neighbor.
- Pixel allocation preserves hard minimums when possible.
- Temporary focused fit enlarges eligible panes without mutating the base root.

Tests:

- Split, remove, focus, resize, balance, swap, move, boundary hit, focus fit,
  and pixel minimum contracts.

### Phase 5: Custom GTK Pane Container

Deliver:

- `AgentPaneLayoutWidget` subclass.
- `PaneLayoutContainer`.
- Child placement from `layout_pixel_rects`.
- Divider overlays from `layout_split_boundaries`.
- Mouse drag resize through boundary hit testing.

Acceptance:

- Splits track window resize.
- Pane widgets do not keep stale size requests.
- Divider overlays are visible.
- Dragged sizes survive re-rendering.

Tests:

- Source guardrails: no `Gtk.Paned`, no `Gtk.Fixed`.
- Custom widget implements `do_size_allocate`.
- `child.allocate(...)` is used for placement.

### Phase 6: Split-Pane Commands

Deliver:

- `TerminalTab` with `panes`, `root`, `active_pane_id`, undo stack, redo stack.
- Split active pane horizontally and vertically.
- Close active pane.
- Focus panes by direction.
- Temporary focused fit.
- Resize nearest split.
- Grow, centered resize, move, swap.
- Local, axis, subtree, whole-tab, weighted, spotlight, and tidy balance.
- Undo and redo for persistent layout changes.

Acceptance:

- All pane operations work by keyboard.
- Focus movement clears temporary fit after resolving the next pane.
- Undo/redo restores pane widgets and active pane safely.
- Closing the final pane closes the tab or window as appropriate.

Tests:

- Layout history and command guardrails in `tests/test_native_terminal.py`.
- Manual multi-pane checklist in `docs/native-terminal-mvp.md`.

### Phase 7: Markdown Viewer

Deliver:

- Markdown parser helpers and block model.
- Passive `MarkdownPane`.
- Rendered headings, paragraphs, lists, task lists, blockquotes, code, tables,
  rules, links, and inline code/emphasis.
- Safe local link resolution.
- Search and zoom.
- Open linked local Markdown and image files as panes.

Acceptance:

- `--markdown README.md` opens a rendered Markdown tab.
- `Ctrl+Shift+O` can open Markdown as a pane.
- Markdown panes can be focused, moved, resized, searched, reloaded, and closed.
- Local links open within the native app when they target supported files.

Tests:

- Markdown parser block coverage.
- Heading/list layout helpers.
- Safe link resolution.
- Inline markup escaping.
- Passive focus guardrails.

### Phase 8: Image Viewer

Deliver:

- `ImagePane` with GdkPixbuf loading and `Gtk.Picture` rendering.
- Actual size default.
- Fit mode, zoom, reset, Ctrl+wheel zoom around pointer, drag pan.
- Image status line.
- Copy path.
- Open supported image paths from Markdown links and picker.

Acceptance:

- `--image screenshot.png` opens an image tab.
- PNG, JPEG, GIF, and WebP load through the native stack.
- Image panes can be focused, moved, resized, zoomed, reloaded, and closed.

Tests:

- Image extension recognition.
- Fit scale bounds.
- Source guardrails for native picture surface and controls.

### Phase 9: TUI Picker And Socket Handoff

Deliver:

- Curses file picker with directory ordering, extension filter, query filter,
  hidden-file toggle, parent traversal, paging, and cancel.
- JSON-line Unix socket protocol.
- Transient app-owned picker window backed by VTE.
- Deferred GTK handoff with `GLib.timeout_add` or `idle_add`.

Acceptance:

- `Ctrl+Shift+O` opens the picker without typing into the active shell.
- Selecting Markdown opens a Markdown pane.
- Selecting an image opens an image pane.
- Cancel closes the picker.

Tests:

- Extension parsing.
- Directory listing order.
- Query filtering.
- Control message shape.
- Socket send protocol.
- Native source guardrails for transient picker windows.

### Phase 10: Packaging And Runtime Polish

Deliver:

- User-local launcher script.
- Desktop entry and packaging metadata.
- Runtime documentation.
- Shortcut guide dialog.
- Preferences placeholder that points to supported CLI/config settings.
- Troubleshooting section for missing GTK/VTE bindings and headless sessions.

Acceptance:

- `packaging/install.sh` installs a working user-local launcher.
- `bin/agent-terminal-native --version` reports the native MVP version.
- Shortcut guide is reachable by `Ctrl+Shift+H`, `F1`, and `Ctrl+?`.

Tests:

- Packaging tests stay green.
- Shortcut guide has explicit dismiss controls.

## Verification Commands

Unit tests that do not require a GUI session:

```bash
python3 -m unittest tests.test_native_terminal tests.test_tui_navigation
```

Broader test suite:

```bash
python3 -m unittest
```

Dependency smoke:

```bash
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Vte','3.91'); from gi.repository import Gtk, Vte; print('GTK/VTE ok')"
```

Manual native run:

```bash
bin/agent-terminal-native
bin/agent-terminal-native --command "bash -lc 'echo ready; exec bash'"
bin/agent-terminal-native --markdown README.md
bin/agent-terminal-native --image path/to/image.png
```

Use the GNOME Terminal comparison checklist in `docs/native-terminal-mvp.md`
before adding any richer agent features.

## Non-Goals For A Rebuild

- Do not implement a custom terminal emulator for the native MVP.
- Do not port browser-only terminal rendering, Kitty graphics parsing, Sixel
  parsing, process dashboards, agent profiles, context memory, or browser panes
  into the native app until the native baseline is stable.
- Do not make pane controls depend on always-visible control panels.
- Do not bind pane commands to plain keys that terminal applications need.
- Do not store layout as pixels.

## Development Rule

Each phase should leave the app runnable. Prefer this order:

1. Stable shell behavior.
2. Stable settings and lifecycle.
3. Stable tabs.
4. Pure layout tests.
5. Native pane allocation.
6. Keyboard pane commands.
7. Viewers.
8. TUI file handoff.
9. Packaging and documentation.

If ordinary shell work diverges from GNOME Terminal, stop feature work and fix
that compatibility gap first.
