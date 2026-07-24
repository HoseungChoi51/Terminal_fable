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

Entries are numbered in the order the plans were developed. Older copilot work
(phases P0–P5) predates this log and is covered by `copilot-development-plan.md`
plus ADRs 0005–0010.

| # | Plan | ADR | Status |
|---|------|-----|--------|
| [0001](0001-ask-mode-terminal-context.md) | Terminal output as ask-mode context (+ LLM A/B mode) | [0008](../decisions/0008-llm-provider-and-remote-gate.md) | Built |
| [0002](0002-workspaces.md) | Workspaces: episode resume, job grouping, live pane moves, naming | [0011](../decisions/0011-workspaces-and-live-pane-moves.md) | Built |
