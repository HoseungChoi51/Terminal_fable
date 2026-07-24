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

**Phase P3 — context awareness (shipped).** The menu now understands
where you are and what you are typing:

- *Project commands* — in a Python/Node/Rust/Go/Make/Just project, the
  menu suggests the right run/test commands (`pytest`, `uv run …`,
  `npm run dev`, `cargo test`), preferring commands it finds in your
  README, so typing `py` surfaces how to run *this* project.
- *Argument completion* — the menu completes the argument a command
  expects: directories after `cd`, files after `cat`, archives after
  `tar -xf`, media after `ffmpeg -i`, the newest `.deb` after
  `sudo apt install ./`, branches after `git checkout`, changed files
  after `git add`, hosts after `ssh`.
- *Typo correction* — a mistyped command or subcommand (`rysnc`,
  `kubectl detele`), a broken URL prefix (`ttps://`), or a path slip
  (`.../`) is offered as a "did you mean" fix. When a command fails
  with "command not found", a quiet chip suggests the correction; click
  to insert it (it never runs on its own). A correction that would make
  a command *more dangerous* is offered only in the menu, never in the
  one-click chip.

Everything here is local — no network, and directories are scanned only
when you open the menu, never in the background.

For a hands-on checklist of what to try and what to watch while
dogfooding this phase, see the
[P2 test guide](copilot-p2-test-guide.md).

**Phase P5 — the LLM assistant (shipped).** Two features that use a
language model, both **off by default** and gated:

