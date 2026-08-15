from pathlib import Path

import pytest
from pydantic import ValidationError

from skye.config import Settings


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123:token",
        "openai_api_key": "sk-test",
        "skye_owner_ids": "1, 2",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_owner_ids_are_parsed() -> None:
    assert settings().skye_owner_ids == frozenset({1, 2})


@pytest.mark.parametrize("owner_ids", ["42", "41,42"])
def test_owner_ids_are_loaded_from_dotenv(tmp_path: Path, owner_ids: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"TELEGRAM_BOT_TOKEN=123:token\nOPENAI_API_KEY=sk-test\nSKYE_OWNER_IDS={owner_ids}\n",
        encoding="utf-8",
    )

    loaded = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert 42 in loaded.skye_owner_ids


def test_owner_is_required() -> None:
    with pytest.raises(ValidationError):
        settings(skye_owner_ids="")


def test_empty_composio_key_is_optional() -> None:
    assert settings(composio_api_key="").composio_api_key is None
    assert settings(composio_api_key="ck_test").composio_api_key == "ck_test"


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ('"ck_live_abc"', "ck_live_abc"),
        ("'ck_live_abc'", "ck_live_abc"),
        ("Bearer ck_live_abc", "ck_live_abc"),
        ("x-api-key: ck_live_abc", "ck_live_abc"),
        ("  ck_live_abc  ", "ck_live_abc"),
    ],
)
def test_composio_key_strips_wrapping(raw: str, cleaned: str) -> None:
    assert settings(composio_api_key=raw).composio_api_key == cleaned


def test_model_catalog_rejects_unknown_model() -> None:
    with pytest.raises(ValidationError):
        settings(skye_default_model="unknown")
