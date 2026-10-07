# Command completion (deterministic, no LLM)

Terminal Fable completes commands from what **you** actually run, using a
local, deterministic ranking model — no network, no model call, nothing
leaves the machine. Two surfaces consume it:

- **Inline ghost text** — a dim suffix at the cursor; `Right`/`Ctrl+Right`
  accepts it, `Esc` dismisses it.
- **The completion menu** — `Ctrl+Shift+Space`, ranked and filterable.

`Tab` remains bash's. This feature deliberately does not intercept it.

## What it learns from

Every finished command is recorded in a corpus at
`$XDG_DATA_HOME/agent-terminal/completion/corpus.json`, keeping per command:
how often it ran, when it last ran, **which directories** it ran in, whether
it succeeded, and which command preceded it.

On first run the corpus is seeded once from `~/.bash_history` so completion is
useful immediately. Seeded entries are deliberately handicapped — aged 30 days,
with no directory or exit information — so a single command you actually run
outranks anything imported.

Secrets are redacted on the way in, and the `sessions.exclude_dirs` /
`exclude_commands` policy applies, so a directory you exclude from session
history is also excluded from completion.

## How it ranks

```
score = log1p(count)                     # frequency, damped
      x (0.3 + 0.7 x 0.5^(age/half_life))  # recency decay
      x cwd/project boost                # 1.6 exact dir, 1.25 same project
      x exit factor                      # succeeded > unknown > only failed
      x match quality                    # prefix beats fuzzy
```

The damping matters: without `log1p`, one command run hundreds of times would
bury everything else forever. The directory boost is what stops `npm test`
from ranking inside a Python repository.

### When ghost text appears

Ghost text is shown only when the winner clearly dominates:

```
confidence = top_score / (top_score + best_rival_score)
```

where the rival must propose a *different* completion. One unambiguous
candidate scores `1.0`; two equally-good candidates score about `0.5` and are
withheld. `suggestions.min_confidence` (default `0.7`) is therefore a real
eagerness dial — lower it to see more suggestions, raise it to see only
near-certain ones.

Destructive, privileged, and unclassifiable commands are never ghosted,
whatever their score.

## Configure it

In `~/.config/agent-terminal/native.json`:

```json
{
  "assistant": {
    "suggestions": { "ghost_text": true, "min_confidence": 0.7 },
    "completion": {
      "ranking": "frecency",
      "corpus": true,
      "max_entries": 5000,
      "half_life_days": 14,
      "seed_bash_history": true,
      "shell_completion": true
    }
  }
}
```

- `ranking` — `frecency` (default), `chain` (also weighs what usually follows
  the previous command), or `token` (also completes per-argument). Switchable
  so the models can be compared on real usage.
- `corpus` — set `false` to disable persistence entirely; completion falls back
  to the current pane's in-memory history.
- `half_life_days` — how fast a command's weight decays. Lower is more
  fashion-following, higher is more habitual.
- `max_entries` — cap; the weakest entries are evicted first.

## Limits

- **Ranking modes beyond `frecency` are staged.** `chain` and `token` are
  accepted by the config today; their ranking layers land in later phases.
- **No shell-native completion yet.** Flags and subcommands for arbitrary
  tools (`docker`, `systemctl`, …) still come only from your own history.
  A `compgen` bridge to a separate non-interactive bash is planned; git
  branches, changed files, and ssh hosts are already offered in the menu.
- **The corpus is a cache, not a record.** If it is corrupt or unreadable it is
  silently treated as empty — completion degrades, the terminal still opens.
- **Bounded fan-out.** A command run in very many directories keeps only its
  most-used ones, so the store cannot grow without bound.
