from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from skye.access import AccessService
from skye.auth import COOKIE_NAME, TelegramAuth
from skye.config import Settings
from skye.db import Database
from skye.ops import CapturedMedia, OpsStore, TraceRecord
from skye.ops_web import OpsPanel
from skye.projects import ProjectService
from skye.runtime import AgentRuntime
from skye.web import WebApp


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123:token",
        "openai_api_key": "sk-test",
        "skye_owner_ids": "1",
        "skye_web_origin": "https://chat.skye-bot.com",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class FakeRuntime(AgentRuntime):
    def __init__(self) -> None:
        pass

    def stop_key(self, key: str) -> bool:
        return True


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


async def ops_client(
    database: Database,
    tmp_path: Path,
    *,
    enabled: bool = True,
) -> tuple[TestClient, ProjectService, OpsStore]:
    config = settings(skye_ops_enabled=enabled)
    projects = ProjectService(database, tmp_path / "web-files")
    auth = TelegramAuth(config, database, projects)
    store = OpsStore(
        database,
        media_path=tmp_path / "ops-media",
        capture_payloads=True,
        capture_media=True,
        max_body_bytes=100_000,
        log_retention_days=14,
        log_max_rows=1_000,
        trace_retention_days=14,
        trace_max_rows=100,
    )
    await store.open()
    panel = OpsPanel(config, database, store, auth, env_file=tmp_path / ".env")
    web_app = WebApp(
        config,
        database,
        AccessService(database, frozenset({1})),
        FakeRuntime(),  # type: ignore[arg-type]
        projects,
        auth,
        cast(Any, AsyncMock()),
        None,
        None,
        panel,
    )
    client = TestClient(TestServer(web_app.app))
    await client.start_server()
    return client, projects, store


async def owner(client: TestClient, projects: ProjectService) -> None:
    session = await projects.create_session(1, "Owner", "owner")
    client.session.cookie_jar.update_cookies({COOKIE_NAME: session.id})


async def guest(client: TestClient, projects: ProjectService) -> None:
    session = await projects.create_session(99, "Guest", "guest")
    client.session.cookie_jar.update_cookies({COOKIE_NAME: session.id})


@pytest.mark.asyncio
async def test_ops_requires_a_session(database: Database, tmp_path: Path) -> None:
    client, _projects, store = await ops_client(database, tmp_path)
    try:
        response = await client.get("/api/admin/overview")
        assert response.status == 401
    finally:
        await client.close()
        await store.close()


@pytest.mark.asyncio
async def test_ops_rejects_non_owner(database: Database, tmp_path: Path) -> None:
    client, projects, store = await ops_client(database, tmp_path)
    try:
        await guest(client, projects)
        response = await client.get("/api/admin/overview")
        assert response.status == 403
    finally:
        await client.close()
        await store.close()


@pytest.mark.asyncio
async def test_ops_hidden_when_disabled(database: Database, tmp_path: Path) -> None:
    client, projects, store = await ops_client(database, tmp_path, enabled=False)
    try:
        await owner(client, projects)
        response = await client.get("/api/admin/overview")
        assert response.status == 404
    finally:
        await client.close()
        await store.close()


@pytest.mark.asyncio
async def test_config_lists_fields_and_masks_secrets(
    database: Database, tmp_path: Path
) -> None:
    client, projects, store = await ops_client(database, tmp_path)
    try:
        await owner(client, projects)
        response = await client.get("/api/admin/config")
        assert response.status == 200
        payload = await response.json()
        fields = {item["key"]: item for item in payload["fields"]}
        assert fields["skye_max_turns"]["value"] == 20
        assert fields["openai_api_key"]["secret"] is True
        assert fields["openai_api_key"]["value"] is None
        assert fields["openai_api_key"]["is_set"] is True
        assert fields["skye_database_path"]["read_only"] is True
    finally:
        await client.close()
        await store.close()


@pytest.mark.asyncio
async def test_config_validate_reports_errors(database: Database, tmp_path: Path) -> None:
    client, projects, store = await ops_client(database, tmp_path)
    try:
        await owner(client, projects)
        response = await client.post(
            "/api/admin/config/validate",
            json={"values": {"skye_max_context_tokens": 1000}},
        )
        assert response.status == 400
        payload = await response.json()
        assert payload["ok"] is False
        assert "skye_max_context_tokens" in payload["errors"]
    finally:
        await client.close()
        await store.close()


