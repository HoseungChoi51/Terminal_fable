# 0010 — Terminal output as ask-mode context (+ LLM A/B mode)

- Developed: 2026-07 · Status: Built · ADR: [0008](../decisions/0008-llm-provider-and-remote-gate.md) (amended)
- Modules: `copilot/digest.py`, `copilot/episode.py`, `copilot/askcontext.py`

## Request

> "How about providing terminal output context to LLM? Then, depending on how
> verbose the last stdout was, we may have to compact (part of) it. Having the
> 'history' of the last few terminal input/output would be better than filling
> the context with the long text of the last output. But then, we may need a
> lightweight always-on summarizer / context organizer as a sidecar? What I
> want is LLM's answer to my question based on the understanding of the task
> I'm on (characterized by a sequence of user inputs 'dense' in time). Give me
> creative suggestions how we could achieve this."
>
> Then: "Turn into a plan. Include steps for review/analysis of the internals
> of each step (why an episode is determined as such, how digest is generated,
> and so on)."

**Rephrased.** Ask mode should answer questions grounded in *what actually
happened in the terminal*, not blind to it — but a naive dump of the last
output is wrong. Give the model a **short, time-coherent view of the task the
user is on**: a handful of recent commands plus **distilled** (not raw) output.
"The task I'm on" is a run of commands that are *dense in time*. Prefer local,
deterministic machinery; only consider an always-on summarizer if truly needed.
Each phase must document *why* its heuristic decides what it does.

## Problem / context

Ask mode sent the model **zero** terminal output (only cwd, project, and recent
command *strings*). The naive fix — dump the last output — fails twice: a
build/test log is thousands of lines, and on a **local** model prompt tokens
cost *wall-clock* (the gateway returns `timings.prompt_per_second`), so a huge
prompt stalls ask mode's sub-200 ms feel. **The 131k context window is not the
constraint; latency is.** So the answer is a distilled, time-coherent view.

## Decisions (clarifying Q&A)

- **Scope** → *Core first, extras as follow-up.* Build the local pieces (a pure
  digester + episode segmentation + a context ladder) now; defer LLM/cross-
  command cleverness.
- **Output default** → *`"digest"`.* Ask mode sends a distilled, redacted
  digest by default (not nothing, not the raw tail).
- **No always-on sidecar.** A local pure-function digester, not a daemon.

### Follow-up request

> "Is it possible to switch between LLM-assisted mode and lightweight,
> deterministic mode? If so, implement LLM-assisted version too so that I can do
> A/B test."

→ Added a second axis, `digest_mode = "heuristic" | "llm"`, orthogonal to
*how much* output is sent. Runtime toggle **Ctrl+Shift+D** flips a session
override live so the two can be compared head to head; the active mode shows in
the ⌁ ASK header.

## Approach

- **`digest.py`** (pure) — distil raw output to ≤ budget lines: keep error /
  summary lines + context, run-length-collapse spam, strip ANSI, head+tail
  elision, each kept line tagged with *why*.
- **`episode.py`** (pure) — segment the journal into episodes at idle gaps
  (>8 min) / cwd / branch changes; a headline (`project: cmd · span · n cmds ·
  k failures`).
- **`askcontext.py`** (pure) — assemble by priority within a byte budget:
  headline → salient command's digest → other failures → one-liners. Salience
  by `fuzzy.score(question, cmd)`, not recency.
- **Config** — `send_output` became tri-state `false|"digest"|"full"`
  (default `"digest"`); `digest_mode` `"heuristic"|"llm"`.
- **Redaction** stays a single choke point at three layers: output redacted
  *before* both digest and tail; askcontext only reshapes redacted data;
  `build_context` re-redacts the whole block.

## Status

Built and verified (real-bash GTK e2e: the digested error + episode headline
reach the request, not the raw log; adversarial leak test drives real secrets
through the whole path). Default flipped to `"digest"`; ADR 0008 amended.

**Deferred (documented, not built):** delta-digests, failure-triggered eager
digest, lazy cached LLM episode-digest (the on-demand, non-daemon "sidecar"),
zero-LLM fast-path answers.
