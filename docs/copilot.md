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

**Phase P1 — titles and session history (shipped).** Two features
built on the journal:

- *Auto tab titles.* Each tab names itself `project: command` — the
  working directory's basename plus a short summary of the most recent
  meaningful command (for example `terminal-fable: pytest`). Titles are
  damped so they never flicker, and a title set by a running program
  (vim, ssh, htop) wins while that program is in the foreground.
- *Session history.* When a meaningful session ends (roughly: three or
  more non-trivial commands, or one that ran a while), it is saved to
  disk. Press **Ctrl+Shift+S** — or **View → Session History…** — to
  browse past sessions. *Restore* opens a new tab in the session's last
  directory alongside a markdown summary of what you did; *Insert last
  command* types that session's last command into the current terminal
  (without running it). A trivial session (a couple of `ls`es and an
  `exit`) is not stored.

**Phase P2 — command menu (shipped).** Press **Ctrl+Shift+Space** to
open a command menu at the cursor: a searchable, ranked list of
built-in recipes and commands from your own history, each tagged with a
**risk badge** (read-only, local-change, install, remote, privileged,
destructive). Type to filter; press Enter or click to insert the chosen
command onto your prompt. Insertion **never runs anything** — it clears
the current input line and types the command in, leaving you to press
Enter yourself. **Alt+Shift+A** pauses the copilot for the current pane
(stops journaling and hides the menu); press it again to resume.

Recipes cover the common needs — "sort by size", "kill process on
port", "unzip all files", "split video into frames", git/docker/kubectl
workflows — with `<placeholder>` slots you fill in before running.

For a hands-on checklist of what to try and what to watch while
dogfooding this phase, see the
[P2 test guide](copilot-p2-test-guide.md).

**Phase P5 — the LLM assistant (shipped).** Two features that use a
language model, both **off by default** and gated:

- *Intent side panel* — press **Ctrl+Shift+P** to open a panel, describe
  a goal in plain language ("split a video into frames"), and get back
  command templates with `<placeholder>` slots and risk badges. Each can
  be **inserted** onto your prompt (never run), **copied**, or
  **explained**. Each tab has at most one panel: pressing Ctrl+Shift+P
  again jumps to it (close it like any pane, Ctrl+Shift+W).
- *Session summaries* — **View → Session Summary…** recaps what you were
  doing in the current terminal; when you leave a session idle after
  real work, a quiet chip points you to it.

The model is reached through a single gated, redacting path (ADR
[0008](decisions/0008-llm-provider-and-remote-gate.md)): nothing is sent
unless you turn `assistant.llm.allow_remote_context` on, and everything
sent is secret-redacted first. The client speaks the OpenAI API against
a configurable `base_url`, so it works with OpenAI now and a local
OpenAI-compatible server (Ollama, llama.cpp, vLLM) later by config alone.

### Enabling the assistant

Turn the gate on in `~/.config/agent-terminal/native.json` and pick an
endpoint. The assistant panel and the summary dialog always display
**which server and model** the copilot is talking to (`model @ host`),
so you can tell at a glance which of your endpoints is active.

**OpenAI cloud** (needs `export OPENAI_API_KEY=…`):

```json
{ "assistant": { "llm": {
    "allow_remote_context": true,
    "model": "gpt-4.1"
} } }
```

**Local Ollama** (`~/local-llm`, no key; Qwen models default to thinking
mode — `/no_think` in the system suffix disables it on the
OpenAI-compatible endpoint):

```json
{ "assistant": { "llm": {
    "allow_remote_context": true,
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "qwen3.5:4b",
    "system_suffix": "/no_think"
} } }
```

**Office server** (no key, office network only):

```json
{ "assistant": { "llm": {
    "allow_remote_context": true,
    "base_url": "http://192.168.210.210:8080/v1",
    "model": "default"
} } }
```

Config changes apply to newly opened windows. If the endpoint is down or
unreachable, the panel shows the error and summaries fall back to the
heuristic text (labeled as such).

