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

    def test_user_agent_is_not_python_urllib(self):
        # urllib's default UA ("Python-urllib/x.y") is 403'd by WAFs in front
        # of some gateways (centinel); we must send our own.
        server = FakeServer()
        cllm.summarize(_cfg(allow_remote_context=True),
                       recent_commands=["ls"], chain=[LAN1], opener=server)
        ua = server.requests[0].get_header("User-agent")
        self.assertTrue(ua)
        self.assertNotIn("urllib", ua.lower())


class LlmDigestTests(unittest.TestCase):
    """The "llm" digest_mode summarizer (digest_output_llm)."""

    def setUp(self):
        cllm._MODEL_CACHE.clear()

    def test_returns_model_summary_for_eligible_endpoint(self):
        server = FakeServer(content="Build failed: error[E0499].")
        out = cllm.digest_output_llm(
            _cfg(allow_remote_context=True), command="cargo build",
            output="error[E0499]: cannot borrow\nerror: could not compile",
            question="why did it fail?", chain=[LAN1], opener=server)
        self.assertEqual(out, "Build failed: error[E0499].")
        # the command's output is what we asked the model to compress
        sent = json.dumps(server.chat_bodies()[0])
        self.assertIn("could not compile", sent)

    def test_input_and_output_are_redacted(self):
        # A secret in the output to summarize never leaves, and a secret the
        # model happens to echo back is scrubbed from the returned digest.
        server = FakeServer(content="key was AKIAIOSFODNN7EXAMPLE")
        out = cllm.digest_output_llm(
            _cfg(allow_remote_context=True), command="cat creds",
            output="AKIAIOSFODNN7EXAMPLE", question="what is it",
            chain=[LAN1], opener=server)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", json.dumps(
            server.chat_bodies()[0]))            # never sent
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", out)  # scrubbed on return

    def test_none_when_no_endpoint_eligible(self):
        # cloud-only + remote off -> no endpoint -> None (caller falls back).
        out = cllm.digest_output_llm(
            _cfg(allow_remote_context=False), command="cargo build",
            output="error", question="why", chain=[CLOUD], opener=FakeServer())
        self.assertIsNone(out)

    def test_none_on_transport_failure(self):
        server = FakeServer(fail_hosts={"192.168.210.210"})
        out = cllm.digest_output_llm(
            _cfg(allow_remote_context=True), command="cargo build",
            output="error", question="why", chain=[LAN1], opener=server)
        self.assertIsNone(out)

    def test_none_for_empty_output(self):
        server = FakeServer()
        out = cllm.digest_output_llm(
            _cfg(allow_remote_context=True), command="true", output="   ",
            question="q", chain=[LAN1], opener=server)
        self.assertIsNone(out)
        self.assertEqual(server.requests, [])   # no call made


