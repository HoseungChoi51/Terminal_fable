# 0013 — Deterministic completion: a persistent corpus ranked by frecency

- Status: Accepted
- Deciders: project authors
- Related: [0003](0003-pure-gtk-free-core.md) (pure core),
  [0005](0005-copilot-pure-core-package.md) (copilot package rule),
  [0006](0006-overlay-ghost-text-no-engine-replacement.md) (ghost-text overlay),
  [0009](0009-session-persistence-format.md) (XDG data layout),
  [completion.md](../completion.md)

## Context

The copilot's non-LLM suggestion path (P2–P4) worked but could not be relied
on daily, and ghost text therefore shipped **off by default**. Four properties
were missing, and each of them is structural rather than a tuning problem:

- **No memory.** Suggestions were drawn from `PaneJournal` — one pane, in
  memory, 200 commands, discarded on exit. Nothing could be learned across
  sessions, so ranking could never improve.
- **No context.** A command scored identically everywhere, so `npm test`
  ranked the same inside a Python repository as inside a JavaScript one.
- **No real confidence.** `ghost_completion` matched with a literal
  `str.startswith` and reported a hardcoded `0.9`/`0.6`. The configured
  `min_confidence` therefore gated nothing — it compared a constant to a
  threshold.
- **No comparable scores.** Each source produced unbounded additive bonuses on
  its own scale, so the menu could not meaningfully rank across sources and
  instead ordered by whichever source ran first.

The LLM path (P5) is orthogonal and unaffected; this ADR is about making the
deterministic path good enough to leave switched on.

## Decision

- **A persistent corpus** (`copilot/corpus.py`) under
  `$XDG_DATA_HOME/agent-terminal/completion/corpus.json`, one entry per unique
  command accumulating count, last-used, per-directory counts, exit tallies,
  and predecessor edges. Chosen over extending the existing session store
  (ADR 0009): that store is retention-capped and shaped for *resume*, where a
  ranking corpus wants unbounded-in-time aggregates and a different eviction
  rule.
- **Frecency with context scoping** (`copilot/ranking.py`) as the model:
  `log1p(count)` damped by exponential recency decay, multiplied by a
  cwd/project boost and an exit-status factor. Chosen over raw recency (a
  most-recently-used list forgets habits) and over raw frequency (which
  ossifies). The `log1p` is what stops one command run 500 times from
  permanently burying everything else.
- **Confidence as a dominance margin**, `s1 / (s1 + s2)` against the best rival
  proposing a *different* suffix. This replaces the fabricated constants with a
  measurement, so `min_confidence` becomes a genuine eagerness dial: one clear
  candidate scores 1.0, two equals score ~0.5 and are correctly withheld.
- **Normalized score bands.** Every source is squashed into `(0, 1)` and scaled
  to a ceiling, then merged in bands: a typo correction always leads, a concrete
  argument completion (path/branch/host) outranks the rest, and habits compete
  with project/README commands and recipes on score. Previously the menu ordered
  purely by source, which made scores decorative.
- **Cold-start from `~/.bash_history`**, once, with entries aged
  `SEED_AGE_DAYS` and given no cwd or exit data — so the corpus is useful on
  day one, while a single genuinely observed run outranks anything seeded.
- **Ingest is a journal sink**, injected rather than imported, so
  `copilot/journal.py` keeps no dependency on the corpus and the pure core
  stays pure.

## Consequences

- New pure, headless-tested modules: `copilot/corpus.py`, `copilot/ranking.py`.
  GTK stays out of the core (ADR 0003) and the one-way import rule (ADR 0005)
  holds — `native_terminal` owns the process-wide corpus instance and the
  save cadence, the core owns the model.
- **Redaction gains an ingress.** `~/.bash_history` is a new source that never
  passed through the journal, so `Corpus.add` redacts unconditionally and
  applies the session store's exclusion policy, regardless of provenance.
- Config gains an `assistant.completion` section. `ranking` selects among
  `frecency` (default), `chain`, and `token`, mirroring the existing
  `digest_mode` A/B pattern so the tiers can be compared on real usage.
- The corpus is a **cache, never a system of record**: every read error yields
  an empty corpus and every write error is swallowed, preserving the
  dogfooding launchability invariant. Writes are atomic (write-then-rename).
- Measured on the author's machine: 11 ms to seed from 2020 history lines
  (364 unique commands), 2.2 ms to load thereafter, and **0.435 ms per
  keystroke** to rank — inside a 16 ms frame budget, which is what makes it
  safe to run ghost text on every commit signal.
- The corpus-free path in `suggest.py` is retained, not deleted: it is the
  documented degradation when `completion.corpus` is disabled.
- `chain` and `token` modes are selectable but their ranking layers land in
  later phases; `token` additionally awaits the out-of-process `compgen`
  bridge, which plugs into the existing `context.menu_suggestions(providers=)`
  seam rather than replacing it.
