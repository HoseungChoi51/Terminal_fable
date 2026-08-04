"""Tests for the completion wiring: config, journal sink, banded merge.

Covers the seams between the new corpus/ranking core and the surfaces
that consume it, which the per-module tests do not exercise.
"""

import unittest

from agent_terminal.copilot import corpus as corpus_mod
from agent_terminal.copilot import journal as journal_mod
from agent_terminal.copilot import ranking
from agent_terminal.copilot import suggest
from agent_terminal.copilot.config import (CompletionConfig,
                                           parse_assistant_config)

NOW = 1_700_000_000.0


class CompletionConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = CompletionConfig()
        self.assertEqual(config.ranking, "frecency")
        self.assertTrue(config.corpus)
        self.assertTrue(config.seed_bash_history)
        self.assertTrue(config.shell_completion)

    def test_parsed_from_the_assistant_section(self):
        parsed = parse_assistant_config(
            {"completion": {"ranking": "chain", "corpus": False,
                            "max_entries": 100, "half_life_days": 7}})
        self.assertEqual(parsed.completion.ranking, "chain")
        self.assertFalse(parsed.completion.corpus)
        self.assertEqual(parsed.completion.max_entries, 100)
        self.assertEqual(parsed.completion.half_life_days, 7)

    def test_all_three_ranking_modes_are_selectable(self):
        for mode in ("frecency", "chain", "token"):
            parsed = parse_assistant_config({"completion": {"ranking": mode}})
            self.assertEqual(parsed.completion.ranking, mode)

    def test_unknown_ranking_mode_falls_back(self):
        parsed = parse_assistant_config(
            {"completion": {"ranking": "telepathy"}})
        self.assertEqual(parsed.completion.ranking, "frecency")

    def test_malformed_section_never_raises(self):
        """Config problems must not keep the terminal from launching."""
        for payload in ({"completion": "nonsense"}, {"completion": None},
                        {"completion": []}):
            self.assertEqual(
                parse_assistant_config(payload).completion.ranking,
                "frecency")

    def test_missing_section_uses_defaults(self):
        self.assertEqual(parse_assistant_config({}).completion,
                         CompletionConfig())


class JournalSinkTests(unittest.TestCase):
    def _run_command(self, journal, cmd, exit_code=0):
        import base64
        payload = base64.b64encode(cmd.encode()).decode()
        journal.apply_batch(
            [(journal_mod.PREEXEC, True)], timestamp=1.0, wall_time=NOW,
            cwd="/repo")
        journal.apply_batch(
            [(journal_mod.COMMAND_TERMPROP, payload),
             (journal_mod.POSTEXEC, str(exit_code)),
             (journal_mod.PRECMD, True)],
            timestamp=2.0, wall_time=NOW, cwd="/repo")

    def test_sink_receives_finished_commands_with_the_predecessor(self):
        seen = []
        journal = journal_mod.PaneJournal(sink=lambda r, p: seen.append(
            (r.cmd, p)))
        self._run_command(journal, "git add .")
        self._run_command(journal, "git commit")
        self.assertEqual(seen, [("git add .", None),
                                ("git commit", "git add .")])

    def test_a_failing_sink_cannot_break_journalling(self):
        def boom(record, previous):
            raise RuntimeError("corpus on fire")

        journal = journal_mod.PaneJournal(sink=boom)
        self._run_command(journal, "ls")
        self.assertEqual(len(journal.snapshot()), 1)

    def test_no_sink_is_the_default(self):
        journal = journal_mod.PaneJournal()
        self._run_command(journal, "ls")
        self.assertEqual(len(journal.snapshot()), 1)

    def test_sink_feeds_a_corpus_end_to_end(self):
        corpus = corpus_mod.Corpus()
        journal = journal_mod.PaneJournal(
            sink=lambda r, p: corpus.add(r.cmd, cwd=r.cwd,
                                         exit_code=r.exit_code,
                                         when=r.started_at, prev=p))
        self._run_command(journal, "make test", exit_code=0)
        entry = corpus.get("make test")
        self.assertEqual(entry.count, 1)
        self.assertEqual(entry.cwds, {"/repo": 1})
        self.assertEqual(entry.ok, 1)


