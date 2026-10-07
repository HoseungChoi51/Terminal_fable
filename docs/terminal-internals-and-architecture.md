# Terminals, TTYs, PTYs, and how I'd architect a terminal from scratch

A from-first-principles explainer, written to answer four questions:

1. What are a TTY, a PTY, a shell, and a terminal emulator — and how do they
   relate?
2. Why do "TTY" and "PTY" seem to behave differently?
3. Why is tmux-style persistence ("the process survives a disconnect") *hard*
   with GTK/VTE — is it because VTE is an emulator? because the GUI is a
   different process than the TTY?
4. If you were building an app to satisfy *all* the user-side requirements
   (pane control, session restore, LLM, Markdown viewer, TUI file explorer, …),
   at whatever level of the Linux stack fits — how would you architect it?

## Short answers first

- A **PTY slave is a TTY.** There is no "TTY vs PTY" behavioral difference. The
  difference people notice is **TTY vs *pipe*** (a real/pseudo terminal vs a
  redirect): `isatty()` flips buffering, colour, and line-editing behaviour. A
  PTY exists precisely to give a program a TTY when there is no hardware
  terminal behind it.
- Persistence is **not** hard "because VTE is an emulator." It's hard because
  **the shell's lifetime is tied to the PTY, and VTE owns that PTY inside the
  GUI process.** Kill the GUI → the PTY master closes → the kernel sends
  `SIGHUP` → the shell dies. The GUI-is-a-different-process fact is the *seam
  that makes the fix possible*, not the obstacle.
- The fix is to put a **long-lived process between the shell and the emulator**
  that owns the PTY master and outlives the GUI. That's what tmux, screen,
  dtach, and our own `ptyd` do.
- The genuinely hard part is **reconstructing the screen on reattach**, and that
  is where the depth lives: to do it *perfectly*, the persistent middle process
  must **itself be a terminal emulator** (keep a grid model). tmux is exactly
  that — a multiplexer that is *also* a headless emulator. Our `ptyd` takes the
  cheap path (replay a byte buffer) and accepts imperfect fidelity.
- We do **not** actually use tmux. The earlier "you'd need tmux" remark was
  about a *different* scenario (ssh-ing from a pane into a remote host). For
  "Terminal Fable's own panes survive a disconnect," we built `ptyd`
  (`docs/persistence.md`). More on the remote story at the end.

---

## Part I — The cast of characters

### The teletype (why it's called a "TTY")

In the beginning there was a **teletypewriter** — a keyboard-plus-printer on the
end of a serial wire. You typed; bytes went up the wire to the computer. The
computer sent bytes back; they printed. The kernel subsystem that drove these
devices was the **TTY** layer, and the device files were `/dev/tty*`. The name
stuck long after the hardware vanished.

A hardware terminal did two jobs:

- **Output:** interpret incoming bytes — printable characters, plus *control*
  bytes and escape sequences (move cursor, clear line, bold) — and put marks on
  the page/screen.
- **Input:** turn keystrokes into bytes and send them up the wire.

Notice: **display and control travel in the same byte stream.** "Print an A" and
"move the cursor to row 3" are both just bytes. This *in-band signalling* is the
original sin that makes everything downstream interesting (and reattach hard).

### The PTY: a terminal made of software

When terminals became windows instead of furniture, the kernel needed a way to
present a *fake* terminal to programs. That's a **pseudo-terminal (PTY)** — a
matched pair of device endpoints:

```
   master (the "wire" end)                    slave  /dev/pts/N (the "terminal" end)
   held by the emulator                       given to the shell as stdin/out/err
            │                                          │
            └──────────────  kernel PTY  ──────────────┘
                    (line discipline lives on the slave)
```

- The **slave** (`/dev/pts/N`) is indistinguishable from a real terminal to any
  program: same `ioctl`s, same job control, same `isatty() == true`. The shell
  genuinely believes it is talking to a terminal.
- The **master** is the puppet-string. Whatever the shell writes to the slave,
  the emulator can read from the master; whatever the emulator writes to the
  master arrives as *input* on the slave.

So the modern picture is:

