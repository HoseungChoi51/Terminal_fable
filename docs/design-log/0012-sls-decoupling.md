# 0012 — Should `sls` be split out of the terminal? — Keep it, but decouple

- Developed: 2026-07 · Status: Built · ADR: [0004](../decisions/0004-shared-tui-core-and-smart-ls.md) (amended)

## Request

> "Is sls part of the working terminal? I get 'sls' not found. Since sls is a
> stand-alone exec, shouldn't I separate it from the terminal itself? Review
> design decisions and determine if we should ditch this from the terminal or
> not."

**Rephrased.** `sls` (smart-ls) isn't runnable from the daily run-from-tree
setup. It's meant to be a standalone tool — so should it be separated from the
terminal, and if so how far?

## Findings

- **"Not found"** was purely distribution: `sls` exists as a `[project.scripts]`
  console script (needs `pip install`) and as `bin/sls` (works, but `bin/` isn't
  on PATH). Running the terminal from the tree installs neither.
- **Coupling.** The terminal has **zero** dependency on sls. sls had a small leak
  the other way: it imported `CONTROL_SOCKET_ENV` + two path predicates from
  `native_terminal`, which transitively imported the entire ~5000-line GTK-shell
  module (and copilot) into a curses tool — even though no GTK is used.

## Decision

**Do not split it into a separate package/repo.** It genuinely shares `tui_core`
with the file picker (which lives here), the control-socket protocol and viewer
integration are co-developed, and a separate repo adds CI/versioning/release
overhead for a small tool. "Standalone" is a *runtime* property (no GTK, runs
over SSH) — best served by fixing the dependency graph, not the repo boundary.

**Keep it in the repo, but decouple:**

1. Move the shared symbols (`CONTROL_SOCKET_ENV`, `MARKDOWN_EXTENSIONS`,
   `IMAGE_EXTENSIONS`, `is_markdown_path`, `is_image_path`) into `tui_core`;
   `native_terminal` and `tui_navigation` consume them from there;
   `native_terminal` re-exports for back-compat.
2. Result: importing `smart_ls` no longer imports `native_terminal` or `gi` —
   verified. The standalone property is now enforced by the dependency graph.
3. Distribution for the run-from-tree workflow: symlink
   `~/.local/bin/sls → bin/sls` (tree stays the source of truth).

## Status

Built; 501 tests green. ADR 0004 amended. The optional cd-on-exit `sls()` shell
function (from `docs/smart-ls.md`) is left for the user to add to `~/.bashrc`.
