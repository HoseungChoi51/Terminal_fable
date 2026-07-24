# 0003 — Copilot P1: tab titles + session history

- Developed: copilot roadmap · Status: Built · ADR: [0009](../decisions/0009-session-persistence-format.md)
- Sources: **reconstructed** from `copilot-development-plan.md`, ADR 0009, and git (`984d01a`, `6b601d8`, `acd3519`).

## Request (reconstructed)

Use the journal to (a) give tabs meaningful, stable titles that say what you're
doing, and (b) remember past sessions so you can look back at — and return to —
earlier work.

## Problem / context

Default tab titles are noise (the shell's last window title). And when a
terminal is closed, everything it knew is lost. A meaningful session (real work,
not a couple of `ls`es) is worth persisting with a human-readable summary.

## Decisions

- **Infer titles from the journal** with damping (`TitlePolicy`) so the title
  doesn't flicker on every command.
- **Persist sessions to disk** as a directory per session under the XDG data
  dir (ADR 0009): `session.json` + a real `summary.md` (so restore can reuse the
  Markdown viewer pane unchanged). Everything stored is already redacted.
- **Meaningfulness gate**: 3+ non-trivial commands, or one that ran a while.

## Approach

`copilot/titles.py` (project:command inference + `TitlePolicy`) and
`copilot/sessions.py` (build/store/list/sweep). **Ctrl+Shift+S** opens a session
browser; restore opens a new tab at the session cwd + its summary.

**Key gotcha (recorded):** termprop batches flush from `idle_add`, so a
synchronous `window-title-changed` handler sees a *stale* journal state; the
title decision is deferred into the same idle flush (`_flush_pending`) so prompt
boilerplate isn't misread as a program title.

## Status

Built. Later extended by design-log [0010](0010-ask-mode-terminal-context.md)
(episodes) and [0011](0011-workspaces.md) (episode-scoped resume + job grouping
read these stored sessions).