```
  ┌────────────────────────┐        master        ┌──────────────────────┐
  │  Terminal emulator      │◀───── (the wire) ────│  kernel PTY + line   │
  │  (VTE / xterm / TF)     │──────────────────────▶│  discipline          │
  │  • reads master bytes   │                       └──────────┬───────────┘
  │  • parses escapes       │                            slave │ /dev/pts/N
  │  • draws a cell grid    │                                  ▼
  │  • sends keystrokes ────┼──────────────────────▶   ┌──────────────┐
  └────────────────────────┘                           │   shell      │
        the human's terminal                           │   (bash)     │
        (screen + keyboard)                             └──────────────┘
                                                        the program's terminal
```

**The single most important sentence in this document:** the "terminal" is not
one thing — it is *two sides of a wire, usually in two different processes*, with
the kernel in the middle. The emulator is the terminal *the human sees*; the PTY
slave (+ line discipline) is the terminal *the program sees*.

### The shell

`bash` is just a program on the slave side. It reads bytes from its stdin (the
slave), writes bytes to its stdout (the slave). It does not know or care that a
GUI is on the far end. When it wants fancy line editing (history, Ctrl-R,
completion), it flips the terminal into *raw mode* and does the editing itself
via readline; when it just wants a line, the **kernel** does the editing for it
(see Part II). Its prompt, its colours, its `[REDACTED]`-or-not — all of it is
just bytes it chooses to emit based on `isatty()` and `$TERM`.

---

## Part II — The kernel machinery (this is where "different behaviour" comes from)

Between the raw device and the program sits the **line discipline** (`N_TTY`).
It is a small kernel state machine configured by the `termios` struct. Two modes
matter:

- **Canonical ("cooked") mode:** the kernel buffers input a line at a time and
  handles editing for you — Backspace erases, `Ctrl-U` kills the line, `Ctrl-D`
  signals EOF — and *echoes* what you type back to the screen. The program only
  sees a finished line. This is why `read()` from a plain shell script gets
  whole lines and Backspace "just works."
- **Raw / non-canonical mode:** every byte is delivered immediately, no editing,
  no echo. This is what `vim`, `less`, readline, and full-screen TUIs use — they
  want each keystroke themselves.

`termios` flags you will meet:

| Flag | Meaning | Why you care |
| --- | --- | --- |
| `ICANON` | canonical mode on/off | cooked vs raw |
| `ECHO` | kernel echoes input | off in raw mode (the app echoes) |
| `ISIG` | control chars raise signals | `Ctrl-C`→`SIGINT`, `Ctrl-Z`→`SIGTSTP` |
| `ICRNL` | map CR→NL on **input** | why Enter (`\r`) reads as `\n` |
| `OPOST`/`ONLCR` | map NL→CR-NL on **output** | why a bare `\n` prints as `\r\n` |
| `IXON` | `Ctrl-S`/`Ctrl-Q` flow control | the classic "my terminal froze" |

> **Callback to the `tty` bug.** Earlier we saw `tty` print `/dev/pts/8` with no
> trailing newline. That was `ONLCR` doing its job on the *bytes the program
> emitted* — the program simply emitted no `\n`, so there was nothing for the
> line discipline to translate. The kernel faithfully renders what it's given;
> it doesn't invent newlines. (The missing newline was a `uutils` bug, not the
> terminal's — see `docs/uutils-coreutils.md`.)

### "Why do TTY and PTY behave differently?" — they don't

A PTY slave *is* a TTY; it runs the same line discipline. What actually changes
behaviour is whether a program's stdout is a **terminal** at all:

```
  program │ stdout is a TTY (real or PTY)      │ stdout is a pipe/file
 ─────────┼───────────────────────────────────┼──────────────────────────
 buffering│ line-buffered (flush per line)     │ fully buffered (4–64 KB blocks)
 colour   │ isatty() → on                      │ isatty() → off ("dumb")
 editing  │ line discipline / readline          │ none
 prompt   │ interactive                        │ often suppressed
```

So `ls | cat` looks different from `ls` not because of "TTY vs PTY" but because
in the pipe case `ls`'s stdout is **not a terminal**. This is the real axis, and
it's why `expect`, `script`, and CI tools allocate a PTY: to *fool* a program
into thinking it has a terminal so it behaves interactively.

### Sessions, process groups, and the signals that decide life and death

