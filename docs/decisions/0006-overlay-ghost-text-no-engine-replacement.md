# 0006 — Grid-aligned overlay ghost text on VTE; no engine replacement

- Status: Accepted
- Deciders: project authors
- Related: [copilot-development-plan.md](../../copilot-development-plan.md),
  [0001](0001-delegate-terminal-emulation-to-vte.md) (VTE for emulation)

## Context

The copilot spec wants inline "ghost text" suggestions — dim text after
the cursor that the user can accept, like fish/zsh autosuggestions.
VTE (through 0.84) has no API to write suggestion cells into its grid,
so the question was whether to replace the terminal engine to get one.

A survey of the alternatives found that **no embeddable engine offers
app-driven in-grid ghost text**: VS Code (xterm.js) renders suggestions
as DOM overlays or detects shell-rendered autosuggest text; libghostty
is the only new-generation embeddable core but its C API is pre-stable,
it has no Python/GI bindings, and its GTK widget layer is explicitly
"longer term"; QTermWidget would mean abandoning GTK entirely; headless
VT libraries (libvterm, alacritty_terminal, pyte) hand you a model and
make you write the renderer — which is building a new engine. Every
production implementation of ghost text is an overlay or shell-side.

## Decision

Render ghost text as a **grid-aligned overlay on VTE**: a dim label in
the terminal's own font, positioned at the cursor cell using
`get_cursor_position` and the character cell metrics, via the existing
`Gtk.Overlay` pattern. Do not replace the terminal engine. The
suggestion engine lives in the pure copilot core, so only the thin
rendering layer is VTE-specific.

libghostty is recorded as the candidate to re-evaluate once its GTK
widget layer and a stable C API ship; if adopted later, the pure
suggestion core is reused and only the overlay renderer is rewritten.

## Consequences

- Ghost text ships with no migration risk and no new dependency; it is
  visually equivalent to in-grid rendering for the prompt line.
- It must hide on any doubt (alternate screen, scroll, resize, wrapped
  lines) because it is painted over VTE, not owned by it — this is a
  hard requirement of the P4 design, not a limitation to paper over.
- The decision is revisitable without touching suggestion logic.
