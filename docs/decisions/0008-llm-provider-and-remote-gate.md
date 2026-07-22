# 0008 — LLM provider: OpenAI-compatible, behind a gated redacting choke point

- Status: Accepted
- Deciders: project authors
- Related: [copilot.md](../copilot.md),
  [copilot-development-plan.md](../../copilot-development-plan.md),
  [0005](0005-copilot-pure-core-package.md) (pure-core package),
  [0007](0007-bash-shell-integration.md) (the journal it draws context from)

## Context

Phase P5 adds the copilot's only network-using features: the intent
side panel (natural language → command templates) and LLM-polished
resume summaries. The terminal observes sensitive data, so sending any
of it to a model is a privacy decision that must be deliberate,
auditable, and off by default (design doc §9). The project also has a
zero-pip-dependency rule, and the author wants to dogfood with a large
cloud model now and swap to a small local model later without a rewrite.

## Decision

- **One OpenAI-compatible client over stdlib `urllib`**, targeting a
  **configurable `base_url`**, so OpenAI, an office LAN server, and a
  local Ollama / llama.cpp / vLLM server are all the same code. `urllib`
  network access is imported only in `copilot/llm.py`, pinned by a
  source-guardrail test.
- **A local-first endpoint chain from `auth.json`.** `copilot/auth.py`
  reads endpoints (base URL, optional key) and classifies each by
  locality — on-device (loopback), LAN (private ranges / `.local`), or
  internet — ordering the chain most-private first. The copilot tries
  each eligible endpoint in turn and falls back to the next only when a
  closer one is unreachable; the cloud is a last resort.
- **The opt-in gates the internet tier only.** On-device and LAN
  endpoints are trusted (data stays on your machine or your local
  network) and are used without `allow_remote_context`. An internet
  endpoint is contacted only when the opt-in is on — enforced *before*
  any network call, including model discovery. If every endpoint is
  gated and the opt-in is off, the call raises rather than sending.
- **Redaction is unconditional.** Context is assembled and secret-
  redacted once, before it is sent to *any* endpoint.
- **Secrets stay in the header.** An endpoint's key (from `auth.json`,
  which is gitignored) is placed only in the `Authorization` header —
  never in the request body, the context, the UI labels, logs, or error
  messages.
- Model output is parsed into risk-classified command templates
  (reusing the P2 classifier); the UI can insert a template
  (`feed_child`, no newline — nothing auto-runs), copy it, or explain
  it, and it shows which endpoint answered.

## Consequences

- The copilot works out of the box against a local/LAN model with no
  opt-in, while the cloud stays off until explicitly enabled — a
  property pinned by tests (with the opt-in off and only an internet
  endpoint, the fake server records zero requests).
- LAN model names are discovered from the server (`GET /v1/models`) and
  cached, so an endpoint needs only a URL in `auth.json`.
- Network work runs on worker threads with a per-endpoint timeout and
  marshals back via the GTK loop, so a slow or unreachable model never
  blocks the UI; summaries fall back to the heuristic version.
- Adding another provider means another `complete()` implementation
  behind the same chain and redaction — the choke point stays put.
