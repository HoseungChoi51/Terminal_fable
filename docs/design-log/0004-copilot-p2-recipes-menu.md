# 0004 — Copilot P2: recipes + fuzzy search + risk classifier + completion menu

- Developed: copilot roadmap · Status: Built
- Sources: **reconstructed** from `copilot-development-plan.md` and git (`1e87708`, `5b0775b`, `3f6035f`).

## Request (reconstructed)

Surface useful commands at the cursor — from history and a built-in recipe book
— ranked well, and label how dangerous each is before it runs.

## Problem / context

The journal knows your history; a recipe book knows common tasks. Together they
can offer completions, but only if they're ranked sensibly (recency matters) and
if the user can see risk (a `read-only` vs a `destructive` command) at a glance.

## Decisions

- **A per-segment risk classifier** (`risk.py`): read-only … destructive, max
  severity wins, aware of `sudo`, `curl | sh`, and git/docker subcommands.
- **A fuzzy scorer** (`fuzzy.py`): subsequence + multi-word tokens, with a
  **recency bonus** so history relevance isn't drowned by a length penalty.
- **~45 built-in recipes** (`recipes.py`) merged with history in `suggest.py`,
  deduped and recency-ranked; commands are inserted **without a trailing
  newline** (never auto-run).

## Approach

**Ctrl+Shift+Space** opens a completion popover at the cursor cell; **Alt+Shift+A**
toggles journal pause.

**Key gotchas (recorded):** headless popovers auto-close (offscreen), so the
accept logic was extracted to `_accept_suggestion` for testability; and the
fuzzy length-penalty was overriding history recency until a per-step recency
bonus was added.

## Status

Built. The risk classifier is reused later by ask mode's auto-run gate; the
fuzzy scorer is reused by ask-mode salience (design-log
[0010](0010-ask-mode-terminal-context.md)).
