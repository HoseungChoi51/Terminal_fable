"""Assistant configuration: dataclass tree + tolerant JSON parsing.

Parsed from the "assistant" section of native.json. Malformed or
missing values always fall back to defaults — configuration problems
must never keep the terminal from launching. Defaults encode the
dogfood policy: local pull features on, push/remote features off.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass(frozen=True)
class JournalConfig:
    max_commands: int = 200
    store_output: bool = True
    output_tail_lines: int = 20


@dataclass(frozen=True)
class TitlesConfig:
    enabled: bool = True
    min_interval_s: int = 30


@dataclass(frozen=True)
class SessionsConfig:
    enabled: bool = True
    retention_days: int = 30
    exclude_dirs: tuple[str, ...] = ()
    exclude_commands: tuple[str, ...] = ()
    store_output: bool = True


@dataclass(frozen=True)
class SuggestionsConfig:
    menu: bool = True
    ghost_text: bool = False
    typo_correction: bool = True
    min_confidence: float = 0.7


@dataclass(frozen=True)
class RecipesConfig:
    enabled: bool = True
    user_recipes_path: str | None = None


@dataclass(frozen=True)
class LlmConfig:
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    api_key_env: str = "OPENAI_API_KEY"
    # Path to auth.json (endpoint chain + keys). None -> search the
    # standard locations (env AGENT_TERMINAL_AUTH_JSON, the config dir).
    auth_path: str | None = None
    allow_remote_context: bool = False
    # Terminal output sent as context, tri-state:
    #   "none"   — send no output (privacy floor)
    #   "digest" — send a distilled, redacted digest (default)
    #   "full"   — send the redacted verbose tail
    # Legacy booleans still parse: false -> "none", true -> "full".
    send_output: str = "digest"
    # How the salient command's output is distilled into context:
    #   "heuristic" — the local pure digester (fast, deterministic; default)
    #   "llm"       — an extra LLM call summarizes it (slower, richer; for
    #                 A/B testing the two against each other)
    # On failure or no eligible endpoint, "llm" falls back to "heuristic".
    digest_mode: str = "heuristic"
    timeout_s: int = 30
    # Appended to the system prompt; lets endpoint quirks be handled by
    # config alone (e.g. "/no_think" to disable Qwen thinking on Ollama).
    system_suffix: str = ""


@dataclass(frozen=True)
class AskConfig:
    """In-place "ask mode" (Ctrl+? overlay)."""
    enabled: bool = True
    # Auto-pilot presses Enter for you; off by default (nothing runs
    # without a keystroke). When on, only commands at or below
    # auto_pilot_max_risk auto-run — riskier ones still wait for Enter.
    auto_pilot: bool = False
    auto_pilot_max_risk: str = "local-change"
    carry_draft: bool = True        # seed the half-typed command as context
    max_turns: int = 8              # conversation turns kept for context


@dataclass(frozen=True)
class ResumeConfig:
    enabled: bool = True
    idle_minutes: int = 30


@dataclass(frozen=True)
class WorkspaceConfig:
    """Grouping sessions into jobs and laying them out as split panes."""
    # Legibility floor: a restored pane is never sub-divided below this.
    min_pane_cols: int = 80
    min_pane_rows: int = 24
    # Soft ceiling on panes per window before spilling into another window.
    max_panes_per_window: int = 6
    # Sessions whose active spans are within this are treated as one job.
    job_gap_minutes: int = 5
    # A workspace rearrange offers Keep/Revert; it auto-reverts after this.
    revert_seconds: int = 15


@dataclass(frozen=True)
class AssistantConfig:
    enabled: bool = True
    shell_integration: bool = True
    journal: JournalConfig = field(default_factory=JournalConfig)
    titles: TitlesConfig = field(default_factory=TitlesConfig)
    sessions: SessionsConfig = field(default_factory=SessionsConfig)
    suggestions: SuggestionsConfig = field(default_factory=SuggestionsConfig)
    recipes: RecipesConfig = field(default_factory=RecipesConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    ask: AskConfig = field(default_factory=AskConfig)
    resume: ResumeConfig = field(default_factory=ResumeConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)


def _coerce(value, default):
    """Coerce one JSON value to the type of `default`, else the default."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return max(int(value), 0)
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return float(value)
    if isinstance(default, str):
        return value if isinstance(value, str) and value else default
    if isinstance(default, tuple):
        if not isinstance(value, list):
            return default
        return tuple(str(item) for item in value if isinstance(item, str))
    if default is None:
        return value if isinstance(value, str) and value else None
    return default


def _parse_section(cls, payload):
    if not isinstance(payload, dict):
        return cls()
    defaults = cls()
    values = {}
    for spec in fields(cls):
        default = getattr(defaults, spec.name)
        if spec.name == "send_output":
            values[spec.name] = _send_output_mode(
                payload.get(spec.name), default)
        elif spec.name == "digest_mode":
            values[spec.name] = _enum(
                payload.get(spec.name), default, ("heuristic", "llm"))
        else:
            values[spec.name] = _coerce(payload.get(spec.name), default)
    return cls(**values)


def _send_output_mode(value, default):
    """Tri-state coercion: bool false/true -> none/full; valid strings pass."""
    if isinstance(value, bool):
        return "full" if value else "none"
    if isinstance(value, str) and value.lower() in ("none", "digest", "full"):
        return value.lower()
    return default


def _enum(value, default, allowed):
    """A case-insensitive string from a fixed set, else the default."""
    if isinstance(value, str) and value.lower() in allowed:
        return value.lower()
    return default


_SECTIONS = {
    "journal": JournalConfig,
    "titles": TitlesConfig,
    "sessions": SessionsConfig,
    "suggestions": SuggestionsConfig,
    "recipes": RecipesConfig,
    "llm": LlmConfig,
    "ask": AskConfig,
    "resume": ResumeConfig,
    "workspace": WorkspaceConfig,
}


def parse_assistant_config(payload) -> AssistantConfig:
    """Parse the "assistant" config section, tolerating any malformed input."""
    if not isinstance(payload, dict):
        return AssistantConfig()
    defaults = AssistantConfig()
    values = {
        "enabled": _coerce(payload.get("enabled"), defaults.enabled),
        "shell_integration": _coerce(payload.get("shell_integration"),
                                     defaults.shell_integration),
    }
    for name, cls in _SECTIONS.items():
        values[name] = _parse_section(cls, payload.get(name))
    return AssistantConfig(**values)
