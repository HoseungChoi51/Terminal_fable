"""Unit-level contracts for the P4 prompt-line state machine."""

import unittest

from agent_terminal.copilot import prompt as cprompt


class PromptTrackerTests(unittest.TestCase):
    def setUp(self):
        self.t = cprompt.PromptTracker()

    def at_prompt(self):
        self.t.on_precmd()

    def test_starts_idle(self):
        self.assertEqual(self.t.state, cprompt.IDLE)
        self.assertIsNone(self.t.typed_prefix())

    def test_typing_accumulates_at_prompt(self):
        self.at_prompt()
        self.t.on_commit("l")
        self.t.on_commit("s")
        self.assertEqual(self.t.typed_prefix(), "ls")

    def test_typing_ignored_when_not_at_prompt(self):
        self.t.on_commit("ls")
        self.assertIsNone(self.t.typed_prefix())

    def test_backspace(self):
        self.at_prompt()
        self.t.on_commit("gil")
        self.t.on_commit("\x7f")
        self.assertEqual(self.t.typed_prefix(), "gi")
        self.t.on_commit("\x08")
        self.assertEqual(self.t.typed_prefix(), "g")

    def test_enter_submits_and_drops_out(self):
        self.at_prompt()
        self.t.on_commit("ls")
        self.t.on_commit("\r")
        self.assertEqual(self.t.state, cprompt.DIRTY)
        self.assertIsNone(self.t.typed_prefix())

    def test_control_char_dirties(self):
        self.at_prompt()
        self.t.on_commit("ls")
        self.t.on_commit("\x12")   # Ctrl-R
        self.assertEqual(self.t.state, cprompt.DIRTY)
        self.assertIsNone(self.t.typed_prefix())

    def test_arrow_escape_sequence_dirties(self):
        self.at_prompt()
        self.t.on_commit("ls")
        self.t.on_commit("\x1b[D")   # left arrow
        self.assertIsNone(self.t.typed_prefix())

    def test_tab_dirties(self):
        self.at_prompt()
        self.t.on_commit("cd sr")
        self.t.on_commit("\t")
        self.assertIsNone(self.t.typed_prefix())

    def test_preexec_then_precmd_cycle(self):
        self.at_prompt()
        self.t.on_commit("make")
        self.t.on_preexec()
        self.assertEqual(self.t.state, cprompt.EXECUTING)
        self.assertIsNone(self.t.typed_prefix())
        self.t.on_precmd()
        self.assertEqual(self.t.typed_prefix(), "")

    def test_invalidate_only_affects_prompt(self):
        self.at_prompt()
        self.t.on_commit("ls")
        self.t.invalidate()
        self.assertEqual(self.t.state, cprompt.DIRTY)
        # invalidate while executing is a no-op
        self.t.on_preexec()
        self.t.invalidate()
        self.assertEqual(self.t.state, cprompt.EXECUTING)

    def test_dirty_recovers_on_next_precmd(self):
        self.at_prompt()
        self.t.on_commit("\t")
        self.assertEqual(self.t.state, cprompt.DIRTY)
        self.t.on_precmd()
        self.assertEqual(self.t.typed_prefix(), "")

    def test_accept_extends_typed(self):
        self.at_prompt()
        self.t.on_commit("git ")
        self.t.accept("status")
        self.assertEqual(self.t.typed_prefix(), "git status")


if __name__ == "__main__":
    unittest.main()
