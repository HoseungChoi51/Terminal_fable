# Default terminal setup & fallback plan

Terminal Fable is bound to **Ctrl+Alt+T** as the daily default terminal on
GNOME (Wayland). The launcher runs the **live working tree**, so a broken
edit to `agent_terminal/native_terminal.py` will break the shortcut. This
doc records how it's wired and, most importantly, how to recover.

## If Ctrl+Alt+T is broken right now

You are probably reading this because the shortcut stopped opening a
terminal. Get a working terminal first, then fix the cause.

**Open a fallback terminal (any one of these):**

- Press **Super** (Activities), type `Terminal`, launch **GNOME Terminal**
  from the app grid. It is still installed and untouched.
- Or, from any running app that can spawn a shell, run `gnome-terminal`.
- Or switch to a virtual console with **Ctrl+Alt+F3** (log in, run
  commands, return to the desktop with **Ctrl+Alt+F2** or **F1**).

**Then diagnose why the launcher failed** (usually a bad working-tree edit):

```bash
# Does it start at all? A traceback here points at the broken change.
~/.local/bin/agent-terminal-native --version

# If it's a recent edit, see what changed and revert if needed.
cd ~/Work/Terminal_fable
git status
git stash          # or: git checkout -- agent_terminal/native_terminal.py
```

## How the binding is wired

1. **Launcher** — `~/.local/bin/agent-terminal-native` (created by
   `packaging/install.sh`) execs the repo's `bin/agent-terminal-native`
   directly. Working-tree edits are therefore live on every launch.
2. **Built-in shortcut cleared** — GNOME's built-in "launch terminal"
   media key (default `<Primary><Alt>t`) was set to empty so it does not
   conflict with the custom binding.
3. **Custom keybinding** — a custom GNOME keybinding named
   `Agent Terminal` on `<Primary><Alt>t` runs the launcher.

Re-create it from scratch (e.g. on a new machine or after a reset):

```bash
cd ~/Work/Terminal_fable
packaging/install.sh                 # (re)install the launcher + desktop entry

# Free the built-in terminal media key.
gsettings set org.gnome.settings-daemon.plugins.media-keys terminal "@as []"

# Register and fill in a custom keybinding slot.
KP="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/agent-terminal/"
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['$KP']"
SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KP"
gsettings set "$SCHEMA" name    "Agent Terminal"
gsettings set "$SCHEMA" command "$HOME/.local/bin/agent-terminal-native"
gsettings set "$SCHEMA" binding "<Primary><Alt>t"
```

No logout is required — GNOME's settings-daemon applies the binding live.

Verify:

```bash
gsettings get org.gnome.settings-daemon.plugins.media-keys terminal           # @as []
gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings # ['.../agent-terminal/']
```

## Revert: hand Ctrl+Alt+T back to GNOME Terminal

```bash
gsettings set org.gnome.settings-daemon.plugins.media-keys terminal "['<Primary><Alt>t']"
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "@as []"
```

## Optional: stop running the live working tree

The risk above exists because the shortcut tracks the live repo. To point
the shortcut at a known-good snapshot instead, copy a vetted build aside
and bind to that copy rather than to `~/Work/Terminal_fable`. Ask Claude to
"pin the default terminal to a stable build" and it will set this up.
