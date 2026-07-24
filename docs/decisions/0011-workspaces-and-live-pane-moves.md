# 0011 — Workspaces: episode resume, job grouping, live pane moves, naming

- Status: Accepted
- Deciders: project authors
- Related: [copilot-development-plan.md](../../copilot-development-plan.md),
  [0005](0005-copilot-pure-core-package.md) (pure copilot core),
  [0008](0008-llm-provider-and-remote-gate.md) (gated redacting choke point),
  [0009](0009-session-persistence-format.md) (session persistence)

## Context

The terminal already records per-pane **sessions** and segments them into
**episodes**, but that memory was under-used: resume was coarse (a tab at the
last cwd, no history), a "job" that spanned several concurrent sessions was
invisible, panes could not leave their window, and windows/panes were
auto-named only. This ADR records the model decisions behind the workspace
features (roadmap phases A–D).

## Decisions

1. **Resume at episode granularity, seeded into shell history.** Restoring a
   session reopens its most recent *episode* — a pane at the episode's cwd with
   that episode's commands loaded for up-arrow recall via a temp
   `AGENT_TERMINAL_SEED_HISTFILE`. The wrapper relocates `HISTFILE` onto that
   temp file, so a restored pane never writes the user's global
   `~/.bash_history`. Seeds are the already-redacted stored commands.

2. **Jobs = co-active sessions; grouping is bounded, never arbitrary.** Sessions
   whose active intervals overlap or nearly touch are one job (same-project
   members get a wider gap tolerance). A job is laid out as split panes, but
   **never below a legibility floor**: packing measures the target monitor
   against a minimum pane size (cols×rows) and spills a large job into more
   balanced windows rather than over-dividing one. The same job opens denser on
   a bigger monitor. Pure logic lives in `copilot/jobs.py`.

3. **Screen rearranges are act-then-revert.** Any operation that reorganizes the
   user's windows (job restore; later, gather) performs the change immediately
   and shows a **Keep / Revert** bar that auto-reverts after a countdown — the
   display-resolution-dialog pattern. Assertive, but never a one-way door.

4. **Pane moves between windows are live.** A pane is *reparented*, not
   respawned: the VTE widget is unparented and re-attached to the target tab,
   the tab-scoped click controller and title/exit callbacks are rebound, and
   both layout trees are fixed. The child process, PTY, and scrollback survive.
   `detach_pane` never disposes; `adopt_pane` rebinds to the new window.

5. **Names are suggestions, not seizures.** A manual name (F2 / Rename Window)
   overrides the journal-inferred title and sticks. LLM naming feeds the model
   each pane's **redacted digest** (via the same choke point and episode/digest
   machinery, ADR 0008) and returns a window name + per-pane names into an
   **editable** dialog; nothing is applied until the user confirms.

## Consequences

- New pure, headless-tested modules: `copilot/resume.py`, `copilot/jobs.py`.
  GTK stays out of the core; redaction stays the single choke point — seeds,
  job titles, and naming contexts are all built from already-redacted data.
- Config gains `assistant.workspace` (legibility floor, soft pane ceiling, job
  gap, revert countdown).
- Cross-window pane moves add a `detach_pane`/`adopt_pane` surface to the tab
  and `eject`/`send`/rename actions to the window; the running process is the
  invariant these must preserve (pinned by GTK e2es asserting PID survival).
- Layout **persistence across app restarts** is explicitly out of scope here
  (no layout serialization yet); this ADR covers only in-session workspaces.
