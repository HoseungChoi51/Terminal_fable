"""Unit-level contracts for the copilot P5 LLM core.

No real network: a fake opener records requests and returns canned
responses. The safety-critical properties — the gate blocks all traffic
when remote context is off, and every payload is redacted — are pinned
here, plus a source guardrail that urllib lives only in llm.py.
"""

import dataclasses
import io
import json
import re
import unittest
from pathlib import Path

from agent_terminal.copilot import llm as cllm
from agent_terminal.copilot import risk as crisk
from agent_terminal.copilot.config import LlmConfig

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class FakeOpener:
    """Records requests; returns a canned assistant message."""

    def __init__(self, content='{"commands": [], "note": ""}'):
        self.content = content
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        body = {"choices": [{"message": {"content": self.content}}]}
        return FakeResponse(json.dumps(body).encode("utf-8"))

    def last_body(self):
        return json.loads(self.requests[-1].data.decode("utf-8"))

    def sent_text(self):
        return json.dumps(self.last_body())


def _cfg(**kw):
    return dataclasses.replace(LlmConfig(allow_remote_context=True), **kw)


class GateTests(unittest.TestCase):
    def test_gate_blocks_when_disabled(self):
        opener = FakeOpener()
        config = LlmConfig(allow_remote_context=False)
        with self.assertRaises(cllm.RemoteDisabledError):
            cllm.suggest_commands(config, query="list files", opener=opener)
        self.assertEqual(opener.requests, [], "network touched while gated")

    def test_gate_blocks_summary_when_disabled(self):
        opener = FakeOpener()
        with self.assertRaises(cllm.RemoteDisabledError):
            cllm.summarize(LlmConfig(allow_remote_context=False),
                           recent_commands=["ls"], opener=opener)
        self.assertEqual(opener.requests, [])

    def test_allowed_reflects_config(self):
        self.assertFalse(cllm.ContextGate(LlmConfig()).allowed())
        self.assertTrue(cllm.ContextGate(_cfg()).allowed())


class RedactionTests(unittest.TestCase):
    def test_context_is_redacted(self):
        opener = FakeOpener()
        cllm.suggest_commands(
            _cfg(), query="deploy",
            recent_commands=["export AWS_SECRET=AKIAIOSFODNN7EXAMPLE",
                             "curl https://u:pw1234@host/x"],
            cwd="/home/x", opener=opener)
        sent = opener.sent_text()
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", sent)
        self.assertNotIn("pw1234", sent)
        self.assertIn("REDACTED", sent)

    def test_build_context_redacts_everything(self):
        ctx = cllm.build_context(
            recent_commands=["mysql --password=hunter2"],
            cwd="/srv")
        self.assertNotIn("hunter2", ctx)


class RequestShapeTests(unittest.TestCase):
    def test_url_and_model_and_json_mode(self):
        opener = FakeOpener()
        cllm.suggest_commands(_cfg(model="big-model"), query="x",
                              opener=opener)
        req = opener.requests[-1]
        self.assertTrue(req.full_url.endswith("/chat/completions"))
        body = opener.last_body()
        self.assertEqual(body["model"], "big-model")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["messages"][0]["role"], "system")

    def test_base_url_configurable(self):
        opener = FakeOpener()
        cllm.suggest_commands(
            _cfg(base_url="http://localhost:11434/v1"), query="x",
            opener=opener)
        self.assertEqual(opener.requests[-1].full_url,
                         "http://localhost:11434/v1/chat/completions")

    def test_api_key_header_from_env(self):
        opener = FakeOpener()
        import os
        os.environ["TEST_LLM_KEY"] = "secret-key-123"
        try:
            cllm.suggest_commands(_cfg(api_key_env="TEST_LLM_KEY"),
                                  query="x", opener=opener)
        finally:
            del os.environ["TEST_LLM_KEY"]
        self.assertEqual(opener.requests[-1].get_header("Authorization"),
                         "Bearer secret-key-123")

    def test_no_key_no_auth_header(self):
        opener = FakeOpener()
        cllm.suggest_commands(_cfg(api_key_env="DEFINITELY_UNSET_KEY_XYZ"),
                              query="x", opener=opener)
        self.assertIsNone(opener.requests[-1].get_header("Authorization"))


class ParseIntentTests(unittest.TestCase):
    def test_parses_commands_and_placeholders(self):
        opener = FakeOpener(json.dumps({
            "commands": [
                {"command": "ffmpeg -i <input> <out>/f_%04d.png",
                 "description": "extract frames"},
                {"command": "ls -la", "description": "list"}],
            "note": "replace placeholders"}))
        result = cllm.suggest_commands(_cfg(), query="frames", opener=opener)
        self.assertEqual(len(result.templates), 2)
        self.assertIn("<input>", result.templates[0].placeholders)
        self.assertIn("<out>", result.templates[0].placeholders)
        self.assertEqual(result.note, "replace placeholders")

    def test_risk_classified(self):
        opener = FakeOpener(json.dumps(
            {"commands": [{"command": "rm -rf <dir>", "description": "d"}]}))
        result = cllm.suggest_commands(_cfg(), query="x", opener=opener)
        self.assertEqual(result.templates[0].risk.display, crisk.DESTRUCTIVE)

    def test_lenient_json_in_fences(self):
        opener = FakeOpener('```json\n{"commands":[{"command":"pwd"}]}\n```')
        result = cllm.suggest_commands(_cfg(), query="x", opener=opener)
        self.assertEqual(result.templates[0].command, "pwd")

    def test_garbage_response_empty(self):
        opener = FakeOpener("not json at all")
        result = cllm.suggest_commands(_cfg(), query="x", opener=opener)
        self.assertEqual(result.templates, ())

    def test_skips_blank_commands(self):
        opener = FakeOpener(json.dumps(
            {"commands": [{"command": "", "description": "d"},
                          {"command": "ls"}]}))
        result = cllm.suggest_commands(_cfg(), query="x", opener=opener)
        self.assertEqual(len(result.templates), 1)


class SummaryTests(unittest.TestCase):
    def test_summary_returns_text(self):
        opener = FakeOpener("You cleaned up build artifacts in ~/proj.")
        text = cllm.summarize(_cfg(), recent_commands=["rm -rf build"],
                              cwd="/home/x/proj", opener=opener)
        self.assertEqual(text, "You cleaned up build artifacts in ~/proj.")


class ErrorHandlingTests(unittest.TestCase):
    def test_urlerror_becomes_llmerror(self):
        import urllib.error

        def broken(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        with self.assertRaises(cllm.LlmError):
            cllm.suggest_commands(_cfg(), query="x", opener=broken)

    def test_malformed_response(self):
        def opener(request, timeout=None):
            return FakeResponse(b'{"no_choices": true}')

        with self.assertRaises(cllm.LlmError):
            cllm.suggest_commands(_cfg(), query="x", opener=opener)


class SourceGuardrailTests(unittest.TestCase):
    def test_urllib_confined_to_llm_module(self):
        copilot = REPO_ROOT / "agent_terminal" / "copilot"
        offenders = []
        for path in copilot.glob("*.py"):
            if path.name == "llm.py":
                continue
            if re.search(r"\burllib\b", path.read_text(encoding="utf-8")):
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"urllib used outside llm.py: {offenders}")


if __name__ == "__main__":
    unittest.main()
