"""Contracts for the pure ask-mode core (copilot/ask.py)."""

import unittest

from agent_terminal.copilot import ask, risk


class SessionStateTests(unittest.TestCase):
    def test_starts_in_input(self):
        session = ask.AskSession()
        self.assertEqual(session.state, ask.INPUT)
        self.assertEqual(session.turns, [])

    def test_question_moves_to_thinking(self):
        session = ask.AskSession()
        query = session.begin_question("list big files")
        self.assertEqual(query, "list big files")
        self.assertEqual(session.state, ask.THINKING)
        self.assertEqual(session.turns[-1].role, "question")

    def test_answer_moves_to_answer(self):
        session = ask.AskSession()
        session.begin_question("q")
        session.add_answer(command="du -ah | sort -h",
                           description="sizes", risk_display=risk.READ_ONLY,
                           endpoint="Loki (210)")
        self.assertEqual(session.state, ask.ANSWER)
        answer = session.turns[-1]
        self.assertEqual(answer.command, "du -ah | sort -h")
        self.assertEqual(answer.endpoint, "Loki (210)")

    def test_error_moves_to_error(self):
        session = ask.AskSession()
        session.begin_question("q")
        session.add_error("endpoint unreachable")
        self.assertEqual(session.state, ask.ERROR)
        self.assertEqual(session.turns[-1].text, "endpoint unreachable")


class SeedTests(unittest.TestCase):
    def test_carry_draft_stores_seed(self):
        session = ask.AskSession(seed="tar czf ", carry_draft=True)
        self.assertEqual(session.seed, "tar czf ")
        self.assertTrue(session.parked)

    def test_no_carry_drops_seed(self):
        session = ask.AskSession(seed="tar czf ", carry_draft=False)
        self.assertEqual(session.seed, "")
        self.assertFalse(session.parked)

    def test_empty_seed_not_parked(self):
        self.assertFalse(ask.AskSession(seed="").parked)


class ComposeQueryTests(unittest.TestCase):
    def test_first_question_is_verbatim(self):
        session = ask.AskSession()
        self.assertEqual(session.compose_query("find logs"), "find logs")

    def test_followup_threads_prior_suggestion(self):
        session = ask.AskSession()
        session.begin_question("find logs")
        session.add_answer(command="find . -name '*.log'",
                           risk_display=risk.READ_ONLY)
        query = session.compose_query("only under /var")
        self.assertIn("find . -name '*.log'", query)
        self.assertIn("only under /var", query)

    def test_last_answer_skips_errors(self):
        session = ask.AskSession()
        session.begin_question("q1")
        session.add_answer(command="ls -a", risk_display=risk.READ_ONLY)
        session.begin_question("q2")
        session.add_error("timeout")
        self.assertEqual(session.last_answer().command, "ls -a")

    def test_history_is_bounded(self):
        session = ask.AskSession(max_turns=2)
        for i in range(10):
            session.begin_question(f"q{i}")
            session.add_answer(command=f"echo {i}",
                               risk_display=risk.READ_ONLY)
        self.assertLessEqual(len(session.turns), 2 * session.max_turns + 1)


class HotkeysArmedTests(unittest.TestCase):
    def test_armed_on_answer_when_entry_unfocused(self):
        self.assertTrue(ask.hotkeys_armed(ask.ANSWER, False))

    def test_disarmed_when_entry_focused(self):
        # Typing a follow-up (focus in the entry) must never trigger accept.
        self.assertFalse(ask.hotkeys_armed(ask.ANSWER, True))

    def test_disarmed_off_answer(self):
        for state in (ask.INPUT, ask.THINKING, ask.ERROR, ask.GATED):
            self.assertFalse(ask.hotkeys_armed(state, False), state)


class CanTakeTests(unittest.TestCase):
    def test_single_line_take_without_autopilot(self):
        take_ok, run_ok = ask.can_take("ls -a", risk.READ_ONLY)
        self.assertTrue(take_ok)
        self.assertFalse(run_ok)

    def test_multiline_command_rejected(self):
        self.assertEqual(ask.can_take("a\nb", risk.READ_ONLY), (False, False))
        self.assertEqual(ask.can_take("a\rb", risk.READ_ONLY), (False, False))

    def test_empty_command_rejected(self):
        self.assertEqual(ask.can_take("", risk.READ_ONLY), (False, False))

    def test_autopilot_runs_allowlisted_safe_commands(self):
        for cmd, label in (("ls -la", risk.READ_ONLY),
                           ("grep -rn x .", risk.READ_ONLY),
                           ("mkdir logs", risk.LOCAL_CHANGE),
                           ("touch f", risk.LOCAL_CHANGE)):
            take_ok, run_ok = ask.can_take(cmd, label, auto_pilot=True)
            self.assertTrue(run_ok, cmd)

    def test_autopilot_holds_high_severity(self):
        # The severity ceiling blocks anything above it, even allowlisted.
        for label in (risk.INSTALL, risk.REMOTE, risk.PRIVILEGED,
                      risk.DESTRUCTIVE, risk.UNKNOWN):
            take_ok, run_ok = ask.can_take("ls", label, auto_pilot=True)
            self.assertTrue(take_ok, label)
            self.assertFalse(run_ok, label)

    def test_allowlist_floor_holds_non_allowlisted_graded_low(self):
        # The floor: even if a classifier gap grades one of these read-only
        # or local-change, a non-allowlisted program never auto-runs.
        for cmd in ("git checkout .", "git switch -f main",
                    "find . -exec cp /dev/null {} +", "sed -i s/a/b/ f",
                    "chmod -R 000 /", "cp a b", "mv x /dev/null"):
            _, run_ok = ask.can_take(cmd, risk.LOCAL_CHANGE, auto_pilot=True)
            self.assertFalse(run_ok, cmd)

    def test_ceiling_read_only_excludes_local_change(self):
        # A stricter ceiling makes even an allowlisted creator wait.
        _, run_ok = ask.can_take("mkdir d", risk.LOCAL_CHANGE,
                                 auto_pilot=True, ceiling=risk.READ_ONLY)
        self.assertFalse(run_ok)

    def test_metacharacters_never_auto_run(self):
        for cmd in ("echo $(rm -rf ~)", "cat x > /dev/sda",
                    "echo hi > ~/.bashrc", "diff <(a) <(b)",
                    "echo `whoami`", "sort f < in.txt"):
            take_ok, run_ok = ask.can_take(cmd, risk.READ_ONLY,
                                           auto_pilot=True)
            self.assertTrue(take_ok, cmd)
            self.assertFalse(run_ok, cmd)


if __name__ == "__main__":
    unittest.main()
