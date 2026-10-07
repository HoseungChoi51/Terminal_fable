"""Tests for frecency ranking and the dominance confidence gate."""

import os
import tempfile
import unittest

from agent_terminal.copilot import corpus as corpus_mod
from agent_terminal.copilot import ranking
from agent_terminal.copilot.config import CompletionConfig

DAY = 86400.0
NOW = 1_700_000_000.0


def corpus_with(*specs):
    """Build a corpus from (cmd, times, cwd, exit, age_days) tuples."""
    corpus = corpus_mod.Corpus()
    for cmd, times, cwd, exit_code, age_days in specs:
        for _ in range(times):
            corpus.add(cmd, cwd=cwd, exit_code=exit_code,
                       when=NOW - age_days * DAY)
    return corpus


class SquashTests(unittest.TestCase):
    def test_squash_is_bounded_and_monotonic(self):
        self.assertEqual(ranking.squash(0, 2.0), 0.0)
        self.assertLess(ranking.squash(1, 2.0), ranking.squash(5, 2.0))
        self.assertLess(ranking.squash(10_000, 2.0), 1.0)

    def test_scale_maps_to_one_half(self):
        self.assertAlmostEqual(ranking.squash(2.0, 2.0), 0.5)

    def test_negative_input_clamps_to_zero(self):
        self.assertEqual(ranking.squash(-5, 2.0), 0.0)


class FrecencyTests(unittest.TestCase):
    def test_recency_halves_at_the_half_life(self):
        self.assertAlmostEqual(
            ranking.recency_factor(NOW - 14 * DAY, NOW, 14), 0.5, places=6)

    def test_recent_beats_stale_at_equal_frequency(self):
        corpus = corpus_with(("make fresh", 3, None, 0, 0),
                             ("make stale", 3, None, 0, 90))
        ranked = ranking.rank(corpus, typed="make", now=NOW)
        self.assertEqual(ranked[0].command, "make fresh")

    def test_frequency_beats_a_single_recent_run(self):
        corpus = corpus_with(("make often", 40, None, 0, 3),
                             ("make once", 1, None, 0, 0))
        ranked = ranking.rank(corpus, typed="make", now=NOW)
        self.assertEqual(ranked[0].command, "make often")

    def test_frequency_is_damped_so_one_command_cannot_bury_everything(self):
        """log1p damping: 500 runs is worth well under 100x a single run."""
        heavy = corpus_with(("a", 500, None, 0, 0)).get("a")
        light = corpus_with(("b", 1, None, 0, 0)).get("b")
        ratio = (ranking.base_weight(heavy, NOW, 14)
                 / ranking.base_weight(light, NOW, 14))
        self.assertLess(ratio, 10.0)


class ContextScopingTests(unittest.TestCase):
    def test_cwd_match_flips_the_winner(self):
        """The defect this fixes: npm test ranking inside a Python repo."""
        corpus = corpus_with(("npm test", 5, "/js", 0, 0),
                             ("pytest", 5, "/py", 0, 0))
        in_js = ranking.rank(corpus, typed="", cwd="/js", now=NOW)
        in_py = ranking.rank(corpus, typed="", cwd="/py", now=NOW)
        self.assertEqual(in_js[0].command, "npm test")
        self.assertEqual(in_py[0].command, "pytest")

    def test_project_boost_applies_to_a_sibling_directory(self):
        corpus = corpus_with(("cargo build", 1, "/repo/src", 0, 0))
        entry = corpus.get("cargo build")
        self.assertEqual(
            ranking.context_factor(entry, "/repo/tests", "/repo"),
            ranking.PROJECT_BOOST)

    def test_exact_cwd_outranks_project_match(self):
        entry = corpus_with(("x", 1, "/repo/src", 0, 0)).get("x")
        exact = ranking.context_factor(entry, "/repo/src", "/repo")
        project = ranking.context_factor(entry, "/repo/tests", "/repo")
        self.assertGreater(exact, project)

    def test_unrelated_directory_gets_no_boost(self):
        entry = corpus_with(("x", 1, "/repo", 0, 0)).get("x")
        self.assertEqual(ranking.context_factor(entry, "/elsewhere", None),
                         1.0)


class ExitStatusTests(unittest.TestCase):
    def test_always_failing_is_damped_hardest(self):
        ok = corpus_with(("a", 3, None, 0, 0)).get("a")
        bad = corpus_with(("b", 3, None, 1, 0)).get("b")
        self.assertGreater(ranking.exit_factor(ok), ranking.exit_factor(bad))
        self.assertEqual(ranking.exit_factor(bad), ranking.EXIT_ONLY_FAILED)

    def test_seeded_entry_with_no_exit_data_still_ranks(self):
        """bash_history has no exit codes; unknown must not mean worthless."""
        unknown = corpus_with(("seeded", 1, None, None, 0)).get("seeded")
        self.assertEqual(ranking.exit_factor(unknown), ranking.EXIT_UNKNOWN)
        self.assertGreater(ranking.exit_factor(unknown),
                           ranking.EXIT_ONLY_FAILED)


class MatchQualityTests(unittest.TestCase):
    def test_exact_typed_text_is_not_a_candidate(self):
        self.assertIsNone(ranking._match_quality("ls", "ls"))

    def test_prefix_beats_fuzzy(self):
        prefix = ranking._match_quality("gco", "gcommit")
        fuzzy_hit = ranking._match_quality("gco", "git checkout")
        self.assertGreater(prefix, fuzzy_hit)

    def test_non_match_is_none(self):
        self.assertIsNone(ranking._match_quality("zzz", "ls -la"))

    def test_empty_typed_matches_everything(self):
        self.assertEqual(ranking._match_quality("", "anything"), 1.0)


