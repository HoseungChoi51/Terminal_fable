"""Assemble ask-mode context from an episode, by priority within a budget.

The model needs to understand *the task you're on*, but a verbose last
command must not crowd out the history. So we fill a byte budget top-down
and drop from the bottom:

  1. episode headline (always)
  2. the *salient* command + its full digest   ← the biggest slice
  3. other failed commands + their error-only digests
  4. everything else → one line each, `cmd → exit`

"Salient" is chosen by relevance to the question (fuzzy.score), not
recency: "why did the build fail?" surfaces the `cargo` record, not
whatever ran last. Pure/stdlib-only (ADR 0005). It consumes only
already-redacted commands and digests (built from redacted output in
journal._finalize) and merely selects/formats them — it never re-reads raw
output, so it can't reintroduce a redacted secret (ADR 0008).
"""

from __future__ import annotations

from agent_terminal.copilot import digest as digest_mod
from agent_terminal.copilot import fuzzy

_DETAIL_DIGEST_LINES = 16     # the salient command's digest cap
_ERROR_DIGEST_LINES = 8       # other failed commands' error cap
_ONE_LINERS = 12              # trailing `cmd -> exit` lines
_ERROR_REASONS = (digest_mod.ERROR, digest_mod.SUMMARY, digest_mod.ELISION)


def _exit_suffix(record) -> str:
    return ("" if record.exit_code in (0, None)
            else f"  (exit {record.exit_code})")


def _first_line(cmd) -> str:
    return (cmd or "").splitlines()[0] if cmd else ""


def _salient_record(records, query):
    """The record the question is about — best fuzzy match, else the last
    one that actually produced output."""
    best, best_score = None, None
    if query.strip():
        for record in records:
            if not record.cmd:
                continue
            s = fuzzy.score(query, record.cmd)
            if s is not None and (best_score is None or s > best_score):
                best, best_score = record, s
    if best is not None:
        return best
    for record in reversed(records):
        dg = getattr(record, "digest", None)
        if dg is not None and not dg.is_empty():
            return record
        if record.output_tail:
            return record
    return records[-1] if records else None


def _detail(lines, record, *, cap, errors_only, output_mode):
    """Append a command line + (per output_mode) its digest / tail."""
    if not record.cmd:
        return
    lines.append(f"$ {_first_line(record.cmd)}{_exit_suffix(record)}")
    if output_mode == "none":
        return
    if output_mode == "full" and record.output_tail:
        for text in record.output_tail[-cap:]:
            lines.append(f"    {text}")
        return
    dg = getattr(record, "digest", None)
    if dg is not None and not dg.is_empty():
        shown = 0
        for dl in dg.lines:
            if errors_only and dl.reason not in _ERROR_REASONS:
                continue
            lines.append(f"    {dl.text}")
            shown += 1
            if shown >= cap:
                break
    elif record.output_tail:
        for text in record.output_tail[-cap:]:
            lines.append(f"    {text}")


def _fit(lines, budget_chars) -> str:
    """Join lines, keeping the first (headline) and dropping trailing lines
    once the budget is exceeded."""
    out, used = [], 0
    for line in lines:
        cost = len(line) + 1
        if out and used + cost > budget_chars:
            out.append("… earlier activity trimmed …")
            break
        out.append(line)
        used += cost
    return "\n".join(out)


def build_ask_context(episode, *, question="", draft="",
                      output_mode="digest", budget_chars=3000) -> str:
    """The activity block ask mode sends as terminal context (or "").

    output_mode: "none" (commands only) | "digest" | "full" (verbose tail).
    """
    if episode is None or not episode.records:
        return ""
    records = list(episode.records)
    query = f"{question} {draft}".strip()

    lines = [f"task: {episode.headline()}"]
    detailed = set()

    salient = _salient_record(records, query)
    if salient is not None:
        _detail(lines, salient, cap=_DETAIL_DIGEST_LINES, errors_only=False,
                output_mode=output_mode)
        detailed.add(id(salient))

    for record in records:
        if id(record) in detailed or record.exit_code in (0, None):
            continue
        _detail(lines, record, cap=_ERROR_DIGEST_LINES, errors_only=True,
                output_mode=output_mode)
        detailed.add(id(record))

    others = [r for r in records if id(r) not in detailed and r.cmd]
    if others:
        lines.append("recent:")
        for record in others[-_ONE_LINERS:]:
            lines.append(f"  {_first_line(record.cmd)}{_exit_suffix(record)}")

    return _fit(lines, budget_chars)
