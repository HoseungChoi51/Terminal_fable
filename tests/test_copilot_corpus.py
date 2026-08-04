"""Tests for the persistent completion corpus (copilot.corpus)."""

import json
import os
import tempfile
import unittest

from agent_terminal.copilot import corpus as corpus_mod
from agent_terminal.copilot.config import CompletionConfig
from agent_terminal.copilot.journal import CommandRecord

DAY = 86400.0
NOW = 1_700_000_000.0


def record(cmd, cwd=None, exit_code=0, started_at=NOW):
    return CommandRecord(seq=1, cmd=cmd, cwd=cwd, started_at=started_at,
                         duration_s=0.1, exit_code=exit_code,
                         output_tail=None)


class IngestTests(unittest.TestCase):
    def test_add_accumulates_count_cwd_and_exit(self):
        corpus = corpus_mod.Corpus()
        corpus.add("make test", cwd="/repo", exit_code=0, when=NOW)
        corpus.add("make test", cwd="/repo", exit_code=1, when=NOW)
        entry = corpus.get("make test")
        self.assertEqual(entry.count, 2)
        self.assertEqual(entry.cwds, {"/repo": 2})
        self.assertEqual((entry.ok, entry.fail), (1, 1))

    def test_bigram_edge_recorded(self):
        corpus = corpus_mod.Corpus()
        corpus.add("git commit", prev="git add .", when=NOW)
        self.assertEqual(corpus.get("git commit").prev, {"git add .": 1})

    def test_add_records_chains_previous_command(self):
        corpus = corpus_mod.Corpus()
        corpus.add_records([record("git add ."), record("git commit -m x")])
        self.assertEqual(corpus.get("git commit -m x").prev,
                         {"git add .": 1})

    def test_leading_space_is_not_ingested(self):
        """The shell's own "don't record this" convention is honoured."""
        corpus = corpus_mod.Corpus()
        self.assertIsNone(corpus.add(" secret-thing", when=NOW))
        self.assertEqual(len(corpus), 0)

    def test_multiline_and_overlong_commands_rejected(self):
        corpus = corpus_mod.Corpus()
        self.assertIsNone(corpus.add("a\nb", when=NOW))
        self.assertIsNone(corpus.add("x" * 600, when=NOW))
        self.assertEqual(len(corpus), 0)

    def test_secrets_are_redacted_on_ingest(self):
        """bash_history is a new ingress, so ingest must redact regardless."""
        corpus = corpus_mod.Corpus()
        entry = corpus.add("curl -H 'authorization: Bearer abc123xyz'",
                           when=NOW)
        self.assertNotIn("abc123xyz", entry.cmd)
        self.assertIn("[REDACTED]", entry.cmd)

    def test_excluded_dirs_and_commands_are_dropped(self):
        corpus = corpus_mod.Corpus()
        self.assertIsNone(corpus.add("ls", cwd="/private/vault", when=NOW,
                                     exclude_dirs=("/private",)))
        self.assertIsNone(corpus.add("vault login", when=NOW,
                                     exclude_commands=("vault *",)))
        self.assertEqual(len(corpus), 0)

    def test_cwd_fanout_is_capped(self):
        corpus = corpus_mod.Corpus()
        for i in range(corpus_mod.MAX_CWDS_PER_ENTRY + 10):
            corpus.add("ls", cwd=f"/dir{i}", when=NOW)
        self.assertLessEqual(len(corpus.get("ls").cwds),
                             corpus_mod.MAX_CWDS_PER_ENTRY)


class PersistenceTests(unittest.TestCase):
    def test_round_trip(self):
        corpus = corpus_mod.Corpus()
        corpus.add("make test", cwd="/repo", exit_code=0, when=NOW)
        restored = corpus_mod.from_json(corpus_mod.to_json(corpus))
        entry = restored.get("make test")
        self.assertEqual(entry.count, 1)
        self.assertEqual(entry.cwds, {"/repo": 1})
        self.assertEqual(entry.ok, 1)

    def test_save_and_load_via_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "corpus.json")
            corpus = corpus_mod.Corpus()
            corpus.add("htop", when=NOW)
            self.assertTrue(corpus_mod.save(corpus, path))
            self.assertFalse(corpus.dirty)
            self.assertIsNotNone(corpus_mod.load(path).get("htop"))

    def test_corrupt_store_yields_empty_corpus_not_an_error(self):
        """A completion cache must never keep the terminal from launching."""
        self.assertEqual(len(corpus_mod.from_json("{not json")), 0)
        self.assertEqual(len(corpus_mod.from_json("[]")), 0)
        self.assertEqual(len(corpus_mod.from_json('{"entries": "nope"}')), 0)

    def test_malformed_entry_is_skipped_not_fatal(self):
        text = json.dumps({"entries": [{"cmd": "ok", "count": 2},
                                       {"no_cmd": True}, "garbage"]})
        parsed = corpus_mod.from_json(text)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed.get("ok").count, 2)

    def test_missing_file_loads_empty(self):
        self.assertEqual(len(corpus_mod.load("/nonexistent/x/corpus.json")), 0)

    def test_save_failure_returns_false(self):
        corpus = corpus_mod.Corpus()
        corpus.add("ls", when=NOW)
        self.assertFalse(corpus_mod.save(corpus, "/proc/nope/corpus.json"))