This is the part that makes persistence hard, so it's worth precision.

- A **session** (created by `setsid(2)`) has a **session leader** and at most one
  **controlling terminal**. Interactive shells are session leaders.
- Within a session, processes are grouped into **process groups**; one is the
  **foreground** group (`tcsetpgrp`). The terminal delivers keyboard-generated
  signals to the *foreground* group: `Ctrl-C`→`SIGINT`, `Ctrl-Z`→`SIGTSTP`. A
  *background* group that reads/writes the terminal gets `SIGTTIN`/`SIGTTOU`
  (this is job control).
- **`SIGWINCH`:** when the window size changes, the kernel signals the foreground
  group; programs re-query `TIOCGWINSZ` and repaint. (Our `ptyd` forwards resize
  as a message → `TIOCSWINSZ` on the master → the kernel raises `SIGWINCH`.)
- **`SIGHUP` — the important one.** When the controlling terminal's connection is
  lost — historically a modem *hang-up*, today the **PTY master being closed** —
  the kernel sends `SIGHUP` to the session leader (and thence the foreground
  group). The default action is *terminate*. **This is why your work dies when
  you close the terminal.** `nohup`, `disown`, and `setsid` exist to escape it.

Hold onto that: **master closes → SIGHUP → shell dies.** Everything about
persistence is a fight against that one arrow.

---

## Part III — What a terminal emulator actually does

A terminal emulator (VTE, xterm, and the rendering half of Terminal Fable) runs
a loop:

```
  loop:
    bytes = read(master)
    for each byte: feed a parser (a VT100/ECMA-48/xterm state machine)
       → printable? write a cell into the grid at the cursor
       → escape sequence? move cursor / set colour / clear / scroll / set title …
    render the visible grid (fonts, colours, cursor) to the screen
  on keypress: encode it (with modifiers, bracketed paste, mouse) → write(master)
```

Two consequences matter for the rest:

1. **The screen is a *fold over the entire byte history*.** The current grid is
   the result of replaying every byte since the start through the state machine.
   There is no "get me the current screen" syscall — the screen only exists
   inside whoever ran the state machine. (This is the crux of reattach.)
2. **Control and content share one stream.** "Bold", "move to (3,10)", and the
   letter `A` are peers in the byte flow. A middle-man that wants to *understand*
   the screen must parse all of it; a middle-man that only *relays* bytes stays
   dumb but can't reconstruct the screen.

VTE is a very good implementation of *this* loop — the **client-side** emulator.
It does not, and is not meant to, own session lifetime or reconstruct state for
a future reconnection. That's a different job.

---

## Part IV — Why persistence is "hard", precisely

### The coupling

```
NORMAL (lifetime coupled):

   VTE (inside the GUI process)  ── owns master ──▶ slave ──▶ shell
                     │
   GUI exits/crashes ▼
        master fd closes ─▶ kernel ─▶ SIGHUP ─▶ shell (and its children) die
```

So the difficulty is **not** "VTE is an emulator." It is: *the entity that owns
the PTY master is the same entity that dies when your window/connection dies.*
VTE happens to be that entity, inside the GUI process.

### The fix: a persistent owner in the middle

```
PERSISTENT (decoupled):

   VTE (client) ──unix socket──▶ ptyd / tmux (daemon) ── owns master ──▶ slave ──▶ shell
                                        ▲
   GUI exits ─▶ client exits ───────────┘  daemon keeps the master open
   reconnect ─▶ new client ─────────────▶  daemon replays / redraws
```

You interpose a **long-lived process that owns the master and outlives the
frontend.** The shell's controlling terminal (the slave) never sees its master
close, so no `SIGHUP`, so it lives. The emulator becomes a **client** of that
daemon, talking over a socket, rather than the direct owner of the shell.

**This is exactly why the GUI-being-a-separate-process is the *enabler*, not the
obstacle.** The process boundary is where you insert the daemon. If the emulator
and shell were somehow the same process, you *couldn't* do this at all.

### The genuinely hard part: reconstructing the screen

A freshly-attached client has an empty grid. What should it show? Three answers,
in increasing fidelity and cost:

