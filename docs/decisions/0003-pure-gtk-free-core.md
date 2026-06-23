# 0003 — Keep layout and app logic in a pure, GTK-free core

- Status: Accepted
- Deciders: project authors
- Related: [architecture.md](../architecture.md) (pure core vs GTK shell),
  [developer-guide.md](../developer-guide.md) (testing)

## Context

GTK/VTE code can only run with a display and the PyGObject bindings installed.
If the app's logic — layout math, configuration parsing, Markdown parsing, the
control protocol — were entangled with widgets, none of it could be unit-tested
in CI or on a headless machine, and every behavioral change would require manual
GUI verification. Layout in particular has fiddly invariants (normalization,
weight sums, minimum sizes, focus geometry) that beg for fast automated tests.

## Decision

Maintain a hard boundary between a **pure core** and a **GTK shell**:

- The pure core (config, the `layout_*` engine, Markdown/image helpers, the action
  surface, and the control-message codec) imports **no GTK**. It sits at the top of
  `agent_terminal/native_terminal.py`, above `build_native_classes()`.
- GTK/VTE are imported lazily by `load_gtk()` and only when a GUI is actually
  created. The GTK shell — built inside `build_native_classes()` — calls into the
  core, never the reverse.
- The layout model is immutable: operations return new `LayoutNode` trees, which
  also makes undo/redo a plain stack of snapshots.

As a result, importing the module needs no display, and `tests/test_native_terminal.py`
/ `tests/test_tui_navigation.py` exercise the real logic headlessly.

## Consequences

- **Positive:** fast, deterministic unit tests for splitting, closing, focusing,
  resizing, balancing, parsing, and the control protocol — no GUI required.
- **Positive:** bugs are reproducible as pure-core tests, and refactors are safe
  behind that test wall.
- **Positive:** the immutable tree makes undo/redo and the ephemeral focused-fit
  view trivial and side-effect-free.
- **Negative / accepted:** contributors must respect the boundary — logic goes in
  the core, not in widget callbacks. The discipline is a convention reinforced by
  where the tests can reach; new logic added inside the GTK shell is, by
  definition, untested.