class PruneTests(unittest.TestCase):
    def test_prune_keeps_the_strongest_entries(self):
        config = CompletionConfig(max_entries=2, half_life_days=14)
        corpus = corpus_mod.Corpus(config=config)
        for _ in range(10):
            corpus.add("frequent", when=NOW)
        corpus.add("recent", when=NOW)
        corpus.add("ancient", when=NOW - 400 * DAY)
        removed = corpus.prune(now=NOW)
        self.assertEqual(removed, 1)
        self.assertIsNotNone(corpus.get("frequent"))
        self.assertIsNone(corpus.get("ancient"))

    def test_prune_is_a_noop_under_the_cap(self):
        corpus = corpus_mod.Corpus(config=CompletionConfig(max_entries=50))
        corpus.add("ls", when=NOW)
        self.assertEqual(corpus.prune(now=NOW), 0)


class HistorySeedTests(unittest.TestCase):
    def test_parse_skips_timestamps_and_dedupes(self):
        text = "#1700000000\nls -la\nmake test\nls -la\n\n"
        self.assertEqual(corpus_mod.parse_bash_history(text),
                         ["ls -la", "make test"])

    def test_seed_marks_entries_old_so_real_usage_outranks_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hist")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("make build\n")
            corpus = corpus_mod.Corpus()
            added = corpus_mod.seed_from_history(corpus, path=path, now=NOW)
            self.assertEqual(added, 1)
            entry = corpus.get("make build")
            self.assertEqual(entry.count, 1)
            self.assertAlmostEqual(
                entry.last_used, NOW - corpus_mod.SEED_AGE_DAYS * DAY,
                delta=1.0)
            # History records neither cwd nor exit status.
            self.assertEqual(entry.cwds, {})
            self.assertEqual((entry.ok, entry.fail), (0, 0))

    def test_seeding_happens_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hist")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("ls\n")
            corpus = corpus_mod.Corpus()
            corpus_mod.seed_from_history(corpus, path=path, now=NOW)
            self.assertEqual(
                corpus_mod.seed_from_history(corpus, path=path, now=NOW), 0)

    def test_missing_history_marks_seeded_and_does_not_raise(self):
        corpus = corpus_mod.Corpus()
        self.assertEqual(
            corpus_mod.seed_from_history(corpus, path="/nope/hist"), 0)
        self.assertTrue(corpus.seeded)

    def test_seed_redacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hist")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("export API_KEY=sk-abcdefghijklmnopqrstuvwxyz01\n")
            corpus = corpus_mod.Corpus()
            corpus_mod.seed_from_history(corpus, path=path, now=NOW)
            self.assertTrue(all("sk-abcdefghij" not in e.cmd
                                for e in corpus.all()))


class OpenCorpusTests(unittest.TestCase):
    def test_disabled_config_yields_an_inert_corpus(self):
        corpus = corpus_mod.open_corpus(
            config=CompletionConfig(corpus=False))
        self.assertEqual(len(corpus), 0)
        self.assertTrue(corpus.seeded)

    def test_open_seeds_then_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist = os.path.join(tmp, "hist")
            store = os.path.join(tmp, "corpus.json")
            with open(hist, "w", encoding="utf-8") as fh:
                fh.write("make test\n")
            env = {"HISTFILE": hist}
            first = corpus_mod.open_corpus(path=store, env=env, now=NOW)
            self.assertIsNotNone(first.get("make test"))
            # Second open loads from disk and does not re-seed.
            second = corpus_mod.open_corpus(path=store, env=env, now=NOW)
            self.assertTrue(second.seeded)
            self.assertIsNotNone(second.get("make test"))

    def test_default_store_path_follows_xdg(self):
        path = corpus_mod.default_store_path({"XDG_DATA_HOME": "/x/data"})
        self.assertEqual(
            path, "/x/data/agent-terminal/completion/corpus.json")


if __name__ == "__main__":
    unittest.main()