1. **Nothing / redraw nudge (dtach, abduco).** Relay bytes only; on attach, send
   `SIGWINCH` so full-screen apps repaint themselves. A *plain shell* leaves you
   with a blank screen and a fresh prompt. Cheap, byte-transparent, forgets
   scrollback.
2. **Replay a byte ring (our `ptyd`).** Keep the last N KB of raw output and
   re-send it on attach; the client's emulator re-folds it into a grid. You get
   your recent screen and scrollback back. Imperfect if the program used
   absolute cursor addressing (the replayed prefix may not reproduce the exact
   grid), which the resize-repaint mitigates. Still byte-transparent — so our
   OSC-666 shell-integration flows straight through and the journal keeps
   working.
3. **Keep a real screen model (tmux, screen).** The daemon **is itself a terminal
   emulator**: it runs the Part-III state machine server-side, maintains the grid
   per pane, and on attach *serialises the current grid* into escape sequences
   the client can draw. Perfect reconstruction — at the cost of reimplementing a
   terminal, and of *re-encoding* output for whatever the client claims to be
   (terminfo capability translation). It also means tmux must decide what to do
   with sequences it doesn't model (hence `allow-passthrough` for things like our
   OSC-666).

> **The deep insight:** *perfect* persistence requires a **second, headless
> terminal emulator on the server side.* When you run tmux inside a GUI terminal
> you are running **two emulators stacked** — tmux parses the shell's bytes into
> its grid, then re-renders that grid as new bytes for VTE to parse into *its*
> grid. tmux is a multiplexer **and** an emulator **and** a session server **and**
> a client. That bundle is why "just add persistence" is really "embed a
> terminal."

### Where the four tools sit

| | owns master & survives | keeps scrollback | server-side grid | byte-transparent | extra cost |
| --- | :---: | :---: | :---: | :---: | --- |
| **dtach/abduco** | yes | no | no | yes | tiny; blank on reattach |
| **ptyd** (ours) | yes | ring | no | yes | replay imperfect for cursor apps |
| **screen/tmux** | yes | yes | **yes** | no (filters) | reimplements a terminal; prefix key |

Our choice of `ptyd` was a deliberate trade: keep byte-transparency (so
shell-integration/journal work untouched) and simplicity, accept imperfect
reattach fidelity, and avoid maintaining a terminal emulator. tmux's choice buys
perfect fidelity by *being* that emulator.

---

## Part V — The Linux stack, and where each requirement lives

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │  Human: eyes + fingers                                               │
   ├─────────────────────────────────────────────────────────────────────┤
   │  Display server (Wayland/X)          ← pixels, input events          │
   ├─────────────────────────────────────────────────────────────────────┤
   │  GUI toolkit (GTK4)                  ← windows, widgets, layout       │
   │  Terminal emulator (VTE)             ← escape parsing, grid, render   │  ← "frontend"
   ├─────────────────────────────────────────────────────────────────────┤
   │  === PTY master / socket boundary ===                                 │
   ├─────────────────────────────────────────────────────────────────────┤
   │  Kernel: PTY subsystem, line discipline, sessions/pgroups, signals    │  ← the substrate
   ├─────────────────────────────────────────────────────────────────────┤
   │  Shell (bash): job control, readline                                  │
   │  TUI programs (vim, less, a curses file explorer) on the slave        │  ← "workloads"
   └─────────────────────────────────────────────────────────────────────┘
