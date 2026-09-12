"""Durable operator state: structured logs, captured model traffic, config overrides.

One writer task drains bounded in-memory queues into SQLite so request handling
and the event loop are never blocked by a disk write. Everything the panel shows
lives here; the store never contains business logic.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import traceback
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .db import Database

OPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    logger TEXT,
    chat_id INTEGER,
    user_id INTEGER,
    thread_id INTEGER,
    run_key TEXT,
    run_id TEXT,
    transport TEXT,
    exception TEXT,
    context TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ops_logs_ts ON ops_logs(ts DESC);
CREATE INDEX IF NOT EXISTS ops_logs_level ON ops_logs(level, ts DESC);
CREATE INDEX IF NOT EXISTS ops_logs_event ON ops_logs(event, ts DESC);
CREATE INDEX IF NOT EXISTS ops_logs_chat ON ops_logs(chat_id, ts DESC);
CREATE INDEX IF NOT EXISTS ops_logs_user ON ops_logs(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS ops_logs_run ON ops_logs(run_id);

CREATE TABLE IF NOT EXISTS ops_traces (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    ts TEXT NOT NULL,
    run_id TEXT,
    run_key TEXT,
    transport TEXT,
    label TEXT,
    chat_id INTEGER,
    user_id INTEGER,
    thread_id INTEGER,
    method TEXT,
    url TEXT,
    host TEXT,
    status INTEGER,
    ok INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    model TEXT,
    stream INTEGER NOT NULL DEFAULT 0,
    request_bytes INTEGER,
    response_bytes INTEGER,
    request_content_type TEXT,
    response_content_type TEXT,
    request_headers TEXT,
    response_headers TEXT,
    request_body TEXT,
    response_body TEXT,
    error TEXT,
    tokens INTEGER,
    media TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ops_traces_ts ON ops_traces(ts DESC);
CREATE INDEX IF NOT EXISTS ops_traces_status ON ops_traces(status, ts DESC);
CREATE INDEX IF NOT EXISTS ops_traces_chat ON ops_traces(chat_id, ts DESC);
CREATE INDEX IF NOT EXISTS ops_traces_user ON ops_traces(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS ops_traces_run ON ops_traces(run_id);
"""

_RESERVED_LOG_KEYS = frozenset(
    {
        "timestamp",
        "level",
        "log_level",
        "event",
        "exception",
        "exc_info",
        "chat_id",
        "user_id",
        "thread_id",
        "run_key",
        "run_id",
        "transport",
        "logger",
        "_ops_skip",
    }
)
_REDACTED_HEADERS = frozenset(
    {"authorization", "api-key", "x-api-key", "cookie", "set-cookie", "proxy-authorization"}
)
_MAX_LOG_QUEUE = 5_000
_MAX_TRACE_QUEUE = 400


@dataclass(slots=True)
class CapturedMedia:
    name: str
    mime: str
    kind: str  # image | audio | file | video
    data: bytes
    where: str  # request | response
    detail: str = ""


