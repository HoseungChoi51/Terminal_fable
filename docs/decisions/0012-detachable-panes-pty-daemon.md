# 0012 — Detachable panes via an in-tree PTY daemon

- Status: Accepted
- Deciders: project authors
- Related: [0001](0001-delegate-terminal-emulation-to-vte.md) (VTE owns the PTY),
  [0003](0003-pure-gtk-free-core.md) (pure core),
  [0007](0007-bash-shell-integration.md) (OSC-666 termprops),
  [persistence.md](../persistence.md)

## Context

VTE spawns each pane's shell with `Vte.PtyFlags.DEFAULT`, so the PTY lives inside
the GUI process; when the frontend goes away (window closed, app crashed, or a
remote viewing link dropped) the child gets SIGHUP and the work dies. The user
wants a pane's process to survive a disconnect and be reattached with its screen
context — tmux-style — whether Terminal Fable runs locally or on a server.

## Decision

- **A custom in-tree PTY daemon** (`agent_terminal/ptyd.py`), one per pane, owns
  the PTY + child and daemonizes (`setsid` + double-fork + ignore SIGHUP) so it
  outlives the frontend. Chosen over delegating to **tmux/dtach**:
  - *vs tmux:* tmux filters/rewrites escape sequences and would need
    `allow-passthrough` tuning for our OSC-666 shell-integration, and imposes a
    prefix key + its own model. A byte-transparent daemon keeps the journal
    working untouched.
  - *vs dtach/abduco:* those preserve no scrollback (only a resize repaint) and
    aren't installed; we want context replay.
- **VTE spawns a thin attach client**, not the daemon's PTY master fd. The
  daemon must be the *sole* reader of the master to maintain the replay ring;
  handing VTE the raw master would race those reads. The attach client bridges
  VTE's PTY ↔ the daemon socket, avoiding fd-passing (SCM_RIGHTS).
- **Context is a bounded raw-output ring**, replayed on connect (+ a
  resize-driven repaint), rather than a full terminal screen-model in the
  daemon. Lighter and stdlib-only; the honest cost is imperfect fidelity for
  cursor-addressed output and no scrollback beyond the ring.
- **Opt-in, fails open.** `persistence.enabled` is off by default; only the
  default shell is routed through the daemon, and any error falls back to a
  direct spawn — preserving the dogfooding launchability invariant.
- **Explicit reattach**, not automatic layout restore, for now. Live sessions
  are discovered by scanning the socket dir (like `tmux ls`) and reattached from
  the session browser; a durable per-pane session id keys each daemon.

## Consequences

- Pure/stdlib core (ADR 0003): the daemon, attach client, and protocol are
  GTK-free and headless-tested with real PTYs. Bytes pass through untouched, so
  ADR-0007 termprops and the journal are unaffected.
- New spawn seam: when enabled, VTE runs `python -m agent_terminal.ptyd attach`
  instead of the shell; detach = the client exits and the daemon survives; `exit`
  in the shell ends it for real.
- Reattach is bounded by the ring and is per-session; **full-layout restore**
  (needs layout serialization) is a documented follow-up.
- Orphaned daemons persist until reattached or killed; the session browser lists
  and reaps them, and a launch-time nudge surfaces them.
