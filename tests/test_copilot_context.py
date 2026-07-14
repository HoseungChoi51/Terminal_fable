"""Unit-level contracts for the P3 context + typo core."""

import os
import tempfile
import time
import unittest
from pathlib import Path

from agent_terminal.copilot import context as cctx
from agent_terminal.copilot import risk as crisk
from agent_terminal.copilot import typo as ctypo
from agent_terminal.native_terminal import parse_markdown_blocks


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def touch(self, name, content="x"):
        p = Path(self.dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p


class ProjectDetectionTests(_Tmp):
    def test_python(self):
        self.touch("pyproject.toml")
        self.touch("run.py")
        project = cctx.detect_project(self.dir)
        self.assertEqual(project.kind, "python")
        self.assertEqual(project.name, os.path.basename(self.dir))

    def test_node(self):
        self.touch("package.json", "{}")
        self.assertEqual(cctx.detect_project(self.dir).kind, "node")

    def test_rust(self):
        self.touch("Cargo.toml")
        self.assertEqual(cctx.detect_project(self.dir).kind, "rust")

    def test_git_fallback(self):
        (Path(self.dir) / ".git").mkdir()
        self.assertEqual(cctx.detect_project(self.dir).kind, "git")

    def test_none(self):
        self.touch("random.txt")
        self.assertIsNone(cctx.detect_project(self.dir))


class ProjectRunCommandsTests(_Tmp):
    def test_python_with_run_and_uv(self):
        for f in ("pyproject.toml", "uv.lock", "run.py"):
            self.touch(f)
        project = cctx.detect_project(self.dir)
        cmds = cctx.project_run_commands(project, cwd=self.dir)
        self.assertIn("python run.py", cmds)
        self.assertIn("uv run pytest", cmds)
        self.assertIn("pytest", cmds)

    def test_node_scripts_from_package_json(self):
        self.touch("package.json",
                   '{"scripts": {"dev": "vite", "test": "jest"}}')
        project = cctx.detect_project(self.dir)
        cmds = cctx.project_run_commands(project, cwd=self.dir)
        self.assertIn("npm run dev", cmds)
        self.assertIn("npm run test", cmds)


class ReadmeExtractionTests(unittest.TestCase):
    def test_extracts_run_commands(self):
        readme = ("# Project\n\nRun locally:\n\n"
                  "```bash\n$ uv run this_app\npytest -q\n```\n\n"
                  "```\nsome prose, not a command\n```\n")
        blocks = parse_markdown_blocks(readme)
        cmds = cctx.readme_run_commands(blocks)
        self.assertIn("uv run this_app", cmds)
        self.assertIn("pytest -q", cmds)
        self.assertNotIn("some prose, not a command", cmds)

    def test_ignores_non_shell_language(self):
        blocks = parse_markdown_blocks("```python\nimport os\n```\n")
        self.assertEqual(cctx.readme_run_commands(blocks), [])


class ArgumentExpectationTests(unittest.TestCase):
    def test_cd_expects_dirs(self):
        self.assertEqual(cctx.argument_expectation("cd ").kind, cctx.DIRS)

    def test_cat_expects_files(self):
        self.assertEqual(cctx.argument_expectation("cat re").kind, cctx.FILES)

    def test_tar_x_expects_archives(self):
        self.assertEqual(cctx.argument_expectation("tar -xf ").kind,
                         cctx.ARCHIVES)

    def test_ffmpeg_i_expects_media(self):
        self.assertEqual(cctx.argument_expectation("ffmpeg -i ").kind,
                         cctx.MEDIA)

    def test_apt_install_deb(self):
        self.assertEqual(cctx.argument_expectation("sudo apt install ./").kind,
                         cctx.DEB)

    def test_git_checkout_branches(self):
        self.assertEqual(cctx.argument_expectation("git checkout ").kind,
                         cctx.BRANCHES)

    def test_git_clone_repo_url_guide(self):
        spec = cctx.argument_expectation("git clone ")
        self.assertEqual(spec.kind, cctx.REPO_URL)
        self.assertIn("repository URL", spec.guide)

    def test_ssh_hosts(self):
        self.assertEqual(cctx.argument_expectation("ssh ").kind,
                         cctx.SSH_HOSTS)

    def test_partial_and_prefix(self):
        spec = cctx.argument_expectation("sudo apt install ./pk")
        self.assertEqual(spec.partial, "./pk")
        self.assertEqual(spec.prefix, "sudo apt install ")

    def test_plain_command_none(self):
        self.assertEqual(cctx.argument_expectation("ls").kind, cctx.NONE)


class FileCompletionTests(_Tmp):
    def test_newest_deb_first(self):
        old = self.touch("old_1.0_amd64.deb")
        new = self.touch("new_2.0_amd64.deb")
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        self.touch("notes.txt")
        out = cctx.file_completions(self.dir, cctx.DEB, "./")
        self.assertEqual(out[0], "./new_2.0_amd64.deb")
        self.assertNotIn("./notes.txt", out)

    def test_dirs_only(self):
        (Path(self.dir) / "src").mkdir()
        self.touch("file.txt")
        out = cctx.file_completions(self.dir, cctx.DIRS, "")
        self.assertIn("src/", out)
        self.assertNotIn("file.txt", out)

    def test_partial_fragment_filters(self):
        self.touch("alpha.mp4")
        self.touch("beta.mp4")
        out = cctx.file_completions(self.dir, cctx.MEDIA, "al")
        self.assertEqual(out, ["alpha.mp4"])


class SshHostsTests(_Tmp):
    def test_parses_host_aliases(self):
        cfg = self.touch(".ssh_config",
                         "Host prod web1\n  HostName x\nHost *\n  User y\n")
        hosts = cctx.ssh_hosts(str(cfg))
        self.assertIn("prod", hosts)
        self.assertIn("web1", hosts)
        self.assertNotIn("*", hosts)


class MenuSuggestionTests(_Tmp):
    def test_deb_completion_suggestion(self):
        deb = self.touch("pkg_1_amd64.deb")
        os.utime(deb, (2000, 2000))
        out = cctx.menu_suggestions("sudo apt install ./", self.dir)
        self.assertTrue(out)
        self.assertEqual(out[0].command, "sudo apt install ./pkg_1_amd64.deb")
        self.assertEqual(out[0].label, "recent .deb")

    def test_project_commands_when_fresh(self):
        self.touch("pyproject.toml")
        self.touch("run.py")
        project = cctx.detect_project(self.dir)
        out = cctx.menu_suggestions("", self.dir, project=project)
        commands = [s.command for s in out]
        self.assertIn("python run.py", commands)

    def test_branch_provider(self):
        out = cctx.menu_suggestions(
            "git checkout ", self.dir,
            providers={cctx.BRANCHES: ["main", "dev"]})
        self.assertIn("git checkout main", [s.command for s in out])


class TypoTests(unittest.TestCase):
    def setUp(self):
        self.known = frozenset({"rsync", "git", "python", "kubectl",
                                "make", "grep", "ls"})

    def test_mistyped_command(self):
        c = ctypo.correct_command("rysnc -av a b", known=self.known)
        self.assertEqual(c.corrected, "rsync -av a b")
        self.assertEqual(c.reason, "command")
        self.assertFalse(c.escalates_risk)

    def test_url_prefix(self):
        c = ctypo.correct_command("curl ttps://example.com",
                                  known={"curl"})
        self.assertEqual(c.corrected, "curl https://example.com")

    def test_path_shorthand(self):
        c = ctypo.correct_command("cd .../src", known={"cd"})
        self.assertEqual(c.corrected, "cd ../src")

    def test_no_change_returns_none(self):
        self.assertIsNone(ctypo.correct_command("ls -la", known=self.known))

    def test_path_command_not_touched(self):
        self.assertIsNone(
            ctypo.correct_command("./rysnc", known=self.known))

    def test_ambiguous_not_corrected(self):
        # equidistant from two known commands -> no correction
        self.assertIsNone(
            ctypo.correct_command("cat", known={"bat", "car"}))

    def test_risk_escalation_flagged(self):
        c = ctypo.correct_command("kubectl detele pod api",
                                  known=self.known | {"kubectl"})
        self.assertEqual(c.corrected, "kubectl delete pod api")
        self.assertTrue(c.escalates_risk)

    def test_path_commands_includes_builtins(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "mytool").write_text("#!/bin/sh\n")
            names = ctypo.path_commands(d)
            self.assertIn("mytool", names)
            self.assertIn("cd", names)


if __name__ == "__main__":
    unittest.main()
