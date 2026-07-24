# 0009 — Should we adopt ACP (Agent Client Protocol)? — Decided against, for now

- Developed: 2026-07 · Status: Decided (not adopted) · ADR: none (a scoping decision, not an architecture change)

## Request

> "So, what do you think? Should we use ACP?"

**Rephrased.** Evaluate whether the terminal's assistant should speak the Agent
Client Protocol (a standard for editor/agent interop) instead of, or in addition
to, the current direct LLM integration.

## Problem / context

ACP standardizes how a client (editor/terminal) talks to an external agent
process. Adopting it could open the door to third-party agents, but it also adds
a protocol layer, a process boundary, and a dependency — against a copilot whose
whole design is a **local-first, single redacting choke point** (ADR 0008) with
tight, in-process control over what leaves the machine.

## Decision

> "We will not worry about ACP anytime soon."

Do **not** adopt ACP now. The current gated/redacting in-process LLM integration
serves the immediate goals, and the assistant's value is in local understanding
(journal, episodes, digests) that doesn't need a cross-process agent protocol.
Revisit only if a concrete need for external-agent interop appears.

## Status

Recorded as a deliberate non-adoption so it isn't re-litigated. No code changed.
