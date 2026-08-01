from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field
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


OwnerIds = Annotated[frozenset[int], NoDecode, BeforeValidator(_ids)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = Field(min_length=1)
    openai_api_key: str = Field(min_length=1)
    skye_owner_ids: OwnerIds = Field(min_length=1)
    skye_database_path: Path = Path("data/skye.db")
    skye_base_prompt_path: Path = Path("BASE_PROMPT.md")
    skye_default_model: ModelId = "gpt-5.6-luna"
    skye_default_reasoning: Reasoning = "medium"
    skye_max_turns: int = Field(default=20, ge=2, le=100)
    skye_run_timeout_seconds: int = Field(default=300, ge=10, le=1800)
    skye_max_attachment_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    skye_transcription_model: str = "gpt-transcribe"
    skye_group_context_messages: int = Field(default=200, ge=20, le=500)
    skye_group_context_images: int = Field(default=10, ge=0, le=50)
    skye_tracing: bool = False
