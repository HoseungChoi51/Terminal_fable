# Native Terminal MVP

Usage guide for the native GTK4/VTE terminal launched by
`bin/agent-terminal-native`. The implementation plan and architecture
map is `native-terminal-reproducible-development-plan.md` at the
repository root.

## Runtime

- Python 3 with PyGObject, GTK 4, VTE 3.91, GdkPixbuf, Pango, Graphene,
  and GSK system bindings.
- Terminal emulation is delegated entirely to VTE; the app never parses
  escape sequences itself.

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-vte-3.91 gir1.2-adw-1
```

## Concepts

- **Window** — a `Gtk.ApplicationWindow` with a header bar, a search
  bar, and a `Gtk.Notebook` of tabs.
- **Tab** — one split-layout tree of panes plus undo/redo history for
  persistent layout changes. Focus movement never enters history.
- **Pane** — a terminal (VTE), a Markdown viewer, or an image viewer.
  All pane kinds share the same behavior surface (focus, copy, paste,
  search, zoom, reload, …) so window actions route uniformly.
- **Pane control mode** — a transient modal window (`Alt+Shift+Space`)
  exposing many pane commands without stealing normal terminal keys.

Layout state is stored as split weights (persistent intent), never as
pixels. Pixel rectangles are recomputed from the current allocation on
every resize, and hard pane minimums are respected when the window is
large enough.

## Shortcuts

### Window and tabs

| Shortcut | Action |
| -------- | ------ |
| `Ctrl+Shift+N` | New window |
| `Ctrl+Shift+T` | New tab |
| `Ctrl+Shift+O` | Open file picker (Markdown / images) |
| `Ctrl+Shift+W` | Close pane, or the tab when it is the last pane |
| `Ctrl+PageDown` / `Ctrl+PageUp` | Next / previous tab |
| `Alt+1` … `Alt+9` | Jump to tab N |
| `Ctrl+Shift+Q` | Quit |

### Panes

| Shortcut | Action |
| -------- | ------ |
| `Alt+Shift+H` | Split left-right |
| `Alt+Shift+V` | Split top-bottom |
| `Alt+Shift+Arrows` | Focus pane in direction |
| `Alt+Shift+F` | Temporary focused-pane fit (toggle) |
| `Alt+Shift+Enter` | Zoom pane (stronger temporary fit) |
| `Alt+Shift+Space` | Pane control mode |
| `Alt+Shift+Z` / `Alt+Shift+Y` | Undo / redo layout change |

### Terminal

| Shortcut | Action |
| -------- | ------ |
| `Ctrl+Shift+C` / `Ctrl+Shift+V` | Copy / paste |
| `Ctrl+Shift+A` | Select all |
| `Ctrl+Shift+F` | Find (searches VTE scrollback) |
| `Ctrl+Shift+G` / `Ctrl+Shift+B` | Find next / previous |
| `Ctrl+Shift+R` | Reset terminal |
| `Ctrl+Shift+K` | Clear scrollback |
| `Ctrl++` / `Ctrl+-` / `Ctrl+0` | Font zoom in / out / reset (active pane only) |

### Viewers

| Shortcut | Action |
| -------- | ------ |
| `F5` | Reload the viewer pane |
| `F` | Image: fit to pane |
| `1` | Image: actual size |
| `Ctrl+wheel` | Image: zoom |
| drag | Image: pan |

### Help

| Shortcut | Action |
| -------- | ------ |
| `Ctrl+Shift+H`, `F1`, `Ctrl+?` | Shortcut guide |
| `Ctrl+,` | Preferences (points at CLI/config settings) |

## Pane control mode

`Alt+Shift+Space` opens a transient modal window. `Esc` returns to
command mode or closes it.

- **Command mode** — arrows focus panes; `h`/`v` split; `x` close;
  `f` fit; `z`/`y` undo/redo; `r` resize mode; `g` grow mode; `m` move
  mode; `b` balance mode.
- **Resize mode** — arrows move the nearest divider.
- **Grow mode** — arrows grow the pane from that side; `Shift+arrows`
  resize around the pane center.
- **Move mode** — arrows move the pane next to its neighbor;
  `Shift+arrows` swap with the neighbor.
- **Balance mode** — `l` local split, `a` axis, `s` subtree, `t` whole
  tab, `w` weighted, `o` spotlight, `d` tidy (equal leaf areas).

## File picker and control socket

`Ctrl+Shift+O` opens a transient VTE-backed window running the curses
picker (`python -m agent_terminal.tui_navigation select-file`). Nothing
is ever typed into your active shell. Selecting a file sends one JSON
line over a process-local Unix socket
(`$XDG_RUNTIME_DIR/agent-terminal-native-<pid>.sock`):

```json
{"action": "open-file", "path": "/absolute/path"}
```

The GTK main loop then opens the file as a Markdown or image pane in
the most recent native window. Terminal panes also export the socket
path as `AGENT_TERMINAL_NATIVE_CONTROL_SOCKET`, so scripts inside the
terminal can open files in the app:

```bash
python3 -c 'import json,os,socket; s=socket.socket(socket.AF_UNIX); s.connect(os.environ["AGENT_TERMINAL_NATIVE_CONTROL_SOCKET"]); s.sendall(json.dumps({"action":"open-file","path":os.path.abspath("README.md")}).encode()+b"\n")'
```

## Markdown viewer

A passive native renderer (GTK labels and Pango markup, no web view).
Supported subset: headings, paragraphs, blockquotes, fenced and indented
code blocks, horizontal rules, task lists, ordered and unordered lists,
basic tables, links, inline code, bold, and italics.

- Local links to Markdown or image files open as new panes.
- External links open only for safe schemes (`http`, `https`, `mailto`,
  `file`).
- Find re-renders with highlighted matches and scrolls to them.

## Image viewer

PNG, JPEG, GIF, and WebP through GdkPixbuf, rendered with
`Gtk.Picture`. Images open at actual size; `F` fits to the pane, `1`
returns to actual size, `Ctrl+wheel` zooms (clamped), dragging pans,
and copy puts the image path on the clipboard.

## GNOME Terminal comparison checklist

Run through this before adding richer features. Ordinary shell work
must not diverge from GNOME Terminal:

1. Typing, Enter, Backspace, arrow keys, and tab completion behave
   normally in `bash`, `zsh`, and `python3` REPL.
2. `Ctrl+C` interrupts, `Ctrl+D` sends EOF, `Ctrl+Z` suspends.
3. `Ctrl+H`, `Ctrl+W`, `Ctrl+A`, `Ctrl+E`, `Ctrl+R`, `Ctrl+L` reach the
   shell (nothing is stolen by app shortcuts).
4. Full-screen TUI apps (`htop`, `vim`, `less`) render and resize
   correctly inside splits.
5. Mouse selection, `Ctrl+Shift+C/V`, and middle-click paste work.
6. True-color output (`curl -s https://gist.githubusercontent.com/...truecolor test`)
   and 256-color `ls --color` look identical to GNOME Terminal.
