"""Unit-level contracts for the copilot P0 pure core."""

import base64
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_terminal.copilot import config as cconfig
from agent_terminal.copilot import journal as cjournal
from agent_terminal.copilot import redact as credact
from agent_terminal.copilot import shellintegration as cshell

REPO_ROOT = Path(__file__).resolve().parent.parent
SNIPPET = REPO_ROOT / "agent_terminal" / "copilot" / "shell" / "agent-terminal.bash"


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = cconfig.parse_assistant_config(None)
        self.assertTrue(cfg.enabled)
        self.assertTrue(cfg.shell_integration)
        self.assertEqual(cfg.journal.max_commands, 200)
        self.assertFalse(cfg.suggestions.ghost_text)
        self.assertFalse(cfg.llm.allow_remote_context)

    def test_valid_payload(self):
        cfg = cconfig.parse_assistant_config({
            "enabled": False,
            "shell_integration": False,
            "journal": {"max_commands": 50, "store_output": False,
                        "output_tail_lines": 5},
            "sessions": {"exclude_dirs": ["/secret", 3]},
            "llm": {"model": "gpt-9", "timeout_s": 5},
        })
        self.assertFalse(cfg.enabled)
        self.assertFalse(cfg.shell_integration)
        self.assertEqual(cfg.journal.max_commands, 50)
        self.assertFalse(cfg.journal.store_output)
        self.assertEqual(cfg.sessions.exclude_dirs, ("/secret",))
        self.assertEqual(cfg.llm.model, "gpt-9")
        self.assertEqual(cfg.llm.timeout_s, 5)

    def test_garbage_tolerated(self):
        for payload in ("nope", 42, [], {"journal": "x"},
                        {"enabled": "yes", "journal": {"max_commands": "many"}},
                        {"llm": {"timeout_s": None, "model": 3}}):
            cfg = cconfig.parse_assistant_config(payload)
            self.assertTrue(cfg.enabled)
            self.assertEqual(cfg.journal.max_commands, 200)
            self.assertEqual(cfg.llm.model, "gpt-4.1-mini")

    def test_negative_ints_clamped(self):
        cfg = cconfig.parse_assistant_config(
            {"journal": {"max_commands": -5}})
        self.assertEqual(cfg.journal.max_commands, 0)

    def test_send_output_tri_state(self):
        p = cconfig.parse_assistant_config
        self.assertEqual(p({}).llm.send_output, "digest")          # default
        for value, want in ((False, "none"), (True, "full"),
                            ("full", "full"), ("none", "none"),
                            ("digest", "digest"), ("garbage", "digest"),
                            (3, "digest")):
            got = p({"llm": {"send_output": value}}).llm.send_output
            self.assertEqual(got, want, value)

    def test_digest_mode_enum(self):
        p = cconfig.parse_assistant_config
        self.assertEqual(p({}).llm.digest_mode, "heuristic")       # default
        for value, want in (("llm", "llm"), ("heuristic", "heuristic"),
                            ("LLM", "llm"), ("garbage", "heuristic"),
                            (True, "heuristic"), (5, "heuristic")):
            got = p({"llm": {"digest_mode": value}}).llm.digest_mode
            self.assertEqual(got, want, value)


