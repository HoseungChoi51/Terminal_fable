"""Unit-level contracts for the copilot P2 suggestion core."""

import unittest

from agent_terminal.copilot import fuzzy
from agent_terminal.copilot import recipes as crecipes
from agent_terminal.copilot import risk as crisk
from agent_terminal.copilot import suggest as csuggest


class FuzzyTests(unittest.TestCase):
    def test_empty_query_matches(self):
        self.assertEqual(fuzzy.score("", "anything"), 0.0)

    def test_no_match_returns_none(self):
        self.assertIsNone(fuzzy.score("xyz", "abc"))
        self.assertIsNone(fuzzy.score("abc", ""))

    def test_prefix_beats_midword(self):
        prefix = fuzzy.score("git", "git status")
        mid = fuzzy.score("git", "digit gizmo grit")
        self.assertIsNotNone(prefix)
        self.assertGreater(prefix, mid or -1)

    def test_multiword_requires_all_tokens(self):
        hay = "Show the largest files and directories, biggest first"
        self.assertIsNotNone(fuzzy.score("largest files", hay))
        self.assertIsNone(fuzzy.score("largest bananas", hay))

    def test_case_insensitive(self):
        self.assertIsNotNone(fuzzy.score("GIT", "git status"))


class FuzzyRankingTests(unittest.TestCase):
    """Design doc 5.9 example queries rank the right recipe first."""

    def _top(self, query):
        results = crecipes.search(query, limit=1)
        return results[0].command if results else None

    def test_sort_by_size(self):
        self.assertEqual(self._top("sort by size"),
                         "du -ah . | sort -rh | head -20")

    def test_number_of_lines(self):
        self.assertIn("wc -l", self._top("number of lines"))

    def test_unzip_all_files(self):
        self.assertIn("unzip", self._top("unzip all files"))

    def test_kill_process_on_port(self):
        self.assertIn("tcp:<port>", self._top("kill process on port"))

    def test_split_video_frames(self):
        self.assertIn("ffmpeg", self._top("split video into frames"))


