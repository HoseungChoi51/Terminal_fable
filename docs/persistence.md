# Detachable panes (persistence)

Normally VTE owns each pane's PTY inside the GUI process, so the shell dies when
the frontend goes away. With **persistence** on, each pane's shell instead runs
inside a small **ptyd** daemon that outlives the frontend — so a running command
survives the window closing, the app crashing, or (when Terminal Fable runs on a
server you view over ssh/waypipe) the viewing link dropping. You reattach later
and the recent screen context is replayed.

Same idea as `tmux`, but built in and byte-transparent, so OSC-666
shell-integration and the command journal keep working.

## Enable it (opt-in)

Off by default. In `~/.config/agent-terminal/native.json`:

```json
{ "persistence": { "enabled": true, "scrollback_bytes": 524288 } }
```

- `enabled` — run new default-shell panes inside a ptyd daemon.
- `scrollback_bytes` — how much recent output the daemon keeps to replay on
  reattach (the "context"; default 512 KiB, min 4 KiB).

Only the default shell is made persistent; explicit one-off commands
(`--command`, the file picker, …) spawn directly. Anything unexpected falls back
to a normal spawn, so enabling it can't make a pane fail to open.

## Detach and reattach

- **Detach** — closing a persistent pane's frontend (close the pane/window, or
  the app quits) leaves its process running in the background. *View → Detach
  Pane (keep running)* does this explicitly. Typing `exit` in the shell, by
  contrast, ends the process for real.
- **Reattach** — open **Ctrl+Shift+S** (Session History). Detached sessions
  appear under **Running (detached)** with their cwd and pid; **Reattach** opens
  a pane bound to that session and replays its recent screen; **Kill** ends it.
  A one-line nudge on launch points you there when detached sessions exist.

## How it works

```
frontend (VTE) --spawn--> `ptyd attach --create -- <shell>`
                                |  unix socket ($XDG_RUNTIME_DIR/agent-terminal/pty/)
                                v
                            ptyd daemon (owns the PTY master + child shell)
```

The daemon (`agent_terminal/ptyd.py`) daemonizes (setsid + double-fork), is the
sole reader of the PTY master, keeps a bounded output ring, and forwards bytes
between the child and any attached clients. VTE spawns a thin **attach client**
in place of the shell; it bridges VTE's PTY to the daemon and, on connect,
receives the replayed ring to reload context. When the frontend dies the client
exits and the daemon keeps the child alive.

## Limits

- **Bounded context.** Only the last `scrollback_bytes` of output is replayed;
  older scrollback is not restored. A full-screen program repaints cleanly on
  resize; a plain shell shows the replayed tail.
- **Not exact for cursor-addressed output.** Replaying raw bytes can be
  imperfect for programs that used absolute cursor positioning before you
  detached; the resize-driven repaint mitigates this.
- **Same host only.** The daemon runs where Terminal Fable runs. It cannot keep
  a *remote* process alive across an ssh drop from a pane — for that, run a
  multiplexer or mosh on the remote.
- **Layout isn't restored yet.** Reattach is per-session (explicit picker);
  automatic full-window/pane-layout restore is a planned follow-up.
