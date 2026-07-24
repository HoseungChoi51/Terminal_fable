# Smart ls (`sls`)

A full-screen directory browser in the spirit of `ls`: it renders the
listing on the alternate screen (like `vi` or `man`) instead of
scrolling text into the terminal, and single keys toggle the behaviors
you would otherwise pass as `ls` flags. It runs in **any** terminal —
inside Terminal Fable it additionally opens Markdown and images in
native viewer panes.

## Run

```bash
bin/sls              # browse the current directory
bin/sls ~/Work       # browse a specific directory
bin/sls --long       # start in long format
bin/sls --show-hidden
```

(Equivalent: `python -m agent_terminal.smart_ls`; `packaging/install.sh`
also installs `~/.local/bin/sls`.)

## Smart truncation

A single very long file name normally ruins `ls` output: every column
becomes as wide as that one name. `sls` instead computes the **mode**
(most common value) of the name lengths in the listing — with a median
guard for pathological distributions — and ellipsizes only the outliers
down to `mode + 4` characters. Typical names stay untouched; the one
`a_very_extremely_long_file_name….tar.gz` no longer dictates the layout.

Truncation never fires when it would save less than 3 columns, when all
names are about the same length (a uniformly long listing just gets
fewer columns), and never cuts below 8 characters. The `e` key cycles
where the ellipsis goes:

| Mode | Example (width 14) |
| --- | --- |
| `end` (default) | `a_very_extrem…` |
| `middle` (keeps the extension) | `a_very_ex…r.gz` |
| `start` | `…orever.tar.gz` |

The grid is column-major like `ls`: entries read down each column,
directories first and **bold**, with the column width driven by the
truncated name width.

## Keys

| Key | Action | ls flag |
| --- | --- | --- |
| `↑` `↓` | move within a column | |
| `←` `→` | move across columns | |
| `PgUp` `PgDn` | page | |
| `Home`/`g`, `End`/`G` | first / last entry | |
| `Enter` | enter directory / open file | |
| `Backspace` | parent directory (cursor lands on the child you left) | |
| `a` | toggle hidden files | `-a` |
| `l` | toggle long format (permissions, size, date) | `-l` |
| `h` | toggle human-readable sizes | `-h` |
| `t` | sort by mtime, newest first (press again for name) | `-t` |
| `S` | sort by size, largest first (press again for name) | `-S` |
| `r` | reverse the sort | `-r` |
| `e` | cycle truncation: end → middle → start | |
| `q` | quit **and cd** (writes `--cwd-file`) | |
| `Q` / `Esc` | quit without cd | |

The header shows the directory and the active toggles, e.g.
`~/Work  [mtime↓] [hidden] [human] [trunc:middle]`.

## cd on exit

`sls` is also a navigator: quit with `q` and your shell follows you to
the browsed directory. The mechanism is nnn-style — `--cwd-file PATH`
names a file that `q` writes the final directory into (and `Q`/`Esc`
does not). Add this function to `~/.bashrc`:

```bash
sls() {
  local t
  t="$(mktemp)"
  command sls --cwd-file "$t" "$@"
  [ -s "$t" ] && cd -- "$(cat "$t")"
  rm -f "$t"
}
```

Without the wrapper (or without `--cwd-file`) `sls` is a pure viewer and
your shell stays where it was.

## Opening files

`Enter` on a file:

- **Inside Terminal Fable**, Markdown and image files open in a native
  viewer pane: `sls` sends the existing `open-file` action over the
  app's control socket (terminal panes export
  `AGENT_TERMINAL_NATIVE_CONTROL_SOCKET`, see
  [native-terminal-mvp.md](native-terminal-mvp.md)).
- **Anywhere else** — or for any other file type — the file opens with
  a detached `xdg-open`.

`sls` keeps running either way — it is a browser, not a picker. Inside
Terminal Fable the new viewer pane takes keyboard focus (so you can
scroll and zoom it immediately); press `Esc` there to hand input back
to the terminal and continue browsing.

## Limitations

- Name widths are measured in code points: CJK/fullwidth characters
  occupy two terminal cells and can misalign columns (nothing crashes;
  curses clips). A cell-width-aware measure is a possible follow-up.
- The `…` ellipsis assumes a UTF-8 locale.
- No type-to-filter: unlike the file picker, letters are commands.
  `/` is reserved for a future less/vim-style filter.
