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
| P3 context heuristics | ⬜ next | Project/README awareness, argument completion, typo hints (local, no LLM) |
| P4 ghost text | ⬜ planned | Inline overlay suggestion + prompt tracker (local, no LLM) |
| P5 LLM assistant | ✅ shipped (early, for dogfooding) | Intent panel, session summaries, endpoint indicator |
| P6 learning/polish | ⬜ planned | Acceptance tracking, personalized ranking |

## What you can use today

| Feature | How | Notes |
| --- | --- | --- |
| Command journal | automatic | Per pane: command, cwd, exit code, duration, redacted output tail. Inspect: **View ▸ Copilot Journal (Debug)** |
| Auto tab titles | automatic | `project: command`; damped; program titles (vim/ssh) win while running |
| Session history | **Ctrl+Shift+S** | Restore = new tab at last cwd + summary pane; "Insert last command" types, never runs |
| Command menu | **Ctrl+Shift+Space** | Recipes + your history, fuzzy search, risk badges; Enter inserts **without newline** |
| Assistant panel | **Ctrl+Shift+P** | Natural language → command templates (insert/copy/explain); needs the LLM gate on |
| Session summary | **View ▸ Session Summary…** | LLM-polished when remote is on, heuristic otherwise; footer names the source |
| Idle resume chip | automatic | After `resume.idle_minutes` of idleness following real work |
| Pause one pane | **Alt+Shift+A** | Stops journaling + menu for that pane; toggle to resume |

## LLM endpoints (P5)

Off by default (`assistant.llm.allow_remote_context: false`). The
assistant panel and summary dialog always show **`model @ host`** so the
active endpoint is visible. Three known profiles (full JSON in
[`docs/copilot.md`](../docs/copilot.md)):

| Endpoint | base_url | Notes |
| --- | --- | --- |
| OpenAI cloud | `https://api.openai.com/v1` (default) | needs `OPENAI_API_KEY` |
| Office server | `http://192.168.210.210:8080/v1` | no key; office network only |
| Local Ollama | `http://127.0.0.1:11434/v1` | no key; set `"system_suffix": "/no_think"` (Qwen thinking mode); see `~/local-llm/README.md` |

Dogfooding plan: large cloud model now → smallest workable local model
later; the swap is config-only (`base_url` + `model`).

## Safety invariants (hold everywhere, pinned by tests)

1. **Nothing ever runs a command.** Insertion feeds text without a
   newline; you press Enter.
2. **Leading-space commands are never recorded** (enforced in the shell
   snippet and again in the parser).
3. **Everything stored is secret-redacted** (keys, tokens, passwords,
   URL credentials, private keys).
4. **Nothing leaves the machine unless `allow_remote_context` is true**,
   and every remote payload is redacted again; `urllib` exists only in
   `copilot/llm.py`.

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
| `llm.py` | P5 | OpenAI-compatible client, ContextGate, redacting choke point |

Tests: `tests/test_copilot_{core,sessions,suggest,llm}.py` (all headless;
run `python3 -m unittest discover -s tests`).

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