class BandedMergeTests(unittest.TestCase):
    def _suggestion(self, command, score):
        return suggest.make_suggestion(command, "label", score=score)

    def test_bands_outrank_scores(self):
        merged = suggest.merge_suggestions(
            (suggest.BAND_PRIMARY, [self._suggestion("high-score", 99.0)]),
            (suggest.BAND_CORRECTION, [self._suggestion("correction", 0.1)]))
        self.assertEqual(merged[0].command, "correction")

    def test_score_orders_within_a_band(self):
        merged = suggest.merge_suggestions(
            (suggest.BAND_PRIMARY, [self._suggestion("weak", 1.0),
                                    self._suggestion("strong", 9.0)]))
        self.assertEqual([s.command for s in merged], ["strong", "weak"])

    def test_a_strong_habit_can_outrank_a_weak_readme_command(self):
        """The old merge ordered by source, making scores decorative."""
        merged = suggest.merge_suggestions(
            (suggest.BAND_PRIMARY, [self._suggestion("make docs", 5.0)]),
            (suggest.BAND_PRIMARY, [self._suggestion("make test", 9.5)]))
        self.assertEqual(merged[0].command, "make test")

    def test_duplicates_keep_the_higher_ranked_copy(self):
        merged = suggest.merge_suggestions(
            (suggest.BAND_ARGUMENT, [self._suggestion("dup", 1.0)]),
            (suggest.BAND_PRIMARY, [self._suggestion("dup", 9.0)]))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].score, 1.0)   # the higher band won

    def test_limit_is_respected(self):
        items = [self._suggestion(f"c{i}", float(i)) for i in range(20)]
        self.assertEqual(
            len(suggest.merge_suggestions((suggest.BAND_PRIMARY, items),
                                          limit=5)), 5)

    def test_empty_groups_are_tolerated(self):
        self.assertEqual(
            suggest.merge_suggestions((suggest.BAND_PRIMARY, []),
                                      (suggest.BAND_ARGUMENT, None)), [])


class NormalizationTests(unittest.TestCase):
    def test_raw_scores_are_squashed_into_the_band(self):
        raw = [suggest.make_suggestion("a", "l", score=50.0)]
        self.assertLessEqual(suggest.normalized(raw)[0].score,
                             suggest.CORPUS_CEILING)

    def test_normalization_preserves_order(self):
        raw = [suggest.make_suggestion("a", "l", score=2.0),
               suggest.make_suggestion("b", "l", score=20.0)]
        out = suggest.normalized(raw)
        self.assertLess(out[0].score, out[1].score)


class CorpusSuggestionTests(unittest.TestCase):
    def _corpus(self):
        corpus = corpus_mod.Corpus()
        for _ in range(10):
            corpus.add("make test", cwd="/repo", exit_code=0, when=NOW)
        return corpus

    def test_corpus_entries_are_bounded_by_the_ceiling(self):
        out = suggest.build_corpus_suggestions(
            "make", self._corpus(), cwd="/repo", now=NOW)
        self.assertTrue(out)
        self.assertLessEqual(out[0].score, suggest.CORPUS_CEILING)
        self.assertEqual(out[0].source, suggest.SOURCE_HISTORY)

    def test_recipes_rank_below_your_own_habits(self):
        from agent_terminal.copilot.recipes import BUILTIN_RECIPES
        out = suggest.build_corpus_suggestions(
            "make", self._corpus(), recipes=BUILTIN_RECIPES, cwd="/repo",
            now=NOW, limit=None)
        habits = [s for s in out if s.source == suggest.SOURCE_HISTORY]
        recipes = [s for s in out if s.source == suggest.SOURCE_RECIPE]
        if recipes:
            self.assertGreater(max(s.score for s in habits),
                               max(s.score for s in recipes))

    def test_results_are_sorted_by_score(self):
        corpus = corpus_mod.Corpus()
        for _ in range(20):
            corpus.add("make test", exit_code=0, when=NOW)
        corpus.add("make rare", exit_code=0, when=NOW)
        out = suggest.build_corpus_suggestions("make", corpus, now=NOW)
        self.assertEqual(out[0].command, "make test")


