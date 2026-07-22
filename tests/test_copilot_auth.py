"""Unit-level contracts for auth.json endpoint parsing and tiering."""

import json
import tempfile
import unittest
from pathlib import Path

from agent_terminal.copilot import auth


# Mirrors the real auth.json shape: two LAN "Local LLM" servers + a GPT key.
_AUTH = {
    "custom:local-llm-(192.168.210.205)": [{
        "id": "701288", "label": "hulk (205)", "auth_type": "no_auth",
        "priority": 0, "source": "config:Local LLM (192.168.210.205)",
        "base_url": "http://192.168.210.205:8080/v1",
        "secret_fingerprint": "abc123",
    }],
    "custom:local-llm-(192.168.210.210)": [{
        "id": "07e188", "label": "Loki (210)", "auth_type": "no_auth",
        "priority": 0, "source": "config:Local LLM (192.168.210.210)",
        "base_url": "http://192.168.210.210:8080/v1",
    }],
    "GPT": [{"key": "sk-proj-EXAMPLEKEY0000000000"}],
}


class ClassifyTests(unittest.TestCase):
    def test_on_device(self):
        for url in ("http://127.0.0.1:11434/v1", "http://localhost:8080/v1",
                    "http://[::1]:8080/v1"):
            self.assertEqual(auth.classify_host(url), auth.ON_DEVICE, url)

    def test_lan(self):
        for url in ("http://192.168.210.210:8080/v1", "http://10.0.0.5/v1",
                    "http://172.16.3.4/v1", "http://box.local/v1"):
            self.assertEqual(auth.classify_host(url), auth.LAN, url)

    def test_internet(self):
        for url in ("https://api.openai.com/v1", "http://8.8.8.8/v1",
                    "https://example.com/v1"):
            self.assertEqual(auth.classify_host(url), auth.INTERNET, url)

    def test_bare_hostname_is_internet(self):
        # no IP / .local suffix -> internet (must not borrow the OpenAI key)
        self.assertEqual(auth.classify_host("http://bigserver:8080/v1"),
                         auth.INTERNET)


class IsOpenAIHostTests(unittest.TestCase):
    def test_true_only_for_openai(self):
        self.assertTrue(auth.is_openai_host("https://api.openai.com/v1"))
        self.assertTrue(auth.is_openai_host("https://api.openai.com/"))

    def test_false_for_others(self):
        for url in ("https://api.groq.com/openai/v1",
                    "http://192.168.1.5:8080/v1", "http://bigserver:8080/v1",
                    "https://openai.example.com/v1", "http://127.0.0.1/v1"):
            self.assertFalse(auth.is_openai_host(url), url)


class ParseTests(unittest.TestCase):
    def test_parses_real_shape(self):
        eps = auth.parse_endpoints(_AUTH)
        self.assertEqual(len(eps), 3)
        by_label = {e.label: e for e in eps}
        self.assertEqual(by_label["hulk (205)"].tier, auth.LAN)
        self.assertIsNone(by_label["hulk (205)"].api_key)
        self.assertEqual(by_label["GPT"].tier, auth.INTERNET)
        self.assertEqual(by_label["GPT"].base_url, auth.OPENAI_BASE_URL)
        self.assertTrue(by_label["GPT"].api_key.startswith("sk-"))

    def test_ordered_local_first(self):
        eps = auth.parse_endpoints(_AUTH)
        self.assertEqual([e.tier for e in eps],
                         [auth.LAN, auth.LAN, auth.INTERNET])
        self.assertEqual(eps[-1].label, "GPT")  # cloud last

    def test_gated_only_internet(self):
        eps = auth.parse_endpoints(_AUTH)
        gated = {e.label: e.gated() for e in eps}
        self.assertFalse(gated["hulk (205)"])
        self.assertFalse(gated["Loki (210)"])
        self.assertTrue(gated["GPT"])

    def test_priority_orders_within_tier(self):
        data = {
            "a": [{"label": "a", "base_url": "http://10.0.0.1/v1",
                   "priority": 5}],
            "b": [{"label": "b", "base_url": "http://10.0.0.2/v1",
                   "priority": 1}],
        }
        eps = auth.parse_endpoints(data)
        self.assertEqual([e.label for e in eps], ["b", "a"])

    def test_ignores_junk(self):
        self.assertEqual(auth.parse_endpoints({"x": "nope"}), [])
        self.assertEqual(auth.parse_endpoints({"x": [{"no": "url"}]}), [])
        self.assertEqual(auth.parse_endpoints(None), [])

    def test_dict_entry_allowed(self):
        eps = auth.parse_endpoints(
            {"GPT": {"key": "sk-x" + "y" * 20}})
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0].tier, auth.INTERNET)


class LoadTests(unittest.TestCase):
    def test_load_and_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text(json.dumps(_AUTH))
            eps = auth.load_endpoints(str(path))
            self.assertEqual(len(eps), 3)

    def test_missing_file_empty(self):
        self.assertEqual(auth.load_endpoints("/no/such/auth.json"), [])

    def test_candidate_paths_order(self):
        env = {"AGENT_TERMINAL_AUTH_JSON": "/x/auth.json",
               "XDG_CONFIG_HOME": "/cfg"}
        paths = auth.candidate_paths(env, repo_root="/repo")
        self.assertEqual(paths[0], "/x/auth.json")
        self.assertEqual(paths[1], "/cfg/agent-terminal/auth.json")
        self.assertEqual(paths[2], "/repo/auth.json")

    def test_load_from_candidates_first_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "auth.json").write_text(json.dumps(_AUTH))
            eps = auth.load_from_candidates(
                env={"AGENT_TERMINAL_AUTH_JSON": str(Path(tmp) / "auth.json")})
            self.assertEqual(len(eps), 3)


if __name__ == "__main__":
    unittest.main()