7. URLs are highlighted on hover and scrollback search finds earlier
   output.
8. Child exit closes the pane (without `--hold-on-exit`); the window
   closes when the last pane exits.
9. Window resize keeps split proportions; no pane collapses below its
   hard minimum while space allows.

## Troubleshooting

- **`Namespace Gtk not available` or `cannot import gi`** — install the
  system bindings: `sudo apt install python3-gi gir1.2-gtk-4.0
  gir1.2-vte-3.91 gir1.2-adw-1`. Run with system `python3`, not a venv
  without system site packages (the launcher does this by default).
- **`Namespace Vte not available for version 3.91`** — your distro ships
  only the GTK3 VTE (`gir1.2-vte-2.91`). Install the GTK4 variant
  `gir1.2-vte-3.91`.
- **Headless session / `cannot open display`** — the native app needs a
  graphical session. On a remote machine, use X forwarding or run the
  unit tests instead (`python3 -m unittest`).
- **Picker does not open files** — the control socket lives in
  `$XDG_RUNTIME_DIR`; if that is unset the app falls back to `/tmp`.
  Check that the path printed by the picker matches
  `AGENT_TERMINAL_NATIVE_CONTROL_SOCKET` inside the terminal pane.
- **Blurry fonts under fractional scaling** — set a slightly larger
  integer `--font-size` instead of relying on display scaling.
