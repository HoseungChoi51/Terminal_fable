# 0005 — Copilot P3: context heuristics + typo correction

- Developed: copilot roadmap (built after P5/P4) · Status: Built
- Sources: **reconstructed** from `copilot-development-plan.md` and git (`54da748`, `4399305`, `ee43063`).

## Request (reconstructed)

Make suggestions *context-aware* — know the project, what a command's next
argument should be, and fix obvious typos — without any model call.

## Problem / context

A good completion depends on where you are: which project (python/node/rust/…),
what `git checkout` expects (branches) vs `cd` (dirs) vs `ssh` (hosts), and
whether the last command was a near-miss typo. All of this is derivable locally.

## Decisions

- **Local project + argument detection** (`context.py`): project kind, README
  run-commands, npm scripts, and an `argument_expectation` table
  (`cd`→dirs, `git checkout`→branches, `ssh`→hosts, `tar -xf`→archives, …).
- **Damerau-OSA typo correction** (`typo.py`) so transpositions count as one
  edit; corrects commands and tool subcommands; flags a fix only when it would
  **escalate risk** into destructive/privileged.

## Approach

The completion menu merges typo → context → fuzzy. An **exit-127** ("command
not found") shows a "did you mean" chip, suppressed if the correction escalates
risk.

**Key gotchas (recorded):** the just-failed command is in history, so it must be
excluded from typo candidates or it blocks its own correction; project
run-commands must gate on a bare command being typed so they don't leak into
argument contexts like `git checkout `.

## Status

Built. Its `context` detection is reused by ask mode when gathering per-question
context.
