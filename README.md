# Terminal Fable

A native Linux terminal with cmux-style split panes, built on GTK 4 and
VTE. One window holds tabs; each tab holds an n-ary split tree of panes.
Panes can be interactive terminals, rendered Markdown viewers, or native
image viewers, and everything is drivable from the keyboard.

This repository implements
[`native-terminal-reproducible-development-plan.md`](native-terminal-reproducible-development-plan.md).
The user-facing guide lives in [`docs/native-terminal-mvp.md`](docs/native-terminal-mvp.md).

## Requirements

- Linux with a graphical session (X11 or Wayland)
- Python 3 with the system GTK 4 / VTE introspection bindings:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-vte-3.91 gir1.2-adw-1
```

## Run

```bash
bin/agent-terminal-native
bin/agent-terminal-native --command "bash -lc 'echo ready; exec bash'"
bin/agent-terminal-native --markdown README.md
bin/agent-terminal-native --image path/to/image.png
```

The launcher uses system `python3` by default so the system PyGObject
bindings are visible. Set `AGENT_TERMINAL_NATIVE_USE_UV=1` to debug
under `uv` instead.

### Options

| Flag | Effect |
| ---- | ------ |
| `--working-directory DIR` | Starting directory for new terminals |
| `--command CMD` | Run a command instead of the default shell |
| `--title TITLE` | Fixed window title |
| `--hold-on-exit` | Keep the pane open after the child exits |
| `--font-family NAME`, `--font-size PT` | Terminal font |
| `--scrollback-lines N` | Scrollback depth (default 10000) |
| `--cursor-style block\|ibeam\|underline`, `--no-cursor-blink` | Cursor |
| `--palette agent-dark\|agent-light\|solarized-dark` | Color preset |
| `--markdown PATH`, `--image PATH` | Open viewer tabs at startup |
| `--version` | Print the native MVP version |

User config lives at `~/.config/agent-terminal/native.json`:

```json
{
  "pane_close_policy": "adjacent_expand",
  "palette": "agent-dark"
}
```

`pane_close_policy` may be `adjacent_expand` (the closed pane's space
goes to its adjacent sibling) or `same_axis_reflow` (the space is
redistributed proportionally across the remaining siblings).

## Key shortcuts

Pane management uses `Alt+Shift`, common terminal-window commands use
`Ctrl+Shift`, so ordinary terminal input (including plain `Ctrl+H`,
`Ctrl+C`, `Ctrl+D`) is never stolen.

```text
Ctrl+Shift+T      new tab
Ctrl+Shift+O      open file picker
Ctrl+Shift+W      close pane or tab
Alt+Shift+H/V     split left-right / top-bottom
Alt+Shift+Arrows  focus panes
Alt+Shift+F       temporary focus fit
Alt+Shift+Space   pane control mode
Ctrl+Shift+C/V    copy / paste
Ctrl+Shift+F      find
F5                reload viewer
F / 1             image fit / actual size
Ctrl+Shift+H, F1  shortcut guide
```

The full table is in `docs/native-terminal-mvp.md` and in the in-app
shortcut guide.

## Install a user-local launcher

```bash
packaging/install.sh
```

This installs `~/.local/bin/agent-terminal-native` and a desktop entry.

## Development

Unit tests run headlessly (no GUI session or PyGObject needed):

```bash
python3 -m unittest tests.test_native_terminal tests.test_tui_navigation
python3 -m unittest          # full suite
```

Dependency smoke test on a desktop machine:

```bash
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Vte','3.91'); from gi.repository import Gtk, Vte; print('GTK/VTE ok')"
```

### Layout

- `agent_terminal/native_terminal.py` — app entry point, CLI parser,
  pure split-layout engine, Markdown/image helpers, actions and
  shortcuts, control socket, and all GTK/VTE widget classes.
- `agent_terminal/tui_navigation.py` — curses file picker that hands the
  selection back over a Unix control socket.
- `bin/agent-terminal-native` — launcher.
- `tests/` — headless behavioral contracts and source guardrails.
- `docs/native-terminal-mvp.md` — usage guide and comparison checklist.
- `packaging/` — user-local install script and desktop entry.
