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
| [0004](0004-shared-tui-core-and-smart-ls.md) | Share one TUI core; ship smart-ls as a standalone subprocess | Accepted |
| [0005](0005-copilot-pure-core-package.md) | Copilot as a pure-core package with a one-way import rule | Accepted |
| [0006](0006-overlay-ghost-text-no-engine-replacement.md) | Grid-aligned overlay ghost text on VTE; no engine replacement | Accepted |
| [0007](0007-bash-shell-integration.md) | Bash shell integration via an auto-injected rcfile + termprops | Accepted |
| [0008](0008-llm-provider-and-remote-gate.md) | LLM provider: OpenAI-compatible, behind a gated redacting choke point | Accepted |
| [0009](0009-session-persistence-format.md) | Session persistence: a directory per session under the XDG data dir | Accepted |
| [0010](0010-in-place-ask-mode.md) | In-place ask mode instead of an assistant side panel | Accepted |
| [0011](0011-workspaces-and-live-pane-moves.md) | Workspaces: episode resume, job grouping, live pane moves, naming | Accepted |
| [0012](0012-detachable-panes-pty-daemon.md) | Detachable panes via an in-tree PTY daemon | Accepted |
