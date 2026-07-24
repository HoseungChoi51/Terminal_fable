"""Unit-level contracts for the copilot P1 titles + sessions core."""

import tempfile
import time
import unittest
from pathlib import Path

from agent_terminal.copilot import sessions as csess
from agent_terminal.copilot import titles as ctitles
from agent_terminal.copilot.config import SessionsConfig
from agent_terminal.copilot.journal import CommandRecord


def _rec(cmd, *, exit_code=0, cwd="/home/x/proj", duration=1.0, seq=1,
         started_at=1000.0, tail=None):
    return CommandRecord(seq=seq, cmd=cmd, cwd=cwd, started_at=started_at,
                         duration_s=duration, exit_code=exit_code,
                         output_tail=tail, capture="termprop")


class CommandAnalysisTests(unittest.TestCase):
    def test_command_base(self):
        self.assertEqual(ctitles.command_base("git status"), "git")
        self.assertEqual(ctitles.command_base("  ls -la  "), "ls")
        self.assertEqual(ctitles.command_base("FOO=bar make build"), "make")
        self.assertEqual(ctitles.command_base("./run.sh"), "./run.sh")
        self.assertIsNone(ctitles.command_base(""))
        self.assertIsNone(ctitles.command_base(None))

    def test_is_trivial(self):
        self.assertTrue(ctitles.is_trivial("ls -la"))
        self.assertTrue(ctitles.is_trivial("cd /tmp"))
        self.assertFalse(ctitles.is_trivial("pytest -q"))
        self.assertFalse(ctitles.is_trivial("git commit -m x"))

    def test_command_summary(self):
        self.assertEqual(ctitles.command_summary("git commit -m 'x'"),
                         "git commit")
        self.assertEqual(ctitles.command_summary("docker -q logs api"),
                         "docker logs")
        self.assertEqual(ctitles.command_summary("pytest -q"), "pytest")
        self.assertEqual(ctitles.command_summary("/usr/bin/make all"),
                         "make all")


class InferTitleTests(unittest.TestCase):
    def test_project_and_command(self):
        records = [_rec("cd proj"), _rec("pytest -q")]
        self.assertEqual(ctitles.infer_title(records, "/home/x/proj"),
                         "proj: pytest")

    def test_skips_trivial_for_command_part(self):
        records = [_rec("pytest"), _rec("ls -la")]
        self.assertEqual(ctitles.infer_title(records, "/home/x/proj"),
                         "proj: pytest")

    def test_project_only_when_no_meaningful_command(self):
        records = [_rec("ls"), _rec("cd x")]
        self.assertEqual(ctitles.infer_title(records, "/home/x/proj"),
                         "proj")

    def test_none_when_nothing(self):
        self.assertIsNone(ctitles.infer_title([], None))


class TitlePolicyTests(unittest.TestCase):
    def test_first_change_accepted(self):
        policy = ctitles.TitlePolicy(30)
        self.assertEqual(policy.propose("a: x", 100.0), "a: x")

    def test_same_title_suppressed(self):
        policy = ctitles.TitlePolicy(30)
        policy.propose("a: x", 100.0)
        self.assertIsNone(policy.propose("a: x", 200.0))

    def test_damped_within_interval(self):
        policy = ctitles.TitlePolicy(30)
        policy.propose("a: x", 100.0)
        self.assertIsNone(policy.propose("a: y", 120.0))   # 20s < 30s
        self.assertEqual(policy.propose("a: y", 131.0), "a: y")  # 31s

    def test_none_candidate(self):
        policy = ctitles.TitlePolicy(30)
        self.assertIsNone(policy.propose(None, 100.0))


class MeaningfulnessTests(unittest.TestCase):
    def test_three_nontrivial(self):
        self.assertTrue(csess.is_meaningful(
            [_rec("pytest"), _rec("git status"), _rec("make")]))

    def test_only_trivial_not_meaningful(self):
        self.assertFalse(csess.is_meaningful(
            [_rec("ls"), _rec("cd x"), _rec("pwd"), _rec("clear")]))

    def test_single_long_command_meaningful(self):
        self.assertTrue(csess.is_meaningful([_rec("make", duration=9.0)]))

    def test_two_short_not_meaningful(self):
        self.assertFalse(csess.is_meaningful(
            [_rec("pytest", duration=1.0), _rec("make", duration=1.0)]))


class SessionIdTests(unittest.TestCase):
    def test_millisecond_unique_and_sortable(self):
        a = csess.make_session_id(1000.100, localtime=time.gmtime)
        b = csess.make_session_id(1000.200, localtime=time.gmtime)
        self.assertTrue(a.endswith("-100"))
        self.assertTrue(b.endswith("-200"))
        self.assertLess(a, b)


