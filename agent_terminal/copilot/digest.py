"""Distill a command's output into a few salient lines within a budget.

On a local model, prompt tokens cost wall-clock (the gateway even reports
`timings.prompt_per_second`), so ask mode must never send a raw build/test
log. This turns the output of one command into a short, deterministic
digest: the error / summary lines a question is actually about, with the
bulk elided.

Design (see the plan): keep *salient* lines (errors) with a little context,
keep known *summary* lines (pytest/cargo/make counts) verbatim, collapse
repeated lines, and elide the rest to a head + tail — dropping lowest
priority first when over budget. Every kept line carries a `reason` tag so
a test can explain *why* it survived and rule-tuning stays visible.

Pure, stdlib-only, GTK-free (ADR 0005). It runs on lines that redact.py has
already scrubbed (ADR 0008); it only ever *drops* content, never adds, so
it can't reintroduce a secret redaction removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Reason tags, ordered by importance — used both for the trace and to decide
# what to drop first when over budget (higher number drops sooner).
SUMMARY = "summary"
ERROR = "error"
CONTEXT = "context"
TAIL = "tail"
HEAD = "head"
ELISION = "elision"
_PRIORITY = {SUMMARY: 0, ERROR: 1, CONTEXT: 2, TAIL: 3, HEAD: 4, ELISION: 9}

# A line naming a failure / diagnostic — the thing questions are about.
# Word-bounded so a substring inside a path (pytest_fail.txt) doesn't trip.
_SALIENT = re.compile(
    r"\b(errors?|failed|failing|failure|fail|panic|exception|assert\w*|"
    r"fatal|denied|refused|undefined|unresolved|traceback|"
    r"abort(ed|ing)?|segmentation fault|core dumped|cannot|"
    r"could ?n[o']t|no such|not found|unexpected)\b|"
    r"^\s*E[\s\d]|^\s{0,4}at\s|"                    # pytest "E  ", stack "at "
    r"^[-+]{3}\s|^@@ ",                             # diff hunks
    re.IGNORECASE)

# A one-line outcome worth keeping verbatim (highest priority).
_SUMMARY = re.compile(
    r"\b\d+\s+(passed|failed|error|errors|warning|warnings|skipped)\b|"
    r"^=+.*(passed|failed|error).*=+\s*$|"          # pytest ==== N failed ====
    r"^error\[[A-Z]?\d+\]|"                         # rust error[E0499]
    r"^make(\[\d+\])?:\s+\*\*\*|"                   # make: *** [t] Error 1
    r"^\s*\d+\s+error(s)?\b|"                        # tsc/eslint "3 errors"
    r"build (successful|failed)|"
    r"^\s*Finished\b|^\s*Compiling\b",
    re.IGNORECASE)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


@dataclass(frozen=True)
class DigestLine:
    text: str
    reason: str


@dataclass(frozen=True)
class Digest:
    lines: tuple[DigestLine, ...]
    total_lines: int          # raw line count before digesting

    @property
    def kept(self) -> int:
        return sum(1 for line in self.lines if line.reason != ELISION)

    def is_empty(self) -> bool:
        return not self.lines

    def render(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def reasons(self) -> tuple[str, ...]:
        return tuple(line.reason for line in self.lines)


def _clean(line) -> str:
    """ANSI-strip, collapse CR redraws (keep the last segment), rstrip."""
    text = str(line)
    if "\r" in text:
        text = text.split("\r")[-1]
    text = _ANSI.sub("", text)
    return text.rstrip()


def _classify(text) -> str | None:
    if _SUMMARY.search(text):
        return SUMMARY
    if text.strip() and _SALIENT.search(text):
        return ERROR
    return None


def digest_output(lines, *, max_lines=24, max_chars=2000, context=2,
                  head=3, tail=4) -> Digest:
    """Digest raw output `lines` into at most `max_lines`/`max_chars`."""
    cleaned = [_clean(line) for line in lines]
    total = len(cleaned)
    if total == 0:
        return Digest((), 0)

    # 1. Run-length collapse of consecutive identical (non-blank) lines.
    #    runs: [(text, count)] preserving order.
    runs: list[list] = []
    for text in cleaned:
        if runs and runs[-1][0] == text and text != "":
            runs[-1][1] += 1
        else:
            runs.append([text, 1])
    n = len(runs)

    # 2. Classify, 3. add context around salient/summary lines.
    reason: list[str | None] = [_classify(t) for t, _ in runs]
    keep = [r is not None for r in reason]
    for j in range(n):
        if reason[j] in (SUMMARY, ERROR):
            for k in range(max(0, j - context), min(n, j + context + 1)):
                if not keep[k]:
                    keep[k] = True
                    reason[k] = CONTEXT

    # 4. Head + tail so the invocation and the result are always visible.
    for j in range(min(head, n)):
        if not keep[j]:
            keep[j], reason[j] = True, HEAD
    for j in range(max(0, n - tail), n):
        if not keep[j]:
            keep[j], reason[j] = True, TAIL

    kept = [j for j in range(n) if keep[j]]

    # 5. Budget: drop lowest-priority kept lines first (keep newest in a tier).
    def over_budget(indices):
        if len(indices) > max_lines:
            return True
        return sum(len(runs[j][0]) + 1 for j in indices) > max_chars

    if over_budget(kept):
        droppable = sorted(
            kept, key=lambda j: (_PRIORITY.get(reason[j], 9), j))
        # Remove from the tail of the priority-ordered list (lowest priority,
        # oldest first) until within budget — but never drop summary/error.
        while over_budget(kept) and droppable:
            victim = droppable.pop()
            if reason[victim] in (SUMMARY, ERROR):
                break
            kept.remove(victim)

    kept_set = set(kept)

    # 6. Emit in original order, inserting one elision marker per skipped gap.
    out: list[DigestLine] = []
    gap = 0
    for j in range(n):
        if j in kept_set:
            if gap:
                out.append(DigestLine(f"… {gap} line(s) elided …", ELISION))
                gap = 0
            text, count = runs[j]
            if count > 1:
                text = f"{text}    (×{count})"
            out.append(DigestLine(text, reason[j] or CONTEXT))
        else:
            gap += runs[j][1]
    if gap:
        out.append(DigestLine(f"… {gap} line(s) elided …", ELISION))

    return Digest(tuple(out), total)
