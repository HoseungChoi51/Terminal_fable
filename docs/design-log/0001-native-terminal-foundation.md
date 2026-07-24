# 0001 — Native terminal foundation

- Developed: early · Status: Built · ADRs: [0001](../decisions/0001-delegate-terminal-emulation-to-vte.md)–[0004](../decisions/0004-shared-tui-core-and-smart-ls.md)
- Sources: this entry is **reconstructed** from the ADRs, `native-terminal-reproducible-development-plan.md`, and git history (no verbatim planning transcript).

## Request (reconstructed)

Build a GTK4 terminal application with tmux-style pane management that can be
the author's daily driver, without reinventing a terminal emulator, and with
logic that can be tested without a display.

## Problem / context

A terminal needs escape-sequence parsing, scrollback, selection, true color,
hyperlinks, and a PTY — an enormous long-tail to implement. It also needs
n-ary split panes with focus-by-direction, resizable dividers, grow/shrink,
swap/move, balance, and undo/redo. And it must stay launchable and testable on
headless machines.

## Decisions

- **Delegate emulation to VTE** (ADR 0001) rather than writing an engine — the
  emulator is not where this project's value lies.
- **A custom pane-layout widget** (ADR 0002), `AgentPaneLayoutWidget`
  (`do_measure`/`allocate` + `Gsk.Transform`), instead of nested `Gtk.Paned`,
  because n-ary splits + undo + fit-focused don't fit Paned's binary model.
- **A pure, GTK-free core** (ADR 0003): layout math, config, Markdown, the
  control protocol live above the GTK boundary so they unit-test headless.
- **Share one TUI core; ship smart-ls as a standalone subprocess** (ADR 0004),
  reusing the directory model and control-socket client.

## Approach

`agent_terminal/native_terminal.py` hosts the GTK/VTE classes built by
`build_native_classes(g)`; the pure layout engine and helpers sit above it and
are exercised by `tests/` without a display. `smart-ls` runs as a subprocess
speaking the control socket.

## Status

Built; the invariants (pure-core boundary, VTE delegation, custom layout) still
hold and are pinned by source-guardrail tests. This foundation is the substrate
every later phase builds on.
