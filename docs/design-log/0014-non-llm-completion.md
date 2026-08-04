# 0014 — A robust non-LLM algorithm for command autocompletion

- Developed: 2026-08 · Status: Built (Phase A) · ADR: [0013](../decisions/0013-frecency-completion-corpus.md)
- Modules: `agent_terminal/copilot/corpus.py`, `copilot/ranking.py`
  (+ `copilot/suggest.py`, `copilot/journal.py`, `copilot/config.py`,
  `native_terminal.py`, `docs/completion.md`)

## Request

> "I have been thinking about multiple new features, but haven't come up with a
> definite conclusion. Compare branches: main, copilot-persistence,
> copilot-workspaces, copilot-ask-mode. I want to checkpoint and continue to
> develop a major feature before it diverge too much."
>
> Clarified: "Basically Copilot P6, but need a separate planning stage to
> clarify the requirement. Clipboard gap is a minor issue that I can ignore for
> the moment. persistence and layout serialization may be better implemented in
> the 'next generation' terminal copilot. **What is important for now is to
> develop a robust non-LLM algorithm for command autocompletion.**"
>
> On ranking: "Implement all three. Default to the context scoping, but let me
> switch to others in the settings. Implement and let me test easier one first."

**Rephrased.** Make the *deterministic* completion path good enough to depend
on daily — persistent, context-aware, honestly gated — rather than adding
another LLM-backed feature. Ship it in testable stages, easiest first, with the
ranking model switchable so the tiers can be compared on real usage.

## Decisions (clarifying Q&A)

- **The branches had not diverged.** `copilot-ask-mode` →
  `copilot-output-context` → `copilot-workspaces` → `copilot-persistence` was a
  strict linear stack (each an ancestor of the next), and `origin/main` had
  already merged the workspaces tail via PR #2. Content-wise the persistence tip
  differed from `main` by a single file. So "checkpoint" cost one cherry-pick,
  not a merge exercise — the premise of the question dissolved on inspection.
- **Surfaces** → *ghost text + the `Ctrl+Shift+Space` menu*. **Tab stays
  bash's**: intercepting it would mean beating bash at its own job or degrading
  daily use, and it would also force a resolution of `prompt.py`'s
  deliberate drop-to-DIRTY on `\t`.
- **Corpus** → *persistent store, seeded from `~/.bash_history`*, so ranking is
  useful on day one instead of after weeks of dogfooding.
- **Ranking** → *all three tiers, config-switchable, `frecency` default*,
  mirroring the existing `digest_mode` A/B pattern.
- **Shell bridge** → *out-of-process helper* (a separate non-interactive bash
  with bash-completion sourced), never the live shell — the daily-driver shell
  is risk-register item 1.

## Approach

Exploration changed the shape of the work: `context.py` already had
`argument_expectation`, `file_completions`, and — decisively — a documented
`menu_suggestions(providers=...)` seam "for the git/ssh expectations this module
cannot resolve without a subprocess". P3 had left the socket; nothing had been
plugged into it. So the compgen bridge *extends* existing machinery rather than
replacing it, and Phase A reduced to one new ranking core plus wiring.

- **`corpus.py`** (pure/stdlib): one entry per unique command with count,
  last-used, per-directory counts, exit tallies, and predecessor edges.
  Atomic write-then-rename; every read/write error degrades silently.
  Redaction runs on ingest regardless of provenance, because `~/.bash_history`
  is an ingress the journal never saw.
- **`ranking.py`** (pure): frecency (`log1p` frequency x exponential recency
  decay) x cwd/project boost x exit factor x match quality. Confidence is a
  **dominance margin** against the best rival proposing a different suffix,
  which turns `min_confidence` from a comparison against a hardcoded constant
  into a real eagerness dial.
- **Merging**: sources are squashed into comparable bands, then ordered
  typo-fix > argument completion > everything else by score. The old merge
  ordered purely by source, so scores were decorative.
- **Ingest** is an injected journal *sink*, keeping `journal.py` free of any
  dependency on the corpus; a raising sink cannot break journalling.

## Status

Phase A built and verified. 604 tests green (514 baseline + 90 new), GTK launch
confirmed. Measured on the author's machine: 11 ms to seed 2020 history lines
into 364 unique commands, 2.2 ms to load thereafter, **0.435 ms per keystroke**
to rank — comfortably inside a 16 ms frame, which is what makes running this on
every commit signal safe.

Follow-ups: Phase B (`chain` bigram ranking) and Phase C (token-level
completion + the `compgen` bridge behind the `providers` seam). Ghost text
remains default-off until Phase A has been dogfooded.
