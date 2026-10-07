"""Persistent command corpus behind deterministic completion.

The pane journal (``copilot.journal``) is a per-pane, in-memory ring that
dies with the process, so ranking built on it can never learn. This module
is the durable counterpart: one entry per unique command, accumulating how
often it was run, when it was last run, which directories it was run in,
and whether it tended to succeed. ``copilot.ranking`` scores against it.

Everything here is pure and stdlib-only (ADR 0003); the store path and the
clock are injectable so tests never touch the real home directory.

Privacy: commands arriving from the journal are already redacted, but
``~/.bash_history`` seeding is a *new* ingress, so ingest routes every
command through ``redact.redact_lines`` regardless of source and honours
the same exclusion policy as the session store.

Durability: the corpus is a cache, never a system of record. Any read
error yields an empty corpus and any write error is swallowed — a
completion store must never keep the terminal from launching.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch

from agent_terminal.copilot.config import CompletionConfig
from agent_terminal.copilot.redact import redact_lines

SCHEMA_VERSION = 1

# Seeded history predates observation, so it starts at this age. Old
# enough that a single real run outranks it, recent enough to be useful.
SEED_AGE_DAYS = 30.0
# Commands longer than this are pathological (pasted blobs, heredocs).
MAX_COMMAND_LEN = 512
# Per-entry cwd fan-out cap, so one command run everywhere cannot bloat
# the file without bound.
MAX_CWDS_PER_ENTRY = 16


def default_store_path(env=None) -> str:
    """`$XDG_DATA_HOME/agent-terminal/completion/corpus.json`."""
    env = os.environ if env is None else env
    base = (env.get("XDG_DATA_HOME")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(base, "agent-terminal", "completion", "corpus.json")


@dataclass
class Entry:
    """One unique command and everything learned about it."""
    cmd: str
    count: int = 0
    last_used: float = 0.0
    # cwd -> how many times the command ran there.
    cwds: dict = field(default_factory=dict)
    ok: int = 0            # runs that exited 0
    fail: int = 0          # runs that exited non-zero
    # previous-command -> count, the bigram edges phase B ranks on.
    prev: dict = field(default_factory=dict)

    def record(self, *, cwd=None, exit_code=None, when=0.0, prev=None):
        self.count += 1
        self.last_used = max(self.last_used, when)
        if cwd:
            if cwd in self.cwds or len(self.cwds) < MAX_CWDS_PER_ENTRY:
                self.cwds[cwd] = self.cwds.get(cwd, 0) + 1
            else:
                # Full: evict the least-used directory to make room.
                weakest = min(self.cwds, key=self.cwds.get)
                if self.cwds[weakest] <= 1:
                    del self.cwds[weakest]
                    self.cwds[cwd] = 1
        if exit_code is not None:
            if exit_code == 0:
                self.ok += 1
            else:
                self.fail += 1
        if prev:
            self.prev[prev] = self.prev.get(prev, 0) + 1

    def to_dict(self) -> dict:
        return {"cmd": self.cmd, "count": self.count,
                "last_used": self.last_used, "cwds": dict(self.cwds),
                "ok": self.ok, "fail": self.fail, "prev": dict(self.prev)}


def _entry_from_dict(data) -> Entry | None:
    """Tolerant per-entry parse; a malformed entry is skipped, not fatal."""
    if not isinstance(data, dict):
        return None
    cmd = data.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    def _counts(value):
        if not isinstance(value, dict):
            return {}
        return {str(k): int(v) for k, v in value.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}
    try:
        return Entry(
            cmd=cmd,
            count=max(int(data.get("count", 0)), 0),
            last_used=float(data.get("last_used", 0.0)),
            cwds=_counts(data.get("cwds")),
            ok=max(int(data.get("ok", 0)), 0),
            fail=max(int(data.get("fail", 0)), 0),
            prev=_counts(data.get("prev")))
    except (TypeError, ValueError):
        return None


def is_ingestable(cmd) -> bool:
    """Cheap structural filter applied before anything else."""
    if not isinstance(cmd, str):
        return False
    text = cmd.strip()
    if not text or len(text) > MAX_COMMAND_LEN:
        return False
    # A leading space is the shell's own "don't record this" convention.
    if cmd.startswith(" "):
        return False
    return "\n" not in text


def _dir_excluded(cwd, exclude_dirs) -> bool:
    if not cwd:
        return False
    resolved = os.path.normpath(cwd)
    for pattern in exclude_dirs or ():
        prefix = os.path.normpath(os.path.expanduser(pattern))
        if resolved == prefix or resolved.startswith(prefix + os.sep):
            return True
    return False


def _command_excluded(cmd, exclude_commands) -> bool:
    return any(fnmatch(cmd, pattern) for pattern in exclude_commands or ())


class Corpus:
    """In-memory index over the persistent store.

    Loaded once per process and mutated in place; ``save()`` is explicit so
    the caller controls how often the file is rewritten.
    """

    def __init__(self, entries=(), *, config: CompletionConfig | None = None,
                 seeded=False):
        self.config = config or CompletionConfig()
        self.seeded = seeded
        self.entries: dict[str, Entry] = {}
        for entry in entries:
            self.entries[entry.cmd] = entry
        self.dirty = False

    # -- ingest ----------------------------------------------------------

    def add(self, cmd, *, cwd=None, exit_code=None, when=None, prev=None,
            exclude_dirs=(), exclude_commands=()):
        """Record one observed command. Returns the Entry, or None if dropped.

        `cmd` is redacted here regardless of provenance — journal records
        arrive already-redacted (idempotent), bash_history does not.
        """
        if not is_ingestable(cmd):
            return None
        if _dir_excluded(cwd, exclude_dirs):
            return None
        text = cmd.strip()
        if _command_excluded(text, exclude_commands):
            return None
        cleaned, _ = redact_lines([text])
        text = cleaned[0] if cleaned else text
        if not text.strip():
            return None
        entry = self.entries.get(text)
        if entry is None:
            entry = Entry(cmd=text)
            self.entries[text] = entry
        entry.record(cwd=cwd, exit_code=exit_code,
                     when=time.time() if when is None else when, prev=prev)
        self.dirty = True
        return entry

    def add_records(self, records, **kwargs):
        """Ingest a sequence of journal CommandRecords, chaining bigrams."""
        prev = None
        added = 0
        for record in records:
            cmd = getattr(record, "cmd", None)
            entry = self.add(cmd, cwd=getattr(record, "cwd", None),
                             exit_code=getattr(record, "exit_code", None),
                             when=getattr(record, "started_at", None) or None,
                             prev=prev, **kwargs)
            if entry is not None:
                added += 1
                prev = entry.cmd
        return added

    # -- queries ---------------------------------------------------------

    def all(self) -> tuple[Entry, ...]:
        return tuple(self.entries.values())

    def get(self, cmd) -> Entry | None:
        return self.entries.get(cmd)

    def __len__(self) -> int:
        return len(self.entries)

    # -- maintenance -----------------------------------------------------

    def prune(self, *, now=None, max_entries=None):
        """Evict the weakest entries down to the cap. Returns the count."""
        cap = self.config.max_entries if max_entries is None else max_entries
        cap = max(int(cap), 0)
        if cap and len(self.entries) <= cap:
            return 0
        now = time.time() if now is None else now
        half_life = max(float(self.config.half_life_days), 0.5)
        ordered = sorted(
            self.entries.values(),
            key=lambda e: _decayed_weight(e, now, half_life), reverse=True)
        keep = ordered[:cap]
        removed = len(self.entries) - len(keep)
        if removed:
            self.entries = {e.cmd: e for e in keep}
            self.dirty = True
        return removed


def _decayed_weight(entry: Entry, now: float, half_life_days: float) -> float:
    """Frequency damped by recency — the eviction and ranking primitive."""
    age_days = max(now - entry.last_used, 0.0) / 86400.0
    recency = 0.5 ** (age_days / half_life_days)
    return math.log1p(entry.count) * (0.3 + 0.7 * recency)


# -- serialization -------------------------------------------------------

def to_json(corpus: Corpus) -> str:
    return json.dumps({
        "version": SCHEMA_VERSION,
        "seeded": corpus.seeded,
        "entries": [e.to_dict() for e in corpus.all()],
    }, indent=1)


def from_json(text, *, config=None) -> Corpus:
    """Parse a stored corpus; anything unparseable yields an empty one."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return Corpus(config=config)
    if not isinstance(data, dict):
        return Corpus(config=config)
    raw = data.get("entries")
    entries = []
    if isinstance(raw, list):
        for item in raw:
            entry = _entry_from_dict(item)
            if entry is not None:
                entries.append(entry)
    return Corpus(entries, config=config,
                  seeded=bool(data.get("seeded", False)))


