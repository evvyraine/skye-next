from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from skye.access import AccessService
from skye.auth import COOKIE_NAME, TelegramAuth
from skye.billing import PLANS, SUBSCRIPTION_PERIOD, BillingService
from skye.config import Settings
from skye.db import Database
from skye.models import Scope
from skye.projects import ProjectService
from skye.runtime import AgentRuntime, RunEvent, RunOutput
from skye.web import WebApp


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123:token",
        "openai_api_key": "sk-test",
        "skye_owner_ids": "1",
        "skye_web_origin": "https://chat.skye-bot.com",
        "telegram_login_client_id": "99",
        "telegram_login_client_secret": "login-secret",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


class FakeRuntime(AgentRuntime):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._stopped: list[str] = []

    async def run(self, *args: Any, **kwargs: Any) -> RunOutput:  # type: ignore[override]
        self.calls.append({"args": args, "kwargs": kwargs})
        on_reply = kwargs.get("on_reply")
        on_event = kwargs.get("on_event")
        if on_event is not None:
            await on_event(
                RunEvent(
                    kind="tool",
                    tool_id="t1",
                    tool_name="web_search",
                    tool_label="Searched the web",
                    tool_status="done",
                )
            )
        if on_reply is not None:
            await on_reply("Hello from Skye.")
            return RunOutput("Hello from Skye.", (), sent=1)
        return RunOutput("Hello from Skye.", ())

    def stop_key(self, key: str) -> bool:
        self._stopped.append(key)
        return True


async def app_client(
    database: Database, tmp_path: Path, *, owner_ids: frozenset[int] = frozenset({1})
) -> tuple[TestClient, ProjectService, FakeRuntime]:
    config = settings()
    projects = ProjectService(database, tmp_path / "web-files")
    auth = TelegramAuth(config, database, projects)
    runtime = FakeRuntime()
    web_app = WebApp(
        config,
        database,
        AccessService(database, owner_ids),
        runtime,  # type: ignore[arg-type]
        projects,
        auth,
        cast(Any, AsyncMock()),
    )
    client = TestClient(TestServer(web_app.app))
    await client.start_server()
    return client, projects, runtime


async def signed_in(client: TestClient, projects: ProjectService, user_id: int = 1) -> None:
    session = await projects.create_session(user_id, "Owner", "owner")
    client.session.cookie_jar.update_cookies({COOKIE_NAME: session.id})


