# Developer Guide

How to set up, run, test, and contribute to the native GTK4/VTE terminal. Read
[`architecture.md`](architecture.md) first for the mental model; this guide is the
day-to-day workflow.

## Prerequisites

The app uses the distribution's PyGObject and VTE introspection bindings, so it
runs on **system Python**, not a virtualenv. On Debian/Ubuntu-like systems:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-vte-3.91 gir1.2-adw-1
```

The launcher (`bin/agent-terminal-native`) defaults to system `python3` for this
reason. Set `AGENT_TERMINAL_NATIVE_USE_UV=1` only when you specifically want to
debug under `uv`.

### Smoke-test the bindings

```bash
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Vte','3.91'); from gi.repository import Gtk, Vte; print('GTK/VTE ok')"
```

If this fails, the app's own `load_gtk()` will raise a `NativeDependencyError`
naming the exact packages — that error message is the canonical fix list.

## Running

```bash
bin/agent-terminal-native                                   # default shell
bin/agent-terminal-native --command "bash -lc 'echo ready; exec bash'"
bin/agent-terminal-native --markdown README.md              # open a Markdown tab
bin/agent-terminal-native --image path/to/image.png         # open an image tab
bin/agent-terminal-native --version
```

Other useful flags: `--working-directory`, `--title`, `--hold-on-exit`,
`--font-family`, `--font-size`, `--scrollback-lines`, `--cursor-style`,
`--no-cursor-blink`, `--palette`, `--config`. See `build_arg_parser()` /
`parse_args()` in `agent_terminal/native_terminal.py` for the authoritative list.

Persistent settings live in `~/.config/agent-terminal/native.json`:

```json
{
  "pane_close_policy": "adjacent_expand",
  "palette": "agent-dark",
  "pane_tints": true
}
```

CLI flags override the config file.

## Testing

The whole point of the pure-core/GTK-shell split is that the interesting logic is
testable **without a display**. The unit suites import only the pure module and
never start GTK:

```bash
python3 -m unittest tests.test_native_terminal tests.test_tui_navigation
# or the whole suite:
python3 -m unittest
```

`tests/test_native_terminal.py` covers options parsing, the layout engine (split,
remove, focus, resize, balance, swap, move, boundary hit-test, focused-fit, pixel
minimums), Markdown/image helpers, action names, and shortcut contracts. It also
contains **source guardrails** that assert architectural rules (see below).
`tests/test_tui_navigation.py` covers the curses picker and the control-socket
payload shape.

Run the unit tests before every commit. They are fast and need no GUI.

### Manual / GUI checks

GUI behavior can't be unit-tested, so for changes that touch widgets, run the app
and walk the GNOME-Terminal comparison checklist in
[`native-terminal-mvp.md`](native-terminal-mvp.md). A headless session (no
`DISPLAY`/Wayland) cannot open a window — run GUI checks from a real desktop
session.

## Conventions and invariants

These are enforced by review and, where possible, by the guardrail tests. Breaking
them is how regressions get in.

1. **The pure core never imports GTK.** Configuration, layout, Markdown, image
   math, the action surface, and the control protocol stay GTK-free and
   unit-tested. GTK lives only inside `build_native_classes()` and below.
2. **Store layout as weights, never pixels.** Pixels are derived on every
   allocation. (Guardrail-adjacent: the tests check the custom container, and the
   rebuild plan calls this out explicitly.)
3. **Use the custom pane container, not `Gtk.Paned`/`Gtk.Fixed`.** The tests
   assert the source uses `do_size_allocate`, `child.allocate`, and `Gsk.Transform`
   and contains no `Gtk.Paned`/`Gtk.Fixed`.
4. **Don't steal plain terminal keys.** Pane management is `Alt+Shift`,
   terminal-window commands are `Ctrl+Shift`; `RESERVED_PLAIN_ACCELERATORS`
   documents the plain control keys that must reach the shell.
5. **Declare commands as actions first, bind accelerators second.** Add to
   `ACTION_NAMES` + `ACCELERATORS`; don't hard-wire key handlers.
6. **Keep panes polymorphic.** Any new pane kind implements the full `PaneBase`
   behavior surface so the action dispatcher's `_route()` keeps working.
7. **Markdown panes stay passive.** Non-focusable/non-selectable where required,
   to avoid focus-chain crashes (there is a guardrail test for this).
8. **If shell behavior diverges from GNOME Terminal, fix that first** before
   adding richer features (a stated project rule).

## Commit and release workflow

The project accumulates fixes on a branch and folds them into a release. The
observed workflow:

- **One concern per branch and per commit.** Branch names describe the change
  (e.g. `fix/clickable-url-open-browser`); commits are small and focused
  (e.g. *"Open clicked URLs in the default browser"*, *"Add per-pane background
  tints"*).
- **Keep unrelated work out of a commit.** When a file has unrelated in-flight
  edits, stage selectively (`git add -p`) so each commit stays scoped.
- **Update the docs in the same change.** User-facing changes update `README.md`
  and/or `docs/`; architectural changes update `docs/architecture.md` and, when a
  significant choice is made, add an ADR under `docs/decisions/`.
- **Tests stay green.** Run `python3 -m unittest` before committing; add or update
  a pure-core test for any logic change.

## Debugging tips

- **Missing bindings / blank failure on launch:** read the `NativeDependencyError`
  text — it lists the apt packages. Re-run the smoke test above.
- **Headless environment:** if there's no `DISPLAY`/`WAYLAND_DISPLAY`, the GUI
  can't start; this is expected. Unit tests still run.
- **Layout looks wrong:** reproduce it as a pure-core test against the `layout_*`
  functions first — most layout bugs are visible without a window.
- **A pane command does nothing:** confirm the action is in `ACTION_NAMES`, bound
  in `ACCELERATORS`, and handled in `_on_action`/`_route`; confirm the pane kind
  implements the routed method.
- **`uv` debugging:** `AGENT_TERMINAL_NATIVE_USE_UV=1 bin/agent-terminal-native`.

## Where to go next

- Add something new: [`extending.md`](extending.md).
- Why the architecture is shaped this way: [`decisions/`](decisions/README.md).