```

Now map your requirements onto this — and notice they live at **three different
altitudes**, which is the key to a clean design:

| Requirement | Where it belongs | Is it even a "terminal"? |
| --- | --- | --- |
| **Pane control** (split/focus/resize/layout) | GUI/window layer — pure geometry + a widget tree | No — window management |
| **Session restore / persistence** | A daemon *below the emulator* that owns PTYs and outlives the GUI (kernel gives pty+sessions+signals; the daemon uses them) | It's the *plumbing* of terminals |
| **TUI file explorer** (`smart-ls`) | A normal program on a PTY slave — *or* a native GUI pane | Optionally a terminal workload |
| **Markdown / image viewer** | A native GUI widget pane | **No** — not a terminal at all |
| **LLM assistant** | A network client + UI, reading *structured* context (the command journal) | No — an application feature |

The lesson: **most of what you want is not "terminal" at all.** A terminal
(emulator + PTY + shell) is *one kind of pane*. Pane layout is window management.
Persistence is a session daemon. The viewer and the LLM are application panes
that never touch a PTY. Conflating all of this into "a terminal" is what makes
the code feel tangled; separating them by altitude is what makes it clean.

---

## Part VI — How I'd architect it from scratch

If I were handed your full requirement list with a blank repo, I'd design around
one decision: **split the persistent *backend* from the *frontend*, and treat a
terminal as just one *pane kind*.** That single split answers persistence,
remote work, and the GUI-vs-TUI question all at once.

```
        ┌───────────────────────────── FRONTEND (a client) ─────────────────────────────┐
        │  Pane compositor + workspace manager                                          │
        │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐                     │
        │  │ terminal  │ │ terminal  │ │ md viewer │ │ LLM chat  │   ← pane KINDS       │
        │  │  pane     │ │  pane     │ │  pane     │ │  pane     │                     │
        │  └─────┬─────┘ └─────┬─────┘ └───────────┘ └─────┬─────┘                     │
        │        │             │        (native widget)    │ (network client)          │
        │  layout engine • tabs/windows • input routing • session-restore UI           │
        └────────┼─────────────┼──────────────────────────┼────────────────────────────┘
                 │ pane protocol (attach/detach, io, resize, snapshot, spawn, list)
        ┌────────▼─────────────▼──────────────────────────▼───── BACKEND (per host) ───┐
        │  Session server (headless daemon)                                            │
        │   • owns PTYs + child processes, one per terminal pane                        │
        │   • maintains a server-side GRID MODEL per pane (⇒ perfect reattach)          │
        │   • the command JOURNAL lives here (it sees all shell activity centrally)     │
        │   • survives frontend disconnects; discoverable + reattachable                 │
        └──────────────────────────────────────────────────────────────────────────────┘
                 │  kernel: PTY, line discipline, sessions, signals
                 ▼
              shells / TUI programs
