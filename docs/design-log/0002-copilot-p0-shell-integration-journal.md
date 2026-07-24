# 0002 — Copilot P0: shell integration + command journal + redaction

- Developed: copilot roadmap · Status: Built · ADRs: [0005](../decisions/0005-copilot-pure-core-package.md), [0007](../decisions/0007-bash-shell-integration.md)
- Sources: **reconstructed** from `copilot-development-plan.md`, `AI_Integration_design.md`, ADRs, and git (`04d3588`, `3208604`, `1c2ddb1`).

## Request (reconstructed)

Give the terminal a memory of what the user does — the commands run, their
outcomes, and their output — as the foundation for every later assistant
feature. It must work out of the box (no manual shell config) and never leak
secrets.

## Problem / context

An assistant that understands "what you're doing" needs a structured record of
commands and results. The terminal can only see raw bytes; it needs the shell
to mark command boundaries. And whatever is recorded may later be sent to a
model, so redaction must be built in from the start, not bolted on.

## Decisions

- **A pure copilot core package** (`agent_terminal/copilot/`, ADR 0005): all
  assistant logic is GTK-free and stdlib-only, imported one-way from the shell.
- **Bash shell integration, auto-injected at spawn** (ADR 0007): a generated
  `--rcfile` wrapper sources `~/.bashrc` then the shipped snippet, which emits
  VTE OSC 666 termprops (`vte.shell.preexec/postexec/precmd`, and the command
  as base64 in `vte.ext.agentterm.cmd`). Fails open; disable with a kill switch.
- **Redaction as a single choke point** — output and commands are redacted
  before they are stored.

## Approach

`copilot/{config,redact,journal,shellintegration}.py`; `wrap_argv` injects the
rcfile in `TerminalPane._spawn`; a per-pane `PaneJournal` is fed by
`termprop-changed`. A "Copilot Journal (Debug)" menu action dumps it.

**Key gotcha (recorded):** VTE coalesces one termprop burst into a single signal
batch whose within-batch order is *not* the emission order, so
`PaneJournal.apply_batch` re-sorts to canonical order
(preexec → cmd → postexec → precmd).

## Status

Built and dogfooded. This journal is what episodes, sessions, digests, and
naming all read from.
