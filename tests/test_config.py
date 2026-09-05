from pathlib import Path

import pytest
from pydantic import ValidationError

from skye.config import SANDBOX_DOMAINS, Settings


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123:token",
        "openai_api_key": "sk-test",
        "openrouter_api_key": None,
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


def test_any_non_empty_model_is_accepted() -> None:
    assert settings(skye_default_model="unknown").skye_default_model == "unknown"
    assert (
        settings(skye_default_model="anthropic/claude-sonnet-4.6").skye_default_model
        == "anthropic/claude-sonnet-4.6"
    )


def test_openrouter_key_selects_openrouter_and_accepts_namespaced_models() -> None:
    loaded = settings(
        openai_api_key=None,
        openrouter_api_key="sk-or-test",
        skye_default_model="anthropic/claude-sonnet-4.6",
        skye_transcription_model="openai/whisper-large-v3",
        skye_speech_model="openai/gpt-4o-mini-tts",
        skye_image_model="openai/gpt-5-image",
    )

    assert loaded.provider == "openrouter"
    assert loaded.provider_api_key == "sk-or-test"
    assert loaded.provider_base_url == "https://openrouter.ai/api/v1"


def test_openrouter_key_takes_precedence_when_both_keys_are_present() -> None:
    loaded = settings(openrouter_api_key="sk-or-test")

    assert loaded.provider == "openrouter"


def test_unified_provider_key_wins_over_legacy_keys() -> None:
    loaded = settings(
        openai_api_key="sk-legacy",
        openrouter_api_key="sk-or-legacy",
        skye_provider_api_key="sk-unified",
        skye_provider_base_url="https://llm.example.com/v1",
    )

    assert loaded.provider_api_key == "sk-unified"
    assert loaded.provider_base_url == "https://llm.example.com/v1"
    assert loaded.provider == "openai"


def test_unified_openrouter_base_url_routes_to_openrouter_shim() -> None:
    loaded = settings(
        openai_api_key=None,
        openrouter_api_key=None,
        skye_provider_api_key="sk-unified",
        skye_provider_base_url="https://openrouter.ai/api/v1",
    )

    assert loaded.provider == "openrouter"
    assert loaded.provider_base_url == "https://openrouter.ai/api/v1"


def test_sandbox_and_exa_defaults() -> None:
    loaded = settings()

    assert loaded.skye_exa_api_key is None
    assert loaded.skye_sandbox_enabled is False
    assert loaded.skye_sandbox_image == "python:3.14-slim"
    assert loaded.skye_sandbox_timeout_seconds == 120


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
    assert loaded.skye_tpm_budget == 1_800_000
    assert loaded.skye_max_concurrent_runs == 8
    assert loaded.skye_youtube_transcript_max_chars == 48_000
    assert loaded.skye_youtube_proxy_url is None


def test_context_limit_must_exceed_compaction_threshold() -> None:
    with pytest.raises(ValidationError):
        settings(
            skye_compaction_threshold_tokens=50_000,
            skye_max_context_tokens=50_000,
        )


def test_tpm_budget_must_cover_one_maximum_request() -> None:
    with pytest.raises(ValidationError):
        settings(skye_tpm_budget=53_999)


def test_concurrent_run_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        settings(skye_max_concurrent_runs=0)


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
