# 0002 — Workspaces: episode resume, job grouping, live pane moves, naming

- Developed: 2026-07 · Status: Built · ADR: [0011](../decisions/0011-workspaces-and-live-pane-moves.md)
- Modules: `copilot/resume.py`, `copilot/jobs.py` (+ `native_terminal.py`)

## Request

> "Since we are recording per-episode, it will be great if I can hop on where I
> had left by restoring CWD and session's cmd history (that matches summarized
> episode context). Cmd history may not include the previous episode of the
> same session.
>
> Also, by comparing the period of active interaction, we may also suggest
> other tabs that were a part of the same job (One 'job' sometimes spreads over
> multiple terminal sessions: server on a tab, client on the other tab, coding
> in another, reviewing document yet another). In such cases, I believe having
> all related tabs open in one overarching window, by split panes … will steer
> user use split panes (desired usage) instead of opening multiple windows.
>
> 1. what if a job is complicated enough to spread over more panes than a
> display can hold … is it a good idea that we suggest arbitrarily concatenated
> arrangement of panes at all?
> 2. I need a mechanism to gather panes under a window or take one out from a
> window.
> 3. it will be great if LLM can automatically suggest the name of a window
> (name of a project/job), and name of each pane."

**Rephrased.** Turn the session/episode memory into a *workspace* model: (A)
resume the exact stretch of work (cwd + **episode-scoped** history, not the
whole session); (B) detect that several concurrent sessions were one **job** and
open them as **split panes in one window** to steer toward split-pane use; (C) a
way to **move panes between windows** (gather / eject) when a workflow diverges;
(D) **LLM-suggested** window and pane names from context.

## Problem / context

Resume was coarse (a tab at the last cwd, no history, whole-session grain). A
job that spanned concurrent sessions was invisible, so it scattered across tabs
and windows. Panes were trapped in their window. Names were auto-only, so
multi-pane windows were hard to read.

## Decisions (clarifying Q&A)

- **Scope / order** → *All four, staged A→B→C→D*, each shipped independently.
- **Moving a live pane** → *Live reparent, keep the process* (not respawn): the
  running shell, PTY, and scrollback come with it.
- **How assertive is grouping?** → the user's own answer: *"Move first and ask
  if user wants to revert (like display-resolution change)."* → grouping acts
  immediately, then shows a **Keep / Revert** bar that auto-reverts on a
  countdown.
- **Q1 — pane cap when a job exceeds one display** → the user's answer: *cap by
  pane **dimensions**, not a fixed count.* They run three setups (14" laptop,
  3440×1440, 3840×1600) and want pane width/height (info density) as the
  criterion. → a **legibility floor** (min cols×rows) measured against the
  target monitor, a soft ceiling ~6, and **spill into more balanced windows**
  rather than over-dividing one. So we suggest *bounded* arrangements, never
  arbitrary ones — answering Q1 as "no, only bounded".

## Approach

- **A — `resume.py`** (pure): segment a stored session into episodes; render an
  episode's commands as a bash history seed. The wrapper relocates `HISTFILE`
  onto a temp seed (`AGENT_TERMINAL_SEED_HISTFILE`) — ↑ recalls just that
  episode, nothing runs, global `~/.bash_history` untouched.
- **B — `jobs.py`** (pure): `cluster()` groups sessions whose active intervals
  overlap/near-touch (same-project = wider gap); `pack()` packs into the fewest
  balanced windows that respect the floor. Restore is act-then-revert
  (`_show_confirm_revert`).
- **C — live reparent**: `TerminalTab.detach_pane`/`adopt_pane` move a running
  VTE across windows (process/PTY/scrollback survive). Actions eject
  (Alt+Shift+E) / send (Alt+Shift+G).
- **D — naming**: manual `rename` (F2) overrides the inferred title and sticks;
  `llm.suggest_names` feeds the model each pane's **redacted digest** and
  returns names into an **editable** dialog (applied only on Apply).

## Status

Built and verified with real-bash GTK e2es per phase (episode recall with atuin
present; job restore into one split window + Revert; eject/send keep the PID
alive; a manual name survives a running command; the AI dialog applies).
Config: `assistant.workspace`. Recorded in ADR 0011.

**Deferred (not built):** layout persistence across app restarts (needs layout
serialization — none exists yet); auto-detecting a job the moment it forms;
live "gather-related" that pulls already-open job-mate panes together (the C
primitives exist; live-pane relatedness detection is the missing piece);
per-pane role hints from listening ports.
