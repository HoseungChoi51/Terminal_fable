# 0008 — In-place ask mode (replacing the side panel)

- Developed: 2026-07 · Status: Built · ADR: [0010](../decisions/0010-in-place-ask-mode.md)
- Related memory: [[ask-mode-autorun-allowlist]]

## Request

Dogfooding P5's side-panel assistant, a second pane felt distracting — so ask
mode was reworked into the terminal itself (Ctrl+?). Then, on hitting a bug:

> "After pressing Ctrl+? (Ctrl+Shift+/), the terminal eats up what I type …
> Window buttons respond to my clicking, but nothing happens … I also need a
> status bar showing which model is attached to the copilot (abbreviated,
> expandable on request by shortcut key). I want a visual indication that I'm
> in a copilot mode."

**Rephrased.** Ask mode must not be a modal that traps keystrokes or steals
focus with no visible signal. Make it an unmistakable, non-modal in-window
surface, always show which model is attached, and make "I'm in copilot mode"
obvious.

## Problem / context

The first ask surface was a separate `IntentPane` split pane (Ctrl+Shift+P). It
broke the sense of working in one place. The Ctrl+? popover then had a modal
grab that ate typed input, froze the window chrome, and gave no visual cue.

## Decisions

- **In-place ask mode, not a side panel** (ADR 0010): ask mode is a **non-modal
  `Gtk.Revealer`** bar in the window, never a modal popover that can grab the
  window or trap keystrokes.
- **A persistent status bar** shows the attached model, abbreviated
  (`⌁ copilot: loki`), expandable via **Ctrl+Shift+M** (the model picker).
- **A ⌁ ASK badge** is the unmistakable mode signal.
- **Auto-run stays gated** by a tiny inert-reader allowlist (never by severity
  classification), off by default — see [[ask-mode-autorun-allowlist]].

## Approach

The ask bar is a window-level revealer above the status bar; a model picker
dialog (not a popover, to survive async discovery); the draft shell line is
carried as redacted context and parked while asking.

## Status

Built; the `IntentPane` side panel is superseded. This surface is what
design-log [0010](0010-ask-mode-terminal-context.md) later feeds with digested
terminal context, and what [0011](0011-workspaces.md)'s naming reuses.
