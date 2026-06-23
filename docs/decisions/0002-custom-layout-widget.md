# 0002 — Custom pane-layout widget instead of `Gtk.Paned`

- Status: Accepted
- Deciders: project authors
- Related: [architecture.md](../architecture.md) (custom pane container, layout
  engine)

## Context

The app needs tmux-style pane management: n-ary splits, focus by direction,
drag-resizable dividers, grow/shrink, swap/move, balance, undo/redo, and a
temporary "fit the focused pane" view.

GTK's built-in `Gtk.Paned` only does **binary** splits and stores divider
*positions in pixels*. Building n side-by-side panes means nesting `Gtk.Paned`s,
which produces a brittle binary tree, fights you on proportional resize, and makes
operations like "balance every pane" or "flatten same-axis splits" awkward. Pixel
positions also don't survive window resizes cleanly. `Gtk.Fixed` has the same
pixel problem with no help at all.

## Decision

Implement a **custom layout widget**:

- `AgentPaneLayoutWidget` subclasses `Gtk.Widget` and implements `do_measure` and
  `do_size_allocate`, delegating allocation to its owner.
- `PaneLayoutContainer` converts the pure split tree into pixel rectangles on every
  allocation via `layout_pixel_rects()`, places children with `child.allocate(...)`
  and `Gsk.Transform` translations, and manages divider widgets as drag targets.

Layout is stored as an **n-ary tree of weights** (see
[0003](0003-pure-gtk-free-core.md)), and pixels are recomputed each allocation —
never stored.

This rule is protected by guardrail tests in `tests/test_native_terminal.py`,
which assert the source uses `do_size_allocate`, `child.allocate`, and
`Gsk.Transform`, and contains **no** `Gtk.Paned` or `Gtk.Fixed`.

## Consequences

- **Positive:** native support for n-ary splits, proportional resize, balance,
  swap/move, and focused-fit — operations that are pure tree transforms.
- **Positive:** splits track window resizing correctly because pixels are derived
  from weights each allocation, honoring hard minimums (`MIN_PANE_WIDTH`/`HEIGHT`)
  and `PANE_GAP`.
- **Negative / accepted:** more code than reusing `Gtk.Paned`, and we own the
  measure/allocate logic. The guardrail tests exist precisely to keep a future
  change from "simplifying" back to `Gtk.Paned` and silently losing these
  properties.
