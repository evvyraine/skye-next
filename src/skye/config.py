from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ModelId = Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
Reasoning = Literal["none", "low", "medium", "high", "xhigh", "max"]

MODELS: dict[ModelId, str] = {
    "gpt-5.6-luna": "Luna",
    "gpt-5.6-terra": "Terra",
    "gpt-5.6-sol": "Sol",
}


def _ids(value: object) -> frozenset[int]:
    if isinstance(value, int):
        return frozenset({value})
    if isinstance(value, str):
        return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
    if isinstance(value, (set, frozenset, list, tuple)):
        return frozenset(int(item) for item in value)
    return frozenset()


SANDBOX_DOMAINS: tuple[str, ...] = (
    "api.github.com",
    "codeload.github.com",
    "crates.io",
    "files.pythonhosted.org",
    "github.com",
    "gitlab.com",
    "index.crates.io",
    "npmjs.com",
    "objects.githubusercontent.com",
    "pkg.go.dev",
    "proxy.golang.org",
    "pypi.org",
    "pypi.python.org",
    "raw.githubusercontent.com",
    "registry.npmjs.org",
    "rubygems.org",
    "static.crates.io",
    "sum.golang.org",
)


def _domains(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        items = tuple(
            item.strip().lower().lstrip(".")
            for item in value.replace(";", ",").split(",")
            if item.strip()
        )
        return items or SANDBOX_DOMAINS
    if isinstance(value, (list, tuple, set, frozenset)):
        items = tuple(str(item).strip().lower() for item in value if str(item).strip())
        return items or SANDBOX_DOMAINS
    return SANDBOX_DOMAINS


OwnerIds = Annotated[frozenset[int], NoDecode, BeforeValidator(_ids)]
SandboxDomains = Annotated[tuple[str, ...], NoDecode, BeforeValidator(_domains)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = Field(min_length=1)
    openai_api_key: str = Field(min_length=1)
    composio_api_key: str | None = None
    skye_owner_ids: OwnerIds = Field(min_length=1)
    skye_database_path: Path = Path("data/skye.db")
    skye_base_prompt_path: Path = Path("BASE_PROMPT.md")
    skye_default_model: ModelId = "gpt-5.6-luna"
    skye_default_reasoning: Reasoning = "medium"
    skye_max_turns: int = Field(default=20, ge=2, le=100)
    skye_run_timeout_seconds: int = Field(default=300, ge=10, le=1800)
    skye_max_attachment_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    skye_transcription_model: str = "gpt-transcribe"
    skye_group_context_messages: int = Field(default=20, ge=1, le=100)
    skye_sandbox_allowed_domains: SandboxDomains = Field(default=SANDBOX_DOMAINS, min_length=1)
    skye_tracing: bool = False

    @field_validator("composio_api_key", mode="before")
    @classmethod
    def _empty_key(cls, value: object) -> object:
        return _clean_secret(value)


def _clean_secret(value: object) -> object:
    if not isinstance(value, str):
        return value
    text = value.strip().strip("\ufeff")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    lowered = text.lower()
    if lowered.startswith("bearer "):
        text = text[7:].strip()
    if lowered.startswith("x-api-key:"):
        text = text.split(":", 1)[1].strip()
    return text or None
