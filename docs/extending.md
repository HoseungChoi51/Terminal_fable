# Extending the Terminal — Cookbook

Step-by-step recipes for the common ways this app grows. Each recipe lists the
exact places to touch in `agent_terminal/native_terminal.py` and what to test.
Read [`architecture.md`](architecture.md) for the structure these recipes assume,
and follow the invariants in the [developer guide](developer-guide.md).

A theme runs through every recipe: **put the logic in the pure core, add a thin
GTK binding, and write the test against the pure core.**

---

## 1. Add an action + keyboard shortcut

Goal: a new command reachable by a key.

1. **Declare it.** Add the name to `ACTION_NAMES` (or `APP_LEVEL_ACTIONS` if it
   acts on the application rather than a window).
2. **Bind it.** Add an entry to `ACCELERATORS`, mapping the action name to a tuple
   of GTK accelerator strings (e.g. `("<Alt><Shift>j",)`). Use `Alt+Shift` for
   pane management and `Ctrl+Shift` for window commands; never reuse a key listed
   in `RESERVED_PLAIN_ACCELERATORS`.
3. **Handle it.** Add a branch in `_on_action(...)`. If the command operates on the
   active pane, route it through `_route(...)` so it works for every pane kind; if
   it manipulates layout, call the relevant `TerminalTab` method.
4. **Test it.** `tests/test_native_terminal.py` asserts the public action set and
   shortcut contracts — extend those assertions so the new action and its
   accelerator are covered, and confirm it doesn't shadow a reserved plain key.

If the command should also appear in pane-leader mode, add it to the relevant mode
in `PaneLeaderWindow` and its hint text.

---

## 2. Add a new pane type

Goal: a new kind of content pane alongside `TerminalPane`, `MarkdownPane`,
`ImagePane`.

1. **Implement the behavior surface.** Create a class (inside
   `build_native_classes()`, next to the other panes) exposing the full `PaneBase`
   contract: `kind`, `pane_id`, `widget`, `title`, and the methods `focus`,
   `copy`, `paste`, `select_all`, `reset`, `clear_scrollback`, `reload`,
   `set_search`, `find_next`, `find_previous`, `zoom_in`, `zoom_out`,
   `zoom_reset`. Methods that don't apply should be safe no-ops (as `ImagePane`
   does for `paste`/search).
2. **Build the widget.** Construct it from native GTK primitives. If it shows text
   that users shouldn't be able to break focus on, keep widgets
   non-focusable/non-selectable (the `MarkdownPane` guardrail).
3. **Wire an opener.** Add the path that creates it — a CLI flag, an action, a
   control-socket action, and/or a Markdown-link target — depending on how users
   reach it.
4. **Test it.** Add pure-core tests for any parsing/scaling/formatting helpers the
   pane needs (model these on the Markdown/image helper tests), and a guardrail if
   the pane has a rule worth protecting.

Because the dispatcher routes via `_route()`, once the surface is complete the
existing copy/search/zoom/reset actions work on the new pane for free.

---

## 3. Add a color palette

Goal: a new selectable theme.

1. **Define it.** Add an entry to the `PALETTES` dict: a `foreground`, a
   `background`, and the 16 ANSI `colors` (indices 0–7 normal, 8–15 bright, in
   hex). Match the structure of the existing palettes.
2. **That's the wiring.** `--palette` and the `palette` config key validate against
   `PALETTES.keys()`, so the new name is immediately selectable, and per-pane tints
   blend from its accent slots automatically (`PANE_TINT_SLOTS`).
3. **Test it.** Extend the palette-normalization test so the new palette is
   accepted and its color count/format is validated.

---

## 4. Add a config option

Goal: a new persistent setting in `~/.config/agent-terminal/native.json`.

1. **Add the field.** Add it to the `NativeConfig` dataclass with a sensible
   default (and to `TerminalSettings`/`LaunchOptions` if it is also a per-launch
   value).
2. **Parse and validate it.** In `load_native_config()`, read the key, validate
   against allowed values, and fall back to the default on missing/invalid input —
   the function must keep tolerating a missing or malformed file.
3. **Expose it (optional).** If it should also be a CLI flag, add it in
   `build_arg_parser()`/`parse_args()` and make the flag override the file value.
4. **Use it.** Thread the value to wherever it takes effect (a setting on
   `TerminalPane._configure`, a layout policy, etc.).
5. **Test it.** Add a `load_native_config` test for the default, a valid value, and
   an invalid value falling back.

---

## 5. Add a control-socket command

Goal: a new IPC action a helper process can send over the Unix socket.

1. **Accept it.** Extend `parse_control_message()` to recognize the new `action`
   (and any new fields), keeping the "return `None` for anything malformed"
   contract.
2. **Dispatch it.** Handle the parsed message where control messages are applied
   (the GTK-main-loop handler that today turns `open-file` into a pane), using
   `GLib.idle_add` so all GTK work happens on the main loop — never on the daemon
   accept thread.
3. **Send it.** If a bundled helper should emit the command, add a client call via
   `send_control_message(...)` in `tui_navigation.py` (or your new helper).
4. **Test it.** `tests/test_tui_navigation.py` checks the control-message shape —
   add cases for the new action's encode/parse round-trip and its rejection when
   malformed.

Keep the socket optional: the app must still run if the socket can't bind.

---

## 6. Add a layout operation

Goal: a new way to rearrange or resize panes.

1. **Write it pure.** Add a `layout_*` function that takes a `LayoutNode` (plus
   parameters) and returns a **new** tree, preserving every invariant: normalized
   same-axis groups, weights summing to 1.0 and respecting `MIN_SPLIT_WEIGHT`, and
   no mutation of the input. Reuse the existing helpers (`layout_split_group`,
   `_weighted_sizes`, etc.) rather than hand-rolling normalization.
2. **Test it pure.** Add unit tests in `tests/test_native_terminal.py` covering the
   structural result and the invariants — this is where layout work is actually
   verified, before any widget exists.
3. **Bind it.** Add a `TerminalTab` method that snapshots the layout for undo (push
   a `LayoutSnapshot`) if the change is structural, applies your function to
   `self.root`, updates `active_pane_id`, and re-allocates. Skip the undo snapshot
   for pure focus/view changes (like focused-fit).
4. **Expose it.** Add an action + shortcut (recipe 1) and/or a pane-leader binding.

---

## Checklist for any change

- [ ] Logic added to the pure core, not the GTK shell, where possible.
- [ ] Pure-core unit test added/updated and `python3 -m unittest` is green.
- [ ] No `Gtk.Paned`/`Gtk.Fixed`; layout stored as weights, not pixels.
- [ ] No plain terminal key stolen (`RESERVED_PLAIN_ACCELERATORS`).
- [ ] Docs updated: `README.md`/`docs/` for users, `architecture.md` for structure,
      a new ADR under `docs/decisions/` for a significant choice.
