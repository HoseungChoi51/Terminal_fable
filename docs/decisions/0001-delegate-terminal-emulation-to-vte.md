# 0001 — Delegate terminal emulation to VTE

- Status: Accepted
- Deciders: project authors
- Related: [architecture.md](../architecture.md) (TerminalPane), the rebuild
  plan's "Non-Goals"

## Context

A terminal application needs a terminal emulator: escape-sequence parsing,
scrollback, selection, true color, hyperlinks, keyboard/mouse handling, and a PTY
to spawn child processes. Implementing that correctly is an enormous, long-tail
effort — and getting it subtly wrong shows up as broken interactive programs
(editors, pagers, shells with fancy prompts).

The project's stated rule is that ordinary shell behavior must match GNOME
Terminal; if it diverges, that gets fixed before any richer feature. A
hand-written emulator works directly against that rule.

## Decision

Use **VTE (`Vte.Terminal`)** as the only terminal engine. `TerminalPane` wraps a
`Vte.Terminal` inside a `Gtk.ScrolledWindow` and delegates spawning, scrollback,
true color, copy/paste, selection, search, font zoom, cursor styling, and URL
matching to VTE. There is no fallback emulator; the dependency is hard and is
version-checked at startup (`load_gtk()` requires VTE 3.91, failing with a concrete
package hint).

## Consequences

- **Positive:** GNOME-Terminal-grade compatibility for free; the app's own code is
  about *layout, panes, and viewers*, not emulation. The MVP stays small.
- **Positive:** "Don't reimplement a terminal emulator" is an explicit non-goal in
  the rebuild plan, keeping scope honest.
- **Negative / accepted:** a hard system dependency on VTE introspection bindings
  (`gir1.2-vte-3.91`). This is why the launcher defaults to system Python rather
  than a virtualenv. The startup dependency error names the exact packages.
- **Negative / accepted:** behavior that VTE doesn't expose can't be added without
  upstream support; the app works within VTE's API surface.
