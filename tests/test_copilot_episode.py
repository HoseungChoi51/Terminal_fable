"""Contracts for episode segmentation (copilot/episode.py)."""

import unittest
from types import SimpleNamespace

from agent_terminal.copilot import episode as ep


def rec(started_at, cmd="ls", cwd="/home/x/proj", duration=1.0, exit_code=0,
        branch=None):
    return SimpleNamespace(seq=0, cmd=cmd, cwd=cwd, started_at=started_at,
                           duration_s=duration, exit_code=exit_code,
                           output_tail=None, branch=branch)


class SegmentTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(ep.segment([]), [])
        self.assertIsNone(ep.current_episode([]))

    def test_dense_run_is_one_episode(self):
        # 6 commands 30s apart, same cwd -> one task
        records = [rec(1000 + i * 30, cmd=f"cargo build {i}") for i in range(6)]
        episodes = ep.segment(records)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].command_count, 6)

    def test_idle_gap_splits(self):
        records = [rec(1000, cmd="pytest"), rec(1030, cmd="pytest -x"),
                   rec(1030 + 900, cmd="git log")]     # 15-min gap
        episodes = ep.segment(records)
        self.assertEqual(len(episodes), 2)
        self.assertEqual(len(episodes[0].records), 2)
        self.assertEqual(len(episodes[1].records), 1)

    def test_cwd_change_splits(self):
        records = [rec(1000, cwd="/a/proj"), rec(1010, cwd="/a/proj"),
                   rec(1020, cwd="/b/other")]
        episodes = ep.segment(records)
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[1].project, "other")

    def test_branch_change_splits(self):
        records = [rec(1000, branch="main"), rec(1010, branch="main"),
                   rec(1020, branch="feature")]
        episodes = ep.segment(records)
        self.assertEqual(len(episodes), 2)

    def test_threshold_sensitivity(self):
        # A single 500s gap: with a 600s threshold it's one episode, with a
        # 400s threshold it's two. The knob is justified and tunable.
        records = [rec(1000, cmd="make"), rec(1500, cmd="make test")]
        self.assertEqual(len(ep.segment(records, idle_gap_s=600)), 1)
        self.assertEqual(len(ep.segment(records, idle_gap_s=400)), 2)

    def test_current_episode_is_the_tail(self):
        records = [rec(1000, cmd="old"), rec(1000 + 3600, cmd="new1"),
                   rec(1000 + 3630, cmd="new2")]
        cur = ep.current_episode(records)
        self.assertEqual([r.cmd for r in cur.records], ["new1", "new2"])


class HeadlineTests(unittest.TestCase):
    def test_headline_has_task_span_and_counts(self):
        cwd = "/home/x/Terminal_fable"
        records = [rec(1000, cmd="cargo build", cwd=cwd),
                   rec(1300, cmd="cargo test", cwd=cwd, exit_code=101),
                   rec(1360, cmd="cargo test", cwd=cwd, exit_code=101,
                       duration=20)]
        e = ep.current_episode(records)
        head = e.headline()
        self.assertIn("Terminal_fable", head)
        self.assertIn("cargo", head)
        self.assertIn("min", head)          # ~6 min span
        self.assertIn("3 cmds", head)
        self.assertIn("2 failures", head)

    def test_human_span(self):
        self.assertEqual(ep._human_span(45), "45s")
        self.assertEqual(ep._human_span(600), "10 min")
        self.assertEqual(ep._human_span(3 * 3600 + 120), "3h 2min")


if __name__ == "__main__":
    unittest.main()
