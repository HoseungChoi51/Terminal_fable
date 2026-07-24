"""Contracts for the ask-mode context ladder (copilot/askcontext.py)."""

import unittest
from types import SimpleNamespace

from agent_terminal.copilot import askcontext, digest as digest_mod, episode

CARGO_ERR = [
    "   Compiling proj v0.1.0",
    "error[E0499]: cannot borrow `buf` as mutable more than once",
    "  --> src/lib.rs:42:5",
    "error: could not compile `proj` due to 1 previous error",
]
PYTEST_ERR = [
    "E   AssertionError: assert False",
    "========================= 1 failed, 3 passed =========================",
]


def rec(cmd, *, exit_code=0, started_at=1000.0, cwd="/home/x/proj",
        output_lines=None):
    dg = digest_mod.digest_output(output_lines) if output_lines else None
    return SimpleNamespace(seq=0, cmd=cmd, cwd=cwd, started_at=started_at,
                           duration_s=1.0, exit_code=exit_code,
                           output_tail=None, digest=dg, branch=None)


def _episode(records):
    return episode.current_episode(records)


class AskContextTests(unittest.TestCase):
    def test_empty_episode(self):
        self.assertEqual(askcontext.build_ask_context(None, question="q"), "")

    def test_headline_present(self):
        e = _episode([rec("cargo build", started_at=1000)])
        ctx = askcontext.build_ask_context(e, question="hi")
        self.assertTrue(ctx.startswith("task: "))

    def test_salient_command_gets_the_big_digest(self):
        # "ls" ran last, but the question is about the build → cargo wins.
        records = [
            rec("cargo build", exit_code=101, started_at=1000,
                output_lines=CARGO_ERR),
            rec("ls", started_at=1010),
        ]
        ctx = askcontext.build_ask_context(
            _episode(records), question="why did the build fail?")
        self.assertIn("$ cargo build", ctx)
        self.assertIn("error[E0499]", ctx)           # its digest, in full
        self.assertIn("could not compile", ctx)

    def test_failures_always_included_even_if_not_salient(self):
        records = [
            rec("pytest", exit_code=1, started_at=1000,
                output_lines=PYTEST_ERR),
            rec("cargo build", exit_code=101, started_at=1010,
                output_lines=CARGO_ERR),
        ]
        # question matches cargo; pytest failure must still appear
        ctx = askcontext.build_ask_context(
            _episode(records), question="fix the cargo build")
        self.assertIn("error[E0499]", ctx)            # salient cargo digest
        self.assertIn("$ pytest", ctx)                # other failure present
        self.assertIn("1 failed, 3 passed", ctx)      # its summary line

    def test_fallback_to_last_with_output_when_no_match(self):
        records = [
            rec("cargo build", exit_code=101, started_at=1000,
                output_lines=CARGO_ERR),
            rec("cd somewhere", started_at=1005),
        ]
        # a question that fuzzy-matches nothing → last-with-output (cargo)
        ctx = askcontext.build_ask_context(
            _episode(records), question="zzzzz")
        self.assertIn("error[E0499]", ctx)

    def test_non_salient_commands_are_one_liners(self):
        records = [
            rec("cargo build", exit_code=101, started_at=1000,
                output_lines=CARGO_ERR),
            rec("git status", started_at=1010),
            rec("ls -la", started_at=1020),
        ]
        ctx = askcontext.build_ask_context(
            _episode(records), question="cargo build error")
        self.assertIn("recent:", ctx)
        self.assertIn("git status", ctx)
        self.assertIn("ls -la", ctx)

    def test_budget_trims_from_the_bottom(self):
        records = [rec("cargo build", exit_code=101, started_at=1000,
                       output_lines=CARGO_ERR)]
        records += [rec(f"echo cmd number {i}", started_at=1000 + i)
                    for i in range(200)]
        ctx = askcontext.build_ask_context(
            _episode(records), question="build error", budget_chars=400)
        self.assertLessEqual(len(ctx), 400 + 60)
        self.assertIn("task: ", ctx)                  # headline survives
        self.assertIn("error[E0499]", ctx)            # salient survives
        self.assertIn("trimmed", ctx)                 # trim marker

    def test_only_selects_never_invents(self):
        # A redacted line stays redacted; nothing not in a cmd/digest appears.
        records = [rec("cat key", exit_code=0, started_at=1000,
                       output_lines=["[REDACTED]", "done"])]
        ctx = askcontext.build_ask_context(_episode(records), question="key")
        self.assertNotIn("secret", ctx.lower())


class OutputModeTests(unittest.TestCase):
    """The tri-state send_output knob threaded through as output_mode."""

    def _cargo(self):
        return _episode([rec("cargo build", exit_code=101, started_at=1000,
                             output_lines=CARGO_ERR)])

    def test_none_sends_commands_only(self):
        # "none" keeps the command lines but strips every output line.
        ctx = askcontext.build_ask_context(
            self._cargo(), question="why fail?", output_mode="none")
        self.assertIn("$ cargo build", ctx)
        self.assertNotIn("error[E0499]", ctx)
        self.assertNotIn("could not compile", ctx)

    def test_digest_is_the_default(self):
        default = askcontext.build_ask_context(self._cargo(), question="x")
        explicit = askcontext.build_ask_context(
            self._cargo(), question="x", output_mode="digest")
        self.assertEqual(default, explicit)
        self.assertIn("error[E0499]", default)

    def test_full_sends_the_verbose_tail_not_the_digest(self):
        # "full" reads output_tail verbatim (here noise a digest would elide).
        tail = [f"progress {i}" for i in range(30)] + ["error: boom"]
        record = SimpleNamespace(
            seq=0, cmd="make", cwd="/home/x/proj", started_at=1000.0,
            duration_s=1.0, exit_code=2, output_tail=tail,
            digest=digest_mod.digest_output(tail), branch=None)
        ctx = askcontext.build_ask_context(
            _episode([record]), question="why?", output_mode="full")
        self.assertIn("$ make", ctx)
        self.assertIn("error: boom", ctx)
        self.assertIn("progress 29", ctx)   # a raw tail line the digest drops


if __name__ == "__main__":
    unittest.main()