class CorpusGhostTests(unittest.TestCase):
    def test_dominant_habit_produces_a_suffix(self):
        corpus = corpus_mod.Corpus()
        for _ in range(20):
            corpus.add("make test", cwd="/repo", exit_code=0, when=NOW)
        self.assertEqual(
            suggest.corpus_ghost_completion("make", corpus, cwd="/repo",
                                            now=NOW),
            " test")

    def test_short_prefixes_are_ignored(self):
        corpus = corpus_mod.Corpus()
        corpus.add("make test", exit_code=0, when=NOW)
        self.assertIsNone(suggest.corpus_ghost_completion("m", corpus))

    def test_destructive_commands_are_never_ghosted(self):
        corpus = corpus_mod.Corpus()
        for _ in range(50):
            corpus.add("rm -rf /var/data", cwd="/repo", exit_code=0,
                       when=NOW)
        self.assertIsNone(
            suggest.corpus_ghost_completion("rm ", corpus, cwd="/repo",
                                            now=NOW))

    def test_ambiguity_yields_nothing(self):
        corpus = corpus_mod.Corpus()
        for _ in range(5):
            corpus.add("make test", exit_code=0, when=NOW)
            corpus.add("make build", exit_code=0, when=NOW)
        self.assertIsNone(
            suggest.corpus_ghost_completion("make", corpus, now=NOW))

    def test_ghost_suffix_rejects_a_non_extension(self):
        self.assertIsNone(suggest.ghost_suffix("git status", "make"))
        self.assertIsNone(suggest.ghost_suffix("make", "make"))

    def test_cwd_decides_which_habit_is_ghosted(self):
        corpus = corpus_mod.Corpus()
        for _ in range(10):
            corpus.add("make test-js", cwd="/js", exit_code=0, when=NOW)
            corpus.add("make test-py", cwd="/py", exit_code=0, when=NOW)
        self.assertEqual(
            suggest.corpus_ghost_completion("make test-", corpus, cwd="/js",
                                            now=NOW, min_confidence=0.5),
            "js")
        self.assertEqual(
            suggest.corpus_ghost_completion("make test-", corpus, cwd="/py",
                                            now=NOW, min_confidence=0.5),
            "py")


class RankingModeSwitchTests(unittest.TestCase):
    def test_mode_constants_match_the_config_vocabulary(self):
        self.assertEqual(
            {ranking.FRECENCY, ranking.CHAIN, ranking.TOKEN},
            {"frecency", "chain", "token"})


class ProcessCorpusLifecycleTests(unittest.TestCase):
    """The module-level corpus accessor in native_terminal.

    Guards the dogfooding invariant: nothing about the completion cache
    may prevent a pane from opening.
    """

    def setUp(self):
        from agent_terminal import native_terminal as nt
        self.nt = nt
        self._saved = nt._COMPLETION_CORPUS
        nt._COMPLETION_CORPUS = None
        self.addCleanup(self._restore)

    def _restore(self):
        self.nt._COMPLETION_CORPUS = self._saved

    def _assistant(self, **completion):
        from agent_terminal.copilot.config import AssistantConfig
        return AssistantConfig(completion=CompletionConfig(**completion))

    def test_disabled_corpus_returns_none(self):
        self.assertIsNone(
            self.nt.completion_corpus(self._assistant(corpus=False)))

    def test_no_assistant_returns_none(self):
        self.assertIsNone(self.nt.completion_corpus(None))

    def test_a_broken_store_degrades_to_an_empty_corpus(self):
        import agent_terminal.copilot.corpus as corpus_module

        def explode(**kwargs):
            raise OSError("disk gone")

        original = corpus_module.open_corpus
        corpus_module.open_corpus = explode
        self.addCleanup(setattr, corpus_module, "open_corpus", original)
        corpus = self.nt.completion_corpus(
            self._assistant(seed_bash_history=False))
        self.assertIsNotNone(corpus)
        self.assertEqual(len(corpus), 0)

    def test_saving_a_clean_corpus_is_a_noop(self):
        self.nt._COMPLETION_CORPUS = corpus_mod.Corpus()
        self.nt.save_completion_corpus(force=True)   # must not raise

    def test_save_failure_is_swallowed(self):
        import agent_terminal.copilot.corpus as corpus_module

        def explode(*args, **kwargs):
            raise OSError("read-only filesystem")

        corpus = corpus_mod.Corpus()
        corpus.add("ls", when=NOW)
        self.nt._COMPLETION_CORPUS = corpus
        original = corpus_module.save
        corpus_module.save = explode
        self.addCleanup(setattr, corpus_module, "save", original)
        self.nt.save_completion_corpus(force=True)   # must not raise


if __name__ == "__main__":
    unittest.main()
