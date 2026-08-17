from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from skye.access import AccessService
from skye.auth import COOKIE_NAME, TelegramAuth
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
        on_event = kwargs.get("on_event")
        if on_event is not None:
            await on_event(RunEvent(kind="text", text="Hello from Skye."))
            await on_event(
                RunEvent(
                    kind="tool",
                    tool_id="t1",
                    tool_name="web_search",
                    tool_label="Searched the web",
                    tool_status="done",
                )
            )
        return RunOutput("Hello from Skye.", ())

    def stop_key(self, key: str) -> bool:
        self._stopped.append(key)
        return True


async def app_client(
    database: Database, tmp_path: Path, *, owner_ids: frozenset[int] = frozenset({1})
) -> tuple[TestClient, ProjectService, FakeRuntime]:
    config = settings()
    client = AsyncMock()
    client.conversations.create = AsyncMock(return_value=SimpleNamespace(id="conv_web"))
    projects = ProjectService(database, client, tmp_path / "web-files")
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
async def test_web_allowlist_denies_unknown_users(database: Database, tmp_path: Path) -> None:
    client, projects, _runtime = await app_client(database, tmp_path)
    try:
        await signed_in(client, projects, user_id=99)
        response = await client.get("/api/projects")
        assert response.status == 403
        assert "private" in (await response.text()).lower()
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
        response = await client.post(
            f"/api/projects/{skye_id}/messages", json={"text": "hello"}
        )
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