class ConfidenceTests(unittest.TestCase):
    def test_single_candidate_is_fully_confident(self):
        corpus = corpus_with(("make test", 5, None, 0, 0))
        ranked = ranking.rank(corpus, typed="make", now=NOW)
        self.assertEqual(ranking.confidence(ranked, "make"), 1.0)

    def test_two_equals_score_one_half_and_fail_the_default_gate(self):
        corpus = corpus_with(("make test", 5, None, 0, 0),
                             ("make build", 5, None, 0, 0))
        ranked = ranking.rank(corpus, typed="make", now=NOW)
        # Near a coin flip (the shorter completion keeps a marginal edge),
        # and comfortably below the default gate.
        self.assertAlmostEqual(ranking.confidence(ranked, "make"), 0.5,
                               delta=0.02)
        self.assertLess(ranking.confidence(ranked, "make"), 0.7)

    def test_entries_proposing_the_same_suffix_are_not_rivals(self):
        ranked = [ranking.Scored(corpus_mod.Entry("make test"), 4.0),
                  ranking.Scored(corpus_mod.Entry("make test"), 3.0)]
        self.assertEqual(ranking.confidence(ranked, "make"), 1.0)

    def test_a_dominant_winner_clears_the_gate(self):
        corpus = corpus_with(("make test", 60, None, 0, 0),
                             ("make zzz", 1, None, 1, 200))
        ranked = ranking.rank(corpus, typed="make", now=NOW)
        self.assertGreater(ranking.confidence(ranked, "make"), 0.7)

    def test_empty_ranking_is_zero(self):
        self.assertEqual(ranking.confidence([]), 0.0)


class BestCompletionTests(unittest.TestCase):
    def test_returns_the_dominant_command(self):
        corpus = corpus_with(("make test", 20, None, 0, 0))
        command, conf = ranking.best_completion(corpus, "make", now=NOW)
        self.assertEqual(command, "make test")
        self.assertEqual(conf, 1.0)

    def test_ambiguity_is_withheld(self):
        corpus = corpus_with(("make test", 5, None, 0, 0),
                             ("make build", 5, None, 0, 0))
        command, conf = ranking.best_completion(corpus, "make", now=NOW)
        self.assertIsNone(command)
        self.assertAlmostEqual(conf, 0.5, delta=0.02)

    def test_lowering_the_gate_admits_an_ambiguous_winner(self):
        """min_confidence is a real dial now, not a decorative constant."""
        corpus = corpus_with(("make test", 5, None, 0, 0),
                             ("make build", 5, None, 0, 0))
        command, _ = ranking.best_completion(corpus, "make", now=NOW,
                                             min_confidence=0.4)
        self.assertIsNotNone(command)

    def test_fuzzy_only_matches_are_not_ghostable(self):
        """Ghost text renders a suffix, so only prefix extensions qualify."""
        corpus = corpus_with(("git checkout main", 20, None, 0, 0))
        command, _ = ranking.best_completion(corpus, "gco", now=NOW)
        self.assertIsNone(command)

    def test_empty_corpus_returns_nothing(self):
        command, conf = ranking.best_completion(corpus_mod.Corpus(), "ma")
        self.assertIsNone(command)
        self.assertEqual(conf, 0.0)


class ChainTests(unittest.TestCase):
    def test_chain_mode_promotes_the_usual_follow_up(self):
        corpus = corpus_mod.Corpus()
        for _ in range(5):
            corpus.add("git commit", prev="git add .", exit_code=0, when=NOW)
        for _ in range(5):
            corpus.add("git clean", exit_code=0, when=NOW)
        config = CompletionConfig(ranking="chain")
        ranked = ranking.rank(corpus, typed="git c", now=NOW, config=config,
                              prev_command="git add .")
        self.assertEqual(ranked[0].command, "git commit")

    def test_frecency_mode_ignores_the_bigram(self):
        corpus = corpus_mod.Corpus()
        for _ in range(5):
            corpus.add("git commit", prev="git add .", exit_code=0, when=NOW)
        entry = corpus.get("git commit")
        self.assertEqual(
            ranking.score_entry(entry, typed="git", now=NOW,
                                mode=ranking.FRECENCY,
                                prev_command="git add ."),
            ranking.score_entry(entry, typed="git", now=NOW,
                                mode=ranking.FRECENCY, prev_command=None))

    def test_unknown_predecessor_is_neutral(self):
        entry = corpus_with(("x", 1, None, 0, 0)).get("x")
        self.assertEqual(ranking.chain_factor(entry, "never-seen"), 1.0)


class ProjectRootTests(unittest.TestCase):
    def test_walks_up_to_the_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            deep = os.path.join(tmp, "a", "b", "c")
            os.makedirs(deep)
            os.makedirs(os.path.join(tmp, ".git"))
            self.assertEqual(ranking.find_project_root(deep),
                             os.path.normpath(tmp))

    def test_no_marker_yields_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            deep = os.path.join(tmp, "a")
            os.makedirs(deep)
            # A real ancestor could carry a marker, so use a stub.
            self.assertIsNone(
                ranking.find_project_root(deep, exists=lambda p: False))

    def test_empty_cwd_is_none(self):
        self.assertIsNone(ranking.find_project_root(None))


if __name__ == "__main__":
    unittest.main()
