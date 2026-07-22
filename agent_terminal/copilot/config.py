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
    send_output: bool = False
    timeout_s: int = 30
    # Appended to the system prompt; lets endpoint quirks be handled by
    # config alone (e.g. "/no_think" to disable Qwen thinking on Ollama).
    system_suffix: str = ""


@dataclass(frozen=True)
class ResumeConfig:
    enabled: bool = True
    idle_minutes: int = 30


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
    resume: ResumeConfig = field(default_factory=ResumeConfig)


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
        values[spec.name] = _coerce(payload.get(spec.name), default)
    return cls(**values)


_SECTIONS = {
    "journal": JournalConfig,
    "titles": TitlesConfig,
    "sessions": SessionsConfig,
    "suggestions": SuggestionsConfig,
    "recipes": RecipesConfig,
    "llm": LlmConfig,
    "resume": ResumeConfig,
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
