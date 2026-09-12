from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from skye.config import HOSTED_MODEL
from skye.db import Database
from skye.ops import OpsStore
from skye.ops_capture import (
    CapturingTransport,
    OpsContext,
    bind_context,
    clear_context,
    new_run_id,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 128
PNG_B64 = base64.b64encode(PNG).decode()


def build_store(tmp_path: Path, **overrides: object) -> tuple[Database, OpsStore]:
    database = Database(tmp_path / "skye.db", HOSTED_MODEL, "medium")
    store = OpsStore(
        database,
        media_path=tmp_path / "media",
        capture_payloads=bool(overrides.get("capture_payloads", True)),
        capture_media=bool(overrides.get("capture_media", True)),
        max_body_bytes=2_000_000,
        log_retention_days=14,
        log_max_rows=50,
        trace_retention_days=14,
        trace_max_rows=5,
    )
    return database, store


async def test_logs_round_trip_and_filter(tmp_path: Path) -> None:
    database, store = build_store(tmp_path)
    await database.open()
    await store.open()
    try:
        store.record_log(
            {
                "event": "openai_run_started",
                "level": "info",
                "timestamp": "2026-01-01T00:00:00.000Z",
                "chat_id": 7,
                "user_id": 5,
                "run_key": "tg:7:0",
                "queued": False,
            },
            "info",
        )
        store.record_log(
            {
                "event": "openai_run_failed",
                "level": "error",
                "timestamp": "2026-01-01T00:00:01.000Z",
                "chat_id": 9,
                "exc_info": ValueError("nope"),
            },
            "error",
        )
        await store._flush()
        assert [row["event"] for row in await store.query_logs(limit=10)] == [
            "openai_run_failed",
            "openai_run_started",
        ]
        assert [row["event"] for row in await store.query_logs(level="error")] == [
            "openai_run_failed"
        ]
        assert [row["event"] for row in await store.query_logs(chat_id=7)] == [
            "openai_run_started"
        ]
        error_row = (await store.query_logs(level="error"))[0]
        assert error_row["has_exception"] == 1
        detail = await store.get_log(int(error_row["id"]))
        assert detail is not None
        assert "ValueError: nope" in (detail["exception"] or "")
        assert detail["context"] == {}
    finally:
        await store.close()
        await database.close()


async def test_duplicate_logs_are_dropped_when_the_queue_is_full(tmp_path: Path) -> None:
    database, store = build_store(tmp_path)
    await database.open()
    await store.open()
    try:
        store._logs = type(store._logs)(maxlen=2)
        for index in range(4):
            store.record_log({"event": f"e{index}", "level": "info"}, "info")
        assert store.dropped_logs == 2
    finally:
        await store.close()
        await database.close()


async def test_capture_extracts_media_and_scrubs_bodies(tmp_path: Path) -> None:
    database, store = build_store(tmp_path)
    await database.open()
    await store.open()
    try:
        payload = {
            "model": "gpt-x",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "data": [{"b64_json": PNG_B64}],
        }
        body = json.dumps(payload).encode()

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=httpx.ByteStream(body),
            )

        transport = CapturingTransport(httpx.MockTransport(handler), store)
        client = httpx.AsyncClient(transport=transport)
        token = bind_context(
            OpsContext(
                run_id=new_run_id(),
                run_key="tg:7:0",
                transport="telegram",
                label="Chat 7",
                chat_id=7,
                user_id=5,
            )
        )
        try:
            response = await client.post(
                "https://api.example.com/v1/chat/completions",
                headers={"Authorization": "Bearer sk-secret"},
                json={
                    "model": "gpt-x",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image_url": f"data:image/png;base64,{PNG_B64}",
                                }
                            ],
                        }
                    ],
                },
            )
        finally:
            clear_context(token)
            await client.aclose()
        assert response.status_code == 200
        await store._flush()

        traces = await store.query_traces()
        assert len(traces) == 1
        summary = traces[0]
        assert summary["model"] == "gpt-x"
        assert summary["ok"] is True
        assert summary["tokens"] == 15
        assert summary["media_count"] == 2
        assert summary["chat_id"] == 7
        assert summary["transport"] == "telegram"

        trace = await store.get_trace(str(summary["id"]))
        assert trace is not None
        assert trace["request_body"]["messages"][0]["content"][0]["image_url"]["__media__"]
        assert trace["response_body"]["data"][0]["b64_json"]["__media__"]
        assert trace["request_headers"]["authorization"] == "***"
        for item in trace["media"]:
            path = await store.media_file(str(summary["id"]), str(item["name"]))
            assert path is not None and path.read_bytes() == PNG
    finally:
        await store.close()
        await database.close()


async def test_capture_can_be_disabled(tmp_path: Path) -> None:
    database, store = build_store(tmp_path, capture_payloads=False)
    await database.open()
    await store.open()
    try:
        transport = CapturingTransport(
            httpx.MockTransport(lambda _r: httpx.Response(200)), store
        )
        client = httpx.AsyncClient(transport=transport)
        await client.get("https://api.example.com/v1/models")
        await client.aclose()
        await store._flush()
        assert await store.query_traces() == []
    finally:
        await store.close()
        await database.close()


async def test_retention_caps_rows_and_media(tmp_path: Path) -> None:
    database, store = build_store(tmp_path)
    await database.open()
    await store.open()
    try:
        now = datetime.now(UTC).isoformat(timespec="milliseconds")
        for index in range(60):
            store.record_log({"event": f"e{index}", "level": "info", "timestamp": now}, "info")
        await store._flush()
        removed = await store.prune()
        assert removed["logs"] >= 10
        remaining = await store.query_logs(limit=100)
        assert len(remaining) == 50
    finally:
        await store.close()
        await database.close()


async def test_config_overrides_round_trip(tmp_path: Path) -> None:
    database, store = build_store(tmp_path)
    await database.open()
    await store.open()
    try:
        await store.set_overrides({"SKYE_MAX_TURNS": 25}, [])
        assert await store.config_overrides() == {"SKYE_MAX_TURNS": 25}
        await store.set_overrides({}, ["SKYE_MAX_TURNS"])
        assert await store.config_overrides() == {}
    finally:
        await store.close()
        await database.close()


async def test_media_path_traversal_is_rejected(tmp_path: Path) -> None:
    database, store = build_store(tmp_path)
    await database.open()
    await store.open()
    try:
        assert await store.media_file("abc", "../skye.db") is None
    finally:
        await store.close()
        await database.close()


def test_run_ids_are_unique() -> None:
    assert len({new_run_id(), new_run_id()}) == 2
