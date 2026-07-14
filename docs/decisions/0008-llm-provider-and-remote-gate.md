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
  **configurable `base_url`**. OpenAI's frontier models now, a local
  Ollama / llama.cpp / vLLM server later, is a config edit — no code
  change. The API key is read from a configurable env var and omitted
  when unset (local servers usually need none). `urllib` is imported
  only in `copilot/llm.py`, pinned by a source-guardrail test.
- **A single remote choke point.** Every feature calls
  `llm.suggest_commands` / `summarize` / `explain`, and each of those,
  before touching the network, (1) passes through `ContextGate`, which
  raises unless `assistant.llm.allow_remote_context` is true, and
  (2) runs all assembled context through the same secret redaction used
  for on-disk storage. Redaction is unconditional — not a setting.
- **Off by default.** `allow_remote_context` ships false; the intent
  panel says how to enable it rather than calling out silently.
- Model output is parsed into risk-classified command templates
  (reusing the P2 classifier); the UI can insert a template
  (`feed_child`, no newline — nothing auto-runs), copy it, or ask for
  an explanation.

## Consequences

- With the gate closed, zero bytes leave the machine — a property
  pinned by tests (the fake opener records no requests). With it open,
  every payload is redacted first.
- The same code dogfoods a big model and later serves a small local
  one; only config changes.
- Network work runs on worker threads with a timeout and marshals back
  via the GTK loop, so a slow or unreachable model never blocks the UI;
  summaries fall back to the heuristic version on error.
- Adding another provider means another `complete()` implementation
  behind the same gate — the choke point and redaction stay put.
