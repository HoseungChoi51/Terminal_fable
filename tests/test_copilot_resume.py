"""Contracts for episode-scoped resume (copilot/resume.py)."""

import unittest
from types import SimpleNamespace

from agent_terminal.copilot import resume, episode as episode_mod


def rec(cmd, *, cwd="/home/x/proj", started_at=1000.0):
    return SimpleNamespace(seq=0, cmd=cmd, cwd=cwd, started_at=started_at,
                           duration_s=1.0, exit_code=0, output_tail=None,
                           digest=None, branch=None)


def ep(records):
    return SimpleNamespace(records=tuple(records))


class SeedHistoryTests(unittest.TestCase):
    def test_none_episode_is_empty(self):
        self.assertEqual(resume.seed_history_lines(None), ())
        self.assertEqual(resume.seed_file_content(None), "")

    def test_keeps_nontrivial_drops_trivial(self):
        lines = resume.seed_history_lines(ep([
            rec("ls"), rec("cargo build"), rec("cd /tmp"),
            rec("pytest -x"), rec("clear")]))
        self.assertEqual(lines, ("cargo build", "pytest -x"))

    def test_erasedups_keeps_latest_position(self):
        # a repeated command keeps only its most-recent occurrence, in place
        lines = resume.seed_history_lines(ep([
            rec("make"), rec("./run"), rec("make")]))
        self.assertEqual(lines, ("./run", "make"))

    def test_order_preserved(self):
        lines = resume.seed_history_lines(ep([
            rec("git status"), rec("git add -A"), rec("git commit -m x")]))
        self.assertEqual(list(lines),
                         ["git status", "git add -A", "git commit -m x"])

    def test_multiline_command_collapsed_to_one_entry(self):
        lines = resume.seed_history_lines(ep([
            rec("cat <<EOF\nhello\nworld\nEOF")]))
        self.assertEqual(len(lines), 1)
        self.assertNotIn("\n", lines[0])
        self.assertIn("cat <<EOF", lines[0])

    def test_cap_keeps_most_recent(self):
        records = [rec(f"cmd-{i}") for i in range(300)]
        lines = resume.seed_history_lines(ep(records), max_lines=10)
        self.assertEqual(len(lines), 10)
        self.assertEqual(lines[-1], "cmd-299")     # newest kept
        self.assertEqual(lines[0], "cmd-290")

    def test_seed_file_content_is_newline_terminated(self):
        content = resume.seed_file_content(ep([rec("make"), rec("./run")]))
        self.assertEqual(content, "make\n./run\n")

    def test_redacted_command_stays_redacted(self):
        # resume only selects/reshapes already-redacted commands.
        lines = resume.seed_history_lines(ep([
            rec("export API_KEY=[REDACTED]")]))
        self.assertEqual(lines, ("export API_KEY=[REDACTED]",))


class ResumeCwdTests(unittest.TestCase):
    def test_last_known_cwd(self):
        e = ep([rec("a", cwd="/one"), rec("b", cwd="/two"),
                rec("c", cwd=None)])
        self.assertEqual(resume.resume_cwd(e), "/two")

    def test_none_when_no_cwd(self):
        self.assertIsNone(resume.resume_cwd(ep([rec("a", cwd=None)])))
        self.assertIsNone(resume.resume_cwd(None))


class EpisodesOfTests(unittest.TestCase):
    def test_segments_a_stored_session(self):
        # two episodes: an idle gap wider than the default splits them.
        gap = episode_mod.DEFAULT_IDLE_GAP_S + 60
        session = SimpleNamespace(commands=(
            rec("cargo build", started_at=1000),
            rec("cargo test", started_at=1005),
            rec("git log", started_at=1000 + gap),
            rec("git push", started_at=1000 + gap + 5)))
        episodes = resume.episodes_of(session)
        self.assertEqual(len(episodes), 2)
        # resuming the last episode seeds only its commands
        last = resume.seed_history_lines(episodes[-1])
        self.assertEqual(last, ("git log", "git push"))
        self.assertNotIn("cargo build", last)

    def test_empty_session(self):
        self.assertEqual(resume.episodes_of(SimpleNamespace(commands=())), [])
        self.assertEqual(resume.episodes_of(SimpleNamespace(commands=None)), [])


if __name__ == "__main__":
    unittest.main()