@pytest.mark.asyncio
async def test_config_apply_and_revert(database: Database, tmp_path: Path) -> None:
    client, projects, store = await ops_client(database, tmp_path)
    try:
        await owner(client, projects)
        applied = await client.post(
            "/api/admin/config/apply",
            json={"values": {"skye_max_turns": 25, "skye_sandbox_enabled": True}},
        )
        assert applied.status == 200
        result = await applied.json()
        assert result["ok"] is True
        assert result["restart_required"] is True
        assert await store.config_overrides() == {
            "SKYE_MAX_TURNS": 25,
            "SKYE_SANDBOX_ENABLED": True,
        }

        refreshed = await client.get("/api/admin/config")
        fields = {item["key"]: item for item in (await refreshed.json())["fields"]}
        assert fields["skye_max_turns"]["override"] is True
        assert fields["skye_max_turns"]["value"] == 25
        assert fields["skye_sandbox_enabled"]["value"] is True

        reverted = await client.post(
            "/api/admin/config/revert", json={"env": "SKYE_MAX_TURNS"}
        )
        assert reverted.status == 200
        assert await store.config_overrides() == {"SKYE_SANDBOX_ENABLED": True}
    finally:
        await client.close()
        await store.close()


@pytest.mark.asyncio
async def test_logs_and_traces_are_served(database: Database, tmp_path: Path) -> None:
    client, projects, store = await ops_client(database, tmp_path)
    try:
        await owner(client, projects)
        store.record_log(
            {
                "event": "ops_demo",
                "level": "warning",
                "timestamp": "2026-02-01T10:00:00.000Z",
                "chat_id": 7,
                "run_id": "run-1",
            },
            "warning",
        )
        store.record_trace(
            TraceRecord(
                id="trace-demo",
                ts="2026-02-01T10:00:01.000Z",
                run_id="run-1",
                run_key="tg:7:0",
                transport="telegram",
                label="Chat 7",
                chat_id=7,
                user_id=5,
                thread_id=0,
                method="POST",
                url="https://api.example.com/v1/chat/completions",
                host="api.example.com",
                status=200,
                ok=True,
                duration_ms=1200,
                model="gpt-x",
                stream=True,
                request_bytes=120,
                response_bytes=240,
                request_content_type="application/json",
                response_content_type="text/event-stream",
                request_headers={"authorization": "***"},
                response_headers={},
                request_body={"model": "gpt-x"},
                response_body={"__stream__": True, "events": []},
                error=None,
                tokens=42,
                media=[
                    CapturedMedia(
                        name="response-1.png",
                        mime="image/png",
                        kind="image",
                        data=b"\x89PNG\r\n\x1a\nbytes",
                        where="response",
                        detail="output image",
                    )
                ],
            )
        )
        await store._flush()

        logs = await client.get("/api/admin/logs?level=warning")
        assert logs.status == 200
        rows = (await logs.json())["logs"]
        assert rows and rows[0]["event"] == "ops_demo"
        assert rows[0]["has_exception"] == 0

        events = await client.get("/api/admin/logs/events")
        assert (await events.json())["events"][0]["event"] == "ops_demo"

        traces = await client.get("/api/admin/traces?status=ok")
        assert traces.status == 200
        items = (await traces.json())["traces"]
        assert items[0]["model"] == "gpt-x"
        assert items[0]["media_count"] == 1
        assert items[0]["tokens"] == 42

        detail = await client.get("/api/admin/traces/trace-demo")
        payload = (await detail.json())["trace"]
        assert payload["request_headers"]["authorization"] == "***"
        assert payload["logs"][0]["event"] == "ops_demo"

        media = await client.get("/api/admin/traces/trace-demo/media/response-1.png")
        assert media.status == 200
        assert await media.read() == b"\x89PNG\r\n\x1a\nbytes"

        missing = await client.get("/api/admin/traces/trace-demo/media/../skye.db")
        assert missing.status == 404
    finally:
        await client.close()
        await store.close()
