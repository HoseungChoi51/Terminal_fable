# 0010 — In-place ask mode instead of an assistant side panel

- Status: Accepted
- Deciders: project authors
- Related: [copilot-development-plan.md](../../copilot-development-plan.md),
  [0008](0008-llm-provider-and-remote-gate.md) (gated redacting choke point),
  [0005](0005-copilot-pure-core-package.md) (pure copilot core)
- Supersedes the P5 side-panel UX (the `IntentPane` surface)

## Context

P5 first shipped the LLM "ask" surface as a **separate split pane**
(`IntentPane`, Ctrl+Shift+P): describe a goal, get command templates.
Dogfooding it as a daily driver, a second pane felt distracting and
broke the sense of working in one place — the terminal is *both* a shell
and, when you want it, a place to talk to a model. What was wanted was a
quick **mode switch** at the prompt, not another window.

Two forces shaped the replacement. First, safety: this surface is one
keystroke from the shell, so it must never run a command the user did
not ask to run. Second, privacy: the same gated, redacting LLM chain
(ADR 0008) must cover a new kind of context — the half-typed command the
user was reaching for.

## Decision

Replace the side panel with an **in-place ask mode**: a floating popover
anchored at the prompt cursor (**Ctrl+?**, also Ctrl+Shift+/), driving a
pure `AskSession` state machine in the GTK-free core
(`copilot/ask.py`). The GTK layer only renders and wires it.

- **Seed carry + park.** On open, the current shell input line is read
  from the prompt tracker, carried into the request as a redacted
  `draft command:` context line, and parked (Ctrl-U clears it) so the
  conversation starts clean. Cancel restores the parked draft; Take does
  not.
- **Take, don't run.** An answer is *taken* onto the shell line with no
  trailing newline. Auto-run is opt-in (`assistant.ask.auto_pilot`, off
  by default) and even then only for commands at or below a risk ceiling
  (`local-change`) and never `unknown`. `can_take` also refuses any
  multi-line command (a newline would run everything up to it) — those
  are copied instead.
- **One guarded submit path.** `_ask_commit` is the single place in the
  app that may feed a submit byte (`\r`), and only under `if run:`. A
  source-guardrail test forbids any other newline/CR feed.
- **Hotkey disambiguation.** Y/N/T act as one-key take/cancel/explain
  only while the entry is empty; once you type, they are ordinary
  characters and Enter sends a follow-up that refines the last
  suggestion (multi-turn).
- **A conversation surface, not a menu.** The popover pops up (so the
  autohide grab keeps it in front) and then drops autohide, so it
  survives the async gap while the model answers instead of
  self-dismissing on focus churn; Escape / Cancel / Take close it, and
  the focused entry captures typing so keystrokes never leak into the
  shell behind it.

The side-panel action, its Ctrl+Shift+P accelerator, `IntentPane`, and
`show_intent_panel` are removed; the LLM logic they held moves into the
overlay. Ctrl+? is freed from the shortcut-help binding (which keeps
Ctrl+Shift+H + F1).

## Consequences

- One place to work: no pane split, and the prompt is both shell and
  chat. The trade-off is that ask mode is modal (a popover) rather than a
  persistent pane you can leave open beside your work.
- The safety story is small and testable: single-line-only Take,
  auto-pilot capped by risk, one guarded submit helper, and a guardrail
  test — plus a live GTK e2e (real VTE, stubbed LLM) that asserts seed
  parking, no-newline Take, single-CR auto-run, and Cancel restoring the
  draft.
- Privacy is unchanged in shape: everything still flows through the ADR
  0008 choke point, now including the carried draft and the question.
- `Ctrl+?` depends on the layout naming the shifted slash `question`; the
  raw `Ctrl+Shift+/` binding is kept alongside it for robustness.