class RiskTests(unittest.TestCase):
    def assertRisk(self, command, expected_display):
        result = crisk.classify(command)
        self.assertEqual(result.display, expected_display,
                         f"{command!r} -> {result.display}")

    def test_read_only(self):
        self.assertRisk("ls -la", crisk.READ_ONLY)
        self.assertRisk("git status", crisk.READ_ONLY)
        self.assertRisk("grep -rn foo .", crisk.READ_ONLY)

    def test_local_change(self):
        self.assertRisk("mkdir logs", crisk.LOCAL_CHANGE)
        self.assertRisk("git commit -m x", crisk.LOCAL_CHANGE)
        self.assertRisk("cp a b", crisk.LOCAL_CHANGE)

    def test_install(self):
        self.assertRisk("pip install requests", crisk.INSTALL)
        self.assertRisk("npm install", crisk.INSTALL)
        self.assertRisk("docker pull nginx", crisk.REMOTE)  # pull is network

    def test_remote(self):
        self.assertRisk("ssh user@host", crisk.REMOTE)
        self.assertRisk("git push origin main", crisk.REMOTE)
        self.assertRisk("curl https://example.com", crisk.REMOTE)

    def test_privileged(self):
        result = crisk.classify("sudo systemctl restart nginx")
        self.assertIn(crisk.PRIVILEGED, result.labels)

    def test_destructive_examples(self):
        # every design-doc 3.3 example
        self.assertRisk("rm -rf build/", crisk.DESTRUCTIVE)
        self.assertRisk("git reset --hard", crisk.DESTRUCTIVE)
        self.assertRisk("git clean -fd", crisk.DESTRUCTIVE)
        self.assertRisk("docker system prune", crisk.DESTRUCTIVE)
        self.assertRisk("kubectl delete pod api", crisk.DESTRUCTIVE)
        self.assertRisk("terraform destroy", crisk.DESTRUCTIVE)

    def test_sudo_destructive_is_destructive_and_privileged(self):
        result = crisk.classify("sudo rm -rf /var/cache")
        self.assertEqual(result.display, crisk.DESTRUCTIVE)
        self.assertIn(crisk.PRIVILEGED, result.labels)

    def test_curl_pipe_sh_is_install_and_destructive(self):
        result = crisk.classify("curl -fsSL https://get.example.com | sh")
        self.assertIn(crisk.INSTALL, result.labels)
        self.assertIn(crisk.DESTRUCTIVE, result.labels)

    def test_pipeline_takes_max_severity(self):
        self.assertRisk("cat urls.txt | grep http | rm -rf -",
                        crisk.DESTRUCTIVE)

    def test_find_delete_and_exec_are_destructive(self):
        self.assertRisk("find . -delete", crisk.DESTRUCTIVE)
        self.assertRisk("find / -name '*.log' -delete", crisk.DESTRUCTIVE)
        self.assertRisk("find . -type f -exec rm {} +", crisk.DESTRUCTIVE)
        self.assertRisk("find . -name x", crisk.READ_ONLY)  # plain find

    def test_passthrough_wrappers_classify_inner_command(self):
        # The wrapper must not hide a destructive inner command.
        self.assertRisk("env rm -rf /tmp/x", crisk.DESTRUCTIVE)
        self.assertRisk("env FOO=bar rm -rf x", crisk.DESTRUCTIVE)
        self.assertRisk("xargs rm", crisk.DESTRUCTIVE)
        self.assertRisk("xargs -0 rm -f", crisk.DESTRUCTIVE)
        self.assertRisk("timeout 5 rm -rf x", crisk.DESTRUCTIVE)
        self.assertRisk("nice rm -rf x", crisk.DESTRUCTIVE)
        self.assertRisk("nohup rm -rf x", crisk.DESTRUCTIVE)
        # …and keep a benign inner command benign.
        self.assertRisk("env FOO=bar ls", crisk.READ_ONLY)
        self.assertRisk("timeout 5 ls -la", crisk.READ_ONLY)

    def test_unknown(self):
        self.assertRisk("frobnicate --wibble", crisk.UNKNOWN)

    def test_empty(self):
        self.assertRisk("", crisk.UNKNOWN)

    def test_known_beats_unknown_in_pipeline(self):
        result = crisk.classify("frobnicate | rm -rf x")
        self.assertNotIn(crisk.UNKNOWN, result.labels)
        self.assertEqual(result.display, crisk.DESTRUCTIVE)


class RecipeMetadataTests(unittest.TestCase):
    def test_all_recipes_have_metadata(self):
        self.assertGreaterEqual(len(crecipes.BUILTIN_RECIPES), 40)
        for recipe in crecipes.BUILTIN_RECIPES:
            self.assertTrue(recipe.command)
            self.assertTrue(recipe.description)
            self.assertTrue(recipe.keywords)
            self.assertIsNotNone(recipe.risk)
            self.assertEqual(recipe.source, "builtin")

    def test_placeholders_extracted(self):
        recipe = next(r for r in crecipes.BUILTIN_RECIPES
                      if "<port>" in r.command)
        self.assertIn("<port>", recipe.placeholders)

    def test_recipes_with_no_placeholder(self):
        recipe = next(r for r in crecipes.BUILTIN_RECIPES
                      if r.command == "df -h")
        self.assertEqual(recipe.placeholders, ())

    def test_commands_unique(self):
        commands = [r.command for r in crecipes.BUILTIN_RECIPES]
        self.assertEqual(len(commands), len(set(commands)))


