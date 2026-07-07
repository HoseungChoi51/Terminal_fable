# Copilot Development Plan

Phased implementation roadmap for the context-aware terminal copilot
specified in [`AI_Integration_design.md`](AI_Integration_design.md),
adapted to this repository's architecture
([`docs/architecture.md`](docs/architecture.md)), conventions
([`docs/extending.md`](docs/extending.md), ADR 0003), and the constraint
that this tree is the author's daily terminal: **every commit stays
launchable, and anything that can disturb normal terminal use lands
behind a config flag that defaults off or fails open.**

The design document is a product-requirements spec written without
knowledge of this repo. This plan maps its features onto what the repo
can actually do, phase by phase. Section references (§) point into the
design document.

---

## 1. Settled decisions

These were researched and decided up front; the ADRs listed in §8
record them permanently.

### 1.1 LLM backend: OpenAI API over stdlib urllib

Zero-pip-dependency constraint holds: the OpenAI client is written
against `urllib.request`, behind a small provider interface so other
backends can be added later. Local-first: every heuristic feature
(titles, sessions, recipes, context completions, typo correction) works
with no network at all. Only the intent panel and summary polish call
the API. Remote context is config-gated **off by default** and passes
through unconditional secret redaction.

### 1.2 Inline suggestions: grid-aligned overlay ghost text on VTE

VTE (through 0.84) has no decoration/annotation API, and a survey of
alternatives found **no existing framework that provides app-driven
in-grid ghost text**:

| Option | Ghost-text API | Verdict |
| --- | --- | --- |
| VTE + grid-aligned overlay | Build (small): overlay label at the cursor cell, terminal's own font | **Chosen** — zero migration, engine-agnostic core |
| libghostty (Ghostty's embeddable core) | None (client-side anyway); C API alpha, GTK widget layer "longer term", no Python bindings | Future re-evaluation candidate (ADR 0006) |
| xterm.js + WebKitGTK | Decorations API — but itself overlay-DOM/shell-detected (VS Code's approach) | Rewrite that lands on the same technique; rejected |
| QTermWidget (Qt) | None; requires abandoning GTK entirely | Rejected |
| Headless VT libs (libvterm, alacritty_terminal, pyte) | You write the renderer | Excluded: that is developing a new engine |

The requirement is therefore restated: *inline suggestions render as
dim overlay text pixel-aligned to the character grid*
(`get_cursor_position` × `get_char_width/height`, the existing
`Gtk.Overlay` pattern), hidden instantly on any doubt. The suggestion
engine lives in the pure core, so a future engine migration would swap
only the rendering layer.

### 1.3 Shell integration: auto-injected at spawn

Command tracking uses VTE 0.84's shell-integration termprops
(`vte.shell.precmd` / `preexec` / `postexec`, detailed
`termprop-changed` signal). The terminal spawns bash with a wrapper
rcfile that sources `~/.bashrc` first, then idempotently installs the
hooks. A config flag and an `AGENT_TERMINAL_NO_INTEGRATION=1` env
variable disable injection; a documented manual snippet covers
non-bash shells; without integration everything degrades to cwd-only
features (OSC 7 already works).

### 1.4 Verified integration points

All confirmed by introspection/code reading before planning:

- `TerminalPane._spawn` — controllable argv, existing `extra_env` hook.
- VTE termprops + detailed `termprop-changed`; `commit` signal
  (keystroke stream); `get_text`/`get_text_range`;
  `get_cursor_position` + char-cell metrics; `feed_child` (unused so
  far); `current-directory-uri` (OSC 7, already consumed).
- `ShortcutHintMixin` overlay-chip pattern; `MarkdownPane` as the
  new-pane-kind precedent; `Gtk.SearchBar` strip; `PaneLeaderWindow`
  transient keyboard UI; action/accelerator registry.
- `NativeConfig` + tolerant `load_native_config`; JSON-line control
  socket; pure helpers `parse_markdown_blocks`,
  `tui_core.scan_directory`, ranking style of `smart_ls.sort_entries`.

---

## 2. Module layout

A new pure-core package plus one GTK factory module, mirroring the
ADR 0004 precedent (`tui_core.py`/`smart_ls.py` extraction). Wiring
added to `native_terminal.py` stays thin (~300–500 lines total across
all phases).

**Import rule (ADR 0005): `native_terminal.py` imports `copilot.*`;
no copilot module ever imports `native_terminal`.** Existing pure
helpers are injected as parameters. `copilot/ui.py` exposes
`build_copilot_classes(g, deps)` and imports no GTK at module level;
every other copilot module is GTK-free and headless-testable.

```
agent_terminal/copilot/
    __init__.py            (P0)  docstring only
    config.py              (P0)  AssistantConfig dataclass tree + tolerant parser
    redact.py              (P0)  secret redaction ruleset (pure)
    journal.py             (P0)  CommandRecord, PaneJournal ring, integration state
    shellintegration.py    (P0)  wrap-argv decision + rcfile generation (pure)
    shell/
        agent-terminal.bash (P0) shipped idempotent bash integration snippet
    titles.py              (P1)  title inference from the journal (pure)
    sessions.py            (P1)  session model, XDG JSON store, retention, heuristic summary
    fuzzy.py               (P2)  subsequence/token scorer (shared: recipes, typo)
    risk.py                (P2)  risk classifier (pure)
    recipes.py             (P2)  Recipe model + builtin recipes + search
    suggest.py             (P2)  Suggestion model, source merge/rank engine
    context.py             (P3)  project detection, README extraction, arg-aware rules
    typo.py                (P3)  high-confidence corrections
    prompt.py              (P4)  PromptTracker state machine
    llm.py                 (P5)  provider interface, OpenAI urllib client, ContextGate
    ui.py                  (P1+) GTK factory: session browser, completion menu,
                                 chips, ghost overlay, IntentPane
tests/
    test_copilot_core.py       (P0)   test_copilot_sessions.py (P1)
    test_copilot_suggest.py    (P2)   test_copilot_context.py  (P3)
    test_copilot_prompt.py     (P4)   test_copilot_llm.py      (P5)
docs/
    copilot.md            user-facing doc, grows each phase
    decisions/0005..0009  see §8
```

---

## 3. Cross-cutting designs

### 3.1 Config: one `assistant` section in `~/.config/agent-terminal/native.json`

`NativeConfig` gains one field, `assistant: AssistantConfig` (frozen
dataclass tree in `copilot/config.py`, parsed tolerantly — missing or
invalid keys fall back to defaults, per extending.md recipe 4).

```json
{
  "assistant": {
    "enabled": true,
    "shell_integration": true,
    "journal": {"max_commands": 200, "store_output": true, "output_tail_lines": 20},
    "titles": {"enabled": true, "min_interval_s": 30},
    "sessions": {"enabled": true, "retention_days": 30,
                 "exclude_dirs": [], "exclude_commands": [], "store_output": true},
    "suggestions": {"menu": true, "ghost_text": false,
                    "typo_correction": true, "min_confidence": 0.7},
    "recipes": {"enabled": true, "user_recipes_path": null},
    "llm": {"provider": "openai", "model": "gpt-4.1-mini",
            "api_key_env": "OPENAI_API_KEY",
            "allow_remote_context": false, "send_output": false, "timeout_s": 30},
    "resume": {"enabled": true, "idle_minutes": 30}
  }
}
```

Defaults encode the dogfood policy: local *pull* features default on
(menu, titles, sessions); *push*/remote features default off
(`ghost_text`, `allow_remote_context`). Redaction on the remote path
is **not configurable** — it always runs; only local storage honors the
`store_output` toggles.

### 3.2 Journal (per-pane in-memory ring, `copilot/journal.py`)

```python
CommandRecord = {
  "seq": int,                 # per-pane monotonic
  "cmd": str | None,          # typed command, post-redaction
  "cwd": str | None,          # at preexec
  "started_at": float, "duration_s": float | None,
  "exit_code": int | None,    # from vte.shell.postexec
  "output_tail": tuple[str, ...] | None,   # ≤ output_tail_lines, redacted
  "capture": "termprop" | "screen" | "none",   # provenance/confidence
}
```

`PaneJournal` also tracks live, unpersisted state: integration level
(`"integrated"` after the first precmd termprop, else `"cwd-only"`),
the prompt anchor (cursor row/col at precmd), alternate-screen flag,
and per-pane paused flag. Commands typed with a leading space are never
recorded (bash `HISTCONTROL` convention).

### 3.3 Sessions (`$XDG_DATA_HOME/agent-terminal/sessions/<id>/`)

One directory per meaningful session: `session.json` + `summary.md`
(a real markdown file, so restore reuses `MarkdownPane` unchanged).
Listing is readdir + head-reads — no index file to corrupt. Retention
sweep on startup. Exclusions: prefix-matched `exclude_dirs` discard the
session; `exclude_commands` patterns drop individual records.
Meaningful = ≥3 non-trivial commands, or 1 non-trivial command with
duration ≥ 5 s (trivial: ls, cd, clear, pwd, exit, top, …).

### 3.4 Redaction (`copilot/redact.py`)

`redact_line(text) -> (text, hit)` applied at the storage boundary and
again — unconditionally — at the single remote choke point in `llm.py`.
Deliberately over-broad rules: secret-ish key/value assignments and
flags (`(?i)(pass(word)?|secret|token|api[_-]?key|auth|bearer|credential|private[_-]?key|cookie)`),
URL userinfo, known token shapes (AWS `AKIA…`, GitHub `ghp_…`, Slack
`xox…`, `sk-…`, JWTs, PEM private-key blocks), `Authorization:`/`Cookie:`
header values, long base64/hex blobs adjacent to secret-ish keys.
Tested against a corpus of true positives and benign near-misses.

### 3.5 Risk classifier (`copilot/risk.py`, design doc §8.3)

`classify(command) -> RiskResult`. Labels: `read-only`, `local-change`,
`install`, `remote`, `privileged`, `destructive`, `unknown`; severity
order `destructive > privileged > remote > install > local-change >
read-only`. Token-based rules per pipeline segment, max severity wins;
`sudo` recurses into the wrapped command; `curl … | sh` is
install+destructive; unmatched commands are `unknown` and treated
conservatively (never ghost-texted). Consumers: recipe labels and menu
badges (P2), ghost gating (P4), intent-panel labels (P5).

### 3.6 Actions and keybindings

None touch `RESERVED_PLAIN_ACCELERATORS`; conventions kept
(Ctrl+Shift = window commands, Alt+Shift = pane-scoped):

| Action | Accelerator | Phase |
| --- | --- | --- |
| `copilot-menu` | `Ctrl+Shift+Space` | P2 |
| `copilot-sessions` | `Ctrl+Shift+S` | P1 |
| `copilot-panel` | `Ctrl+Shift+P` | P5 |
| `copilot-pause` (active pane) | `Alt+Shift+A` | P2 |
| `copilot-debug` (journal dump) | menu/socket only | P0 |

Ghost accept/dismiss are **not** accelerators: a
`Gtk.EventControllerKey` on the terminal consumes `Right` (only when a
ghost is visible and the cursor sits at end of typed input),
`Ctrl+Right` as the unambiguous fallback, and `Escape` (only while a
ghost is visible; ghosts never render on the alternate screen, so TUI
Escape is unaffected). With no ghost visible, the controller claims
nothing.

---

## 4. Phases

Ordering rationale: titles ship before any suggestion UI because they
are the *proving ground* for journal correctness — read-only, visible,
zero input-path risk. The menu (pull; a bug costs a keystroke) ships
before ghost text (push; a bug corrupts the visual terminal). The menu
does **not** need live keystroke tracking — a one-shot screen read
between the precmd anchor and the cursor yields the typed prefix,
robust against readline editing; the continuous `PromptTracker` is
needed only for ghost text and lives entirely in P4.

### P0 — Foundation: shell integration, journal, redaction, config

**Goal.** The terminal knows, per pane, what command ran, where, with
what exit code and duration — secrets scrubbed — and nothing about
daily use changes. Demoable via a debug dump.

- Injection wraps only default-shell bash spawns (never `--command`):
  argv becomes `["/bin/bash", "--rcfile", <snippet>]`. The snippet
  sources `~/.bashrc` first (guarded), sources the system `vte.sh` if
  present, installs PROMPT_COMMAND/DEBUG-trap hooks emitting OSC 7 +
  precmd/preexec/postexec, everything `|| true`-guarded, idempotent via
  a guard variable, and honors `AGENT_TERMINAL_NO_INTEGRATION=1`.
- `TerminalPane` connects `termprop-changed::vte.shell.*` and feeds a
  pure `PaneJournal`. Command text: `get_text_range` between the precmd
  anchor and the preexec cursor. Output tail: between preexec and the
  next precmd. Open item: whether a user termprop can carry
  `$BASH_COMMAND` from the DEBUG trap (preferred); screen-read is the
  fallback; the `capture` field records provenance either way.
- `copilot-debug` renders the journal as markdown to
  `$XDG_RUNTIME_DIR` and opens it via the existing `open_path`.
- Tests: config parsing, redaction corpus, ring/degradation semantics,
  wrap-argv decision table, snippet source-level guardrails.
- **Gate: one-week dogfood soak** (plain/starship prompts, ssh
  (degrades), htop (alt-screen ignored), nested bash) before P1 builds
  on the data.
- ADRs 0005, 0006, 0007. Deferred: persistence, UI, zsh/fish.

### P1 — Auto titles + session store + heuristic summaries + restore

**Goal.** Tabs name themselves; meaningful sessions persist and can be
listed, summarized, and restored.

- Title inference (`project: command` from journal + cwd +
  long-running process), damped by `min_interval_s`, changes only on
  precmd boundaries; user-set OSC titles always win. Feeds the existing
  `update_tab_title` path.
- Session flush on pane dispose + periodic checkpoint (crash safety);
  browser window (`Ctrl+Shift+S`, PaneLeader-style list); restore =
  `add_terminal_tab(working_directory=…)` + `open_path(summary.md)`;
  rerun of a past command = `feed_child` **without newline**.
- Tests: inference table, no-flapping, schema round-trip + tolerant
  reads of corrupt files, retention/exclusion, meaningfulness, summary
  snapshots. ADR 0009.
- Deferred: LLM summaries, idle chip, live reattach (explicit non-goal
  §5.8).

### P2 — Recipes, fuzzy search, risk classifier, completion menu

**Goal.** `Ctrl+Shift+Space` opens a menu at the cursor with ranked,
source-labeled, risk-badged suggestions (builtin recipes + own
history), fuzzily searchable; Enter inserts via `feed_child` with
**no newline, ever**.

- Pure: fuzzy scorer, risk classifier (§3.5), ~50 builtin recipes with
  §4.4 metadata, `Suggestion` model + merge/rank engine, insert-suffix
  math (insert only what's beyond the typed prefix).
- GTK: transient undecorated menu at the cursor cell (geometry shared
  with P4), typed-prefix via one-shot anchor→cursor screen read, filter
  entry, risk badge CSS; `copilot-pause` + paused chip.
- Tests: §5.9 query set for ranking; classifier table incl. every §3.3
  example; metadata completeness; merge/dedupe; prefix-strip; extended
  action/accelerator contract tests.
- Verification: §14 demo item 11; menu opens ≤50 ms; insertion never
  executes.

### P3 — Context heuristics: project, README, recent files, arguments, typos

**Goal.** The menu gets smart; §14 demo items 1–7 pass.

- Project detection (pyproject/uv.lock/package.json/Cargo.toml/
  Makefile/justfile/.git → type + name). README run-command extraction
  consumes **pre-parsed** `parse_markdown_blocks` output (injected —
  keeps the import rule). Recent-file argument completion via
  `tui_core.scan_directory(with_stat=True)`, mtime-sorted, extension
  filters (the `sudo apt install ./` → newest `.deb` case). §5.4
  argument-rule table for the MVP command list (cd→dirs, cat→files,
  tar -xf→archives, ffmpeg -i→media, git checkout→branches, git add→
  changed files, ssh→known hosts) with guide-text fallbacks.
- Typo correction, split by risk: post-failure "did you mean" chip on
  exit-127 (ShortcutHintMixin pattern; inserts, never runs) plus
  menu-rank corrections. Safety rule: **a correction that raises the
  risk class is menu-only, never one-keystroke** (§5.5's
  `kubectl detele` case).
- Directory scans on menu-open only — no background watching.
- Deferred: clipboard-URL suggestion for `git clone` (P4), manpage
  semantics, learned guides.

### P4 — Ghost text: prompt tracker + inline top suggestion (default OFF)

**Goal.** The highest-confidence suggestion renders as dim,
grid-aligned ghost text at the cursor; `Right`-at-end / `Ctrl+Right`
accepts; `Escape` dismisses; it vanishes on any doubt.

- `PromptTracker` (pure): state machine over `commit` bytes +
  precmd/preexec + alt-screen/scroll/resize events; IDLE →
  AT_PROMPT(typed, col) → EXECUTING; any unmodelable input (arrows,
  Ctrl+R, tab-completion output) → DIRTY until the next precmd.
- Ghost renders **only** in clean AT_PROMPT after a one-shot
  screen-read cross-check, with score ≥ `min_confidence`, prefix ≥ 2
  chars, and risk ∈ {read-only, local-change} — destructive,
  privileged, and unknown commands never ghost (§8.4).
- Overlay label in the terminal's exact font at the cursor cell; hides
  instantly on `contents-changed`-without-commit, scroll, resize,
  focus-out, alternate screen. Accept = `feed_child(suffix)`, no
  newline.
- Ships default-off; long personal dogfood soak (vim/htop/tmux, paste,
  resize mid-typing, wrapped lines, reverse-i-search) before any
  default flip (a P6, data-driven decision).

### P5 — LLM: provider, intent side panel, NL templates, resume summaries

**Goal.** A new `IntentPane` turns natural language into placeholder
command templates with explanations and risk labels (insert / copy /
explain — never auto-run); resume summaries gain LLM polish and the
idle chip. The full §14 demo passes.

- Pure: `Provider` protocol; `OpenAIProvider` over `urllib.request`
  (key from `api_key_env`, model configurable); request builders
  assemble context (cwd, project type, redacted recent commands, README
  commands; output tails only if `send_output`) and **always re-redact
  the final payload**; response parsing to templates + placeholders
  (`<input_video>` style); `ContextGate` — the single enforcement
  point; remote off ⇒ zero network attempts.
- GTK: `IntentPane(PaneBase)` per extending.md recipe 2, opened by
  `Ctrl+Shift+P` as a split; network on a worker thread with a hard
  timeout, results via `GLib.idle_add`; insert targets the most recent
  terminal pane via `feed_child`, no newline. Idle-resume chip
  (heuristic text when remote off; LLM text when on).
- Tests: request shape with a fake opener (no real network); gate
  tests; payload-redaction reuse of the P0 corpus; placeholder parsing;
  pane-surface contract test; source guardrail pinning `urllib` to
  `copilot/llm.py`.
- Deferred (per §7): interactive slot filling, workflows, streaming,
  "run when safe", other providers.

### P6 — Learning and polish

Local counters (accept/dismiss per source, recipe usage, typo undo)
under `$XDG_DATA_HOME/agent-terminal/assistant-stats.json`;
frequency/recency ranking boosts (bounded: a learned boost can never
promote a destructive item into ghost text); guide retention;
data-driven decision on the `ghost_text` default; documentation
completion pass. Post-roadmap: team-shared recipes, manpage semantics,
autonomous execution, live session reattach, libghostty re-evaluation
(per ADR 0006).

---

## 5. Risk register

| # | Risk | Mitigation |
| --- | --- | --- |
| 1 | rcfile injection breaks the daily-driver shell | Source `~/.bashrc` first with guards; wrap only default-shell bash spawns, never `--command`; env + config kill switches; silent cwd-only degradation; one-week P0 soak gate |
| 2 | Secret leakage to disk or the API | Redaction at both boundaries (storage + unconditional remote choke point); `allow_remote_context` default off; leading-space skip, exclusions, retention; corpus tests; urllib-confined-to-llm.py guardrail test |
| 3 | Ghost overlay corrupts the terminal experience | Tracker-clean-state + screen cross-check + hide-on-any-doubt; default off until P6 data; per-pane pause; no key stolen while hidden |
| 4 | Wrong command capture poisons downstream features | `capture` provenance flags; alt-screen/non-integrated records excluded from inference; titles-first ordering as the low-risk canary |
| 5 | UI noise / main-loop jank | Heavy work only on menu-open or worker threads; confidence gating; title damping; per-feature/per-pane/master switches; P6 metrics make annoyance measurable |

## 6. Success criteria

Design doc §11 applies. The cumulative acceptance test is the §14
twelve-step demo, fully passing at the end of P5. The most important
negative metric: surprising or unwanted command modification — target
zero (nothing in this plan ever executes or rewrites a command without
an explicit user keystroke on a visible suggestion).

## 7. Verification policy (uniform per phase)

1. Full headless suite green: `python3 -m unittest discover -s tests`.
2. App builds headlessly: `build_native_classes(load_gtk())`.
3. Live dogfood check of the phase's demo scenario in a fresh window.
4. Phase-specific soak gates as listed (P0 and P4 especially).

## 8. ADRs to write

| # | Title | Phase |
| --- | --- | --- |
| 0005 | Copilot as a pure-core package with a GTK factory; one-way import rule | P0 |
| 0006 | Grid-aligned overlay ghost text on VTE; no engine replacement; libghostty as the future re-evaluation candidate | P0 |
| 0007 | Bash shell integration via auto-injected idempotent rcfile + termprops; manual snippet + cwd-only degradation | P0 |
| 0008 | LLM provider interface with a stdlib-urllib OpenAI backend; local-first, gated + redacting remote choke point | P5 |
| 0009 | Session persistence format (XDG data dir, dir-per-session JSON + summary.md, retention/exclusion) | P1 |