def load(path=None, *, config=None, env=None) -> Corpus:
    """Load the corpus, degrading to an empty one on any error."""
    path = path or default_store_path(env)
    try:
        with open(path, encoding="utf-8") as fh:
            return from_json(fh.read(), config=config)
    except (OSError, UnicodeDecodeError):
        return Corpus(config=config)


def save(corpus: Corpus, path=None, *, env=None) -> bool:
    """Atomically write the corpus. Returns False on any failure."""
    path = path or default_store_path(env)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Write-then-rename so a crash mid-write cannot corrupt the store.
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                                   prefix=".corpus-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(to_json(corpus))
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except (OSError, UnicodeEncodeError, TypeError, ValueError):
        return False
    corpus.dirty = False
    return True


# -- cold start from ~/.bash_history --------------------------------------

def default_history_path(env=None) -> str:
    env = os.environ if env is None else env
    return (env.get("HISTFILE")
            or os.path.join(os.path.expanduser("~"), ".bash_history"))


def parse_bash_history(text) -> list[str]:
    """Commands from bash_history text, oldest first, de-duplicated in order.

    Handles HISTTIMEFORMAT's `#<epoch>` comment lines and drops
    continuation lines of multi-line entries (we only rank single-line
    commands).
    """
    out = []
    seen = set()
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        stripped = line.strip()
        if not stripped or not is_ingestable(line):
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        out.append(stripped)
    return out