class SuggestTests(unittest.TestCase):
    def test_recipes_only(self):
        out = csuggest.build_suggestions("sort by size")
        self.assertTrue(out)
        self.assertEqual(out[0].command, "du -ah . | sort -rh | head -20")
        self.assertEqual(out[0].source, csuggest.SOURCE_RECIPE)
        self.assertEqual(out[0].risk.display, crisk.READ_ONLY)

    def test_history_merges_and_wins_dupes(self):
        history = ["pytest -q", "git commit -m wip", "du -ah . | sort -rh | head -20"]
        out = csuggest.build_suggestions("du", history=history)
        dup = [s for s in out if s.command == "du -ah . | sort -rh | head -20"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0].source, csuggest.SOURCE_HISTORY)

    def test_history_trivial_dropped(self):
        out = csuggest.build_suggestions("", history=["ls", "cd /tmp", "pytest"])
        history_cmds = {s.command for s in out
                        if s.source == csuggest.SOURCE_HISTORY}
        self.assertIn("pytest", history_cmds)
        self.assertNotIn("ls", history_cmds)
        self.assertNotIn("cd /tmp", history_cmds)

    def test_history_recency_order(self):
        history = ["make one", "make two", "make three"]
        out = csuggest.build_suggestions("make", history=history, limit=3)
        hist = [s.command for s in out if s.source == csuggest.SOURCE_HISTORY]
        self.assertEqual(hist[0], "make three")   # most recent first

    def test_limit_respected(self):
        out = csuggest.build_suggestions("git", limit=3)
        self.assertLessEqual(len(out), 3)

    def test_no_match_empty(self):
        self.assertEqual(
            csuggest.build_suggestions("zzzqqq", history=["ls"]), [])

    def test_risk_attached(self):
        out = csuggest.build_suggestions("", history=["rm -rf build"])
        match = [s for s in out if s.command == "rm -rf build"][0]
        self.assertEqual(match.risk.display, crisk.DESTRUCTIVE)


class GhostCompletionTests(unittest.TestCase):
    def test_completes_from_history(self):
        suffix = csuggest.ghost_completion(
            "git st", history=["git status -sb"])
        self.assertEqual(suffix, "atus -sb")

    def test_most_recent_history_wins(self):
        suffix = csuggest.ghost_completion(
            "make ", history=["make one", "make two", "make three"])
        self.assertEqual(suffix, "three")

    def test_too_short_prefix(self):
        self.assertIsNone(csuggest.ghost_completion(
            "g", history=["git status"]))

    def test_no_match(self):
        self.assertIsNone(csuggest.ghost_completion(
            "zzz", history=["git status"]))

    def test_never_ghosts_destructive(self):
        self.assertIsNone(csuggest.ghost_completion(
            "rm ", history=["rm -rf build"]))

    def test_never_ghosts_privileged(self):
        self.assertIsNone(csuggest.ghost_completion(
            "sud", history=["sudo apt update"]))

    def test_never_ghosts_multiline(self):
        self.assertIsNone(csuggest.ghost_completion(
            "for", history=["for f in *; do\necho $f\ndone"]))

    def test_recipes_below_default_threshold(self):
        # a recipe prefix does not ghost at the default confidence...
        self.assertIsNone(csuggest.ghost_completion("du -a"))
        # ...but does when the bar is lowered
        self.assertIsNotNone(
            csuggest.ghost_completion("du -a", min_confidence=0.6))

    def test_history_outranks_recipe(self):
        suffix = csuggest.ghost_completion(
            "du ", history=["du -sh ."], min_confidence=0.6)
        self.assertEqual(suffix, "-sh .")

    def test_exact_match_no_ghost(self):
        self.assertIsNone(csuggest.ghost_completion(
            "git status", history=["git status"]))


class InsertPlanTests(unittest.TestCase):
    def test_prefix_completion_feeds_suffix(self):
        clear, text = csuggest.insert_plan("git ", "git status")
        self.assertFalse(clear)
        self.assertEqual(text, "status")

    def test_non_prefix_clears_and_inserts_full(self):
        clear, text = csuggest.insert_plan("srt", "du -ah . | sort -rh")
        self.assertTrue(clear)
        self.assertEqual(text, "du -ah . | sort -rh")

    def test_empty_typed_clears(self):
        clear, text = csuggest.insert_plan("", "ls -la")
        self.assertTrue(clear)
        self.assertEqual(text, "ls -la")

    def test_never_contains_newline(self):
        for typed in ("", "g", "git s"):
            _, text = csuggest.insert_plan(typed, "git status")
            self.assertNotIn("\n", text)


if __name__ == "__main__":
    unittest.main()
