# 0009 — Session persistence: a directory per session under the XDG data dir

- Status: Accepted
- Deciders: project authors
- Related: [copilot.md](../copilot.md),
  [copilot-development-plan.md](../../copilot-development-plan.md),
  [0007](0007-bash-shell-integration.md) (the journal that feeds sessions)

## Context

Phase P1 persists meaningful terminal sessions so the user can review
or restore them later. A session is one terminal pane's command
history (from the P0 journal). The store must survive crashes, be
listable cheaply, tolerate corrupt files, honor retention and
exclusion rules, and let restore reuse the existing Markdown viewer
pane without new rendering code.

## Decision

Each session is a **subdirectory** under
`$XDG_DATA_HOME/agent-terminal/sessions/<id>/`, containing:

- `session.json` — the structured record (schema `version`, id, title,
  first/last cwd, project, start/end epoch times, the redacted command
  records, and `summary_kind`).
- `summary.md` — a human markdown summary. It is a real file, on
  purpose, so restore opens it with the unchanged `MarkdownPane` (which
  takes a path, not text).

The id is `YYYYmmdd-HHMMSS-mmm` (millisecond suffix): sortable and
collision-free in practice. Listing reads each directory's
`session.json` head — there is no index file to corrupt or keep in
sync. Saving is idempotent (same id overwrites), so a pane can be
checkpointed repeatedly and flushed again at close without duplication.
Retention sweeps on startup delete directories whose `ended_at` is
older than `retention_days`. Exclusions are applied when building a
session: a recorded cwd matching `exclude_dirs` (prefix) discards the
whole session; commands matching `exclude_commands` (glob) are dropped
individually.

Everything stored is already redacted at capture time by the journal
(ADR 0007); nothing here is sent off the machine.

## Consequences

- A crash between checkpoints loses at most the last interval of one
  pane; normal closes flush eagerly, so the common case loses nothing.
- Restore is trivial: a new tab at `cwd_last` plus the summary pane,
  with no bespoke session renderer.
- The per-directory layout scales to thousands of sessions with cheap
  listing and a simple retention sweep, at the cost of one directory
  and two small files per session.
- Only historical restore is promised, never live process reattach
  (design doc §5.8) — the format deliberately stores no process state.