def seed_from_history(corpus: Corpus, *, path=None, env=None, now=None,
                      exclude_dirs=(), exclude_commands=()) -> int:
    """Cold-start `corpus` from bash_history. Returns commands ingested.

    Seeded entries get count=1 at SEED_AGE_DAYS old, so a single genuine
    run — observed with a real cwd and exit code — outranks them. Runs
    once; `corpus.seeded` guards re-seeding on later launches.
    """
    if corpus.seeded:
        return 0
    now = time.time() if now is None else now
    when = now - SEED_AGE_DAYS * 86400.0
    try:
        with open(path or default_history_path(env),
                  encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        corpus.seeded = True     # don't retry a missing file every launch
        corpus.dirty = True
        return 0
    added = 0
    for cmd in parse_bash_history(text):
        # No cwd and no exit code: history records neither. Ranking treats
        # a never-observed-succeeding entry as weak, which is correct.
        if corpus.add(cmd, when=when, exclude_dirs=exclude_dirs,
                      exclude_commands=exclude_commands) is not None:
            added += 1
    corpus.seeded = True
    corpus.dirty = True
    corpus.prune(now=now)
    return added


def open_corpus(*, config: CompletionConfig | None = None, path=None,
                env=None, now=None, exclude_dirs=(),
                exclude_commands=()) -> Corpus:
    """Load (and, on first run, seed) the corpus. Never raises."""
    config = config or CompletionConfig()
    if not config.corpus:
        return Corpus(config=config, seeded=True)
    corpus = load(path, config=config, env=env)
    if config.seed_bash_history and not corpus.seeded:
        seed_from_history(corpus, env=env, now=now,
                          exclude_dirs=exclude_dirs,
                          exclude_commands=exclude_commands)
        save(corpus, path, env=env)
    return corpus