class RedactTests(unittest.TestCase):
    def assertRedacts(self, text, leaked):
        out, hit = credact.redact_line(text)
        self.assertTrue(hit, f"no hit for: {text}")
        self.assertNotIn(leaked, out)
        self.assertIn(credact.REDACTED, out)

    def test_assignments(self):
        self.assertRedacts("export API_KEY=abc123def", "abc123def")
        self.assertRedacts("PASSWORD=hunter2 ./run.sh", "hunter2")
        self.assertRedacts("--token deadbeefcafe", "deadbeefcafe")
        self.assertRedacts("mysql -u root --password=pw1234", "pw1234")
        self.assertRedacts('secret: "s3cr3t value"', "s3cr3t")

    def test_url_userinfo(self):
        out, hit = credact.redact_line(
            "git clone https://user:pw123@example.com/repo.git")
        self.assertTrue(hit)
        self.assertNotIn("pw123", out)
        self.assertIn("https://user:[REDACTED]@example.com", out)

    def test_headers(self):
        self.assertRedacts("Authorization: Bearer abc.def.ghi", "abc.def.ghi")
        self.assertRedacts("Cookie: session=1a2b3c", "1a2b3c")

    def test_token_shapes(self):
        self.assertRedacts("aws AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE")
        self.assertRedacts("ghp_" + "a1B2" * 6, "ghp_" + "a1B2" * 6)
        self.assertRedacts("xoxb-1234567890-abcdef", "xoxb-1234567890")
        self.assertRedacts("sk-" + "x" * 24, "sk-" + "x" * 24)
        jwt = "eyJ" + "a" * 10 + ".eyJ" + "b" * 10 + "." + "c" * 8
        self.assertRedacts(jwt, jwt)

    def test_benign_lines_untouched(self):
        for line in ("ls -la /tmp", "git commit -m 'fix tokenizer'",
                     "echo authentic", "python3 -m unittest",
                     "curl https://example.com/keyboards"):
            out, hit = credact.redact_line(line)
            self.assertFalse(hit, f"false positive: {line} -> {out}")
            self.assertEqual(out, line)

    def test_pem_block_dropped(self):
        lines = ["before", "-----BEGIN OPENSSH PRIVATE KEY-----",
                 "AAAAB3Nza...", "-----END OPENSSH PRIVATE KEY-----",
                 "after"]
        out, hit = credact.redact_lines(lines)
        self.assertTrue(hit)
        self.assertEqual(out, ("before", credact.REDACTED, "after"))


class DecodePayloadTests(unittest.TestCase):
    def test_plain_command(self):
        payload = base64.b64encode(b"echo 'a; b'").decode()
        self.assertEqual(cjournal.decode_command_payload(payload),
                         "echo 'a; b'")

    def test_multiline_command(self):
        payload = base64.b64encode(b"for f in *; do\necho $f\ndone").decode()
        self.assertEqual(cjournal.decode_command_payload(payload),
                         "for f in *; do\necho $f\ndone")

    def test_leading_space_hidden(self):
        payload = base64.b64encode(b" echo hidden").decode()
        self.assertIsNone(cjournal.decode_command_payload(payload))

    def test_garbage(self):
        self.assertIsNone(cjournal.decode_command_payload("not base64!!"))
        self.assertIsNone(cjournal.decode_command_payload(None))
        self.assertIsNone(cjournal.decode_command_payload(
            base64.b64encode(b"   ").decode()))