@pytest.mark.asyncio
async def test_web_allows_free_users(database: Database, tmp_path: Path) -> None:
    client, projects, _runtime = await app_client(database, tmp_path)
    try:
        await signed_in(client, projects, user_id=99)
        response = await client.get("/api/projects")
        assert response.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_ban_denies_users(database: Database, tmp_path: Path) -> None:
    await database.set_access(Scope("user", 99), "ban", created_by=1)
    client, projects, _runtime = await app_client(database, tmp_path)
    try:
        await signed_in(client, projects, user_id=99)
        response = await client.get("/api/projects")
        assert response.status == 403
        assert "private" in (await response.text()).lower()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_stars_plan_grants_private_access(database: Database, tmp_path: Path) -> None:
    import time

    from aiogram.types import SuccessfulPayment

    billing = BillingService(database, "123:token")
    now = int(time.time())
    plan = PLANS["plus"]
    await billing.apply_payment(
        99,
        SuccessfulPayment(
            currency="XTR",
            total_amount=plan.stars,
            invoice_payload=billing.payload(plan, 99),
            telegram_payment_charge_id="web-plus",
            provider_payment_charge_id="",
            subscription_expiration_date=now + SUBSCRIPTION_PERIOD,
            is_recurring=True,
            is_first_recurring=True,
        ),
    )
    client, projects, _runtime = await app_client(database, tmp_path)
    try:
        await signed_in(client, projects, user_id=99)
        response = await client.get("/api/projects")
        assert response.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_owner_can_create_pin_and_cannot_delete_skye(
    database: Database, tmp_path: Path
) -> None:
    client, projects, _runtime = await app_client(database, tmp_path)
    try:
        await signed_in(client, projects)
        listed = await client.get("/api/projects")
        payload = await listed.json()
        assert payload["projects"][0]["name"] == "Skye"
        skye_id = payload["projects"][0]["id"]
        created = await client.post(
            "/api/projects",
            json={"name": "Frontend", "icon": "code-bracket", "color": "blue"},
        )
        assert created.status == 201
        project = (await created.json())["project"]
        pinned = await client.post(f"/api/projects/{project['id']}/pin")
        assert (await pinned.json())["project"]["pinned"] is True
        denied = await client.delete(f"/api/projects/{skye_id}")
        assert denied.status == 403
        deleted = await client.delete(f"/api/projects/{project['id']}")
        assert deleted.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stream_shows_tool_markers_and_keeps_telegram_conversation_separate(
    database: Database, tmp_path: Path
) -> None:
    client, projects, runtime = await app_client(database, tmp_path)
    try:
        await signed_in(client, projects)
        listed = await client.get("/api/projects")
        skye_id = (await listed.json())["projects"][0]["id"]
        response = await client.post(f"/api/projects/{skye_id}/messages", json={"text": "hello"})
        assert response.status == 200
        body = await response.text()
        assert "Searched the web" in body
        assert "Hello from Skye." in body
        assert runtime.calls[0]["kwargs"]["run_key"] == f"web:{skye_id}"
        assert runtime.calls[0]["kwargs"]["conversation_id"]
        assert await database.conversation_id(1, 0) is None
        messages = await client.get(f"/api/projects/{skye_id}/messages")
        roles = [item["role"] for item in (await messages.json())["messages"]]
        assert "tool" in roles
        assert "assistant" in roles
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_project_isolation_on_messages(database: Database, tmp_path: Path) -> None:
    client, projects, _runtime = await app_client(database, tmp_path)
    try:
        await signed_in(client, projects, user_id=1)
        created = await client.post("/api/projects", json={"name": "Mine", "icon": "star"})
        project_id = (await created.json())["project"]["id"]
        other = await projects.create_session(2, "Other", None)
        client.session.cookie_jar.update_cookies({COOKIE_NAME: other.id})
        await database.set_access(Scope("user", 2), "allow", created_by=1)
        response = await client.get(f"/api/projects/{project_id}/messages")
        assert response.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_blocks_when_the_daily_allowance_is_used(
    database: Database, tmp_path: Path
) -> None:
    import time

    from aiogram.types import SuccessfulPayment

    from skye.quota import PLUS_DAILY

    billing = BillingService(database, "123:token")
    now = int(time.time())
    plan = PLANS["plus"]
    await billing.apply_payment(
        99,
        SuccessfulPayment(
            currency="XTR",
            total_amount=plan.stars,
            invoice_payload=billing.payload(plan, 99),
            telegram_payment_charge_id="web-quota",
            provider_payment_charge_id="",
            subscription_expiration_date=now + SUBSCRIPTION_PERIOD,
            is_recurring=True,
            is_first_recurring=True,
        ),
    )
    await database.add_usage(99, PLUS_DAILY)
    client, projects, runtime = await app_client(database, tmp_path)
    try:
        await signed_in(client, projects, user_id=99)
        await projects.ensure_skye(99)
        listed = await client.get("/api/projects")
        skye_id = (await listed.json())["projects"][0]["id"]
        response = await client.post(f"/api/projects/{skye_id}/messages", json={"text": "hello"})
        assert response.status == 429
        body = await response.text()
        assert "daily message allowance" in body
        assert "token" not in body.lower()
        assert runtime.calls == []
    finally:
        await client.close()


class TwoBubbleRuntime(AgentRuntime):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, *args: Any, **kwargs: Any) -> RunOutput:  # type: ignore[override]
        self.calls.append({"args": args, "kwargs": kwargs})
        on_reply = kwargs.get("on_reply")
        if on_reply is not None:
            await on_reply("On it.")
            await on_reply("Here it is.", 999)
        return RunOutput("HIDDEN leftover", (), sent=2)

    def stop_key(self, key: str) -> bool:
        return True


class FallbackRuntime(AgentRuntime):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, *args: Any, **kwargs: Any) -> RunOutput:  # type: ignore[override]
        self.calls.append({"args": args, "kwargs": kwargs})
        return RunOutput("Fallback from leftover.", (), sent=0)

    def stop_key(self, key: str) -> bool:
        return True


class ImageRuntime(AgentRuntime):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, *args: Any, **kwargs: Any) -> RunOutput:  # type: ignore[override]
        self.calls.append({"args": args, "kwargs": kwargs})
        on_event = kwargs.get("on_event")
        if on_event is not None:
            await on_event(RunEvent(kind="image", image=b"png-bytes"))
        return RunOutput("inner monologue about the picture", (b"png-bytes",), sent=0)

    def stop_key(self, key: str) -> bool:
        return True


