from pathlib import Path

from skye.auth import TelegramAuth
from skye.config import Settings
from skye.db import Database
from skye.projects import ProjectService


def settings() -> Settings:
    return Settings(
        telegram_bot_token="123:token",
        openai_api_key="sk-test",
        skye_owner_ids="1",
        skye_web_origin="https://chat.skye-bot.com",
        telegram_login_client_id="99",
        telegram_login_client_secret="login-secret",
        _env_file=None,
    )  # type: ignore[call-arg]


async def test_oidc_state_is_signed_and_expires(tmp_path: Path) -> None:
    database = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await database.open()
    try:
        auth = TelegramAuth(settings(), database, ProjectService(database, tmp_path / "web"))
        url, packed = auth.login_url("https://chat.skye-bot.com")
        assert "oauth.telegram.org/auth" in url
        state, _verifier = auth.parse_oidc(packed)
        assert state in url
        tampered = packed[:-2] + ("ab" if packed[-2:] != "ab" else "cd")
        try:
            auth.parse_oidc(tampered)
            raise AssertionError("tampered cookie should fail")
        except Exception as error:
            assert "expired" in str(error).lower() or "login" in str(error).lower()
    finally:
        await database.close()
