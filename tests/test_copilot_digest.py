"""Contracts for the output digester (copilot/digest.py).

Fixtures under tests/fixtures/output/ are real-shaped captures; the tests
assert the digest keeps the outcome (error/summary), elides the bulk, and
stays within budget. The per-line `reason` tags make "why kept?" checkable.
"""

import unittest
from pathlib import Path

from agent_terminal.copilot import digest as d

_FIX = Path(__file__).parent / "fixtures" / "output"


def _lines(name):
    return (_FIX / name).read_text().splitlines()


class DigestFixtureTests(unittest.TestCase):
    def _digest(self, name, **kw):
        return d.digest_output(_lines(name), **kw)

    def test_pytest_keeps_summary_and_failure(self):
        dg = self._digest("pytest_fail.txt")
        text = dg.render()
        self.assertIn("2 failed, 12 passed", text)          # the outcome
        self.assertIn("AssertionError", text)               # the failure
        self.assertLessEqual(dg.kept, 24)
        self.assertTrue(any(r == d.SUMMARY for r in dg.reasons()))

    def test_cargo_keeps_error_code(self):
        dg = self._digest("cargo_fail.txt")
        text = dg.render()
        self.assertIn("error[E0499]", text)
        self.assertIn("could not compile", text)

    def test_make_keeps_error_line(self):
        dg = self._digest("make_error.txt")
        text = dg.render()
        self.assertIn("make: *** [Makefile:4: main.o] Error 1", text)
        self.assertIn("undeclared", text)

    def test_clean_output_has_no_error_reason(self):
        dg = self._digest("git_status.txt")
        # git status has no failure — nothing should be tagged error/summary
        self.assertNotIn(d.ERROR, dg.reasons())
        self.assertNotIn(d.SUMMARY, dg.reasons())

    def test_all_fixtures_within_budget_and_deterministic(self):
        for name in ("pytest_fail.txt", "cargo_fail.txt", "make_error.txt",
                     "git_status.txt"):
            a = self._digest(name, max_lines=20, max_chars=1500)
            b = self._digest(name, max_lines=20, max_chars=1500)
            self.assertEqual(a, b, name)                     # deterministic
            self.assertLessEqual(a.kept, 20, name)
            self.assertLessEqual(len(a.render()), 1500 + 200, name)


class DigestBehaviourTests(unittest.TestCase):
    def test_empty(self):
        dg = d.digest_output([])
        self.assertTrue(dg.is_empty())
        self.assertEqual(dg.total_lines, 0)

    def test_short_output_kept_whole(self):
        lines = ["$ echo hi", "hi"]
        dg = d.digest_output(lines)
        self.assertEqual(dg.render(), "$ echo hi\nhi")

    def test_repeated_lines_collapse(self):
        lines = ["start"] + ["downloading..."] * 800 + ["done"]
        dg = d.digest_output(lines)
        text = dg.render()
        self.assertIn("(×800)", text)
        self.assertLess(dg.kept, 10)
        self.assertEqual(dg.total_lines, 802)

    def test_huge_output_elided_to_budget(self):
        lines = [f"line {i}" for i in range(5000)]
        dg = d.digest_output(lines, max_lines=24)
        self.assertLessEqual(dg.kept, 24)
        self.assertTrue(any(r == d.ELISION for r in dg.reasons()))
        # head + tail survive
        self.assertIn("line 0", dg.render())
        self.assertIn("line 4999", dg.render())

    def test_error_in_the_middle_survives_elision(self):
        lines = ([f"noise {i}" for i in range(1000)]
                 + ["Traceback (most recent call last):", "  File x",
                    "ValueError: boom"]
                 + [f"trailing {i}" for i in range(1000)])
        dg = d.digest_output(lines, max_lines=24)
        text = dg.render()
        self.assertIn("ValueError: boom", text)
        self.assertIn("Traceback", text)
        self.assertLessEqual(dg.kept, 24)

    def test_errors_never_dropped_even_over_budget(self):
        lines = [f"error: failure number {i}" for i in range(100)]
        dg = d.digest_output(lines, max_lines=10)
        # every line is an error; budget can't silence them all
        self.assertTrue(all(r in (d.ERROR, d.SUMMARY, d.ELISION)
                            for r in dg.reasons()))
        self.assertIn("failure number", dg.render())

    def test_ansi_and_cr_stripped(self):
        lines = ["\x1b[31mred error: boom\x1b[0m",
                 "progress 10%\rprogress 100%"]
        dg = d.digest_output(lines)
        text = dg.render()
        self.assertNotIn("\x1b", text)
        self.assertIn("red error: boom", text)
        self.assertIn("progress 100%", text)
        self.assertNotIn("progress 10%", text)

    def test_digest_only_drops_never_adds(self):
        # Every non-elision line in the digest must be a line from the input
        # (after cleaning) — the digester can't reintroduce redacted content.
        lines = ["[REDACTED]", "some output", "error: nope", "tail"]
        dg = d.digest_output(lines)
        cleaned = set(lines)
        for line in dg.lines:
            if line.reason == d.ELISION:
                continue
            base = line.text.split("    (×")[0]
            self.assertIn(base, cleaned, base)


if __name__ == "__main__":
    unittest.main()
