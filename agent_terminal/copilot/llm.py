"""LLM provider and the single gated, redacting remote choke point.

Everything that leaves the machine passes through here. Two rules hold
unconditionally, enforced in this module and covered by tests:

1. Nothing is sent unless ``allow_remote_context`` is true (ContextGate).
2. Every payload is run through secret redaction first, regardless of
   any other setting.

The client speaks the OpenAI chat-completions API against a configurable
base URL, so the same code targets OpenAI now and a local
OpenAI-compatible server (Ollama, llama.cpp, vLLM) later by config alone.
``urllib`` is imported only here (a source guardrail test pins that).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from agent_terminal.copilot import redact
from agent_terminal.copilot import risk as risk_mod

_PLACEHOLDER = re.compile(r"<[a-z0-9_]+>")
_MAX_RECENT = 12


class LlmError(Exception):
    """Any failure reaching or parsing the model."""


class RemoteDisabledError(LlmError):
    """Raised when a remote call is attempted while the gate is closed."""


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


# -- gate ---------------------------------------------------------------

def endpoint_label(config) -> str:
    """Human-readable "model @ host" for showing where requests go."""
    host = urlsplit(config.base_url).netloc or config.base_url
    return f"{config.model} @ {host}"


class ContextGate:
    """The one place that decides whether anything may go remote."""

    def __init__(self, config):
        self.config = config

    def allowed(self) -> bool:
        return bool(self.config.allow_remote_context)

    def ensure_allowed(self):
        if not self.allowed():
            raise RemoteDisabledError(
                "remote context is disabled "
                "(set assistant.llm.allow_remote_context to enable)")


# -- context assembly (always redacted) ---------------------------------

def build_context(*, cwd=None, project=None, recent_commands=(),
                  output_tails=None, send_output=False) -> str:
    """Assemble a compact, fully-redacted context block."""
    lines = []
    if project:
        lines.append(f"project: {project}")
    if cwd:
        lines.append(f"cwd: {redact.redact_line(str(cwd))[0]}")
    recent = [c for c in recent_commands if c][-_MAX_RECENT:]
    if recent:
        lines.append("recent commands:")
        for command in recent:
            lines.append("  " + redact.redact_line(command)[0])
    if send_output and output_tails:
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


def _system(base, suffix):
    return base + ("\n" + suffix if suffix else "")


def intent_messages(query, context, suffix=""):
    user = f"Context:\n{context}\n\nGoal: {query}" if context else query
    return [{"role": "system", "content": _system(_INTENT_SYSTEM, suffix)},
            {"role": "user", "content": user}]


def summary_messages(context, suffix=""):
    return [{"role": "system", "content": _system(_SUMMARY_SYSTEM, suffix)},
            {"role": "user", "content": context}]


# -- provider -----------------------------------------------------------

class OpenAIProvider:
    """Minimal OpenAI-compatible chat client over urllib."""

    def __init__(self, config, opener=None):
        self.config = config
        self._opener = opener or urllib.request.urlopen

    def complete(self, messages, *, json_mode=False) -> str:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        body = {"model": self.config.model, "messages": messages,
                "temperature": 0.2}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.config.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with self._opener(request,
                              timeout=self.config.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LlmError(f"HTTP {exc.code} from model endpoint") from exc
        except urllib.error.URLError as exc:
            raise LlmError(f"cannot reach model endpoint: {exc.reason}") \
                from exc
        except (ValueError, OSError) as exc:
            raise LlmError(f"bad response from model endpoint: {exc}") \
                from exc
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("malformed model response") from exc


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


# -- orchestration (gate + redact + call + parse) -----------------------

def suggest_commands(config, *, query, cwd=None, project=None,
                     recent_commands=(), opener=None) -> IntentResult:
    ContextGate(config).ensure_allowed()
    context = build_context(cwd=cwd, project=project,
                            recent_commands=recent_commands)
    text = OpenAIProvider(config, opener=opener).complete(
        intent_messages(query, context, config.system_suffix),
        json_mode=True)
    return parse_intent_response(text)


def summarize(config, *, cwd=None, project=None, recent_commands=(),
              output_tails=None, opener=None) -> str:
    ContextGate(config).ensure_allowed()
    context = build_context(cwd=cwd, project=project,
                            recent_commands=recent_commands,
                            output_tails=output_tails,
                            send_output=config.send_output)
    return OpenAIProvider(config, opener=opener).complete(
        summary_messages(context, config.system_suffix)).strip()


def explain(config, command, *, opener=None) -> str:
    ContextGate(config).ensure_allowed()
    messages = [{"role": "system",
                 "content": _system(_EXPLAIN_SYSTEM, config.system_suffix)},
                {"role": "user", "content": redact.redact_line(command)[0]}]
    return OpenAIProvider(config, opener=opener).complete(messages).strip()
