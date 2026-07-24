"""Secret redaction applied before anything is stored or sent anywhere.

Deliberately over-broad: a false positive costs a little context, a
false negative leaks a credential. Applied at the storage boundary
(journal records) and again, unconditionally, at the remote choke
point (copilot.llm, phase P5).
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_SECRET_WORD = (r"(?:pass(?:word|wd|phrase)?|secret|token|api[_-]?key"
                r"|auth|bearer|credential(?:s)?|private[_-]?key|cookie"
                r"|access[_-]?key|client[_-]?secret)")

# (pattern, replacement) applied in order to every line.
_RULES: tuple[tuple[re.Pattern, object], ...] = (
    # key=value / key: value / --key value assignments with a secret-ish key
    (re.compile(r"(?i)((?:--?|\b)[\w.-]*" + _SECRET_WORD +
                r"[\w.-]*\s*(?:[=:]|\s))\s*(\"[^\"]*\"|'[^']*'|\S+)"),
     lambda m: m.group(1) + REDACTED),
    # URL userinfo passwords: scheme://user:pass@host
    (re.compile(r"(\w+://[^/\s:@]+):([^@/\s]+)@"),
     lambda m: m.group(1) + ":" + REDACTED + "@"),
    # Authorization / Cookie header values (curl -H '...' etc.)
    (re.compile(r"(?i)\b((?:authorization|proxy-authorization|cookie"
                r"|set-cookie|x-api-key)\s*:\s*)([^\"']\S.*?|\S+)(?=[\"']|$)"),
     lambda m: m.group(1) + REDACTED),
    # Known token shapes
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}"
                r"\.[A-Za-z0-9_-]{5,}\b"), REDACTED),
    # Long hex/base64 blob adjacent to a secret-ish word
    (re.compile(r"(?i)(" + _SECRET_WORD + r"\b[^\n]{0,20}?)"
                r"([A-Za-z0-9+/_-]{40,}={0,2}|[0-9a-fA-F]{40,})"),
     lambda m: m.group(1) + REDACTED),
)

_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----")
_PEM_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----")


def redact_line(text: str) -> tuple[str, bool]:
    """Redact secrets in one line; returns (text, anything_was_redacted)."""
    hit = False
    for pattern, replacement in _RULES:
        text, count = pattern.subn(replacement, text)
        hit = hit or count > 0
    return text, hit


def redact_lines(lines) -> tuple[tuple[str, ...], bool]:
    """Redact a block of lines, dropping PEM private-key blocks whole."""
    out = []
    hit = False
    in_pem = False
    for line in lines:
        if in_pem:
            hit = True
            if _PEM_END.search(line):
                in_pem = False
            continue
        if _PEM_BEGIN.search(line):
            in_pem = True
            hit = True
            out.append(REDACTED)
            continue
        text, line_hit = redact_line(line)
        hit = hit or line_hit
        out.append(text)
    return tuple(out), hit
