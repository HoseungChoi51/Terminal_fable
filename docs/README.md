# Documentation

Documentation for the native GTK4/VTE terminal (launched by
`bin/agent-terminal-native`). Start with the entry point that matches what you're
trying to do.

## I want to…

### …use the terminal

- **[native-terminal-mvp.md](native-terminal-mvp.md)** — usage guide: how to run
  it, the keyboard shortcuts, the GNOME-Terminal comparison checklist, and
  troubleshooting.
- **[../README.md](../README.md)** — project overview, requirements, install, and
  options at a glance.

### …understand how it works

- **[architecture.md](architecture.md)** — the design: the pure-core / GTK-shell
  boundary, the layout engine and pane model, actions and shortcuts, the control
  socket, theming, and the invariants the code relies on. **Read this first** if
  you're going to change anything.
- **[decisions/](decisions/README.md)** — Architecture Decision Records explaining
  *why* the big choices were made (VTE for emulation, a custom layout widget, the
  GTK-free core).

### …contribute to it

- **[developer-guide.md](developer-guide.md)** — set up the toolchain, run the app,
  run the headless test suite, the coding conventions/invariants, and the
  commit/release workflow.
- **[extending.md](extending.md)** — cookbook recipes: add an action + shortcut, a
  new pane type, a palette, a config option, a control-socket command, or a layout
  operation.

### …rebuild it from scratch

- **[../native-terminal-reproducible-development-plan.md](../native-terminal-reproducible-development-plan.md)**
  — the phased plan and architecture map for reproducing the terminal from zero.

## Map

```
docs/
  README.md                     ← you are here (documentation index)
  native-terminal-mvp.md        usage guide & shortcuts
  architecture.md               design & architecture overview
  developer-guide.md            setup, running, testing, conventions, workflow
  extending.md                  cookbook recipes for adding features
  decisions/
    README.md                   ADR index
    0001-…-vte.md               delegate terminal emulation to VTE
    0002-custom-layout-widget.md  custom pane container, not Gtk.Paned
    0003-pure-gtk-free-core.md  keep logic in a GTK-free, testable core
```
