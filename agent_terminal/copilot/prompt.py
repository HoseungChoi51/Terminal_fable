"""Prompt-line state machine for inline ghost text (copilot P4).

Fed the bytes the user commits to the child (VTE's ``commit`` signal)
plus precmd/preexec marks and invalidation events, it tracks whether we
are at a clean shell prompt and, if so, exactly what has been typed.
Ghost text is offered only in the clean AT_PROMPT state; anything we
cannot model (arrows, Ctrl-R, tab completion, a running program,
scroll/resize) drops to DIRTY until the next prompt, so a stale or wrong
completion is never shown.
"""

from __future__ import annotations

IDLE = "idle"
AT_PROMPT = "at_prompt"
EXECUTING = "executing"
DIRTY = "dirty"

_BACKSPACE = ("\x7f", "\x08")


class PromptTracker:
    def __init__(self):
        self.state = IDLE
        self.typed = ""

    def on_precmd(self):
        """A fresh prompt is being drawn: clean slate, cursor at the start."""
        self.state = AT_PROMPT
        self.typed = ""

    def on_preexec(self):
        """A command started running."""
        self.state = EXECUTING
        self.typed = ""

    def on_commit(self, text):
        """Absorb bytes the user sent to the shell (VTE commit signal)."""
        if self.state != AT_PROMPT or not text:
            return
        for char in text:
            if char in ("\r", "\n"):
                # Line submitted; wait for the next prompt mark.
                self.state = DIRTY
                self.typed = ""
                return
            if char in _BACKSPACE:
                self.typed = self.typed[:-1]
                continue
            if char == "\t" or ord(char) < 0x20:
                # Tab completion, Ctrl-R, arrows (ESC ...), etc.: we can
                # no longer trust our idea of the line.
                self.state = DIRTY
                return
            self.typed += char

    def invalidate(self):
        """Something off-model happened (scroll, resize, output): give up."""
        if self.state == AT_PROMPT:
            self.state = DIRTY

    def typed_prefix(self):
        """The typed command line, or None when not at a clean prompt."""
        return self.typed if self.state == AT_PROMPT else None

    def accept(self, suffix):
        """Record that an accepted ghost suffix is now on the line."""
        if self.state == AT_PROMPT:
            self.typed += suffix