class BuildSessionTests(unittest.TestCase):
    def setUp(self):
        self.config = SessionsConfig()

    def test_meaningful_session_built(self):
        records = [_rec("pytest", seq=1, started_at=1000.0),
                   _rec("git commit", seq=2, started_at=1005.0),
                   _rec("make", seq=3, started_at=1010.0)]
        session = csess.build_session(records, config=self.config, now=1100.0,
                                      localtime=time.gmtime)
        self.assertIsNotNone(session)
        self.assertEqual(session.project, "proj")
        self.assertEqual(session.title, "proj: make")
        self.assertEqual(session.started_at, 1000.0)
        self.assertEqual(session.ended_at, 1100.0)
        self.assertIn("Session summary", session.summary)

    def test_trivial_session_skipped(self):
        records = [_rec("ls"), _rec("cd x")]
        self.assertIsNone(csess.build_session(records, config=self.config,
                                              now=1.0))

    def test_excluded_dir_skipped(self):
        config = SessionsConfig(exclude_dirs=("/secret",))
        records = [_rec("pytest", cwd="/secret/proj"),
                   _rec("make", cwd="/secret/proj"),
                   _rec("git status", cwd="/secret/proj")]
        self.assertIsNone(csess.build_session(records, config=config, now=1.0))

    def test_excluded_command_dropped(self):
        config = SessionsConfig(exclude_commands=("* --password *",))
        records = [_rec("mysql -u root --password secret", seq=1),
                   _rec("pytest", seq=2), _rec("make", seq=3),
                   _rec("git status", seq=4)]
        session = csess.build_session(records, config=config, now=1.0,
                                      localtime=time.gmtime)
        self.assertIsNotNone(session)
        self.assertTrue(all("--password" not in (r.cmd or "")
                            for r in session.commands))

    def test_store_output_off_strips_tail(self):
        config = SessionsConfig(store_output=False)
        records = [_rec("pytest", tail=("secret out",), seq=1),
                   _rec("make", seq=2), _rec("git status", seq=3)]
        session = csess.build_session(records, config=config, now=1.0,
                                      localtime=time.gmtime)
        self.assertTrue(all(r.output_tail is None for r in session.commands))

    def test_summary_notes_last_failure(self):
        records = [_rec("pytest", seq=1), _rec("make", seq=2),
                   _rec("git push", exit_code=1, seq=3)]
        session = csess.build_session(records, config=self.config, now=1.0,
                                      localtime=time.gmtime)
        self.assertIn("git push", session.summary)
        self.assertIn("exit 1", session.summary)


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = csess.SessionStore(self.tmp.name)
        self.config = SessionsConfig()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, now=1000.5):
        records = [_rec("pytest", seq=1, started_at=now),
                   _rec("git commit", seq=2, started_at=now + 1),
                   _rec("make", seq=3, started_at=now + 2)]
        return csess.build_session(records, config=self.config, now=now + 50,
                                   localtime=time.gmtime)

    def test_save_and_load_roundtrip(self):
        session = self._session()
        self.assertTrue(self.store.save(session))
        loaded = self.store.load(session.id)
        self.assertEqual(loaded.title, session.title)
        self.assertEqual([r.cmd for r in loaded.commands],
                         [r.cmd for r in session.commands])
        self.assertTrue(Path(self.store.summary_path(session.id)).exists())

    def test_list_sorted_newest_first(self):
        self.store.save(self._session(now=1000.0))
        self.store.save(self._session(now=2000.0))
        listed = self.store.list()
        self.assertEqual(len(listed), 2)
        self.assertGreater(listed[0].ended_at, listed[1].ended_at)
        self.assertEqual(listed[0].last_command, "make")
        self.assertEqual(listed[0].command_count, 3)

    def test_list_tolerates_corrupt_dir(self):
        (Path(self.tmp.name) / "sessions" / "junk").mkdir(parents=True)
        (Path(self.tmp.name) / "sessions" / "junk" / "session.json"
         ).write_text("not json")
        self.store.save(self._session())
        self.assertEqual(len(self.store.list()), 1)

    def test_sweep_removes_old(self):
        old = self._session(now=1000.0)      # ended ~1050
        self.store.save(old)
        removed = self.store.sweep(retention_days=1, now=1000.0 + 3 * 86400)
        self.assertEqual(removed, 1)
        self.assertEqual(self.store.list(), [])

    def test_sweep_keeps_recent(self):
        self.store.save(self._session(now=1000.0))
        removed = self.store.sweep(retention_days=30, now=1000.0 + 3600)
        self.assertEqual(removed, 0)
        self.assertEqual(len(self.store.list()), 1)

    def test_missing_root_lists_empty(self):
        store = csess.SessionStore("/nonexistent/path")
        self.assertEqual(store.list(), [])
        self.assertEqual(store.sweep(30, time.time()), 0)


if __name__ == "__main__":
    unittest.main()