def _cmd_payload(text):
    return base64.b64encode(text.encode()).decode()


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.journal = cjournal.PaneJournal(cconfig.JournalConfig(
            max_commands=3, store_output=True, output_tail_lines=2))

    def run_command(self, cmd="echo hi", exit_code=0, start_row=10,
                    end_row=13, lines=("out1", "out2", "out3")):
        self.journal.apply_batch([(cjournal.PREEXEC, None)],
                                 timestamp=1.0, wall_time=1000.0,
                                 cwd="/home/x", cursor_row=start_row)
        events = [(cjournal.POSTEXEC, exit_code), (cjournal.PRECMD, None)]
        if cmd is not None:
            events.insert(0, (cjournal.COMMAND_TERMPROP, _cmd_payload(cmd)))
        self.journal.apply_batch(
            events, timestamp=2.5, wall_time=1001.5, cwd="/home/x",
            cursor_row=end_row,
            read_rows=lambda s, e: list(lines)[s - start_row - 1:
                                              e - start_row - 1])

    def test_full_cycle(self):
        self.run_command()
        record = self.journal.last_record()
        self.assertEqual(record.cmd, "echo hi")
        self.assertEqual(record.exit_code, 0)
        self.assertEqual(record.cwd, "/home/x")
        self.assertAlmostEqual(record.duration_s, 1.5)
        self.assertEqual(record.capture, cjournal.CAPTURE_TERMPROP)
        # tail limited to output_tail_lines=2: rows [11, 13) of 10..13
        self.assertEqual(record.output_tail, ("out1", "out2"))
        self.assertEqual(self.journal.state, "prompt")
        self.assertEqual(self.journal.integration,
                         cjournal.INTEGRATION_TERMPROPS)

    def test_batch_order_is_canonicalized(self):
        self.journal.apply_batch([(cjournal.PREEXEC, None)],
                                 timestamp=1.0, wall_time=1000.0,
                                 cwd="/x", cursor_row=0)
        # precmd delivered FIRST inside the burst (as VTE actually does)
        self.journal.apply_batch(
            [(cjournal.PRECMD, None), (cjournal.POSTEXEC, 7),
             (cjournal.COMMAND_TERMPROP, _cmd_payload("make"))],
            timestamp=2.0, wall_time=1001.0, cwd="/x", cursor_row=5,
            read_rows=lambda s, e: [])
        record = self.journal.last_record()
        self.assertEqual(record.cmd, "make")
        self.assertEqual(record.exit_code, 7)

    def test_first_prompt_burst_records_nothing(self):
        self.journal.apply_batch(
            [(cjournal.POSTEXEC, 0), (cjournal.PRECMD, None)],
            timestamp=1.0, wall_time=1000.0, cursor_row=0)
        self.assertEqual(len(self.journal.records), 0)
        self.assertEqual(self.journal.state, "prompt")
        self.assertEqual(self.journal.integration,
                         cjournal.INTEGRATION_TERMPROPS)

    def test_uncaptured_command_has_none_cmd(self):
        self.run_command(cmd=None)
        record = self.journal.last_record()
        self.assertIsNone(record.cmd)
        self.assertEqual(record.capture, cjournal.CAPTURE_NONE)

    def test_finalize_builds_digest_from_redacted_output(self):
        self.run_command(lines=("compiling", "error: boom", "trailing"),
                         start_row=10, end_row=14)
        record = self.journal.last_record()
        self.assertIsNotNone(record.digest)
        self.assertIn("error: boom", record.digest.render())

    def test_output_tail_drops_pem_split_by_window(self):
        # The private-key BEGIN sits just above the 2-line tail window; the
        # margin read must still recognize and drop the whole block.
        pem = ("-----BEGIN PRIVATE KEY-----", "MIISECRETkeybodyONE",
               "MIISECRETkeybodyTWO")
        self.run_command(lines=pem, start_row=10, end_row=14)
        record = self.journal.last_record()
        self.assertNotIn("SECRETkeybody", "".join(record.output_tail or ()))

    def test_ring_caps_records(self):
        for i in range(5):
            self.run_command(cmd=f"cmd{i}")
        self.assertEqual(len(self.journal.records), 3)
        self.assertEqual([r.cmd for r in self.journal.records],
                         ["cmd2", "cmd3", "cmd4"])

    def test_missed_precmd_finalizes_previous(self):
        self.journal.apply_batch([(cjournal.PREEXEC, None)],
                                 timestamp=1.0, wall_time=1000.0,
                                 cwd="/x", cursor_row=0)
        self.journal.apply_batch([(cjournal.PREEXEC, None)],
                                 timestamp=2.0, wall_time=1001.0,
                                 cwd="/x", cursor_row=1)
        self.assertEqual(len(self.journal.records), 1)
        self.assertIsNone(self.journal.last_record().exit_code)
        self.assertEqual(self.journal.state, "executing")

    def test_store_output_off_skips_tail(self):
        journal = cjournal.PaneJournal(cconfig.JournalConfig(
            store_output=False))
        journal.apply_batch([(cjournal.PREEXEC, None)], timestamp=1.0,
                            wall_time=1000.0, cursor_row=0)
        journal.apply_batch(
            [(cjournal.POSTEXEC, 0), (cjournal.PRECMD, None)],
            timestamp=2.0, wall_time=1001.0, cursor_row=5,
            read_rows=lambda s, e: ["secret output"])
        self.assertIsNone(journal.last_record().output_tail)

    def test_paused_ignores_events(self):
        self.journal.paused = True
        self.run_command()
        self.assertEqual(len(self.journal.records), 0)

    def test_idle_seconds_tracks_last_activity(self):
        self.assertIsNone(self.journal.idle_seconds(100.0))
        self.journal.apply_batch([(cjournal.PRECMD, None)],
                                 timestamp=50.0, wall_time=1000.0,
                                 cursor_row=0)
        self.assertEqual(self.journal.idle_seconds(50.0), 0.0)
        self.assertEqual(self.journal.idle_seconds(110.0), 60.0)

    def test_records_are_redacted(self):
        self.run_command(cmd="export TOKEN=abc123secret",
                         lines=("PASSWORD=leaked1", "ok line", "x"))
        record = self.journal.last_record()
        self.assertNotIn("abc123secret", record.cmd)
        self.assertNotIn("leaked1", "".join(record.output_tail))
        self.assertTrue(record.redacted)

    def test_markdown_dump(self):
        self.run_command()
        text = self.journal.to_markdown()
        self.assertIn("echo hi", text)
        self.assertIn("exit 0", text)


