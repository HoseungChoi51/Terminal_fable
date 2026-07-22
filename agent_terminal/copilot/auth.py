"""Read LLM connection info from auth.json into an ordered endpoint chain.

Each endpoint is classified by where it lives — on this machine, on the
local network, or on the public internet — so the privacy gate can treat
them differently: on-device and LAN endpoints are trusted and used
freely, while internet endpoints (e.g. OpenAI) require the explicit
remote-context opt-in. The chain is ordered most-private first, so the
copilot is local-first and falls back to the cloud only as a last
resort.

Secrets read from the file are held only in memory and are never logged
or written anywhere. auth.json itself must stay out of version control
(it is gitignored).
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

ON_DEVICE = "on-device"
LAN = "lan"
INTERNET = "internet"
_TIER_RANK = {ON_DEVICE: 0, LAN: 1, INTERNET: 2}

OPENAI_BASE_URL = "https://api.openai.com/v1"
# Top-level auth.json keys that mean "the OpenAI cloud model".
_OPENAI_NAMES = frozenset({"gpt", "openai", "chatgpt", "cloud", "gpt-4",
                           "gpt4"})


@dataclass(frozen=True)
class Endpoint:
    label: str
    base_url: str
    tier: str
    priority: int = 0
    api_key: str | None = None
    model: str | None = None       # None -> discover / default at call time
    provider: str = "openai"       # OpenAI-compatible chat API
    # Explicit trust override for an internet-tier host the operator vouches
    # for — e.g. a private LiteLLM gateway on a public domain that fronts
    # local models. Set via "trusted": true in auth.json. Never auto-set:
    # a host is trusted only because a human said so.
    trusted: bool = False

    def gated(self) -> bool:
        """Does reaching this endpoint require the remote-context opt-in?"""
        return self.tier == INTERNET and not self.trusted

    def with_(self, **changes) -> "Endpoint":
        return replace(self, **changes)


_OPENAI_HOSTS = frozenset({"api.openai.com"})


def is_openai_host(base_url) -> bool:
    """True only for the real OpenAI API host.

    The OpenAI key (from api_key_env) must never be attached to any other
    endpoint — a keyless third-party or LAN host must not borrow it.
    """
    return (urlsplit(base_url).hostname or "").lower() in _OPENAI_HOSTS


def classify_host(base_url) -> str:
    """Tier for a base URL: on-device (loopback), LAN, or internet."""
    host = (urlsplit(base_url).hostname or "").lower()
    if host in ("localhost", "ip6-localhost") or host.endswith(".localhost"):
        return ON_DEVICE
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host.endswith((".local", ".lan", ".internal", ".home")):
            return LAN
        return INTERNET
    if ip.is_loopback:
        return ON_DEVICE
    if ip.is_private or ip.is_link_local:
        return LAN
    return INTERNET


def _parse_one(name, obj) -> Endpoint | None:
    if not isinstance(obj, dict):
        return None
    base_url = obj.get("base_url") or obj.get("baseUrl")
    api_key = obj.get("key") or obj.get("api_key") or obj.get("apiKey")
    label = obj.get("label") or name
    is_openai = name.strip().lower() in _OPENAI_NAMES
    if not base_url:
        # A keyed entry named GPT/OpenAI with no URL is the cloud model.
        if is_openai and api_key:
            base_url = OPENAI_BASE_URL
        else:
            return None
    try:
        priority = int(obj.get("priority", 0))
    except (TypeError, ValueError):
        priority = 0
    return Endpoint(label=str(label), base_url=str(base_url),
                    tier=classify_host(base_url), priority=priority,
                    api_key=api_key, model=obj.get("model"),
                    trusted=bool(obj.get("trusted")))


def parse_endpoints(data) -> list[Endpoint]:
    """Endpoints from parsed auth.json, ordered most-private first."""
    endpoints = []
    if not isinstance(data, dict):
        return endpoints
    for name, entries in data.items():
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            continue
        for obj in entries:
            endpoint = _parse_one(name, obj)
            if endpoint is not None:
                endpoints.append(endpoint)
    # Most-private tier first; within a tier, usable (ungated) before gated
    # so a trusted internet gateway is preferred over one needing opt-in.
    endpoints.sort(key=lambda e: (_TIER_RANK.get(e.tier, 9), e.gated(),
                                  e.priority, e.label))
    return endpoints


def load_endpoints(path) -> list[Endpoint]:
    """Parse the endpoints in an auth.json file (empty on any problem)."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return parse_endpoints(data)


def candidate_paths(env=None, repo_root=None) -> list[str]:
    """Where auth.json may live, most specific first."""
    env = os.environ if env is None else env
    paths = []
    explicit = env.get("AGENT_TERMINAL_AUTH_JSON")
    if explicit:
        paths.append(explicit)
    config_home = (env.get("XDG_CONFIG_HOME")
                   or os.path.join(os.path.expanduser("~"), ".config"))
    paths.append(os.path.join(config_home, "agent-terminal", "auth.json"))
    if repo_root:
        paths.append(os.path.join(repo_root, "auth.json"))
    return paths


def load_from_candidates(env=None, repo_root=None) -> list[Endpoint]:
    """Load endpoints from the first candidate auth.json that has any."""
    for path in candidate_paths(env, repo_root):
        endpoints = load_endpoints(path)
        if endpoints:
            return endpoints
    return []