class ListModelsTests(unittest.TestCase):
    def test_lists_all_advertised_models(self):
        server = FakeServer(models=("hulk", "loki"))
        models = cllm.list_models(_cfg(), LAN1, opener=server)
        self.assertEqual(models, ["hulk", "loki"])
        self.assertTrue(server.requests[0].full_url.endswith("/models"))

    def test_raises_on_failure(self):
        server = FakeServer(fail_hosts={"192.168.210.210"})
        with self.assertRaises(cllm.LlmError):
            cllm.list_models(_cfg(), LAN1, opener=server)

    def test_multiline_recent_command_drops_pem_block(self):
        # A heredoc history entry that writes a private key must not ship the
        # key body — redact_lines drops the PEM block whole.
        heredoc = ("cat > k.pem <<EOF\n"
                   "-----BEGIN PRIVATE KEY-----\n"
                   "MIIEvQIBADANBgkqhkiG9w0BStstSECRETkeymaterial\n"
                   "-----END PRIVATE KEY-----\nEOF")
        server = FakeServer()
        cllm.summarize(_cfg(allow_remote_context=True),
                       recent_commands=[heredoc], chain=[LAN1], opener=server)
        sent = json.dumps(server.chat_bodies()[0])
        self.assertNotIn("SECRETkeymaterial", sent)

    def test_multiline_draft_drops_pem_block(self):
        heredoc = ("cat <<EOF\n-----BEGIN PRIVATE KEY-----\n"
                   "MIIsecretDRAFTkeymaterial\n-----END PRIVATE KEY-----\nEOF")
        server = FakeServer()
        cllm.suggest_commands(_cfg(allow_remote_context=True), query="do it",
                              draft_command=heredoc, chain=[LAN1],
                              opener=server)
        sent = json.dumps(server.chat_bodies()[0])
        self.assertNotIn("secretDRAFTkeymaterial", sent)

    def test_explain_drops_multiline_pem_block(self):
        heredoc = ("cat <<EOF\n-----BEGIN PRIVATE KEY-----\n"
                   "MIIsecretEXPLAINkeymaterial\n-----END PRIVATE KEY-----"
                   "\nEOF")
        server = FakeServer()
        cllm.explain(_cfg(allow_remote_context=True), heredoc,
                     chain=[LAN1], opener=server)
        sent = json.dumps(server.chat_bodies()[0])
        self.assertNotIn("secretEXPLAINkeymaterial", sent)

    def test_multiline_query_drops_pem_block(self):
        query = ("please run this:\n-----BEGIN PRIVATE KEY-----\n"
                 "MIIsecretQUERYkeymaterial\n-----END PRIVATE KEY-----")
        server = FakeServer(content='{"commands":[]}')
        cllm.suggest_commands(_cfg(allow_remote_context=True), query=query,
                              chain=[LAN1], opener=server)
        sent = json.dumps(server.chat_bodies()[0])
        self.assertNotIn("secretQUERYkeymaterial", sent)


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
    def _write_auth(self, tmp, data):
        path = Path(tmp) / "auth.json"
        path.write_text(json.dumps(data))
        return str(path)

    def test_falls_back_to_single_config_endpoint(self):
        # no auth.json at the given path -> one endpoint from config
        cfg = _cfg(base_url="http://127.0.0.1:11434/v1", model="m",
                   auth_path="/definitely/missing/auth.json")
        chain = cllm.resolve_chain(cfg)
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].tier, auth.ON_DEVICE)
        self.assertEqual(chain[0].model, "m")

    def test_openai_key_scoped_to_openai_host(self):
        import os
        import tempfile
        os.environ["TEST_OAI_KEY"] = "sk-realsecret"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = self._write_auth(tmp, {
                    "groq": [{"base_url": "https://api.groq.com/openai/v1",
                              "model": "llama-3"}],
                    "GPT": [{"base_url": "https://api.openai.com/v1"}],
                })
                chain = cllm.resolve_chain(
                    _cfg(api_key_env="TEST_OAI_KEY", auth_path=path))
                by = {e.label: e for e in chain}
                # the OpenAI key goes ONLY to the OpenAI host
                self.assertEqual(by["GPT"].api_key, "sk-realsecret")
                self.assertIsNone(by["groq"].api_key)
        finally:
            del os.environ["TEST_OAI_KEY"]

    def test_single_endpoint_lan_gets_no_openai_key(self):
        import os
        os.environ["TEST_OAI_KEY2"] = "sk-realsecret"
        try:
            cfg = _cfg(base_url="http://192.168.1.50:8080/v1", model="m",
                       api_key_env="TEST_OAI_KEY2",
                       auth_path="/missing/auth.json")
            chain = cllm.resolve_chain(cfg)
            self.assertEqual(chain[0].tier, auth.LAN)
            self.assertIsNone(chain[0].api_key)   # no borrowed OpenAI key
        finally:
            del os.environ["TEST_OAI_KEY2"]

    def test_single_endpoint_openai_gets_key(self):
        import os
        os.environ["TEST_OAI_KEY3"] = "sk-realsecret"
        try:
            cfg = _cfg(base_url="https://api.openai.com/v1", model="gpt",
                       api_key_env="TEST_OAI_KEY3",
                       auth_path="/missing/auth.json")
            chain = cllm.resolve_chain(cfg)
            self.assertEqual(chain[0].tier, auth.INTERNET)
            self.assertEqual(chain[0].api_key, "sk-realsecret")
        finally:
            del os.environ["TEST_OAI_KEY3"]

    def test_chain_label_marks_gated(self):
        label = cllm.chain_label([LAN1, CLOUD], allow_remote=False)
        self.assertIn("Loki", label)
        self.assertIn("GPT (gated)", label)
        self.assertNotIn("(gated)", cllm.chain_label([LAN1, CLOUD],
                                                     allow_remote=True))