- *Ask mode* — press **Ctrl+?** (or **Ctrl+Shift+/**) to turn the prompt
  itself into a chat with the model, without leaving the command line or
  splitting a pane. A bar slides in at the bottom of the window with an
  **⌁ ASK** badge (so it's obvious you've switched modes) and the model
  it's using; it's an ordinary in-window panel, so it never grabs the
  window or traps your typing — Esc or the ✕ closes it. If you had
  started typing a command, that half-typed line is **carried in as
  context and parked** (cleared from the shell), so you can ask "…how do
  I only keep files from the last day?" and the model answers knowing
  what you were reaching for. The answer is a ready-to-run command with a
  risk badge, and you decide what happens to it:
  - **Y** — *take* it onto the shell line (typed, **not run** — you still
    press Enter yourself).
  - **N** / **Esc** — *cancel*; the parked draft is put back.
  - **T** — *explain* it in place.

  When an answer appears, focus moves to it, so Y/N/T act on the answer
  and Enter never accidentally runs anything. To ask a **follow-up**
  ("…exclude node_modules") that refines the previous suggestion, click
  the entry (or Tab to it) and type — then Enter sends it. A
  multi-line answer is never fed to the shell — it is copied instead.
  Nothing runs on a keystroke unless you turn **auto-pilot** on, and even
  then a command auto-runs only if it clears every safety gate: risk at
  or below the ceiling (default `local-change`, never `unknown`),
  single-line, no substitution/redirection, **and** on a small curated
  allowlist of safe programs (read-only utilities plus `mkdir`/`touch`).
  Anything else — git subcommands, `find`, installers, anything the
  classifier can't vouch for — still waits for your Enter. Auto-pilot is
  `assistant.ask.auto_pilot` (off by default).

  Ask mode also sends a **distilled view of the task you're on** — the
  recent commands in this working session (an *episode*, split at idle
  gaps and directory changes) with the salient command's output **digested
  to its errors/summary** rather than the raw log, so answers are grounded
  in what actually happened without a 10k-line prompt. The ⌁ ASK bar
  header shows the task the copilot inferred
  (`task: project: cargo test · 6 min · 3 cmds · 2 failures`) so you can
  see what it's working from. How much output is included is
  `assistant.llm.send_output` (default `"digest"`; see below).

  A **status bar** along the window bottom always shows the attached
  model, abbreviated (e.g. `⌁ copilot: loki`). Click it or press
  **Ctrl+Shift+M** to open the **model picker** — it shows the full
  local-first chain and lists the models the primary endpoint advertises
  (e.g. a LiteLLM gateway fronting several backends); pick one to pin it
  for the session. **Ctrl+Shift+D** flips how the task context is built —
  the local heuristic digest vs. an LLM-written summary — so you can A/B
  the two live; the ⌁ ASK header shows which is active.
- *Session summaries* — **View → Session Summary…** recaps what you were
  doing in the current terminal; when you leave a session idle after
  real work, a quiet chip points you to it.

The model is reached through a single redacting choke point (ADR
[0008](decisions/0008-llm-provider-and-remote-gate.md)): context is
always secret-redacted before it is sent — including the carried draft
and your question. Endpoints are **local-first** — the copilot tries a
model on your machine or your local network before ever considering the
cloud, and only falls back to the next one when a closer one is
unreachable. The ask popover and the summary dialog show the whole chain
(with the gated cloud marked) and which endpoint answered.

### Endpoints and the privacy tiers

Each endpoint is classified by where it lives, and the privacy opt-in
applies only to the internet tier:

| Tier | Example host | Opt-in needed? |
| --- | --- | --- |
| on-device | `127.0.0.1`, `localhost` | no — data never leaves your machine |
| LAN | `192.168.x`, `10.x`, `*.local` | no — trusted local network |
| internet | `api.openai.com` | **yes** — `allow_remote_context` must be on |
| internet, `"trusted": true` | your own gateway on a public domain | no — you vouched for it |

So local and LAN models work out of the box; the cloud (OpenAI) is used
only as a last resort, and only after you turn
`assistant.llm.allow_remote_context` on. A private gateway that lives on
a public domain (e.g. a LiteLLM proxy fronting your local models) is
classified `internet` and gated by default — mark it `"trusted": true`
in its auth.json entry to use it freely without un-gating OpenAI too.
Redacted context still travels over the internet to reach it, so only
trust a host you control.

### Configuring endpoints (auth.json)

Connection info lives in **`auth.json`** — kept out of version control
(gitignored) because it holds API keys. Put it at
`~/.config/agent-terminal/auth.json`, or point at it with
`assistant.llm.auth_path` or the `AGENT_TERMINAL_AUTH_JSON` env var (a
copy in the repo root is also picked up in development).

```json
{
  "custom:local-llm-(192.168.210.210)": [
    { "label": "Loki (210)", "base_url": "http://192.168.210.210:8080/v1" }
  ],
  "GPT": [ { "key": "sk-…your OpenAI key…" } ]
}
```

- Each entry has a `base_url`; add `"model"` to pin one, otherwise the
  model name is discovered from the server (`GET /v1/models`). An
  OpenAI-compatible gateway (LiteLLM, etc.) that fronts several backends
  works as one entry — the model name selects the backend.
- A LAN/on-device entry needs no key. A `GPT`/`OpenAI` entry supplies the
  cloud key (and defaults `base_url` to OpenAI); its key is sent only in
  the `Authorization` header, never in the request or logs.
- Add `"trusted": true` to an internet-tier entry you control (e.g. a
  private gateway on a public domain) to un-gate that host without
  turning on the global cloud opt-in.
- Entries are ordered most-private first automatically; within a tier,
  usable (ungated/trusted) endpoints beat gated ones, then lower
  `"priority"` wins.

To turn on the cloud fallback, enable the opt-in in
`~/.config/agent-terminal/native.json`:

```json
{ "assistant": { "llm": { "allow_remote_context": true } } }
```

If no `auth.json` is found, the copilot falls back to a single endpoint
from the `llm.base_url`/`llm.model`/`llm.api_key_env` config keys.
Config changes apply to newly opened windows. If every endpoint is
unreachable, the panel shows the error and summaries fall back to the
heuristic text (labeled as such). For a quirky endpoint (e.g. Ollama's
Qwen models default to a slow "thinking" mode), `llm.system_suffix`
(such as `"/no_think"`) is appended to the system prompt.

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
            "allow_remote_context": false, "send_output": "digest",
            "digest_mode": "heuristic", "timeout_s": 30},
    "ask": {"enabled": true, "auto_pilot": false,
            "auto_pilot_max_risk": "local-change", "carry_draft": true,
            "max_turns": 8},
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
- `llm.allow_remote_context` — opt-in for the internet tier only; LAN/on-device work without it.
- `llm.auth_path` — path to auth.json (endpoint chain + keys); default searches the standard locations.
- `llm.base_url` / `llm.model` / `llm.api_key_env` — single-endpoint fallback when no auth.json.
- `llm.send_output` — how much terminal output ask mode sends as context:
  `"none"` (commands only), `"digest"` (a distilled, redacted digest of the
  salient command — the default), or `"full"` (the redacted verbose tail).
  Legacy booleans still parse (`false`→none, `true`→full).
- `llm.digest_mode` — *how* the salient command's output is distilled:
  `"heuristic"` (the local pure digester — fast, deterministic; default) or
  `"llm"` (an extra LLM call summarizes it — slower, richer). The two exist
  to be **A/B compared**: toggle between them at runtime with
  **Ctrl+Shift+D** (or *View ▸ Toggle Context Mode*); the active mode shows
  in the ⌁ ASK bar header (`ctx: heuristic` / `ctx: llm`). In `"llm"` mode
  the summarizer runs on a worker thread, stays inside the redaction choke
  point (its input is the already-redacted digest, its reply re-redacted),
  and falls back to the heuristic digest if no endpoint is eligible or the
  call fails. Orthogonal to `send_output` (which governs *how much*).
- `llm.system_suffix` — appended to the system prompt (endpoint quirks, e.g. `/no_think`).
- `llm.timeout_s` — per-request timeout (bounds each endpoint before falling to the next).
- `ask.enabled` — enable ask mode (Ctrl+?); on by default.
- `ask.auto_pilot` — press Enter for you after Take (default off; nothing runs without a keystroke otherwise).
- `ask.auto_pilot_max_risk` — highest risk that auto-runs when auto-pilot is on (`read-only`, `local-change`, …); riskier and `unknown` still wait for Enter.
- `ask.carry_draft` — carry the half-typed shell line into the request as redacted context (default on).
- `ask.max_turns` — conversation turns kept for follow-up context.
- `resume.enabled` / `resume.idle_minutes` — the idle session-summary chip.

Sessions are stored under `$XDG_DATA_HOME/agent-terminal/sessions/`
(see ADR [0009](decisions/0009-session-persistence-format.md)); every
stored command is redacted. Endpoint keys live only in `auth.json`
(gitignored) — see *Configuring endpoints* above.
