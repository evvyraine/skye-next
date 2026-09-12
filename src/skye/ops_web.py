"""Owner-only operator API: logs, captured traffic, and configuration."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any

import structlog
from aiohttp import web

from .auth import COOKIE_NAME, TelegramAuth
from .config import Settings
from .db import Database
from .ops import OpsStore
from .ops_config import (
    GROUPS,
    FieldSpec,
    coerce_change,
    describe_fields,
    env_sources,
    validate_values,
)

log = structlog.get_logger()

_DEFAULT_LOG_LIMIT = 80
_DEFAULT_TRACE_LIMIT = 60


class OpsPanel:
    def __init__(
        self,
        config: Settings,
        database: Database,
        store: OpsStore,
        auth: TelegramAuth,
        *,
        env_file: Path | None = None,
    ) -> None:
        self.config = config
        self.database = database
        self.store = store
        self.auth = auth
        self.env_file = env_file or Path(".env")
        self._specs: dict[str, FieldSpec] = {spec.key: spec for spec in describe_fields()}

    def add_routes(self, app: web.Application) -> None:
        add = app.router.add_route
        add("GET", "/api/admin/overview", self.overview)
        add("GET", "/api/admin/logs", self.list_logs)
        add("GET", "/api/admin/logs/events", self.log_events)
        add("GET", "/api/admin/logs/{id}", self.get_log)
        add("GET", "/api/admin/traces", self.list_traces)
        add("GET", "/api/admin/traces/{id}", self.get_trace)
        add("GET", "/api/admin/traces/{id}/media/{name}", self.get_media)
        add("GET", "/api/admin/config", self.get_config)
        add("POST", "/api/admin/config/validate", self.validate_config)
        add("POST", "/api/admin/config/apply", self.apply_config)
        add("POST", "/api/admin/config/revert", self.revert_config)
        add("POST", "/api/admin/maintenance", self.maintenance)
        add("POST", "/api/admin/restart", self.restart)

    # -- handlers ----------------------------------------------------------

    async def overview(self, request: web.Request) -> web.Response:
        user_id = await self._require_owner(request)
        payload = await self.store.overview()
        payload["me"] = user_id
        payload["capture"] = {
            "enabled": self.store.capture_payloads,
            "media": self.store.capture_media,
        }
        return web.json_response(payload)

    async def list_logs(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        rows = await self.store.query_logs(
            level=_first(request.query.get("level")),
            event=_first(request.query.get("event")),
            chat_id=_optional_int(request.query.get("chat_id")),
            user_id=_optional_int(request.query.get("user_id")),
            transport=_first(request.query.get("transport")),
            query=_first(request.query.get("q")),
            run_id=_first(request.query.get("run_id")),
            since=_first(request.query.get("since")),
            until=_first(request.query.get("until")),
            before=_optional_int(request.query.get("before")),
            limit=_optional_int(request.query.get("limit")) or _DEFAULT_LOG_LIMIT,
        )
        return web.json_response({"logs": rows})

    async def log_events(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        return web.json_response({"events": await self.store.log_events()})

    async def get_log(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        try:
            log_id = int(request.match_info["id"])
        except ValueError as error:
            raise web.HTTPBadRequest(text="Unknown log entry.") from error
        item = await self.store.get_log(log_id)
        if item is None:
            raise web.HTTPNotFound(text="Log entry not found.")
        return web.json_response({"log": item})

    async def list_traces(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        rows = await self.store.query_traces(
            status=_first(request.query.get("status")),
            transport=_first(request.query.get("transport")),
            chat_id=_optional_int(request.query.get("chat_id")),
            user_id=_optional_int(request.query.get("user_id")),
            model=_first(request.query.get("model")),
            query=_first(request.query.get("q")),
            run_id=_first(request.query.get("run_id")),
            since=_first(request.query.get("since")),
            until=_first(request.query.get("until")),
            before=_optional_int(request.query.get("before")),
            limit=_optional_int(request.query.get("limit")) or _DEFAULT_TRACE_LIMIT,
        )
        return web.json_response({"traces": rows, "models": await self.store.trace_models()})

    async def get_trace(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        item = await self.store.get_trace(request.match_info["id"])
        if item is None:
            raise web.HTTPNotFound(text="Request not found.")
        return web.json_response({"trace": item})

    async def get_media(self, request: web.Request) -> web.StreamResponse:
        await self._require_owner(request)
        path = await self.store.media_file(
            request.match_info["id"], request.match_info["name"]
        )
        if path is None:
            raise web.HTTPNotFound(text="Media not found.")
        suffix = path.suffix.lower()
        disposition = "inline" if suffix in _INLINE_SUFFIXES else "attachment"
        return web.FileResponse(
            path,
            headers={
                "Content-Disposition": f'{disposition}; filename="{path.name}"',
                "Cache-Control": "private, max-age=600",
            },
        )

    async def get_config(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        overrides = await self.store.config_overrides()
        configured = _configured_env(self.env_file)
        fields = [
            _field_payload(spec, self.config, overrides, configured)
            for spec in self._specs.values()
        ]
        return web.json_response(
            {
                "fields": fields,
                "groups": list(GROUPS),
                "overrides": sorted(overrides),
                "env_file": str(self.env_file),
                "env_file_exists": self.env_file.is_file(),
                "capture": {
                    "enabled": self.store.capture_payloads,
                    "media": self.store.capture_media,
                },
            }
        )

    async def validate_config(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        values = await self._values(request)
        changes, errors = self._prepare(values)
        if errors:
            return web.json_response({"ok": False, "errors": errors}, status=400)
        validated, validation_errors = validate_values(self.config, changes)
        return web.json_response(
            {
                "ok": not validation_errors,
                "errors": validation_errors,
                "normalized": _normalize(validated, changes) if validated else {},
            },
            status=200 if not validation_errors else 400,
        )

    async def apply_config(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        values = await self._values(request)
        changes, errors = self._prepare(values)
        if errors:
            return web.json_response({"ok": False, "errors": errors}, status=400)
        validated, validation_errors = validate_values(self.config, changes)
        if validated is None or validation_errors:
            return web.json_response(
                {"ok": False, "errors": validation_errors}, status=400
            )
        updates: dict[str, Any] = {}
        removals: list[str] = []
        for key, value in changes.items():
            spec = self._specs.get(key)
            if spec is None or spec.read_only:
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                removals.append(spec.env)
            else:
                updates[spec.env] = value
        await self.store.set_overrides(updates, removals)
        self._apply_live(validated)
        log.info("ops_config_applied", changed=len(updates), reverted=len(removals))
        return web.json_response(
            {
                "ok": True,
                "changed": sorted(updates),
                "reverted": sorted(removals),
                "restart_required": _restart_required(changes),
            }
        )

    async def revert_config(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        body = await _json(request)
        env = body.get("env")
        removals = [str(env)] if isinstance(env, str) and env else []
        if not removals:
            removals = sorted(await self.store.config_overrides())
        await self.store.set_overrides({}, removals)
        log.info("ops_config_reverted", count=len(removals))
        return web.json_response({"ok": True, "reverted": removals, "restart_required": True})

    async def maintenance(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        removed = await self.store.prune()
        return web.json_response({"ok": True, **removed})

    async def restart(self, request: web.Request) -> web.Response:
        await self._require_owner(request)
        log.info("ops_restart_requested")
        loop = asyncio.get_running_loop()
        loop.call_later(0.6, _terminate)
        return web.json_response({"ok": True})

    # -- helpers -----------------------------------------------------------

    def _prepare(self, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        changes: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for key, raw in values.items():
            spec = self._specs.get(key)
            if spec is None:
                continue
            if spec.read_only:
                errors[key] = "This setting is fixed at deploy time."
                continue
            try:
                changes[key] = coerce_change(spec, raw)
            except (TypeError, ValueError):
                errors[key] = "Enter a valid value."
        return changes, errors

    def _apply_live(self, settings: Settings) -> None:
        """Apply the observability toggles without waiting for a restart."""
        self.store.capture_payloads = settings.skye_ops_capture_payloads
        self.store.capture_media = settings.skye_ops_capture_media
        self.store.log_retention_days = settings.skye_ops_log_retention_days
        self.store.log_max_rows = settings.skye_ops_log_max_rows
        self.store.trace_retention_days = settings.skye_ops_trace_retention_days
        self.store.trace_max_rows = settings.skye_ops_trace_max_rows
        self.store.max_body_bytes = settings.skye_ops_max_body_bytes

    async def _require_owner(self, request: web.Request) -> int:
        if not self.config.skye_ops_enabled:
            raise web.HTTPNotFound()
        session = await self.auth.session(request.cookies.get(COOKIE_NAME))
        if session is None:
            raise web.HTTPUnauthorized(text="Sign in with Telegram.")
        if session.user_id not in self.config.skye_owner_ids:
            raise web.HTTPForbidden(text="This panel is for the operator.")
        return session.user_id

    @staticmethod
    async def _values(request: web.Request) -> dict[str, Any]:
        body = await _json(request)
        values = body.get("values")
        return values if isinstance(values, dict) else {}


_INLINE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".avif"}


def _field_payload(
    spec: FieldSpec, settings: Settings, overrides: dict[str, Any], configured: set[str]
) -> dict[str, Any]:
    overridden = spec.env in overrides
    source_value = overrides[spec.env] if overridden else getattr(settings, spec.key)
    secret = spec.secret
    payload: dict[str, Any] = {
        "key": spec.key,
        "env": spec.env,
        "label": spec.label,
        "description": spec.description,
        "group": spec.group,
        "kind": spec.kind,
        "secret": secret,
        "read_only": spec.read_only,
        "advanced": spec.advanced,
        "choices": list(spec.choices),
        "minimum": spec.minimum,
        "maximum": spec.maximum,
        "default": spec.default,
        "value": None if secret else _display(source_value),
        "is_set": bool(source_value),
        "override": overridden,
        "source": _source(spec, overrides, configured),
    }
    return payload


def _source(spec: FieldSpec, overrides: dict[str, Any], configured: set[str]) -> str:
    if spec.env in overrides:
        return "override"
    if spec.env in configured:
        return "environment"
    return "default"


def _display(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, frozenset | set | tuple | list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)


def _normalize(settings: Settings | None, changes: dict[str, Any]) -> dict[str, Any]:
    if settings is None:
        return {}
    result: dict[str, Any] = {}
    for key in changes:
        result[key] = _display(getattr(settings, key))
    return result


def _restart_required(changes: dict[str, Any]) -> bool:
    live = {
        "skye_ops_capture_payloads",
        "skye_ops_capture_media",
        "skye_ops_log_retention_days",
        "skye_ops_log_max_rows",
        "skye_ops_trace_retention_days",
        "skye_ops_trace_max_rows",
        "skye_ops_max_body_bytes",
    }
    return any(key not in live for key in changes)


def _configured_env(env_file: Path) -> set[str]:
    names = set(env_sources())
    try:
        text = env_file.read_text(encoding="utf-8")
    except OSError:
        return names
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        names.add(stripped.split("=", 1)[0].strip())
    return names


def _first(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        return None


async def _json(request: web.Request) -> dict[str, Any]:
    if not request.can_read_body:
        return {}
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _terminate() -> None:
    os.kill(os.getpid(), signal.SIGTERM)