class VoiceRuntime(AgentRuntime):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, *args: Any, **kwargs: Any) -> RunOutput:  # type: ignore[override]
        self.calls.append({"args": args, "kwargs": kwargs})
        on_voice = kwargs.get("on_voice")
        if on_voice is not None:
            await on_voice(b"opus-audio")
        return RunOutput("inner monologue about the voice", (), sent=1)

    def stop_key(self, key: str) -> bool:
        return True


async def _client_with_runtime(
    database: Database, tmp_path: Path, runtime: AgentRuntime
) -> tuple[TestClient, ProjectService]:
    config = settings()
    projects = ProjectService(database, tmp_path / "web-files")
    auth = TelegramAuth(config, database, projects)
    web_app = WebApp(
        config,
        database,
        AccessService(database, frozenset({1})),
        runtime,  # type: ignore[arg-type]
        projects,
        auth,
        cast(Any, AsyncMock()),
    )
    http = TestClient(TestServer(web_app.app))
    await http.start_server()
    return http, projects


@pytest.mark.asyncio
async def test_web_two_send_message_calls_do_not_post_final_output(
    database: Database, tmp_path: Path
) -> None:
    client, projects = await _client_with_runtime(database, tmp_path, TwoBubbleRuntime())
    try:
        await signed_in(client, projects)
        listed = await client.get("/api/projects")
        skye_id = (await listed.json())["projects"][0]["id"]
        response = await client.post(f"/api/projects/{skye_id}/messages", json={"text": "hello"})
        body = await response.text()
        assert "On it." in body
        assert "Here it is." in body
        assert body.index("On it.") < body.index("Here it is.")
        assert "HIDDEN leftover" not in body
        messages = await client.get(f"/api/projects/{skye_id}/messages")
        assistant = [
            item for item in (await messages.json())["messages"] if item["role"] == "assistant"
        ]
        assert {item["text"] for item in assistant} == {"On it.", "Here it is."}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_falls_back_when_send_message_is_never_called(
    database: Database, tmp_path: Path
) -> None:
    client, projects = await _client_with_runtime(database, tmp_path, FallbackRuntime())
    try:
        await signed_in(client, projects)
        listed = await client.get("/api/projects")
        skye_id = (await listed.json())["projects"][0]["id"]
        response = await client.post(f"/api/projects/{skye_id}/messages", json={"text": "hello"})
        body = await response.text()
        assert "Fallback from leftover." in body
        messages = await client.get(f"/api/projects/{skye_id}/messages")
        assistant = [
            item for item in (await messages.json())["messages"] if item["role"] == "assistant"
        ]
        assert [item["text"] for item in assistant] == ["Fallback from leftover."]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_image_only_turn_still_stores_the_image(
    database: Database, tmp_path: Path
) -> None:
    client, projects = await _client_with_runtime(database, tmp_path, ImageRuntime())
    try:
        await signed_in(client, projects)
        listed = await client.get("/api/projects")
        skye_id = (await listed.json())["projects"][0]["id"]
        response = await client.post(
            f"/api/projects/{skye_id}/messages", json={"text": "draw a cat"}
        )
        body = await response.text()
        assert "inner monologue about the picture" not in body
        messages = await client.get(f"/api/projects/{skye_id}/messages")
        payload = await messages.json()
        assistant = [item for item in payload["messages"] if item["role"] == "assistant"]
        assert len(assistant) == 1
        assert assistant[0]["text"] == ""
        assert assistant[0]["file_ids"]
        assert payload["files"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_voice_turn_stores_playable_audio(
    database: Database, tmp_path: Path
) -> None:
    client, projects = await _client_with_runtime(database, tmp_path, VoiceRuntime())
    try:
        await signed_in(client, projects)
        listed = await client.get("/api/projects")
        skye_id = (await listed.json())["projects"][0]["id"]
        response = await client.post(
            f"/api/projects/{skye_id}/messages", json={"text": "say it aloud"}
        )
        body = await response.text()
        assert "inner monologue about the voice" not in body
        messages = await client.get(f"/api/projects/{skye_id}/messages")
        payload = await messages.json()
        assistant = [item for item in payload["messages"] if item["role"] == "assistant"]
        assert len(assistant) == 1
        voice = next(item for item in payload["files"] if item["id"] in assistant[0]["file_ids"])
        assert voice["filename"] == "voice.ogg"
        assert voice["mime"] == "audio/ogg"
        assert voice["kind"] == "document"
    finally:
        await client.close()