class PrivacyFallbackTests(unittest.TestCase):
    def setUp(self):
        cllm._MODEL_CACHE.clear()

    def test_local_down_never_spills_to_gated_cloud(self):
        # local fails, remote OFF -> raises, and the cloud host is untouched
        server = FakeServer(fail_hosts={"192.168.210.210"})
        with self.assertRaises(cllm.LlmError):
            cllm.summarize(_cfg(allow_remote_context=False),
                           recent_commands=["ls"], chain=[LAN1, CLOUD],
                           opener=server)
        self.assertNotIn("api.openai.com", server.hosts_hit())

    def test_send_output_off_omits_output(self):
        server = FakeServer()
        cllm.summarize(_cfg(allow_remote_context=True, send_output=False),
                       recent_commands=["ls"],
                       output_tails=[["secret-AKIAIOSFODNN7EXAMPLE"]],
                       chain=[LAN1], opener=server)
        sent = json.dumps(server.chat_bodies()[0])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", sent)
        self.assertNotIn("recent output", sent)

    def test_send_output_on_redacts_output(self):
        server = FakeServer()
        cllm.summarize(_cfg(allow_remote_context=True, send_output=True),
                       recent_commands=["ls"],
                       output_tails=[["token AKIAIOSFODNN7EXAMPLE here"]],
                       chain=[LAN1], opener=server)
        sent = json.dumps(server.chat_bodies()[0])
        self.assertIn("recent output", sent)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", sent)

    def test_activity_block_replaces_recent_and_is_re_redacted(self):
        server = FakeServer(content='{"commands":[]}')
        activity = ("task: proj: cargo · 2 cmds · 1 failure\n"
                    "$ cargo build  (exit 101)\n"
                    "    error[E0499]: leaked AKIAIOSFODNN7EXAMPLE\n")
        cllm.suggest_commands(
            _cfg(allow_remote_context=True), query="fix it",
            recent_commands=["should-not-appear"], activity=activity,
            chain=[LAN1], opener=server)
        sent = json.dumps(server.chat_bodies()[0])
        self.assertIn("task: proj", sent)               # activity block sent
        self.assertIn("error[E0499]", sent)
        self.assertNotIn("should-not-appear", sent)     # recent_commands dropped
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", sent)  # re-redacted at choke

    def test_full_path_never_leaks_raw_secrets_in_any_mode(self):
        # Adversarial end-to-end: raw output carrying real secrets goes
        # through the same steps as the live path — redact_lines (as in
        # journal._finalize) -> digest -> askcontext -> build_context/send —
        # for every send_output mode. Nothing sensitive may survive, yet the
        # error line must (context stays useful).
        from types import SimpleNamespace
        from agent_terminal.copilot import (askcontext, digest as digest_mod,
                                             episode, redact)
        raw = ([f"building step {i}" for i in range(30)] + [
            "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "AKIAIOSFODNN7EXAMPLE",
            "export API_KEY=hunter2supersecretvalue",
            "-----BEGIN RSA PRIVATE KEY-----",
            "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWy",
            "-----END RSA PRIVATE KEY-----",
        ] + ["error: compilation failed at src/main.rs:9"])
        needles = ["sk-ABCDEF", "AKIAIOSFODNN7EXAMPLE", "hunter2supersecret",
                   "MIIEpAIBAAKCAQEA", "PRIVATE KEY"]
        red, _ = redact.redact_lines(raw)         # journal._finalize step
        record = SimpleNamespace(
            seq=0, cmd="cargo build", cwd="/home/x/proj", started_at=1000.0,
            duration_s=1.0, exit_code=101, output_tail=tuple(red[-20:]),
            digest=digest_mod.digest_output(red), branch=None)
        ep = episode.current_episode([record])
        for mode in ("none", "digest", "full"):
            activity = askcontext.build_ask_context(
                ep, question="why did the build fail?", output_mode=mode)
            server = FakeServer(content='{"commands":[]}')
            cllm.suggest_commands(
                _cfg(allow_remote_context=True), query="why fail?",
                activity=activity, chain=[LAN1], opener=server)
            sent = json.dumps(server.chat_bodies()[0])
            for needle in needles:
                self.assertNotIn(needle, sent, f"{needle!r} leaked in {mode}")
            if mode != "none":
                self.assertIn("compilation failed", sent)  # useful context kept

    def test_project_and_draft_and_query_redacted(self):
        server = FakeServer(content='{"commands":[]}')
        cllm.suggest_commands(
            _cfg(allow_remote_context=True),
            query="export AWS=AKIAIOSFODNN7EXAMPLE",
            project="ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            draft_command="curl https://u:pw1234@host",
            chain=[LAN1], opener=server)
        sent = json.dumps(server.chat_bodies()[0])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", sent)
        self.assertNotIn("ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", sent)
        self.assertNotIn("pw1234", sent)

    def test_discovery_failure_falls_through(self):
        # LAN1 advertises no models -> discovery fails -> fall to LAN2
        server = FakeServer(models=())

        class Mixed:
            def __init__(self):
                self.reqs = []

            def __call__(self, request, timeout=None):
                self.reqs.append(request)
                host = urlsplit(request.full_url).hostname
                if request.full_url.endswith("/models"):
                    models = [] if host == "192.168.210.210" else ["m2"]
                    return FakeResponse(json.dumps(
                        {"data": [{"id": m} for m in models]}).encode())
                return FakeResponse(json.dumps(
                    {"choices": [{"message": {"content": "ok"}}]}).encode())

        opener = Mixed()
        out = cllm.summarize(_cfg(allow_remote_context=True),
                             recent_commands=["ls"], chain=[LAN1, LAN2],
                             opener=opener)
        self.assertEqual(out.endpoint, "hulk")

    def test_discovery_failure_not_cached(self):
        server = FakeServer(models=())
        with self.assertRaises(cllm.LlmError):
            cllm.summarize(_cfg(allow_remote_context=True),
                           recent_commands=["ls"], chain=[LAN1],
                           opener=server)
        self.assertNotIn(LAN1.base_url, cllm._MODEL_CACHE)


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
