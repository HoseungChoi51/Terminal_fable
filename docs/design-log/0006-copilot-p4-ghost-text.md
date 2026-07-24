# 0006 — Copilot P4: inline ghost-text completion

- Developed: copilot roadmap (built out of order, after P5) · Status: Built · ADR: [0006](../decisions/0006-overlay-ghost-text-no-engine-replacement.md)
- Sources: **reconstructed** from `copilot-development-plan.md`, ADR 0006, and git (`14c46f5`, `1539a95`, `1c4e427`).

## Request (reconstructed)

Show inline, greyed-out completions at the cursor (fish/zsh-autosuggest style)
that you accept with →, without swapping out VTE for a custom engine.

## Problem / context

Inline suggestions want to paint dim text *inside* the grid at the cursor. VTE
owns the grid and doesn't expose an overlay text API. The alternative — writing
a new terminal engine — was weighed and **rejected on cost** (ADR 0006).

## Decisions

- **Overlay ghost text on VTE, no engine replacement** (ADR 0006): a dim label
  positioned at the cursor cell via `get_cursor_position` × char-cell metrics,
  inside a `Gtk.Overlay`. libghostty is named as a future re-evaluation
  candidate, not a current dependency.
- Ghost text is **off by default** and only offers safe, single-line, non-
  destructive history completions above a confidence threshold.

## Approach

`copilot/prompt.py` (`PromptTracker` state machine) + `suggest.ghost_completion`.
→ / Ctrl+→ accept (feed the suffix, no newline); Esc dismisses.

**Key gotchas (recorded):** `feed_child` itself fires the `commit` signal, so
ghost-accept must not also advance the tracker (double count); and a screen
cross-check (`visible.endswith(typed)`) is the real drift guard, not
`invalidate()`.

## Status

Built (out of order, after P5, at the author's request to dogfood LLM features
first). Off by default per the dogfooding-safety stance.
