"""LLM providers and the gated, redacting choke point (local-first).

Everything that leaves the process passes through here. The invariants,
enforced in this module and covered by tests:

1. Context is redacted before it is ever sent, unconditionally.
2. An *internet* endpoint is contacted only when ``allow_remote_context``
   is true. On-device and LAN endpoints are trusted and used freely, so
   the copilot works locally without the opt-in; the internet (e.g.
   OpenAI) is only ever a last-resort fallback.
3. Endpoints are tried most-private first (the chain from auth.json,
   already ordered); on failure the next eligible one is tried.

Secrets (API keys from auth.json) are placed in the Authorization header
only and never appear in the request body, the context, logs, or error
messages. ``urllib`` is imported only here (a source guardrail pins it).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from agent_terminal.copilot import auth
from agent_terminal.copilot import redact
from agent_terminal.copilot import risk as risk_mod

_PLACEHOLDER = re.compile(r"<[a-z0-9_]+>")
_MAX_RECENT = 12
_USER_AGENT = "agent-terminal/0.1"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


class LlmError(Exception):
    """Any failure reaching or parsing a model."""


class RemoteDisabledError(LlmError):
    """No endpoint may be used: all are gated and remote context is off."""


@dataclass(frozen=True)
class Template:
    command: str
    description: str
    placeholders: tuple[str, ...]
    risk: risk_mod.RiskResult


@dataclass(frozen=True)
class IntentResult:
    templates: tuple[Template, ...]
    note: str = ""
    endpoint: str = ""


@dataclass(frozen=True)
class Completion:
    text: str
    endpoint: str = ""


# -- endpoint chain resolution ------------------------------------------

def resolve_chain(config) -> list[auth.Endpoint]:
    """The ordered endpoint chain from auth.json, else a single config one.

    The ``api_key_env`` key is the OpenAI key: it is attached ONLY to the
    real OpenAI host — never borrowed by a keyless third-party or LAN
    endpoint (which would leak it cross-host). A keyless OpenAI endpoint
    also inherits the config's default model; LAN endpoints keep
    model=None so it is discovered from the server at call time.
    """
    endpoints = auth.load_from_candidates(
        repo_root=_REPO_ROOT) if config.auth_path is None else \
        auth.load_endpoints(config.auth_path)
    if not endpoints:
        openai = auth.is_openai_host(config.base_url)
        endpoints = [auth.Endpoint(
            label=config.model, base_url=config.base_url,
            tier=auth.classify_host(config.base_url),
            api_key=os.environ.get(config.api_key_env) if openai else None,
            model=config.model)]
    resolved = []
    for endpoint in endpoints:
        changes = {}
        if auth.is_openai_host(endpoint.base_url):
            if not endpoint.api_key:
                changes["api_key"] = os.environ.get(config.api_key_env)
            if not endpoint.model:
                changes["model"] = config.model
        resolved.append(endpoint.with_(**changes) if changes else endpoint)
    return resolved


def eligible_endpoints(chain, allow_remote) -> list[auth.Endpoint]:
    """Endpoints usable given the gate: internet only when opted in."""
    return [e for e in chain if allow_remote or not e.gated()]


def chain_label(chain, allow_remote) -> str:
    """Compact description of the chain for the UI (no secrets)."""
    if not chain:
        return "no endpoints configured"
    parts = []
    for endpoint in chain:
        mark = "" if (allow_remote or not endpoint.gated()) else " (gated)"
        parts.append(endpoint.label + mark)
    return " → ".join(parts)


def endpoint_label(endpoint) -> str:
    """"model @ host" for a single endpoint (no secret)."""
    host = urlsplit(endpoint.base_url).netloc or endpoint.base_url
    model = endpoint.model or "?"
    return f"{model} @ {host}"


# -- context assembly (always redacted) ---------------------------------

def _redact_multiline(text) -> str:
    """Redact a possibly multi-line value, dropping PEM key blocks whole.

    Any user-derived free-text field must use this, never redact_line: the
    single-line rules never match a bare base64 private-key body, so a
    multi-line value (heredoc command, composed follow-up query) would
    otherwise ship the key body intact.
    """
    redacted, _ = redact.redact_lines(str(text or "").split("\n"))
    return "\n".join(redacted)


def build_context(*, cwd=None, project=None, recent_commands=(),
                  draft_command=None, output_tails=None,
                  send_output="none", activity=None) -> str:
    """Assemble a compact context block — every field redacted.

    When `activity` (a pre-built, already-redacted episode block from
    copilot.askcontext) is given it replaces the flat recent-commands /
    output section; the legacy path (no activity) is kept for summarize().
    """
    lines = []
    if project:
        lines.append(f"project: {_redact_multiline(project)}")
    if cwd:
        lines.append(f"cwd: {_redact_multiline(cwd)}")
    if draft_command:
        # The half-typed shell command carried into ask mode — may be a
        # multi-line heredoc, so redact as a block (drops PEM key bodies).
        redacted, _ = redact.redact_lines(str(draft_command).splitlines()
                                          or [""])
        if len(redacted) == 1:
            lines.append(f"draft command: {redacted[0]}")
        else:
            lines.append("draft command:")
            lines.extend("  " + line for line in redacted)
    if activity is not None and str(activity).strip():
        # Belt-and-braces: the block is built from already-redacted digests
        # and commands, but re-run redaction at the choke point anyway.
        lines.append(_redact_multiline(activity))
        return "\n".join(lines)
    recent = [c for c in recent_commands if c][-_MAX_RECENT:]
    if recent:
        lines.append("recent commands:")
        for command in recent:
            # A history entry can itself be multi-line (heredoc); redact the
            # whole block so a private-key body is never sent line-by-line.
            redacted, _ = redact.redact_lines(str(command).splitlines())
            lines.extend("  " + line for line in redacted)
    # This legacy path (summarize) can't digest, so it emits raw tails only
    # under "full"; "digest" output flows through ask mode's `activity`.
    if send_output in (True, "full") and output_tails:
        lines.append("recent output:")
        for tail in output_tails[-3:]:
            redacted, _ = redact.redact_lines(tail or ())
            lines.extend("  " + line for line in redacted)
    return "\n".join(lines)


# -- prompts ------------------------------------------------------------

_INTENT_SYSTEM = (
    "You are a command-line assistant embedded in a terminal. The user "
    "describes a goal; you reply with shell commands that achieve it.\n"
    "Reply with ONLY a JSON object of the form:\n"
    '{"commands": [{"command": "<cmd>", "description": "<one line>"}], '
    '"note": "<optional caveat>"}\n'
    "Rules: use angle-bracket <placeholders> for any value the user must "
    "fill in. Prefer safe, standard commands. Give 1-3 options, best "
    "first. Never wrap the JSON in markdown fences. Keep descriptions to "
    "one short line."
)

_SUMMARY_SYSTEM = (
    "You summarize a terminal session for someone returning to it after "
    "a break. Given recent commands and the directory, write 2-4 plain "
    "sentences: what they were doing, and whether the last command "
    "failed. Be concrete and brief. No preamble, no markdown headers."
)

_EXPLAIN_SYSTEM = (
    "Explain what a shell command does in 2-4 concise plain-text "
    "sentences, calling out anything destructive or irreversible. No "
    "markdown, no preamble."
)

_OUTPUT_DIGEST_SYSTEM = (
    "You compress one terminal command's output into context for another "
    "assistant that will answer the user's question. You are given the "
    "command, the user's question, and the command's (already-distilled) "
    "output. Reply with at most 6 short plain-text lines: the outcome "
    "(success or failure), the key error(s) quoted verbatim if any, and "
    "the one or two details most relevant to the question. No preamble, "
    "no markdown, no advice — only the facts from the output."
)


def _system(base, suffix):
    return base + ("\n" + suffix if suffix else "")


def intent_messages(query, context, suffix=""):
    # Redact the user's typed goal too, so the remote choke point scrubs
    # every outgoing field (mirrors explain_messages). The composed
    # follow-up query can be multi-line, so redact as a block.
    query = _redact_multiline(query)
    user = f"Context:\n{context}\n\nGoal: {query}" if context else query
    return [{"role": "system", "content": _system(_INTENT_SYSTEM, suffix)},
            {"role": "user", "content": user}]


def summary_messages(context, suffix=""):
    return [{"role": "system", "content": _system(_SUMMARY_SYSTEM, suffix)},
            {"role": "user", "content": context}]


def explain_messages(command, suffix=""):
    # An answer command can be a multi-line heredoc carrying a key body.
    return [{"role": "system", "content": _system(_EXPLAIN_SYSTEM, suffix)},
            {"role": "user", "content": _redact_multiline(command)}]


_NAMING_SYSTEM = (
    "You label a terminal workspace. You are given several panes, each with "
    "its recent activity. Reply with a short name for the overall window "
    "(the project or job) and a short name for each pane (what it is doing — "
    'e.g. "server", "tests", "db shell"). Reply with ONLY a JSON object: '
    '{"window": "<name>", "panes": ["<name>", ...]} with exactly one pane '
    "name per pane, in order. Keep every name 1-3 words. No markdown."
)


def naming_messages(pane_contexts, suffix=""):
    blocks = []
    for index, context in enumerate(pane_contexts, start=1):
        body = _redact_multiline(str(context)).strip() or "(idle)"
        blocks.append(f"[pane {index}]\n{body}")
    return [{"role": "system", "content": _system(_NAMING_SYSTEM, suffix)},
            {"role": "user", "content": "\n\n".join(blocks)}]


def suggest_names(config, *, pane_contexts, chain=None, opener=None) -> dict:
    """Suggest a window name + a name per pane from each pane's (already-
    redacted) activity context. Returns {"window": str, "panes": [str, ...]}.
    Raises LlmError if no endpoint is eligible or the call fails."""
    if not pane_contexts:
        return {"window": "", "panes": []}
    text, _ = _run_chain(
        config, naming_messages(pane_contexts, config.system_suffix),
        json_mode=True, chain=chain, opener=opener)
    data = _loads_lenient(text)
    if not isinstance(data, dict):
        return {"window": "", "panes": []}
    raw = data.get("panes")
    names = ([_redact_multiline(str(n)).strip() for n in raw]
             if isinstance(raw, list) else [])
    return {"window": _redact_multiline(str(data.get("window", ""))).strip(),
            "panes": names}


def output_digest_messages(command, output, question, suffix=""):
    body = (f"Command: {_redact_multiline(command)}\n"
            f"Question: {_redact_multiline(question)}\n\n"
            f"Output:\n{output}")
    return [{"role": "system",
             "content": _system(_OUTPUT_DIGEST_SYSTEM, suffix)},
            {"role": "user", "content": body}]


# -- provider -----------------------------------------------------------

class OpenAIProvider:
    """Minimal OpenAI-compatible chat client for one endpoint.

    Note: timeout_s is urllib's per-socket-operation deadline, not a
    wall-clock cap — a server that trickles bytes can outlast it. The
    call runs on a GTK worker thread, so the UI never blocks, but a hung
    request holds that endpoint until it errors. Acceptable for now.
    """

    def __init__(self, endpoint, *, model=None, timeout_s=30, opener=None):
        self.endpoint = endpoint
        self.model = model or endpoint.model
        self.timeout_s = timeout_s
        self._opener = opener or urllib.request.urlopen

    def _request(self, path, body):
        url = self.endpoint.base_url.rstrip("/") + path
        # Send an explicit User-Agent: urllib's default "Python-urllib/x.y"
        # is blocked with 403 by WAFs in front of some gateways (e.g. the
        # centinel LiteLLM proxy). Any non-urllib UA passes.
        headers = {"Content-Type": "application/json",
                   "User-Agent": _USER_AGENT}
        if self.endpoint.api_key:
            headers["Authorization"] = f"Bearer {self.endpoint.api_key}"
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8") if body else None,
            headers=headers, method="POST" if body else "GET")
        try:
            with self._opener(request, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LlmError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise LlmError(f"unreachable: {exc.reason}") from exc
        except (ValueError, OSError) as exc:
            raise LlmError(f"bad response: {exc}") from exc

    def discover_model(self):
        payload = self._request("/models", None)
        try:
            return payload["data"][0]["id"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("no models advertised") from exc

    def list_models(self):
        """All model ids the endpoint advertises (for the model picker)."""
        payload = self._request("/models", None)
        try:
            return [m["id"] for m in payload["data"]
                    if isinstance(m, dict) and m.get("id")]
        except (KeyError, TypeError) as exc:
            raise LlmError("no models advertised") from exc

    def complete(self, messages, *, json_mode=False) -> str:
        body = {"model": self.model or "default", "messages": messages,
                "temperature": 0.2}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        payload = self._request("/chat/completions", body)
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("malformed model response") from exc


# Discovered model per base_url, so LAN servers are queried once.
_MODEL_CACHE: dict = {}


def _resolve_model(endpoint, timeout_s, opener):
    if endpoint.model:
        return endpoint.model
    cached = _MODEL_CACHE.get(endpoint.base_url)
    if cached:
        return cached
    provider = OpenAIProvider(endpoint, timeout_s=timeout_s, opener=opener)
    model = provider.discover_model()   # raises LlmError -> caller falls on
    _MODEL_CACHE[endpoint.base_url] = model
    return model


# -- response parsing ---------------------------------------------------

def _loads_lenient(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (ValueError, TypeError):
                return {}
        return {}


def parse_intent_response(text) -> IntentResult:
    data = _loads_lenient(text)
    if not isinstance(data, dict):
        return IntentResult((), "")
    templates = []
    for item in data.get("commands", []):
        if not isinstance(item, dict):
            continue
        command = str(item.get("command", "")).strip()
        if not command:
            continue
        placeholders = tuple(dict.fromkeys(_PLACEHOLDER.findall(command)))
        templates.append(Template(
            command=command,
            description=str(item.get("description", "")).strip(),
            placeholders=placeholders,
            risk=risk_mod.classify(command)))
    note = str(data.get("note", "")).strip()
    return IntentResult(tuple(templates), note)


# -- orchestration (resolve chain + gate + redact + fallback + parse) ----

def _run_chain(config, messages, *, json_mode, chain=None, opener=None):
    """Try each eligible endpoint most-private first; return (text, label)."""
    chain = chain if chain is not None else resolve_chain(config)
    usable = eligible_endpoints(chain, bool(config.allow_remote_context))
    if not usable:
        raise RemoteDisabledError(
            "no local model is available and the cloud model is disabled "
            "(set assistant.llm.allow_remote_context to enable it)")
    errors = []
    for endpoint in usable:
        try:
            model = _resolve_model(endpoint, config.timeout_s, opener)
            text = OpenAIProvider(
                endpoint, model=model, timeout_s=config.timeout_s,
                opener=opener).complete(messages, json_mode=json_mode)
            return text, endpoint.label
        except LlmError as exc:
            # A discovered model that the server later rejects (model
            # swapped/restarted) must not poison the endpoint forever —
            # drop the cache so the next attempt re-discovers.
            if endpoint.model is None:
                _MODEL_CACHE.pop(endpoint.base_url, None)
            errors.append(f"{endpoint.label}: {exc}")
    raise LlmError("all endpoints failed (" + "; ".join(errors) + ")")


def suggest_commands(config, *, query, cwd=None, project=None,
                     recent_commands=(), draft_command=None, activity=None,
                     chain=None, opener=None) -> IntentResult:
    context = build_context(cwd=cwd, project=project,
                            recent_commands=recent_commands,
                            draft_command=draft_command, activity=activity)
    text, label = _run_chain(
        config, intent_messages(query, context, config.system_suffix),
        json_mode=True, chain=chain, opener=opener)
    return replace(parse_intent_response(text), endpoint=label)


def digest_output_llm(config, *, command, output, question="",
                      chain=None, opener=None) -> str | None:
    """An LLM-written digest of one command's output, for the "llm"
    digest_mode A/B (vs the local heuristic digester).

    `output` must already be redacted (it comes from a CommandRecord's
    digest/tail); it is re-redacted going in and the model's reply is
    re-redacted coming out, so this stays inside the choke point. Returns
    None when no endpoint is eligible or the call fails, so the caller
    falls back to the heuristic digest rather than losing context.
    """
    source = _redact_multiline(output or "").strip()
    if not source:
        return None
    try:
        text, _ = _run_chain(
            config,
            output_digest_messages(command or "", source, question,
                                   config.system_suffix),
            json_mode=False, chain=chain, opener=opener)
    except LlmError:
        return None
    return _redact_multiline(text.strip()) or None


def summarize(config, *, cwd=None, project=None, recent_commands=(),
              output_tails=None, chain=None, opener=None) -> Completion:
    context = build_context(cwd=cwd, project=project,
                            recent_commands=recent_commands,
                            output_tails=output_tails,
                            send_output=config.send_output)
    text, label = _run_chain(
        config, summary_messages(context, config.system_suffix),
        json_mode=False, chain=chain, opener=opener)
    return Completion(text.strip(), label)


def explain(config, command, *, chain=None, opener=None) -> Completion:
    text, label = _run_chain(
        config, explain_messages(command, config.system_suffix),
        json_mode=False, chain=chain, opener=opener)
    return Completion(text.strip(), label)


def list_models(config, endpoint, *, opener=None) -> list[str]:
    """Models the endpoint advertises (GET /models). Raises LlmError on any
    network/parse problem — the caller runs it on a worker thread."""
    provider = OpenAIProvider(endpoint, timeout_s=config.timeout_s,
                              opener=opener)
    return provider.list_models()
