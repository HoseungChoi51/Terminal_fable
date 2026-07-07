# Terminal Copilot

The copilot is a context-aware assistant being built into the terminal
in phases (see
[`copilot-development-plan.md`](../copilot-development-plan.md)). This
page documents what has shipped and how to configure it.

Everything lives in the pure-core package `agent_terminal/copilot/`
(ADR [0005](decisions/0005-copilot-pure-core-package.md)); the terminal
adds only thin wiring. All behavior is off-by-default where it could
surprise you, and configurable.

## Status

**Phase P0 — foundation (shipped).** The terminal keeps a per-pane
*command journal*: for each command it records the command line, the
working directory, exit status, duration, and a short tail of output,
with secrets redacted. Nothing about normal terminal use changes; the
journal is the substrate later phases build titles, sessions,
suggestions, and summaries on. Inspect it with **View → Copilot Journal
(Debug)**, which dumps the active pane's journal into a markdown pane.

Later phases (titles, session history, completion menu, context
suggestions, ghost text, the LLM intent panel) are described in the
development plan.

## How command tracking works

Command tracking uses VTE's shell-integration termprops. For bash, the
terminal injects the integration automatically: it launches
`bash --rcfile <wrapper>`, where the wrapper sources your `~/.bashrc`
first and then the shipped snippet
(`agent_terminal/copilot/shell/agent-terminal.bash`). See ADR
[0007](decisions/0007-bash-shell-integration.md) for the full
rationale.

Auto-injection is skipped for explicit `--command` launches, for
non-bash shells, when disabled in config, and when
`AGENT_TERMINAL_NO_INTEGRATION=1` is set. Without integration the
terminal still tracks the working directory (OSC 7), so directory-based
features keep working.

**Privacy.** A command typed with a leading space is never recorded —
this is enforced in the shell snippet itself, so it does not depend on
your `HISTCONTROL`. All stored command text and output is passed through
secret redaction (API keys, tokens, passwords, URL credentials, private
keys). Nothing is ever sent off your machine in this phase.

### Manual setup (non-bash, or auto-injection disabled)

If you disable auto-injection, or use bash outside this terminal, add
the snippet to your shell startup yourself:

```bash
# ~/.bashrc
[ -f /path/to/agent_terminal/copilot/shell/agent-terminal.bash ] && \
    . /path/to/agent_terminal/copilot/shell/agent-terminal.bash
```

The snippet is bash-only and idempotent; sourcing it twice is
harmless.

## Configuration

All settings live under an `"assistant"` key in
`~/.config/agent-terminal/native.json`. Every key is optional and
missing or invalid values fall back to the defaults shown here:

```json
{
  "assistant": {
    "enabled": true,
    "shell_integration": true,
    "journal": {"max_commands": 200, "store_output": true, "output_tail_lines": 20}
  }
}
```

- `enabled` — master switch for all copilot features.
- `shell_integration` — auto-inject the bash rcfile at spawn.
- `journal.max_commands` — ring size of remembered commands per pane.
- `journal.store_output` — whether to keep the redacted output tail.
- `journal.output_tail_lines` — how many trailing output lines to keep.

Additional sections (`titles`, `sessions`, `suggestions`, `recipes`,
`llm`, `resume`) are parsed and reserved for later phases; see the
development plan for their meaning.
