# 0007 — Bash shell integration via an auto-injected rcfile + termprops

- Status: Accepted
- Deciders: project authors
- Related: [copilot.md](../copilot.md),
  [copilot-development-plan.md](../../copilot-development-plan.md)

## Context

Every context feature of the copilot needs to know, per pane, what
command ran, where, and with what exit status. VTE 0.84 exposes this
through shell-integration termprops (`vte.shell.precmd` / `preexec` /
`postexec`), but only if the shell emits the matching OSC 666
sequences. The shell must therefore be taught to emit them, without
the user editing dotfiles and without disturbing their existing setup.

## Decision

When spawning the default shell and that shell is bash, the terminal
swaps `[bash]` for `[bash, --rcfile, <wrapper>]`. The generated wrapper
sources the user's `~/.bashrc` first, then the shipped snippet
(`agent_terminal/copilot/shell/agent-terminal.bash`). The snippet is
idempotent, interactive-bash-only, and reports each command via a
custom `vte.ext.agentterm.cmd` termprop carrying the base64 of
`history 1`.

The decision **fails open at every step**: injection is skipped for
explicit `--command` spawns, non-bash shells, when
`assistant.shell_integration` is false, when
`AGENT_TERMINAL_NO_INTEGRATION=1` is set, or when the snippet is
missing; any error returns the original argv. Without integration the
terminal still has OSC 7, so features degrade to cwd-only rather than
breaking.

Privacy: a command typed with a leading space is never reported. The
snippet enforces this at the emission layer (it does not depend on the
user's `HISTCONTROL`, which a history framework such as bash-preexec
may have rewritten), and the Python parser rejects such commands again
as a second layer.

## Consequences

- Zero-setup command tracking for the common case (bash), with a
  documented manual snippet for other shells or disabled injection.
- The wrapper is the single point where a broken integration could
  disturb a daily-driver shell, so it is deliberately minimal and
  `~/.bashrc` always wins.
- Command capture depends on `history 1`; multi-line and PS2 cases
  carry a `capture` provenance flag on each record so later phases can
  discount low-confidence captures.