@dataclass(slots=True)
class TraceRecord:
    id: str
    ts: str
    run_id: str | None
    run_key: str | None
    transport: str | None
    label: str | None
    chat_id: int | None
    user_id: int | None
    thread_id: int | None
    method: str
    url: str
    host: str
    status: int
    ok: bool
    duration_ms: int
    model: str | None
    stream: bool
    request_bytes: int
    response_bytes: int
    request_content_type: str | None
    response_content_type: str | None
    request_headers: dict[str, str]
    response_headers: dict[str, str]
    request_body: Any
    response_body: Any
    error: str | None
    tokens: int | None
    media: list[CapturedMedia] = field(default_factory=list)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(item) for item in value]
    return str(value)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def redact_headers(headers: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in dict(headers).items():
        lowered = key.lower()
        result[key] = "***" if lowered in _REDACTED_HEADERS else str(value)
    return result


class OpsStore:
    def __init__(
        self,
        database: Database,
        *,
        media_path: Path,
        capture_payloads: bool,
        capture_media: bool,
        max_body_bytes: int,
        log_retention_days: int,
        log_max_rows: int,
        trace_retention_days: int,
        trace_max_rows: int,
    ) -> None:
        self.database = database
        self.media_path = media_path
        self.capture_payloads = capture_payloads
        self.capture_media = capture_media
        self.max_body_bytes = max_body_bytes
        self.log_retention_days = log_retention_days
        self.log_max_rows = log_max_rows
        self.trace_retention_days = trace_retention_days
        self.trace_max_rows = trace_max_rows
        self._logs: deque[dict[str, Any]] = deque(maxlen=_MAX_LOG_QUEUE)
        self._traces: deque[TraceRecord] = deque(maxlen=_MAX_TRACE_QUEUE)
        self._worker: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.dropped_logs = 0
        self.dropped_traces = 0

    async def open(self) -> None:
        await self.database.execute_script(OPS_SCHEMA)
        if self.capture_media:
            self.media_path.mkdir(parents=True, exist_ok=True)
        self._worker = asyncio.create_task(self._run(), name="ops-store")
        await self.prune()

    async def close(self) -> None:
        self._stop.set()
        if self._worker is not None:
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    # -- ingestion ---------------------------------------------------------

    def record_log(self, event: dict[str, Any], level: str) -> None:
        if event.get("_ops_skip"):
            return
        exc_info = event.get("exc_info")
        exception = event.get("exception")
        if exception is None and exc_info:
            exception = _format_exception(exc_info)
        context = {
            key: _jsonable(value)
            for key, value in event.items()
            if key not in _RESERVED_LOG_KEYS
        }
        row = {
            "ts": str(event.get("timestamp") or _now()),
            "level": str(event.get("level") or level or "info").lower(),
            "event": str(event.get("event") or "log"),
            "logger": _none_str(event.get("logger")),
            "chat_id": _safe_int(event.get("chat_id")),
            "user_id": _safe_int(event.get("user_id")),
            "thread_id": _safe_int(event.get("thread_id")),
            "run_key": _none_str(event.get("run_key")),
            "run_id": _none_str(event.get("run_id")),
            "transport": _none_str(event.get("transport")),
            "exception": exception if isinstance(exception, str) else None,
            "context": json.dumps(context, ensure_ascii=False, default=str),
        }
        if len(self._logs) == self._logs.maxlen:
            self.dropped_logs += 1
        self._logs.append(row)

    def record_trace(self, trace: TraceRecord) -> None:
        if len(self._traces) == self._traces.maxlen:
            self.dropped_traces += 1
        self._traces.append(trace)

    # -- worker ------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=0.4)
            await self._flush()
            if self._stop.is_set():
                return

    async def _flush(self) -> None:
        logs = list(self._logs)
        self._logs.clear()
        traces = list(self._traces)
        self._traces.clear()
        if logs:
            try:
                await self.database.execute_many(
                    """INSERT INTO ops_logs
                       (ts, level, event, logger, chat_id, user_id, thread_id, run_key,
                        run_id, transport, exception, context)
                       VALUES (:ts, :level, :event, :logger, :chat_id, :user_id, :thread_id,
                               :run_key, :run_id, :transport, :exception, :context)""",
                    logs,
                )
            except Exception:
                self._warn("ops_logs_write_failed")
        for trace in traces:
            try:
                await self._persist_trace(trace)
            except Exception:
                self._warn("ops_trace_write_failed")

    async def _persist_trace(self, trace: TraceRecord) -> None:
        media_meta: list[dict[str, Any]] = []
        if self.capture_media:
            for item in trace.media:
                written = await self._write_media(trace.id, item)
                if written is not None:
                    media_meta.append(written)
        await self.database.execute_write(
            """INSERT OR REPLACE INTO ops_traces
               (id, ts, run_id, run_key, transport, label, chat_id, user_id, thread_id,
                method, url, host, status, ok, duration_ms, model, stream, request_bytes,
                response_bytes, request_content_type, response_content_type,
                request_headers, response_headers, request_body, response_body, error,
                tokens, media)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trace.id,
                trace.ts,
                trace.run_id,
                trace.run_key,
                trace.transport,
                trace.label,
                trace.chat_id,
                trace.user_id,
                trace.thread_id,
                trace.method,
                trace.url,
                trace.host,
                trace.status,
                int(trace.ok),
                trace.duration_ms,
                trace.model,
                int(trace.stream),
                trace.request_bytes,
                trace.response_bytes,
                trace.request_content_type,
                trace.response_content_type,
                json.dumps(trace.request_headers, ensure_ascii=False),
                json.dumps(trace.response_headers, ensure_ascii=False),
                _body_text(trace.request_body, self.max_body_bytes),
                _body_text(trace.response_body, self.max_body_bytes),
                trace.error,
                trace.tokens,
                json.dumps(media_meta, ensure_ascii=False),
            ),
        )

    async def _write_media(self, trace_id: str, item: CapturedMedia) -> dict[str, Any] | None:
        directory = self.media_path / trace_id
        name = _safe_name(item.name)
        target = directory / name
        try:
            await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, item.data)
        except OSError:
            return None
        return {
            "name": name,
            "mime": item.mime,
            "kind": item.kind,
            "size": len(item.data),
            "where": item.where,
            "detail": item.detail,
        }

    def _warn(self, event: str) -> None:
        # Never route this through structlog: it would feed the queue again.
        print(json.dumps({"event": event, "_ops_skip": True}), flush=True)

    # -- queries -----------------------------------------------------------

    async def query_logs(
        self,
        *,
        level: str | None = None,
        event: str | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
        transport: str | None = None,
        query: str | None = None,
        run_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        before: int | None = None,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if level:
            if level == "warning":
                clauses.append("level IN ('warning', 'warn')")
            else:
                clauses.append("level = ?")
                params.append(level)
        if event:
            clauses.append("event = ?")
            params.append(event)
        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if transport:
            clauses.append("transport = ?")
            params.append(transport)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        if until:
            clauses.append("ts <= ?")
            params.append(until)
        if before is not None:
            clauses.append("id < ?")
            params.append(before)
        if query:
            clauses.append("(event LIKE ? OR context LIKE ? OR exception LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = await self.database.fetch_all(
            f"""SELECT id, ts, level, event, logger, chat_id, user_id, thread_id, run_key,
                       run_id, transport, substr(context, 1, 240) AS preview,
                       CASE WHEN exception IS NULL THEN 0 ELSE 1 END AS has_exception
                FROM ops_logs {where}
                ORDER BY id DESC LIMIT ?""",
            tuple(params),
        )
        return [dict(row) for row in rows]

    async def log_events(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = await self.database.fetch_all(
            """SELECT event, COUNT(*) AS count FROM ops_logs
               GROUP BY event ORDER BY count DESC LIMIT ?""",
            (max(1, min(limit, 200)),),
        )
        return [dict(row) for row in rows]

    async def get_log(self, log_id: int) -> dict[str, Any] | None:
        row = await self.database.fetch_one("SELECT * FROM ops_logs WHERE id = ?", (log_id,))
        return None if row is None else _decode_row(row, json_fields=("context",))

    async def query_traces(
        self,
        *,
        status: str | None = None,
        transport: str | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
        model: str | None = None,
        query: str | None = None,
        run_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        before: int | None = None,
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status == "ok":
            clauses.append("ok = 1")
        elif status == "error":
            clauses.append("ok = 0")
        if transport:
            clauses.append("transport = ?")
            params.append(transport)
        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if model:
            clauses.append("model = ?")
            params.append(model)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        if until:
            clauses.append("ts <= ?")
            params.append(until)
        if before is not None:
            clauses.append("seq < ?")
            params.append(before)
        if query:
            clauses.append("(url LIKE ? OR model LIKE ? OR label LIKE ? OR error LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 300)))
        rows = await self.database.fetch_all(
            f"""SELECT seq, id, ts, run_id, run_key, transport, label, chat_id, user_id,
                       method, url, host, status, ok, duration_ms, model, stream,
                       request_bytes, response_bytes, response_content_type, tokens,
                       CASE WHEN error IS NULL THEN 0 ELSE 1 END AS has_error, media
                FROM ops_traces {where}
                ORDER BY seq DESC LIMIT ?""",
            tuple(params),
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["ok"] = bool(item["ok"])
            item["has_error"] = bool(item["has_error"])
            media = _loads(item.pop("media", "[]"))
            if isinstance(media, list):
                item["media_count"] = len(media)
                item["media_preview"] = [
                    {"name": str(entry.get("name")), "mime": str(entry.get("mime"))}
                    for entry in media
                    if isinstance(entry, dict)
                    and str(entry.get("mime", "")).startswith("image/")
                ][:3]
            else:
                item["media_count"] = 0
                item["media_preview"] = []
            items.append(item)
        return items

    async def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        row = await self.database.fetch_one(
            "SELECT * FROM ops_traces WHERE id = ?", (trace_id,)
        )
        if row is None:
            return None
        item = _decode_row(
            row,
            json_fields=("request_headers", "response_headers", "media"),
        )
        item["ok"] = bool(item["ok"])
        item["stream"] = bool(item["stream"])
        item["request_body"] = _loads(item.get("request_body"))
        item["response_body"] = _loads(item.get("response_body"))
        run_id = item.get("run_id")
        if run_id:
            rows = await self.database.fetch_all(
                """SELECT id, ts, level, event FROM ops_logs
                   WHERE run_id = ? ORDER BY id ASC LIMIT 200""",
                (run_id,),
            )
            item["logs"] = [dict(row) for row in rows]
        return item

    async def trace_models(self) -> list[str]:
        rows = await self.database.fetch_all(
            """SELECT DISTINCT model FROM ops_traces
               WHERE model IS NOT NULL ORDER BY model LIMIT 100"""
        )
        return [str(row["model"]) for row in rows]

    async def media_file(self, trace_id: str, name: str) -> Path | None:
        target = (self.media_path / trace_id / name).resolve()
        root = self.media_path.resolve()
        if root not in target.parents or not target.is_file():
            return None
        return target

    async def overview(self) -> dict[str, Any]:
        logs = await self.database.fetch_one("SELECT COUNT(*) AS n FROM ops_logs")
        errors = await self.database.fetch_one(
            "SELECT COUNT(*) AS n FROM ops_logs WHERE level IN ('error', 'critical')"
        )
        traces = await self.database.fetch_one("SELECT COUNT(*) AS n FROM ops_traces")
        failed = await self.database.fetch_one("SELECT COUNT(*) AS n FROM ops_traces WHERE ok = 0")
        recent = await self.database.fetch_one(
            "SELECT COUNT(*) AS n FROM ops_traces WHERE ts >= ?",
            ((datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="milliseconds"),),
        )
        overrides = await self.database.fetch_one(
            "SELECT COUNT(*) AS n FROM ops_config_overrides"
        )
        media_bytes = await asyncio.to_thread(_directory_size, self.media_path)
        last_error = await self.database.fetch_one(
            "SELECT ts, event, exception FROM ops_logs WHERE level IN ('error', 'critical') "
            "ORDER BY id DESC LIMIT 1"
        )
        return {
            "logs": int(logs["n"]) if logs else 0,
            "errors": int(errors["n"]) if errors else 0,
            "traces": int(traces["n"]) if traces else 0,
            "failed_traces": int(failed["n"]) if failed else 0,
            "traces_24h": int(recent["n"]) if recent else 0,
            "overrides": int(overrides["n"]) if overrides else 0,
            "media_bytes": media_bytes,
            "dropped_logs": self.dropped_logs,
            "dropped_traces": self.dropped_traces,
            "last_error": dict(last_error) if last_error else None,
        }

    # -- config overrides --------------------------------------------------

    async def config_overrides(self) -> dict[str, Any]:
        return await self.database.config_overrides()

    async def set_overrides(self, updates: dict[str, Any], removals: list[str]) -> None:
        await self.database.set_config_overrides(updates, removals)

    # -- maintenance -------------------------------------------------------

    async def prune(self) -> dict[str, int]:
        log_cutoff = (datetime.now(UTC) - timedelta(days=self.log_retention_days)).isoformat(
            timespec="milliseconds"
        )
        trace_cutoff = (
            datetime.now(UTC) - timedelta(days=self.trace_retention_days)
        ).isoformat(timespec="milliseconds")
        stale = await self.database.fetch_all(
            "SELECT id FROM ops_traces WHERE ts < ?", (trace_cutoff,)
        )
        removed_traces = await self._delete_traces([str(row["id"]) for row in stale])
        cursor = await self.database.execute_write(
            "DELETE FROM ops_logs WHERE ts < ?", (log_cutoff,)
        )
        removed_logs = cursor.rowcount
        overflow = await self.database.fetch_all(
            "SELECT id FROM ops_traces ORDER BY seq DESC LIMIT -1 OFFSET ?",
            (self.trace_max_rows,),
        )
        removed_traces += await self._delete_traces([str(row["id"]) for row in overflow])
        cursor = await self.database.execute_write(
            """DELETE FROM ops_logs WHERE id NOT IN
               (SELECT id FROM ops_logs ORDER BY id DESC LIMIT ?)""",
            (self.log_max_rows,),
        )
        removed_logs += cursor.rowcount
        return {"logs": removed_logs, "traces": removed_traces}

    async def _delete_traces(self, trace_ids: list[str]) -> int:
        removed = 0
        for trace_id in trace_ids:
            cursor = await self.database.execute_write(
                "DELETE FROM ops_traces WHERE id = ?", (trace_id,)
            )
            removed += cursor.rowcount
            if self.capture_media:
                with suppress(OSError):
                    await asyncio.to_thread(
                        shutil.rmtree, self.media_path / trace_id, ignore_errors=True
                    )
        return removed


def _decode_row(row: Any, json_fields: tuple[str, ...]) -> dict[str, Any]:
    item = dict(row)
    for key in json_fields:
        if key in item:
            item[key] = _loads(item[key])
    return item


def _loads(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _body_text(value: Any, cap: int) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > cap:
        return text[:cap] + "\n… truncated"
    return text


def _none_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _format_exception(exc_info: Any) -> str:
    if isinstance(exc_info, BaseException):
        return "".join(traceback.format_exception(type(exc_info), exc_info, exc_info.__traceback__))
    if isinstance(exc_info, tuple) and len(exc_info) == 3:
        return "".join(traceback.format_exception(*exc_info))
    return str(exc_info)


def _safe_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    return cleaned[:120] or "file.bin"


def _directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def monotonic_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
