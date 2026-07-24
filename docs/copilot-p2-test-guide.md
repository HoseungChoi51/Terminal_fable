# Copilot P2 — dogfooding test guide

Phase P2 adds the **command menu**: a searchable list of recipes and
your own past commands, opened at the cursor, that inserts a command
onto your prompt without running it. This guide is for driving it in
daily use: what should happen, what to watch for, and what is *not* a
bug (deferred to a later phase).

New in P2 (everything else is P0 journal + P1 titles/sessions):

- **Ctrl+Shift+Space** — open the command menu.
- **Alt+Shift+A** — pause / resume the copilot for the active pane.
- Risk badges on each suggestion.

The one property that matters most: **accepting a suggestion never runs
anything.** It types the command onto your input line and stops; you
press Enter yourself. If you ever see a command auto-execute, that is a
serious bug — capture it and stop dogfooding the menu (`suggestions.menu:
false`, below).

---

## 1. Smoke test (2 minutes, do this first after launching a new window)

1. In a shell prompt, press **Ctrl+Shift+Space**. A small menu should
   appear near the cursor with a search box and a list of commands.
2. Type `sort by size`. The top row should become
   `du -ah . | sort -rh | head -20` with a green **read-only** badge.
3. Press **Enter**. The menu closes and that command appears on your
   prompt — **not executed**. Your cursor is back in the terminal.
4. Press **Enter** yourself to run it (or Ctrl+C to discard).
5. Run a couple of real commands (`pytest`, `git status`, …). Open the
   menu again with an empty search — your recent commands should appear
   near the top, above the recipes.
6. Press **Alt+Shift+A**. A "Copilot paused" chip flashes. Press
   **Ctrl+Shift+Space** — the menu should refuse to open ("Copilot
   paused"). Press **Alt+Shift+A** again to resume.

If all six pass, the feature is working. The rest is what to keep an
eye on during real use.

---

## 2. Expected behavior in detail

**Opening.** Ctrl+Shift+Space opens the menu only in a terminal pane,
only when not paused, and only if `suggestions.menu` is on. The search
box has focus; the first row is preselected.

**Searching.** Typing filters fuzzily. A multi-word query ("kill port")
requires every word to appear somewhere in the recipe's description,
command, or keywords. A single word ("gco", "duh") matches as a loose
subsequence. No matches → empty list.

**Sources and order.** Two sources are merged:
- *history* — commands you actually ran in this pane (most recent
  first, trivial ones like `ls`/`cd` filtered out);
- *recipe* — the ~45 built-ins.
With an empty search, recent history ranks above recipes. An identical
command in both is shown once, as history.

**Risk badges.** Each row is tagged read-only, local-change, install,
remote, privileged, or destructive (loudest color). A `sudo …` command
is privileged; `rm -rf`, `git reset --hard`, `kubectl delete`,
`curl … | sh` are destructive.

**Accepting.** Enter (on the selected row) or a click inserts the
command: the current input line is cleared first (Ctrl-U), then the
command is typed in — **never with a trailing newline**. Focus returns
to the terminal.

**Dismissing.** Escape, clicking elsewhere, or losing focus closes the
menu with nothing inserted.

**Pause.** Alt+Shift+A toggles the active pane. While paused, the menu
won't open and command journaling stops (so paused-time commands don't
enter history or sessions). It's per-pane; other panes are unaffected.

---

## 3. What to watch during dogfooding

Report anything here with the command, what you expected, and what
happened.

**Safety (highest priority).**
- Did any accepted suggestion ever *execute* on its own? It must not.
- After accepting, is the line exactly the command — no stray leftover
  characters, no doubled text, no missing prefix?

**Line clearing (Ctrl-U).** Acceptance clears the line with Ctrl-U,
which assumes default (emacs) readline. Watch if you:
- use `set -o vi` — clearing may misbehave;
- had the cursor *in the middle* of a typed line — Ctrl-U only deletes
  to the left of the cursor, so text to the right may remain.
Note anything that leaves a garbled line.

**Cursor positioning.** The menu should appear at/just below the
prompt cursor. Watch for it appearing in the wrong place after
scrolling, on wrapped/multi-line prompts, in split panes, or with a
large font.

**Focus.** After the menu closes (accept or Escape), typing should go
straight to the terminal. Flag any case where focus is lost or you have
to click back in.

**Full-screen apps.** The menu is a global shortcut, so it will also
open on top of vim/htop/less/a TUI. Inserting there feeds the command
as *input to that app*, which is almost never what you want. For now,
**don't open the menu inside a full-screen program** — P4 will gate
this automatically. If you do and something odd happens, that's a known
rough edge, not a new bug.

**Risk badge accuracy.** If a clearly destructive command shows a mild
badge, or a harmless one shows "destructive", note the exact command —
the classifier is rule-based and will have blind spots.

**History quality.** History suggestions are only as good as the P0
journal. If a suggested "past command" is wrong (mangled multi-line
command, a command you ran with a leading space and hid, etc.), note it
— that's really a journal-capture issue surfacing here.

**Recipe relevance.** If a natural search ("free a port", "tar a
folder") doesn't surface the obvious recipe, tell me the query — recipe
keywords can be tuned.

**Interference with normal terminal use.** The menu and pause are the
only new key grabs (Ctrl+Shift+Space, Alt+Shift+A). Everything else
should feel exactly as before. Flag any new lag, flicker, or stolen
keystroke.

---

## 4. Not bugs (deferred by design)

These are intentionally out of scope for P2 — please don't report them
as defects:

- **The search box starts empty** even if you'd already typed part of a
  command before opening the menu. Pre-filling from what you typed
  needs precise prompt tracking and lands in P4.
- **No inline "ghost" suggestions while you type.** That's P4.
- **No context-aware suggestions** — the menu doesn't yet notice you're
  in a Python project, prioritize a recent `.deb`, or complete
  arguments (`git checkout <branch>`). That's P3.
- **No typo correction / "did you mean".** P3.
- **Recipes are generic built-ins.** No project-specific or
  user-defined recipes, and no learning from what you accept. P6.
- **Acceptance always clears and retypes the whole line** rather than
  completing just the tail. Gentle tail-completion needs the P4 tracker.

---

## 5. Turning it off

Everything is in `~/.config/agent-terminal/native.json` under
`assistant`. Changes apply to newly opened windows.

```json
{
  "assistant": {
    "suggestions": { "menu": false },   // disable the command menu entirely
    "recipes":     { "enabled": false } // menu shows only your history, no recipes
  }
}
```

To pause everything (journal, titles, sessions, menu) for one pane
right now, use **Alt+Shift+A**. To turn the whole copilot off, set
`assistant.enabled: false`.

---

## 6. Quick reference

| Action | Key | Expected |
| --- | --- | --- |
| Open command menu | Ctrl+Shift+Space | Menu at cursor, search focused |
| Accept suggestion | Enter / click | Command typed onto prompt, **not run** |
| Filter | type in the box | Fuzzy match; multi-word = all words |
| Dismiss | Escape / click away | Nothing inserted |
| Pause / resume pane | Alt+Shift+A | Flash chip; menu disabled while paused |

Full behavior and config: [copilot.md](copilot.md). The phased plan and
what each later phase adds: [copilot-development-plan.md](../copilot-development-plan.md).
