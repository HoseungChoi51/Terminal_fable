"""Contracts for the P5 LLM chain: tier gate, local-first, fallback.

No real network: a fake server records requests and answers /models and
/chat/completions. The safety-critical properties are pinned here — an
internet endpoint is untouched when remote is off, context is always
redacted, and secrets never leave the Authorization header — plus a
source guardrail that network urllib stays in llm.py.
"""

import dataclasses
import io
import json
import re
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit

from agent_terminal.copilot import auth
from agent_terminal.copilot import llm as cllm
from agent_terminal.copilot import risk as crisk
from agent_terminal.copilot.config import LlmConfig

REPO_ROOT = Path(__file__).resolve().parent.parent

LAN1 = auth.Endpoint("Loki", "http://192.168.210.210:8080/v1", auth.LAN)
LAN2 = auth.Endpoint("hulk", "http://192.168.210.205:8080/v1", auth.LAN)
CLOUD = auth.Endpoint("GPT", auth.OPENAI_BASE_URL, auth.INTERNET,
                      api_key="sk-secretkey", model="gpt-4.1-mini")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class FakeServer:
    """Answers /models and /chat/completions; can fail chosen hosts."""

    def __init__(self, *, content="hi", models=("srv-model",),
                 fail_hosts=()):
        self.content = content
        self.models = list(models)
        self.fail_hosts = set(fail_hosts)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        host = urlsplit(request.full_url).hostname
        if host in self.fail_hosts:
            raise urllib.error.URLError("connection refused")
        if request.full_url.endswith("/models"):
            body = {"data": [{"id": m} for m in self.models]}
        else:
            body = {"choices": [{"message": {"content": self.content}}]}
        return FakeResponse(json.dumps(body).encode("utf-8"))

    def hosts_hit(self):
        return [urlsplit(r.full_url).hostname for r in self.requests]

    def chat_bodies(self):
        return [json.loads(r.data.decode())
                for r in self.requests if r.data]


def _cfg(**kw):
    return dataclasses.replace(LlmConfig(auth_path="/nonexistent"), **kw)


class TierGateTests(unittest.TestCase):
    def setUp(self):
        cllm._MODEL_CACHE.clear()

    def test_lan_used_without_optin(self):
        server = FakeServer(content="local answer")
        out = cllm.summarize(_cfg(allow_remote_context=False),
                             recent_commands=["ls"],
                             chain=[LAN1, CLOUD], opener=server)
        self.assertEqual(out.text, "local answer")
        self.assertEqual(out.endpoint, "Loki")
        # the cloud endpoint was never contacted
        self.assertNotIn("api.openai.com", server.hosts_hit())

    def test_internet_only_chain_blocked_when_off(self):
        server = FakeServer()
        with self.assertRaises(cllm.RemoteDisabledError):
            cllm.summarize(_cfg(allow_remote_context=False),
                           recent_commands=["ls"], chain=[CLOUD],
                           opener=server)
        self.assertEqual(server.requests, [])   # zero network

    def test_internet_used_when_opted_in(self):
        server = FakeServer(content="cloud answer")
        out = cllm.summarize(_cfg(allow_remote_context=True),
                             recent_commands=["ls"], chain=[CLOUD],
                             opener=server)
        self.assertEqual(out.endpoint, "GPT")

    def test_eligible_filter(self):
        self.assertEqual(
            [e.label for e in cllm.eligible_endpoints([LAN1, CLOUD], False)],
            ["Loki"])
        self.assertEqual(
            [e.label for e in cllm.eligible_endpoints([LAN1, CLOUD], True)],
            ["Loki", "GPT"])


class FallbackTests(unittest.TestCase):
    def setUp(self):
        cllm._MODEL_CACHE.clear()

    def test_falls_through_to_next(self):
        server = FakeServer(content="ok", fail_hosts={"192.168.210.210"})
        out = cllm.summarize(_cfg(allow_remote_context=True),
                             recent_commands=["ls"],
                             chain=[LAN1, LAN2], opener=server)
        self.assertEqual(out.endpoint, "hulk")

    def test_local_first_falls_to_cloud(self):
        server = FakeServer(content="cloud",
                            fail_hosts={"192.168.210.210",
                                        "192.168.210.205"})
        out = cllm.summarize(_cfg(allow_remote_context=True),
                             recent_commands=["ls"],
                             chain=[LAN1, LAN2, CLOUD], opener=server)
        self.assertEqual(out.endpoint, "GPT")

    def test_all_fail_raises(self):
        server = FakeServer(fail_hosts={"192.168.210.210",
                                        "api.openai.com"})
        with self.assertRaises(cllm.LlmError) as ctx:
            cllm.summarize(_cfg(allow_remote_context=True),
                           recent_commands=["ls"], chain=[LAN1, CLOUD],
                           opener=server)
        self.assertIn("all endpoints failed", str(ctx.exception))


class ModelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        cllm._MODEL_CACHE.clear()

    def test_lan_discovers_model(self):
        server = FakeServer(models=["qwen3.5:4b"])
        cllm.summarize(_cfg(allow_remote_context=True),
                       recent_commands=["ls"], chain=[LAN1], opener=server)
        # a /models GET happened, and the chat used the advertised id
        self.assertTrue(any(r.full_url.endswith("/models")
                            for r in server.requests))
        self.assertEqual(server.chat_bodies()[0]["model"], "qwen3.5:4b")

    def test_internet_uses_config_model_no_discovery(self):
        server = FakeServer()
        cllm.summarize(_cfg(allow_remote_context=True),
                       recent_commands=["ls"], chain=[CLOUD], opener=server)
        self.assertFalse(any(r.full_url.endswith("/models")
                             for r in server.requests))
        self.assertEqual(server.chat_bodies()[0]["model"], "gpt-4.1-mini")


class RedactionAndSecretTests(unittest.TestCase):
    def setUp(self):
        cllm._MODEL_CACHE.clear()

    def test_context_redacted_to_every_endpoint(self):
        server = FakeServer()
        cllm.summarize(
            _cfg(allow_remote_context=True),
            recent_commands=["export TOKEN=AKIAIOSFODNN7EXAMPLE"],
            chain=[LAN1], opener=server)
        sent = json.dumps(server.chat_bodies()[0])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", sent)
        self.assertIn("REDACTED", sent)

    def test_key_in_header_only_not_body(self):
        server = FakeServer()
        cllm.summarize(_cfg(allow_remote_context=True),
                       recent_commands=["ls"], chain=[CLOUD], opener=server)
        chat = next(r for r in server.requests
                    if r.full_url.endswith("/chat/completions"))
        self.assertEqual(chat.get_header("Authorization"),
                         "Bearer sk-secretkey")
        self.assertNotIn("sk-secretkey", chat.data.decode())

    def test_lan_endpoint_sends_no_auth_header(self):
        server = FakeServer()
        cllm.summarize(_cfg(allow_remote_context=True),
                       recent_commands=["ls"], chain=[LAN1], opener=server)
        chat = next(r for r in server.requests
                    if r.full_url.endswith("/chat/completions"))
        self.assertIsNone(chat.get_header("Authorization"))


class IntentAndExplainTests(unittest.TestCase):
    def setUp(self):
        cllm._MODEL_CACHE.clear()

    def test_intent_parses_and_reports_endpoint(self):
        server = FakeServer(content=json.dumps(
            {"commands": [{"command": "rm -rf <dir>", "description": "d"}]}))
        result = cllm.suggest_commands(
            _cfg(allow_remote_context=True), query="x",
            chain=[LAN1], opener=server)
        self.assertEqual(result.templates[0].risk.display, crisk.DESTRUCTIVE)
        self.assertEqual(result.endpoint, "Loki")

    def test_explain_returns_completion(self):
        server = FakeServer(content="It lists files.")
        out = cllm.explain(_cfg(allow_remote_context=True), "ls",
                           chain=[LAN1], opener=server)
        self.assertEqual(out.text, "It lists files.")
        self.assertEqual(out.endpoint, "Loki")


class ResolveChainTests(unittest.TestCase):
    def test_falls_back_to_single_config_endpoint(self):
        # no auth.json at the given path -> one endpoint from config
        cfg = _cfg(base_url="http://127.0.0.1:11434/v1", model="m",
                   auth_path="/definitely/missing/auth.json")
        chain = cllm.resolve_chain(cfg)
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].tier, auth.ON_DEVICE)
        self.assertEqual(chain[0].model, "m")

    def test_chain_label_marks_gated(self):
        label = cllm.chain_label([LAN1, CLOUD], allow_remote=False)
        self.assertIn("Loki", label)
        self.assertIn("GPT (gated)", label)
        self.assertNotIn("(gated)", cllm.chain_label([LAN1, CLOUD],
                                                     allow_remote=True))


class SourceGuardrailTests(unittest.TestCase):
    def test_network_urllib_confined_to_llm(self):
        copilot = REPO_ROOT / "agent_terminal" / "copilot"
        offenders = []
        for path in copilot.glob("*.py"):
            if path.name == "llm.py":
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"urllib\.(request|error)", text):
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"network urllib outside llm.py: "
                                        f"{offenders}")


if __name__ == "__main__":
    unittest.main()
