# Design log

A running record of **what was asked and why**, one entry per planning effort.

This complements the two records already in the repo, which it does not
duplicate:

- **`docs/decisions/` (ADRs)** capture *accepted decisions* and their
  consequences — the "what we settled on".
- **`copilot-development-plan.md`** / **`AI_Integration_design.md`** are the
  broad up-front roadmaps.

What was missing was the **request itself**: the feature as the user asked for
it, rephrased/summarized during planning, together with the clarifying
questions and the choices made in answer to them. That context lived only in
the transient plan-mode scratch files, which are reused and overwritten. This
directory keeps it.

Each entry records, for one plan:

1. **Request** — the user's ask, quoted and then rephrased.
2. **Problem / context** — why it mattered.
3. **Decisions** — the clarifying questions and the answers that shaped scope.
4. **Approach** — the shape that was built.
5. **Status** — commits, ADRs, and what was deferred.

Entries are numbered roughly in the order the work was planned. Entries 0001–
0009 were **backfilled** (best-effort) after the log was created, reconstructed
from the ADRs, the roadmap docs (`copilot-development-plan.md`,
`AI_Integration_design.md`), git history, and memory — each notes its sources
and, where no verbatim request survives, says so. Entries 0008 onward include
the user's own words. The broad roadmap remains in `copilot-development-plan.md`.

| # | Plan | ADR | Status |
|---|------|-----|--------|
| [0001](0001-native-terminal-foundation.md) | Native terminal foundation (VTE, custom layout, pure core, smart-ls) | [0001](../decisions/0001-delegate-terminal-emulation-to-vte.md)–[0004](../decisions/0004-shared-tui-core-and-smart-ls.md) | Built |
| [0002](0002-copilot-p0-shell-integration-journal.md) | Copilot P0: shell integration + command journal + redaction | [0005](../decisions/0005-copilot-pure-core-package.md), [0007](../decisions/0007-bash-shell-integration.md) | Built |
| [0003](0003-copilot-p1-titles-sessions.md) | Copilot P1: tab titles + session history | [0009](../decisions/0009-session-persistence-format.md) | Built |
| [0004](0004-copilot-p2-recipes-menu.md) | Copilot P2: recipes + fuzzy search + risk classifier + menu | — | Built |
| [0005](0005-copilot-p3-context-typo.md) | Copilot P3: context heuristics + typo correction | — | Built |
| [0006](0006-copilot-p4-ghost-text.md) | Copilot P4: inline ghost-text completion | [0006](../decisions/0006-overlay-ghost-text-no-engine-replacement.md) | Built |
| [0007](0007-copilot-p5-llm-features.md) | Copilot P5: LLM features (provider + remote gate) | [0008](../decisions/0008-llm-provider-and-remote-gate.md) | Built |
| [0008](0008-in-place-ask-mode.md) | In-place ask mode (replacing the side panel) | [0010](../decisions/0010-in-place-ask-mode.md) | Built |
| [0009](0009-acp-evaluation.md) | Should we adopt ACP? | — | Decided against |
| [0010](0010-ask-mode-terminal-context.md) | Terminal output as ask-mode context (+ LLM A/B mode) | [0008](../decisions/0008-llm-provider-and-remote-gate.md) | Built |
| [0011](0011-workspaces.md) | Workspaces: episode resume, job grouping, live pane moves, naming | [0011](../decisions/0011-workspaces-and-live-pane-moves.md) | Built |
