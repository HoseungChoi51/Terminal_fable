# 0004 — Share one TUI core; ship smart-ls as a standalone subprocess

- Status: Accepted
- Deciders: project authors
- Related: [smart-ls.md](../smart-ls.md),
  [0003](0003-pure-gtk-free-core.md) (pure, GTK-free core)

## Context

The smart-ls browser (`agent_terminal/smart_ls.py`) needs directory
listing, an entry model, and the control-socket client — all of which
already existed inside the curses file picker
(`agent_terminal/tui_navigation.py`). It also had to work as an
"independent screen" like `vi`/`man`, in any terminal, not only inside
Terminal Fable. Two forks were on the table: duplicate the picker's
logic (or bolt the browser onto the picker module), and build the
browser as a GTK pane inside the app.

## Decision

- Extract the picker's pure layer — `PickerEntry`, listing/scanning,
  and the control-socket client — into `agent_terminal/tui_core.py`
  (GTK-free *and* curses-free). Both the picker and smart-ls consume
  it; the picker re-exports the moved names so its public surface and
  the `select-file` subcommand are unchanged.
- Ship smart-ls as a **standalone curses subprocess** with zero changes
  to `native_terminal.py`. Integration with the app rides entirely on
  what already exists: terminal panes export
  `AGENT_TERMINAL_NATIVE_CONTROL_SOCKET`, and the `open-file` control
  action opens viewer panes.
- Keep the state machines separate: `SmartLsState` composes the shared
  core rather than inheriting `PickerState`. The picker is
  filter-driven (type-to-search); the browser is command-driven
  (letters mirror ls flags). Forcing one state class over both UIs
  couples them for ~15 lines of cursor math.

## Consequences

- smart-ls works over SSH and in foreign terminals, and degrades
  gracefully (xdg-open) when no control socket is present.
- The alternate-screen behavior comes free from curses; a GTK pane
  would have had to fake it and would be unusable outside the app.
- Listing behavior (ordering, hidden filter, stat fallbacks) is tested
  once in the shared core; the picker's untouched test suite doubles as
  the regression proof for the extraction.
- A future GTK front-end for the browser remains possible: it would
  consume the same `tui_core` + pure smart-ls helpers.
