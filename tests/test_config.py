from pathlib import Path

import pytest
from pydantic import ValidationError

from skye.config import SANDBOX_DOMAINS, Settings


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


def test_sandbox_domains_default_to_the_code_owned_allowlist() -> None:
    assert settings().skye_sandbox_allowed_domains == SANDBOX_DOMAINS


def test_group_context_defaults_to_twenty_new_messages() -> None:
    loaded = settings()

    assert loaded.skye_group_context_messages == 20
    assert loaded.skye_group_context_message_chars == 1_500
    assert loaded.skye_group_context_total_chars == 16_000


def test_token_safety_defaults_leave_headroom() -> None:
    loaded = settings()

    assert loaded.skye_compaction_threshold_tokens == 40_000
    assert loaded.skye_max_context_tokens == 50_000
    assert loaded.skye_max_output_tokens == 4_000
    assert loaded.skye_tpm_budget == 160_000


def test_context_limit_must_exceed_compaction_threshold() -> None:
    with pytest.raises(ValidationError):
        settings(
            skye_compaction_threshold_tokens=50_000,
            skye_max_context_tokens=50_000,
        )


def test_tpm_budget_must_cover_one_maximum_request() -> None:
    with pytest.raises(ValidationError):
        settings(skye_tpm_budget=53_999)


def test_sandbox_domains_are_parsed_from_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123:token\nOPENAI_API_KEY=sk-test\nSKYE_OWNER_IDS=1\n"
        "SKYE_SANDBOX_ALLOWED_DOMAINS=pypi.org, GitHub.com, files.pythonhosted.org\n",
        encoding="utf-8",
    )

    loaded = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert loaded.skye_sandbox_allowed_domains == (
        "pypi.org",
        "github.com",
        "files.pythonhosted.org",
    )
