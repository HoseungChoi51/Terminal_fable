# 0007 — Copilot P5: LLM features (provider + remote gate)

- Developed: copilot roadmap (built early, out of order) · Status: Built · ADR: [0008](../decisions/0008-llm-provider-and-remote-gate.md)
- Sources: **reconstructed** from `copilot-development-plan.md`, ADR 0008, and git (`42fdc0c`, `37d8617`, `97fd305`, `0d7b837`).

## Request (reconstructed)

Bring an actual model into the terminal: natural-language → command
suggestions, session summaries — but keep local/LAN models first-class and the
cloud strictly opt-in, with everything redacted before it leaves the process.

## Problem / context

The value jump is a model that can answer and suggest. The risk is data egress.
The design must make local-first the default path and the internet a gated,
last-resort fallback, behind one auditable choke point.

## Decisions

- **LLM backend: an OpenAI-compatible client over stdlib `urllib`** (the author
  explicitly chose the OpenAI API over a Claude CLI/API), with a configurable
  `base_url` so "big model now, smallest-working model later" is a config change.
- **A single gated, redacting choke point** (ADR 0008): every call runs
  `ContextGate.ensure_allowed()` (raises unless `allow_remote_context`, default
  off) + unconditional redaction before any network. On-device/LAN endpoints are
  trusted and used freely; the internet is a gated fallback. Endpoints are tried
  most-private first; LAN model names are discovered from the server.
- `urllib` is confined to `llm.py` (a source guardrail pins it).

## Approach

`copilot/llm.py`; a Ctrl+Shift+P `IntentPane` (NL → risk-labeled command
templates) and a View → Session Summary. Three endpoint tiers documented (OpenAI
cloud; office LAN server; local Ollama with a `/no_think` suffix for Qwen).

## Status

Built first (out of order) at the author's request to dogfood LLM features with
a big model. The `IntentPane` side panel was **later superseded** by in-place
ask mode — see design-log [0008](0008-in-place-ask-mode.md).