class WrapArgvTests(unittest.TestCase):
    def setUp(self):
        self.assistant = cconfig.AssistantConfig()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def wrap(self, argv, env=None, assistant=None, **kwargs):
        return cshell.wrap_argv(argv, env or {},
                                assistant or self.assistant,
                                wrapper_dir=self.tmp.name, **kwargs)

    def test_wraps_plain_bash(self):
        result = self.wrap(["/bin/bash"])
        self.assertEqual(result[0], "/bin/bash")
        self.assertEqual(result[1], "--rcfile")
        content = Path(result[2]).read_text()
        self.assertIn(".bashrc", content.splitlines()[1])
        self.assertIn("agent-terminal.bash", content)

    def test_explicit_command_never_wrapped(self):
        argv = ["/bin/bash", "-lc", "echo hi"]
        self.assertEqual(self.wrap(argv, explicit_command=True), argv)
        self.assertEqual(self.wrap(["/bin/bash"], explicit_command=True),
                         ["/bin/bash"])

    def test_non_bash_shell_untouched(self):
        self.assertEqual(self.wrap(["/usr/bin/zsh"]), ["/usr/bin/zsh"])
        self.assertEqual(self.wrap(["/bin/fish"]), ["/bin/fish"])

    def test_kill_switch_env(self):
        self.assertEqual(
            self.wrap(["/bin/bash"], env={cshell.KILL_SWITCH_ENV: "1"}),
            ["/bin/bash"])

    def test_config_flags(self):
        for assistant in (
                cconfig.parse_assistant_config({"enabled": False}),
                cconfig.parse_assistant_config({"shell_integration": False}),
                None):
            self.assertEqual(self.wrap(["/bin/bash"], assistant=assistant)
                             if assistant is not None else
                             cshell.wrap_argv(["/bin/bash"], {}, assistant),
                             ["/bin/bash"])

    def test_missing_snippet_fails_open(self):
        result = self.wrap(["/bin/bash"], snippet="/nonexistent/snippet")
        self.assertEqual(result, ["/bin/bash"])

    def test_wrapper_is_idempotent(self):
        first = self.wrap(["/bin/bash"])
        stamp = Path(first[2]).stat().st_mtime_ns
        second = self.wrap(["/bin/bash"])
        self.assertEqual(first, second)
        self.assertEqual(Path(second[2]).stat().st_mtime_ns, stamp)


class SnippetGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.text = SNIPPET.read_text(encoding="utf-8")

    def test_snippet_parses(self):
        subprocess.run(["bash", "-n", str(SNIPPET)], check=True)

    def test_snippet_never_sources_bashrc(self):
        self.assertNotIn(".bashrc", self.text)

    def test_guards_present(self):
        self.assertIn("AGENT_TERMINAL_NO_INTEGRATION", self.text)
        self.assertIn("_agentterm_installed", self.text)
        self.assertIn('[[ $- == *i* ]] || return 0', self.text)

    def test_prompt_command_prepended_both_forms(self):
        self.assertIn('PROMPT_COMMAND=(_agentterm_precmd '
                      '"${PROMPT_COMMAND[@]}")', self.text)
        self.assertIn('PROMPT_COMMAND="_agentterm_precmd'
                      '${PROMPT_COMMAND:+;$PROMPT_COMMAND}"', self.text)

    def test_emits_all_termprops(self):
        for token in ("vte.shell.preexec", "vte.shell.postexec",
                      "vte.shell.precmd", "vte.ext.agentterm.cmd"):
            self.assertIn(token, self.text)

    def test_seed_block_placement(self):
        # Episode history seed: recall the episode, relocate HISTFILE off the
        # user's global history. Must sit ABOVE the journaling guards (seeding
        # is independent of command journaling) but BELOW the interactive
        # guard.
        text = self.text
        self.assertIn("builtin history -c", text)
        self.assertIn('builtin history -r "${AGENT_TERMINAL_SEED_HISTFILE}"',
                      text)
        self.assertIn('HISTFILE="${AGENT_TERMINAL_SEED_HISTFILE}"', text)
        # order by the actual statements (the names also appear in comments)
        seed = text.index('if [ -n "${AGENT_TERMINAL_SEED_HISTFILE:-}" ]')
        no_integration = text.index(
            '[ -z "${AGENT_TERMINAL_NO_INTEGRATION:-}" ] || return 0')
        self.assertLess(text.index('[[ $- == *i* ]] || return 0'), seed)
        self.assertLess(seed, no_integration)

    def test_seed_block_recalls_and_relocates(self):
        # Behavioral: the seed block's exact commands recall only the episode
        # and relocate HISTFILE so the user's global history is never written.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            glob = os.path.join(d, "global")
            seed = os.path.join(d, "seed")
            with open(glob, "w") as fh:
                fh.write("global_only_cmd\n")
            with open(seed, "w") as fh:
                fh.write("episode_cmd_x\nepisode_cmd_y\n")
            script = (
                "set -o history\n"
                f'HISTFILE="{glob}"; builtin history -r "$HISTFILE"\n'
                f'export AGENT_TERMINAL_SEED_HISTFILE="{seed}"\n'
                'if [ -n "${AGENT_TERMINAL_SEED_HISTFILE:-}" ] '
                '&& [ -f "${AGENT_TERMINAL_SEED_HISTFILE}" ]; then\n'
                "  builtin history -c\n"
                '  builtin history -r "${AGENT_TERMINAL_SEED_HISTFILE}"\n'
                '  HISTFILE="${AGENT_TERMINAL_SEED_HISTFILE}"\n'
                "  unset AGENT_TERMINAL_SEED_HISTFILE\n"
                "fi\n"
                "builtin history\n"
                'echo "HF=$HISTFILE"\n')
            out = subprocess.run(
                ["bash", "--norc", "--noprofile"], input=script,
                capture_output=True, text=True).stdout
            self.assertIn("episode_cmd_x", out)      # episode recalled
            self.assertNotIn("global_only_cmd", out)  # only the episode
            self.assertIn(f"HF={seed}", out)          # HISTFILE relocated
            with open(glob) as fh:
                self.assertEqual(fh.read(), "global_only_cmd\n")  # intact


if __name__ == "__main__":
    unittest.main()