```

### The pieces, and why

1. **A headless *session server* per host (the backend).** This is the tmux-CC
   idea done deliberately. It owns the PTYs, runs a *server-side terminal
   emulator* to keep an authoritative grid per pane (so reattach is exact, not a
   replay approximation), hosts the command **journal** (the natural home —
   it's the one place that sees every command), and outlives any frontend. On a
   remote machine, *this* is what you install and keep running.

2. **A thin, multi-backend *frontend* (the client).** It connects to one or more
   session servers over a small **pane protocol** and does only: rendering,
   layout/tabs/windows, input routing, and the reattach UI. Because it's a
   client, it can be a **GTK GUI** *or* a **curses TUI** (so it works inside any
   terminal, over SSH, in a rescue console). And because it can hold several
   backends at once, you can put **local panes and remote panes side by side in
   one window** — the thing X-forwarding a whole GUI can never give you cheaply.

3. **A `PaneKind` abstraction.** A pane is an interface — `render()`,
   `handle_input()`, `snapshot()` — with implementations: `TerminalPane` (backed
   by a session-server PTY), `ViewerPane` (a native Markdown/image widget, no
   PTY), `TuiPane` (a curses program on a PTY — the file explorer), `ChatPane`
   (an LLM client). The compositor doesn't care which is which. *This is the
   abstraction that stops "terminal" from swallowing everything.*

4. **The assistant plane reads structured data, not screen scrapes.** Because the
   journal lives in the session server, the LLM gets clean command/exit/cwd/
   output records (already redacted at the source) instead of trying to parse the
   rendered grid. Viewers and the explorer are just pane kinds the assistant can
   *open* (it already does, via the control socket).

5. **The remote story, done right.** "Survive an ssh disconnect" becomes trivial
   and correct: run the **session server on the remote**, run the **frontend
   locally**, and connect the pane protocol over a transport:
   - over **SSH** (`ssh host -- session-server --stdio`) for simplicity, like
     tmux `-CC`; or
   - over a **roaming UDP protocol** à la **mosh** (a *state-synchronisation*
     protocol that ships screen *diffs*, survives IP changes and sleep, and does
     local echo prediction) for flaky links.

   This reframes your plan. **You don't need the whole GUI on every server** —
   you need the *session server* on every server, and *one* frontend wherever you
   happen to be sitting. That is lighter, survives disconnects by construction,
   and lets one window show many hosts.

### How this maps to Terminal Fable today (honest accounting)

Terminal Fable currently fuses "frontend" and "backend": VTE owns the PTY in the
GUI process, and `ptyd` is a *bolt-on* daemon that reclaims persistence for the
panes that opt in. Concretely, against the ideal:

| Ideal piece | Today in TF | Gap |
| --- | --- | --- |
| Frontend compositor | ✅ custom layout engine, tabs/windows, pane moves | — |
| `PaneKind` abstraction | ✅ terminal / Markdown / image panes already coexist | LLM/explorer are ad-hoc, not a uniform kind |
| Session server w/ **grid model** | ⚠️ `ptyd` owns the PTY + a **byte ring** (no grid) | reattach is replay, not exact |
| Journal in the backend | ⚠️ journal lives in the *frontend* per pane | doesn't survive/travel with the session |
| Network transport | ❌ `ptyd` is local-socket only | no remote server / roaming yet |
| Curses frontend | ❌ GTK only | can't drive it from inside a plain terminal |

None of these gaps are mistakes — they're the deliberate, pragmatic order of
build. `ptyd` gets you 80% of persistence for 5% of the effort of embedding a
terminal. But if the goal grew to "my environment on every server, one window,
survives anything," the arrows above are the roadmap: give the daemon a grid
model, move the journal into it, and give it a network transport + a curses
client.

### Would I actually build the ideal? A caveat

The ideal architecture is, in plain terms, **"reimplement tmux's core and give it
a GTK *and* a curses client."** That is a large, multi-quarter undertaking, and
much of it *already exists* as tmux (grid model, control mode, session server)
and mosh (roaming transport). A completely reasonable alternative architecture is
**"be a great client of tmux control mode"** (as iTerm2 does): let tmux be the
backend and grid model, and put your energy into the frontend (layout, panes,
viewers, LLM) — trading byte-transparency and a prefix key for not maintaining an
emulator. The right call depends on how much you value byte-transparency (which
our shell-integration relies on) versus the cost of owning a server-side
emulator. Our current answer — a byte-transparent `ptyd` with replay — is a
defensible middle path; this document is meant to let you *choose the next step
with eyes open*, not to declare one true way.

---

## Part VII — A short glossary + where to read more

- **TTY** — the kernel terminal subsystem / a terminal device. `man 4 tty`.
- **PTY** — pseudo-terminal (master+slave). `man 7 pty`, `man 3 openpty`.
- **Line discipline / termios** — cooked vs raw, echo, signals, CR/LF. `man 3
  termios`, `man 3 tcsetattr`.
- **Controlling terminal / session / process group / job control** — `setsid(2)`,
  `tcsetpgrp(3)`, `credentials(7)`; and `SIGHUP`/`SIGWINCH`/`SIGINT` in
  `signal(7)`.
- **Multiplexers** — `tmux(1)` (grid model + control mode `-CC`), `screen(1)`,
  `dtach`/`abduco` (relay-only).
- **Roaming remote** — `mosh` (State Synchronisation Protocol over UDP; screen
  diffs; local echo prediction).
- **Escape sequences** — ECMA-48 / VT100 / the xterm control-sequences reference;
  `terminfo(5)` for capability abstraction.
- **A classic read** — "The TTY demystified" (Linusakesson) is the best short
  tour of controlling terminals, sessions, and signals.

### The one-paragraph takeaway

A terminal is *two ends of a byte wire with the kernel in the middle*: the
program's end is a TTY (real or a PTY slave, identical to the program), and the
human's end is an emulator that folds the byte stream into a grid. Processes die
on disconnect because the emulator owns the PTY master and its death sends
`SIGHUP` down the wire. You fix that by interposing a **persistent PTY owner** —
and you get *perfect* reattach only if that owner is **itself an emulator** with a
grid model. Everything else you want — panes, viewers, the LLM, the file explorer
— is **not a terminal at all**, and the clean architecture is a thin,
possibly-remote, multi-backend **frontend** over a persistent **session server**,
with "terminal" demoted to just one kind of pane.
