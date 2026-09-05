from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ModelId = str
Reasoning = Literal["none", "low", "medium", "high", "xhigh", "max"]
Provider = Literal["openai", "openrouter"]

HOSTED_MODEL: ModelId = "gpt-5.6-luna"
MODELS: dict[ModelId, str] = {
    "gpt-5.6-luna": "Luna",
    "gpt-5.6-terra": "Terra",
    "gpt-5.6-sol": "Sol",
}


def clamp_model(_model: str | None) -> ModelId:
    return HOSTED_MODEL


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
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None
    skye_provider_api_key: str | None = None
    skye_provider_base_url: str | None = None
    skye_exa_api_key: str | None = None
    skye_sandbox_enabled: bool = False
    skye_sandbox_image: str = "python:3.14-slim"
    skye_sandbox_timeout_seconds: int = Field(default=120, ge=5, le=600)
    composio_api_key: str | None = None
    skye_owner_ids: OwnerIds = Field(min_length=1)
    skye_database_path: Path = Path("data/skye.db")
    skye_base_prompt_path: Path = Path("BASE_PROMPT.md")
    skye_default_model: ModelId = HOSTED_MODEL
    skye_default_reasoning: Reasoning = "medium"
    skye_max_turns: int = Field(default=20, ge=2, le=100)
    skye_run_timeout_seconds: int = Field(default=300, ge=10, le=1800)
    skye_compaction_threshold_tokens: int = Field(default=40_000, ge=1)
    skye_max_context_tokens: int = Field(default=50_000, ge=1)
    skye_max_output_tokens: int = Field(default=4_000, ge=1)
    skye_tpm_budget: int = Field(default=1_800_000, ge=1)
    skye_max_concurrent_runs: int = Field(default=8, ge=1, le=64)
    skye_max_attachment_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    skye_transcription_model: str = "gpt-transcribe"
    skye_speech_model: str = "gpt-4o-mini-tts"
    skye_image_model: str = "gpt-image-2"
    skye_youtube_transcript_max_chars: int = Field(default=48_000, ge=1_000, le=200_000)
    skye_youtube_proxy_url: str | None = None
    skye_media_group_settle_seconds: float = Field(default=0.75, ge=0.1, le=5.0)
    skye_group_context_messages: int = Field(default=20, ge=1, le=100)
    skye_group_context_message_chars: int = Field(default=1_500, ge=100, le=4_096)
    skye_group_context_total_chars: int = Field(default=16_000, ge=500, le=50_000)
    skye_sandbox_allowed_domains: SandboxDomains = Field(default=SANDBOX_DOMAINS, min_length=1)
    skye_proxy_url: str | None = None
    skye_tracing: bool = False
    skye_web_origin: str | None = None
    skye_web_host: str = "127.0.0.1"
    skye_web_port: int = Field(default=8080, ge=1, le=65535)
    skye_web_files_path: Path = Path("data/web")
    telegram_login_client_id: str | None = None
    telegram_login_client_secret: str | None = None

    @field_validator("skye_group_context_total_chars")
    @classmethod
    def _group_total_covers_one_message(cls, value: int, info: ValidationInfo) -> int:
        message_chars = info.data.get("skye_group_context_message_chars", 1_500)
        if value <= message_chars:
            raise ValueError("must be greater than skye_group_context_message_chars")
        return value

    @field_validator("skye_max_context_tokens")
    @classmethod
    def _context_above_compaction(cls, value: int, info: ValidationInfo) -> int:
        threshold = info.data.get("skye_compaction_threshold_tokens", 40_000)
        if value <= threshold:
            raise ValueError("must be greater than skye_compaction_threshold_tokens")
        return value

    @field_validator("skye_tpm_budget")
    @classmethod
    def _tpm_covers_one_request(cls, value: int, info: ValidationInfo) -> int:
        context = info.data.get("skye_max_context_tokens", 50_000)
        output = info.data.get("skye_max_output_tokens", 4_000)
        if value < context + output:
            raise ValueError("must cover one maximum-size request")
        return value

    @field_validator("composio_api_key", mode="before")
    @classmethod
    def _empty_key(cls, value: object) -> object:
        return _clean_secret(value)

    @field_validator(
        "openai_api_key",
        "openrouter_api_key",
        "skye_provider_api_key",
        "skye_exa_api_key",
        "skye_youtube_proxy_url",
        "skye_proxy_url",
        "skye_provider_base_url",
        mode="before",
    )
    @classmethod
    def _empty_provider_key(cls, value: object) -> object:
        return _clean_secret(value)

    @model_validator(mode="after")
    def _provider_key_is_required(self) -> Settings:
        has_key = bool(
            self.skye_provider_api_key or self.openai_api_key or self.openrouter_api_key
        )
        if not has_key:
            msg = "SKYE_PROVIDER_API_KEY, OPENAI_API_KEY or OPENROUTER_API_KEY is required"
            raise ValueError(msg)
        if not self.skye_default_model.strip():
            raise ValueError("SKYE_DEFAULT_MODEL must not be empty")
        return self

    @property
    def provider(self) -> Provider:
        # Legacy routing flag for the Responses-era branches in runtime/app.
        # Removed step by step as hosted tools move to local function tools.
        base_url = (self.skye_provider_base_url or "").lower()
        if base_url:
            return "openrouter" if "openrouter" in base_url else "openai"
        return "openrouter" if self.openrouter_api_key else "openai"

    @property
    def provider_api_key(self) -> str:
        key = self.skye_provider_api_key or self.openrouter_api_key or self.openai_api_key
        if key is None:  # guarded by validation; keeps this property precisely typed
            raise RuntimeError("No model provider API key is configured")
        return key

    @property
    def provider_base_url(self) -> str | None:
        if self.skye_provider_base_url:
            return self.skye_provider_base_url
        return "https://openrouter.ai/api/v1" if self.provider == "openrouter" else None

    @field_validator(
        "skye_web_origin",
        "telegram_login_client_id",
        "telegram_login_client_secret",
        mode="before",
    )
    @classmethod
    def _empty_web(cls, value: object) -> object:
        return _clean_secret(value)

    @property
    def web_enabled(self) -> bool:
        return bool(
            self.skye_web_origin
            and self.telegram_login_client_id
            and self.telegram_login_client_secret
        )


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