**Phase P4 — inline ghost text (shipped, default off).** With
`assistant.suggestions.ghost_text` on, as you type at the prompt the
most likely completion from your own history appears as dim text right
after the cursor, in the terminal's font. Press **Right** (or
**Ctrl+Right**) to accept it — only the completion is typed in, never a
newline, so nothing runs — or **Esc** to dismiss it. It shows only at a
clean prompt and vanishes the moment anything is uncertain (you scroll,
resize, run a program, use arrows/Ctrl-R/Tab, or the screen no longer
matches what it thinks you typed). It never completes a destructive,
privileged, or unknown command. Recipes are included only if you lower
`suggestions.min_confidence` below `0.7`.

This is the newest and least-proven feature — it is default-off pending
a dogfooding soak (vim/tmux/paste/resize/wrapped lines). Enable it with:

```json
{ "assistant": { "suggestions": { "ghost_text": true } } }
```

Later phases (context-aware suggestions) are described in the
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

**A note on a possible brief flash.** The integration works by having
the shell emit small marker escape sequences (OSC 666 termprops) each
time you run a command. VTE 0.84 occasionally *paints* one of these
markers for a single frame before it parses and removes it, so you may
rarely glimpse a fragment like `]666;…` at the cursor when you press
Enter. The terminal content is never actually affected — it is a
display-only quirk of VTE, and the markers must use the ST terminator
(VTE ignores the flash-free BEL form for termprops). The snippet keeps
its markers as short and as few writes as possible to minimize this. If
it still bothers you, set `assistant.shell_integration: false` (or
`AGENT_TERMINAL_NO_INTEGRATION=1`) to stop emitting the markers
entirely; command-based features then degrade to working-directory
only.

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
    "journal": {"max_commands": 200, "store_output": true, "output_tail_lines": 20},
    "titles": {"enabled": true, "min_interval_s": 30},
    "sessions": {"enabled": true, "retention_days": 30,
                 "exclude_dirs": [], "exclude_commands": [], "store_output": true},
    "suggestions": {"menu": true},
    "recipes": {"enabled": true},
    "llm": {"provider": "openai", "base_url": "https://api.openai.com/v1",
            "model": "gpt-4.1-mini", "api_key_env": "OPENAI_API_KEY",
            "allow_remote_context": false, "send_output": false,
            "timeout_s": 30},
    "resume": {"enabled": true, "idle_minutes": 30}
  }
}
```

- `enabled` — master switch for all copilot features.
- `shell_integration` — auto-inject the bash rcfile at spawn.
- `journal.max_commands` — ring size of remembered commands per pane.
- `journal.store_output` — whether to keep the redacted output tail.
- `journal.output_tail_lines` — how many trailing output lines to keep.
- `suggestions.menu` — enable the Ctrl+Shift+Space command menu.
- `suggestions.ghost_text` — inline history completion at the cursor (default off).
- `suggestions.min_confidence` — threshold for ghost text (lower to include recipes).
- `recipes.enabled` — include built-in recipes in the menu.
- `titles.enabled` — infer tab titles from the journal.
- `titles.min_interval_s` — minimum seconds between title changes (anti-flicker).
- `sessions.enabled` — save and browse session history.
- `sessions.retention_days` — delete stored sessions older than this.
- `sessions.exclude_dirs` — directories whose sessions are never saved.
- `sessions.exclude_commands` — glob patterns whose commands are dropped before saving.
- `sessions.store_output` — whether saved sessions keep command output.
- `llm.allow_remote_context` — master gate; nothing goes to the model unless true.
- `llm.base_url` / `llm.model` / `llm.api_key_env` — endpoint, model, and key variable.
- `llm.send_output` — also send redacted command output as context (default off).
- `llm.system_suffix` — appended to the system prompt (endpoint quirks, e.g. `/no_think`).
- `llm.timeout_s` — per-request timeout.
- `resume.enabled` / `resume.idle_minutes` — the idle session-summary chip.

Sessions are stored under `$XDG_DATA_HOME/agent-terminal/sessions/`
(see ADR [0009](decisions/0009-session-persistence-format.md)); every
stored command is redacted, and nothing leaves your machine. The
remaining sections (`suggestions`, `recipes`, `llm`, `resume`) are
parsed and reserved for later phases.
