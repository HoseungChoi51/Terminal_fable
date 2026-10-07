# 0013 — Make pane processes survive disconnection (tmux-style)

- Developed: 2026-07 · Status: Built (Phases 1–3) · ADR: [0012](../decisions/0012-detachable-panes-pty-daemon.md)
- Modules: `agent_terminal/ptyd.py` (+ `native_terminal.py`, `docs/persistence.md`)

## Request

> "Make the terminal process survive ssh disconnection just like tmux."
>
> Clarified: "I will run Terminal Fable on a remote server, and the process
> there should survive after I disconnect from the server. The same is true on
> local machine, too. I can re-connect the process after closing the terminal.
> Attach the process and reload the context."

**Rephrased.** A pane's shell + running command must survive the frontend going
away (window closed, app crashed, or a remote viewing link dropped), and be
**reattached with its screen context** — whether Terminal Fable runs locally or
on a server. Same mechanism both ways: the process lives in a backend on the
host that runs TF, decoupled from the GUI.

## Decisions (clarifying Q&A)

- **Scenario** — the process must outlive the *frontend* on the same host, and
  reattach + reload context. (A local terminal can't keep a *remote* process
  alive across an ssh drop by itself — that needs a multiplexer/mosh on the
  remote; out of scope.)
- **Backend** → *custom in-tree PTY daemon* (not tmux/dtach). Chosen for byte
  transparency (our OSC-666 shell-integration/journal keep working) plus context
  replay. See ADR 0012 for why over tmux (escape filtering) and dtach (no
  scrollback).
- **Reattach UX** → *explicit reattach picker* (like `tmux ls` → attach), not
  automatic full-layout restore. Auto layout restore needs layout serialization
  (none yet) and is a follow-up.

## Approach

- **`ptyd.py`** (pure/stdlib): a per-pane daemon that daemonizes (setsid +
  double-fork), owns the PTY + child, keeps a bounded output ring, and forwards
  bytes over a per-session Unix socket; a thin **attach client** VTE spawns in
  place of the shell bridges VTE's PTY ↔ the daemon and replays the ring on
  connect. Length-framed protocol; `list_sessions`/`kill_session` for discovery.
- **Frontend** (opt-in, fails open): `persistence.enabled` routes a new pane's
  shell through `ptyd attach --create`; a durable per-pane session id lets it be
  rediscovered. Detach = the client exits, daemon survives; `exit` ends it.
- **Reattach**: the Ctrl+Shift+S session browser lists **Running (detached)**
  sessions with Reattach/Kill; a launch nudge points at them; *Detach Pane* is
  an explicit action.

## Status

Built and verified with real-PTY + GTK e2es per phase: survive a frontend drop,
reattach replays context onto the SAME shell, winsize propagates, teardown on
exit; detach → orphaned → reattach same pid → kill. 514 tests green. Default
off. Follow-up: automatic full-layout restore (Phase 4).
