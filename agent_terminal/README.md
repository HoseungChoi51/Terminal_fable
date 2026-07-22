# Terminal Copilot — current state (temporary README)

> **Point-in-time snapshot, 2026-07-14.** This file is a working guide
> while the copilot is under active development and will be removed or
> folded into [`docs/copilot.md`](../docs/copilot.md) (the living doc)
> once the roadmap completes. Roadmap:
> [`copilot-development-plan.md`](../copilot-development-plan.md);
> product spec: [`AI_Integration_design.md`](../AI_Integration_design.md).

## Where we are

| Phase | Status | Delivers |
| --- | --- | --- |
| P0 foundation | ✅ shipped | Shell integration, per-pane command journal, secret redaction, config |
| P1 titles + sessions | ✅ shipped | Auto `project: command` tab titles; session history/restore |
| P2 command menu | ✅ shipped | Ctrl+Shift+Space fuzzy menu, risk badges, per-pane pause |
| P3 context heuristics | ✅ shipped | Project/README awareness, argument completion, typo hints (local, no LLM) |
| P4 ghost text | ✅ shipped (default off) | Inline history completion at the cursor + prompt tracker (local, no LLM) |
| P5 LLM assistant | ✅ shipped (early, for dogfooding) | In-place ask mode, session summaries, local-first endpoint chain |
| P6 learning/polish | ⬜ planned | Acceptance tracking, personalized ranking |

## What you can use today

| Feature | How | Notes |
| --- | --- | --- |
| Command journal | automatic | Per pane: command, cwd, exit code, duration, redacted output tail. Inspect: **View ▸ Copilot Journal (Debug)** |
| Auto tab titles | automatic | `project: command`; damped; program titles (vim/ssh) win while running |
| Session history | **Ctrl+Shift+S** | Restore = new tab at last cwd + summary pane; "Insert last command" types, never runs |
| Command menu | **Ctrl+Shift+Space** | Recipes + history + context (project/README, argument completion, typo fixes), risk badges; Enter inserts **without newline** |
| "Did you mean" chip | on a not-found command | Suggests the typo fix; click inserts it, never runs it |
| Ask mode | **Ctrl+?** (or Ctrl+Shift+/) | Chat at the prompt: the half-typed line is carried in + parked; answer is a command you **take** (Y, no newline), **cancel** (N/Esc, restores the draft), or **explain** (T). Click the entry to type a follow-up. Auto-pilot (off by default) auto-runs only safe, no-substitution commands. Needs the LLM gate/endpoints |
| Session summary | **View ▸ Session Summary…** | LLM-polished when remote is on, heuristic otherwise; footer names the source |
| Ghost text | type at prompt | Dim inline completion from your history; **Right**/**Ctrl+Right** accepts, **Esc** dismisses; default off (`suggestions.ghost_text`) |
| Idle resume chip | automatic | After `resume.idle_minutes` of idleness following real work |
| Pause one pane | **Alt+Shift+A** | Stops journaling + menu for that pane; toggle to resume |

## LLM endpoints (P5)

**Local-first fallback chain**, configured in `auth.json` (gitignored;
holds keys). Endpoints are tried most-private first; the cloud is a
last resort. The privacy opt-in (`allow_remote_context`, default off)
gates only the internet tier — local and LAN work without it. The panel
and summary show the chain and which endpoint answered.

| Tier | Example | Gated? |
| --- | --- | --- |
| on-device | `127.0.0.1`, `localhost` | no |
| LAN | `192.168.x`, `10.x`, `*.local` | no |
| internet | `api.openai.com` | yes — needs `allow_remote_context` |

`auth.json` at `~/.config/agent-terminal/auth.json` (or `llm.auth_path`
/ `AGENT_TERMINAL_AUTH_JSON` / repo root). Current chain (from the
user's file): `Loki (210) → hulk (205) → GPT (gated)`. LAN model names
are discovered from the server (`GET /v1/models`); keys are sent only
in the `Authorization` header. Full JSON format in
[`docs/copilot.md`](../docs/copilot.md).

## Safety invariants (hold everywhere, pinned by tests)

1. **Nothing ever runs a command.** Insertion feeds text without a
   newline; you press Enter.
2. **Leading-space commands are never recorded** (enforced in the shell
   snippet and again in the parser).
3. **Everything stored is secret-redacted** (keys, tokens, passwords,
   URL credentials, private keys).
4. **Nothing reaches an internet endpoint unless `allow_remote_context`
   is true** (on-device/LAN are trusted); every payload is redacted
   first, keys ride only the `Authorization` header, and network
   `urllib` exists only in `copilot/llm.py`.

## Module map (`agent_terminal/copilot/`)

Pure core, GTK-free, one-way imports (`native_terminal` → `copilot.*`,
never back — ADR 0005). GTK wiring lives in `native_terminal.py`.

| Module | Phase | Purpose |
| --- | --- | --- |
| `config.py` | P0 | `AssistantConfig` tree + tolerant parser |
| `redact.py` | P0 | Secret redaction (lines + PEM blocks) |
| `journal.py` | P0 | Command journal state machine (termprop batches) |
| `shellintegration.py` + `shell/agent-terminal.bash` | P0 | rcfile injection + the bash snippet |
| `titles.py` | P1 | Title inference + damping |
| `sessions.py` | P1 | Session build/store/list/sweep (XDG data dir) |
| `fuzzy.py` `risk.py` `recipes.py` `suggest.py` | P2 | Scorer, risk classifier, builtin recipes, merge/rank |
| `context.py` `typo.py` | P3 | Project/README/argument context, typo correction |
| `prompt.py` | P4 | Prompt-line state machine for ghost text |
| `auth.py` | P5 | Parse auth.json into a tiered, ordered endpoint chain |
| `llm.py` | P5 | OpenAI-compatible client, ContextGate, redacting choke point |

Tests: `tests/test_copilot_{core,sessions,suggest,context,prompt,llm}.py`
(all headless; run `python3 -m unittest discover -s tests`).

## Known quirks / rough edges

- **Marker flash:** VTE 0.84 may paint a shell-integration marker
  (`]666;…`) for one frame on Enter. Display-only, mitigated, cannot be
  fully eliminated on our side; kill switch:
  `assistant.shell_integration: false`.
- **Command menu over full-screen apps:** opening it inside vim/htop
  inserts into that app. Avoid until P4 gates it automatically.
- **Repeated commands with `HISTCONTROL=ignoredups`:** a consecutive
  re-run creates no new history entry, so its journal record has
  `cmd: null` (capture `none`).
- **Live LLM round-trips** were verified against a stubbed provider;
  first real call against each endpoint is still to be dogfooded.

Dogfooding checklist for the menu:
[`docs/copilot-p2-test-guide.md`](../docs/copilot-p2-test-guide.md).
Config reference and endpoint setup: [`docs/copilot.md`](../docs/copilot.md).
