# Architecture Decision Records

Each ADR captures one significant, hard-to-reverse decision: the context, the
choice, and its consequences. They explain *why* the code looks the way it does so
that future changes don't accidentally undo a deliberate trade-off.

## Format

Short. Context → Decision → Consequences, plus a status. Use the existing records
as the template. Add a new ADR when you make a choice that constrains the rest of
the codebase (a dependency, a boundary, a data model, a protocol). Number them
sequentially and never rewrite history — supersede an old ADR with a new one and
mark the old one `Superseded by NNNN`.

## Records

| # | Title | Status |
| --- | --- | --- |
| [0001](0001-delegate-terminal-emulation-to-vte.md) | Delegate terminal emulation to VTE | Accepted |
| [0002](0002-custom-layout-widget.md) | Custom pane-layout widget instead of `Gtk.Paned` | Accepted |
| [0003](0003-pure-gtk-free-core.md) | Keep layout and app logic in a pure, GTK-free core | Accepted |
