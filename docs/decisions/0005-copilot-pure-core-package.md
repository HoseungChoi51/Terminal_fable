# 0005 — Copilot as a pure-core package with a one-way import rule

- Status: Accepted
- Deciders: project authors
- Related: [copilot-development-plan.md](../../copilot-development-plan.md),
  [0003](0003-pure-gtk-free-core.md) (pure, GTK-free core),
  [0004](0004-shared-tui-core-and-smart-ls.md) (module extraction precedent)

## Context

The terminal copilot (see the development plan) adds roughly a dozen
cohesive units of logic — config, redaction, a command journal,
shell-integration decisions, and later titles, sessions, recipes,
suggestions, an LLM client — plus one block of GTK glue. Folding all of
it into the already 3300-line `native_terminal.py` would destroy the
top-to-bottom "pure core → GTK factory" readability that
`architecture.md` documents. ADR 0004 already established that a
separable concern with its own pure layer earns its own module.

## Decision

Copilot logic lives in a new package `agent_terminal/copilot/`. Every
module there is GTK-free and headless-testable; the one GTK piece is a
factory (`copilot/ui.py`, `build_copilot_classes(g, deps)`) that
imports GTK lazily, mirroring `build_native_classes`.

**Import direction is one-way: `native_terminal` imports `copilot.*`;
no `copilot` module ever imports `native_terminal`.** Where copilot
logic needs an existing pure helper (for example
`parse_markdown_blocks`), the helper is injected as a parameter rather
than imported, which keeps the dependency acyclic and the copilot core
importable on its own.

## Consequences

- The copilot core is unit-tested with zero GTK, exactly like the rest
  of the pure core, and `native_terminal` stays thin (a config field,
  a spawn hook, a signal connection, an action).
- A future GTK-free consumer (a CLI, a different front end) can import
  `copilot.*` without pulling in the terminal.
- Contributors must resist reaching back into `native_terminal` from
  copilot; the injection rule is the escape hatch when a helper is
  genuinely shared.
