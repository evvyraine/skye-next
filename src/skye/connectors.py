from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx
import structlog
from agents import HostedMCPTool, Tool, ToolSearchTool
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from openai.types.responses.tool_param import Mcp

from .db import Database
from .models import (
    AppConnector,
    ConnectorKind,
    ConnectorShare,
    ConnectorSnapshot,
    CustomConnector,
    KnownGroup,
    RequestContext,
    Scope,
)
from .rich import RichMessages

log = structlog.get_logger()

COMPOSIO_API = "https://backend.composio.dev/api/v3.1"
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
BLOCKED_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "cookie",
    "cookie2",
    "upgrade",
    "te",
}
POPULAR_APPS: tuple[tuple[str, str], ...] = (
    ("gmail", "Gmail"),
    ("github", "GitHub"),
    ("slack", "Slack"),
    ("notion", "Notion"),
    ("googlecalendar", "Google Calendar"),
    ("googledrive", "Google Drive"),
    ("linear", "Linear"),
    ("outlook", "Outlook"),
)
MAX_CUSTOM_CONNECTORS = 8
MAX_HEADERS = 8
MAX_SHARES_PER_OWNER = 20
MAX_SHARES_PER_CHAT = 20
SENSITIVE_APPS = frozenset({"gmail", "googlecalendar", "googledrive", "outlook"})


class ConnectorWizard(StatesGroup):
    search = State()
    mcp_name = State()
    mcp_url = State()
    mcp_headers = State()
    edit = State()


class ConnectorError(ValueError):
    """User-facing connector failure."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ComposioAPI(Protocol):
    async def list_toolkits(self, query: str = "", limit: int = 8) -> list[AppConnector]: ...
    async def get_toolkit(self, slug: str) -> AppConnector: ...
    async def list_accounts(self, user_key: str) -> list[AppConnector]: ...
    async def create_link(self, user_key: str, slug: str) -> str: ...
    async def delete_account(self, user_key: str, account_id: str) -> None: ...
    async def create_session(
        self,
        user_key: str,
        slugs: Sequence[str],
        accounts: Mapping[str, str] | None = None,
    ) -> tuple[str, str, dict[str, str]]: ...
    async def delete_session(self, session_id: str) -> None: ...
    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ConnectorTools:
    tools: tuple[Tool, ...]
    labels: tuple[str, ...]


def composio_user_key(user_id: int) -> str:
    return f"tg:{user_id}"


def parse_headers(raw: str) -> dict[str, str]:
    text = raw.strip()
    if not text or text == ".":
        return {}
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise ConnectorError(
                "Headers must be Header: value lines, or a JSON object."
            ) from error
        if not isinstance(payload, dict):
            raise ConnectorError("JSON headers must be an object of name/value strings.")
        items = [(str(key), str(value)) for key, value in payload.items()]
    else:
        items = []
        for line in text.splitlines():
            if not line.strip():
                continue
            if ":" not in line:
                raise ConnectorError("Each header line must look like `Authorization: Bearer …`.")
            name, value = line.split(":", 1)
            items.append((name.strip(), value.strip()))
    if len(items) > MAX_HEADERS:
        raise ConnectorError(f"Use at most {MAX_HEADERS} headers.")
    headers: dict[str, str] = {}
    for name, value in items:
        if not name or not HEADER_NAME.fullmatch(name) or name.lower() in BLOCKED_HEADERS:
            raise ConnectorError(f"Header `{name}` is not allowed.")
        if not value or len(value) > 4096:
            raise ConnectorError(f"Header `{name}` needs a value of at most 4096 characters.")
        headers[name] = value
    return headers


def validate_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= 64:
        raise ConnectorError("Name must be 1–64 characters.")
    return name


def validate_mcp_url(raw: str) -> str:
    url = raw.strip()
    if len(url) > 2048:
        raise ConnectorError("The URL is too long.")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise ConnectorError("Use an https:// URL with no username or password.")
    host = parts.hostname or ""
    if not host or host.endswith(".local") or host in {"localhost", "metadata.google.internal"}:
        raise ConnectorError("That host is not allowed.")
    _reject_ip(host)
    return url


async def resolve_public_https(url: str) -> str:
    url = validate_mcp_url(url)
    host = urlsplit(url).hostname or ""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ConnectorError("Could not resolve that host.") from error
    if not infos:
        raise ConnectorError("Could not resolve that host.")
    for info in infos:
        _reject_ip(str(info[4][0]))
    return url


def mcp_label(prefix: str, raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    label = f"{prefix}_{cleaned}" if cleaned else prefix
    return label[:64]


def _reject_ip(value: str) -> None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ConnectorError("That host is not allowed.")


def composio_auth_headers(api_key: str) -> dict[str, str]:
    prefix = api_key.split("_", 1)[0].lower()
    header = "x-org-api-key" if prefix in {"oak", "oa", "org"} else "x-api-key"
    return {header: api_key, "accept": "application/json"}


class ComposioClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._http = httpx.AsyncClient(
            base_url=COMPOSIO_API,
            headers=composio_auth_headers(api_key),
            timeout=20,
        )
        self._auth_configs: dict[str, str] = {}

    async def list_toolkits(self, query: str = "", limit: int = 8) -> list[AppConnector]:
        params: dict[str, Any] = {"limit": limit, "sort_by": "usage", "type": "native"}
        if query:
            params["search"] = query
        data = await self._request("GET", "/toolkits", params=params)
        return [self._toolkit(item) for item in data.get("items", []) if isinstance(item, dict)]

    async def get_toolkit(self, slug: str) -> AppConnector:
        data = await self._request("GET", f"/toolkits/{slug}")
        return self._toolkit(data)

    async def list_accounts(self, user_key: str) -> list[AppConnector]:
        data = await self._request(
            "GET",
            "/connected_accounts",
            params={"user_ids": user_key, "statuses": "ACTIVE", "limit": 100},
        )
        apps: list[AppConnector] = []
        for item in data.get("items", []):
            if not isinstance(item, dict) or item.get("is_disabled"):
                continue
            raw_toolkit = item.get("toolkit")
            toolkit = raw_toolkit if isinstance(raw_toolkit, dict) else {}
            slug = str(toolkit.get("slug") or "").lower()
            if not slug:
                continue
            apps.append(
                AppConnector(
                    slug=slug,
                    name=slug,
                    status="connected",
                    account_id=str(item.get("id") or ""),
                )
            )
        return apps

    async def create_link(self, user_key: str, slug: str) -> str:
        auth_config_id = await self._auth_config(slug)
        data = await self._request(
            "POST",
            "/connected_accounts/link",
            json={"auth_config_id": auth_config_id, "user_id": user_key},
        )
        url = data.get("redirect_url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ConnectorError("Composio did not return a connect link.")
        return url

    async def delete_account(self, user_key: str, account_id: str) -> None:
        accounts = await self.list_accounts(user_key)
        if not any(item.account_id == account_id for item in accounts):
            raise ConnectorError("That connection is not yours.")
        await self._request("DELETE", f"/connected_accounts/{account_id}")

    async def create_session(
        self,
        user_key: str,
        slugs: Sequence[str],
        accounts: Mapping[str, str] | None = None,
    ) -> tuple[str, str, dict[str, str]]:
        body: dict[str, Any] = {
            "user_id": user_key,
            "toolkits": {"enable": list(slugs)},
            "manage_connections": {"enable": False},
            "workbench": {"enable": False},
            "mcp": True,
        }
        pinned = {slug: [account_id] for slug, account_id in (accounts or {}).items() if account_id}
        if pinned:
            body["connected_accounts"] = pinned
        try:
            data = await self._request("POST", "/tool_router/session", json=body)
        except ConnectorError as error:
            if error.status != 400:
                raise
            body.pop("mcp", None)
            data = await self._request("POST", "/tool_router/session", json=body)
        session_id = data.get("session_id")
        raw_mcp = data.get("mcp")
        mcp = raw_mcp if isinstance(raw_mcp, dict) else {}
        url = mcp.get("url")
        raw_headers = mcp.get("headers")
        headers = {
            str(key): str(value)
            for key, value in (raw_headers.items() if isinstance(raw_headers, dict) else [])
            if isinstance(key, str) and isinstance(value, str)
        }
        if not headers:
            headers = {
                key: value
                for key, value in composio_auth_headers(self.api_key).items()
                if key != "accept"
            }
        if not isinstance(session_id, str) or not isinstance(url, str):
            raise ConnectorError("Composio did not return a session.")
        return session_id, url, headers

    async def delete_session(self, session_id: str) -> None:
        with suppress_http():
            await self._request("DELETE", f"/tool_router/session/{session_id}")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _auth_config(self, slug: str) -> str:
        cached = self._auth_configs.get(slug)
        if cached:
            return cached
        listed = await self._request(
            "GET", "/auth_configs", params={"toolkit_slug": slug, "limit": 10}
        )
        for item in listed.get("items", []):
            if (
                isinstance(item, dict)
                and item.get("status") == "ENABLED"
                and item.get("is_composio_managed", True)
            ):
                auth_id = str(item["id"])
                self._auth_configs[slug] = auth_id
                return auth_id
        created = await self._request(
            "POST",
            "/auth_configs",
            json={
                "toolkit": {"slug": slug},
                "auth_config": {"type": "use_composio_managed_auth"},
            },
        )
        raw_auth = created.get("auth_config")
        auth = raw_auth if isinstance(raw_auth, dict) else {}
        created_id = auth.get("id")
        if not isinstance(created_id, str):
            raise ConnectorError("Could not prepare authentication for that app.")
        self._auth_configs[slug] = created_id
        return created_id

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(method, path, params=params, json=json)
        except httpx.HTTPError as error:
            log.warning(
                "composio_request_failed",
                method=method,
                path=path,
                error=type(error).__name__,
            )
            raise ConnectorError("Couldn't reach the connector service.") from error
        if response.status_code >= 400:
            message = _composio_message(response)
            slug, detail = _composio_error_fields(response)
            log.warning(
                "composio_request_rejected",
                method=method,
                path=path,
                status=response.status_code,
                error_slug=slug,
                error_detail=detail,
                key_prefix=self.api_key.split("_", 1)[0],
                key_length=len(self.api_key),
            )
            raise ConnectorError(message, status=response.status_code)
        if not response.content:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _toolkit(item: Mapping[str, Any]) -> AppConnector:
        raw_meta = item.get("meta")
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        slug = str(item.get("slug") or "").lower()
        return AppConnector(
            slug=slug,
            name=str(item.get("name") or slug),
            status="available",
            no_auth=bool(item.get("no_auth")),
            description=str(meta.get("description") or ""),
        )


class suppress_http:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        return exc_type is not None and issubclass(exc_type, ConnectorError)


def _composio_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code == 401:
        return (
            "Composio rejected the API key. Use a project API key from "
            "Settings → API keys, without quotes, then restart the bot."
        )
    if response.status_code == 400:
        return "Couldn't start the connected apps. Reconnect the app in settings, then try again."
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return "The connector service rejected that request."
        if isinstance(payload.get("message"), str):
            return "The connector service rejected that request."
    if response.status_code == 404:
        return "That app was not found."
    return "The connector service is unavailable right now."


def _composio_error_fields(response: httpx.Response) -> tuple[str | None, str | None]:
    try:
        payload = response.json()
    except ValueError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    error = payload.get("error")
    if isinstance(error, dict):
        slug = error.get("slug")
        detail = error.get("message")
        return (
            slug if isinstance(slug, str) else None,
            detail if isinstance(detail, str) else None,
        )
    return None, None


class ConnectorService:
    def __init__(self, database: Database, client: ComposioAPI | None) -> None:
        self.database = database
        self.client = client
        self._locks: dict[int, asyncio.Lock] = {}

    @property
    def configured(self) -> bool:
        return self.client is not None

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()

    async def snapshot(self, user_id: int) -> ConnectorSnapshot:
        custom = tuple(await self.database.list_custom_connectors(user_id))
        local = set(await self.database.list_user_toolkits(user_id))
        connected: dict[str, AppConnector] = {}
        if self.client is not None:
            try:
                for item in await self.client.list_accounts(composio_user_key(user_id)):
                    connected[item.slug] = item
            except ConnectorError:
                log.warning("composio_list_failed")
        names = {slug: label for slug, label in POPULAR_APPS}
        apps: list[AppConnector] = []
        seen: set[str] = set()
        for slug, item in connected.items():
            seen.add(slug)
            apps.append(replace(item, name=_app_name(slug, item.name, names)))
        for slug in sorted(local - seen):
            apps.append(
                AppConnector(
                    slug=slug,
                    name=_app_name(slug, slug, names),
                    status="connected",
                    no_auth=True,
                )
            )
        apps.sort(key=lambda item: item.name.lower())
        return ConnectorSnapshot(tuple(apps), custom)

    async def connected_count(self, user_id: int) -> int:
        return (await self.snapshot(user_id)).connected_count

    async def catalog(self, query: str = "") -> list[AppConnector]:
        if self.client is None:
            raise ConnectorError("App connections are not configured.")
        if query:
            return await self.client.list_toolkits(query, limit=8)
        popular: list[AppConnector] = []
        for slug, name in POPULAR_APPS:
            try:
                item = await self.client.get_toolkit(slug)
            except ConnectorError as error:
                if error.status == 401:
                    raise
                item = AppConnector(slug=slug, name=name, status="available")
            popular.append(replace(item, name=name or item.name))
        return popular

    async def app(self, user_id: int, slug: str) -> AppConnector:
        slug = _require_slug(slug)
        snapshot = await self.snapshot(user_id)
        for item in snapshot.apps:
            if item.slug == slug:
                return item
        if self.client is None:
            raise ConnectorError("App connections are not configured.")
        return await self.client.get_toolkit(slug)

    async def connect_link(self, user_id: int, slug: str) -> str | None:
        slug = _require_slug(slug)
        if self.client is None:
            raise ConnectorError("App connections are not configured.")
        toolkit = await self.client.get_toolkit(slug)
        if toolkit.no_auth:
            await self.database.add_user_toolkit(user_id, slug)
            await self.database.delete_composio_sessions(user_id)
            return None
        return await self.client.create_link(composio_user_key(user_id), slug)

    async def disconnect_app(self, user_id: int, slug: str) -> None:
        slug = _require_slug(slug)
        snapshot = await self.snapshot(user_id)
        current = next((item for item in snapshot.apps if item.slug == slug), None)
        if current is None:
            raise ConnectorError("That app is not connected.")
        if current.account_id and self.client is not None:
            await self.client.delete_account(composio_user_key(user_id), current.account_id)
        await self.database.remove_user_toolkit(user_id, slug)
        await self.database.delete_connector_shares(owner_id=user_id, kind="app", ref=slug)
        await self.database.delete_composio_sessions(user_id)

    async def add_custom(
        self, user_id: int, name: str, url: str, headers: Mapping[str, str]
    ) -> CustomConnector:
        existing = await self.database.list_custom_connectors(user_id)
        if len(existing) >= MAX_CUSTOM_CONNECTORS:
            raise ConnectorError(f"You can add at most {MAX_CUSTOM_CONNECTORS} custom servers.")
        connector = CustomConnector(
            id=uuid.uuid4().hex[:12],
            user_id=user_id,
            name=validate_name(name),
            url=await resolve_public_https(url),
            headers=dict(headers),
            enabled=True,
            created_at="",
            updated_at="",
        )
        return await self.database.save_custom_connector(connector)

    async def update_custom(
        self,
        user_id: int,
        connector_id: str,
        *,
        name: str | None = None,
        url: str | None = None,
        headers: Mapping[str, str] | None = None,
        enabled: bool | None = None,
    ) -> CustomConnector:
        current = await self.require_custom(user_id, connector_id)
        updated = CustomConnector(
            id=current.id,
            user_id=user_id,
            name=validate_name(name) if name is not None else current.name,
            url=await resolve_public_https(url) if url is not None else current.url,
            headers=dict(headers) if headers is not None else current.headers,
            enabled=current.enabled if enabled is None else enabled,
            created_at=current.created_at,
            updated_at=current.updated_at,
        )
        return await self.database.save_custom_connector(updated)

    async def delete_custom(self, user_id: int, connector_id: str) -> None:
        if not await self.database.delete_custom_connector(user_id, connector_id):
            raise ConnectorError("That connector is not yours.")
        await self.database.delete_connector_shares(
            owner_id=user_id, kind="custom", ref=connector_id
        )

    async def require_custom(self, user_id: int, connector_id: str) -> CustomConnector:
        connector = await self.database.get_custom_connector(user_id, connector_id)
        if connector is None:
            raise ConnectorError("That connector is not yours.")
        return connector

    async def shareable_groups(
        self, user_id: int, kind: ConnectorKind, ref: str
    ) -> list[KnownGroup]:
        shared = {
            item.chat_id
            for item in await self.database.list_connector_shares(
                owner_id=user_id, kind=kind, ref=ref
            )
        }
        known = await self.database.shareable_groups(user_id)
        return [item for item in known if item.chat_id not in shared]

    async def shares_for(
        self, owner_id: int, kind: ConnectorKind, ref: str
    ) -> list[ConnectorShare]:
        shares = await self.database.list_connector_shares(owner_id=owner_id, kind=kind, ref=ref)
        return [await self._resolve_share(item) for item in shares]

    async def group_shares(self, chat_id: int) -> list[ConnectorShare]:
        shares = await self.database.list_connector_shares(chat_id=chat_id)
        resolved = [await self._resolve_share(item) for item in shares]
        resolved.sort(key=lambda item: (item.name.lower(), item.owner_name.lower()))
        return resolved

    async def group_share_count(self, chat_id: int) -> int:
        return await self.database.count_connector_shares(chat_id=chat_id)

    async def require_share(self, share_id: str) -> ConnectorShare:
        share = await self.database.connector_share(share_id)
        if share is None:
            raise ConnectorError("That share is gone.")
        return await self._resolve_share(share)

    async def share(
        self,
        owner_id: int,
        owner_name: str,
        chat_id: int,
        kind: ConnectorKind,
        ref: str,
    ) -> ConnectorShare:
        if chat_id >= 0:
            raise ConnectorError("Share connectors with a group, not a private chat.")
        if await self.database.access_effect(Scope("chat", chat_id)) != "allow":
            raise ConnectorError("That group is not allowlisted.")
        name = await self._owned_name(owner_id, kind, ref)
        existing = await self.database.connector_share_by_target(chat_id, owner_id, kind, ref)
        if existing is not None:
            return await self._resolve_share(existing)
        if await self.database.count_connector_shares(owner_id=owner_id) >= MAX_SHARES_PER_OWNER:
            raise ConnectorError(f"You can share at most {MAX_SHARES_PER_OWNER} connectors.")
        if await self.database.count_connector_shares(chat_id=chat_id) >= MAX_SHARES_PER_CHAT:
            raise ConnectorError(
                f"This group can have at most {MAX_SHARES_PER_CHAT} shared connectors."
            )
        share = ConnectorShare(
            id=uuid.uuid4().hex[:12],
            chat_id=chat_id,
            chat_title="",
            owner_id=owner_id,
            owner_name=owner_name.strip() or "User",
            kind=kind,
            ref=ref,
            name=name,
            available=True,
            created_at="",
        )
        return await self._resolve_share(await self.database.save_connector_share(share))

    async def revoke(
        self, share_id: str, actor_id: int, *, admin_chat_id: int | None = None
    ) -> ConnectorShare:
        share = await self.require_share(share_id)
        if share.owner_id != actor_id and admin_chat_id != share.chat_id:
            raise PermissionError("Only the owner or a chat administrator can stop this share.")
        await self.database.delete_connector_share(share_id)
        return share

    async def hosted_tools(self, context: RequestContext) -> ConnectorTools:
        if context.chat_type == "private":
            return await self._private_tools(context.user_id)
        return await self._group_tools(context.chat_id)

    async def _private_tools(self, user_id: int) -> ConnectorTools:
        snapshot = await self.snapshot(user_id)
        tools: list[Tool] = []
        labels: list[str] = []
        connected = [item for item in snapshot.apps if item.status == "connected"]
        slugs = [item.slug for item in connected]
        if slugs and self.client is not None:
            try:
                url, headers = await self._session_url(user_id, slugs, _account_map(connected))
                tools.append(
                    _composio_tool(url, "composio", "Connected apps for this user.", headers)
                )
                labels.extend(item.name for item in connected)
            except ConnectorError:
                log.warning("composio_session_failed")
        for connector in snapshot.custom:
            if not connector.enabled:
                continue
            tools.append(_custom_tool(connector))
            labels.append(connector.name)
        if tools:
            tools.append(ToolSearchTool())
        return ConnectorTools(tuple(tools), tuple(labels))

    async def _group_tools(self, chat_id: int) -> ConnectorTools:
        shares = await self.group_shares(chat_id)
        tools: list[Tool] = []
        labels: list[str] = []
        slugs_by_owner: dict[int, list[str]] = {}
        owner_names: dict[int, str] = {}
        app_names: dict[tuple[int, str], str] = {}
        for share in shares:
            if not share.available:
                continue
            if share.kind == "app":
                slugs_by_owner.setdefault(share.owner_id, []).append(share.ref)
                owner_names[share.owner_id] = share.owner_name
                app_names[share.owner_id, share.ref] = share.name
                continue
            connector = await self.database.get_custom_connector(share.owner_id, share.ref)
            if connector is None or not connector.enabled:
                continue
            tools.append(_custom_tool(connector))
            labels.append(f"{connector.name} (shared by {share.owner_name})")
        if slugs_by_owner and self.client is not None:
            for owner_id, slugs in slugs_by_owner.items():
                try:
                    owner_apps = await self.snapshot(owner_id)
                    wanted = set(slugs)
                    accounts = _account_map(
                        [item for item in owner_apps.apps if item.slug in wanted]
                    )
                    url, headers = await self._session_url(owner_id, slugs, accounts)
                except ConnectorError:
                    log.warning("composio_session_failed")
                    continue
                tools.append(
                    _composio_tool(
                        url,
                        mcp_label("cmp", str(owner_id)),
                        f"Apps shared by {owner_names.get(owner_id, 'a member')}.",
                        headers,
                    )
                )
                labels.extend(
                    f"{app_names[owner_id, slug]} (shared by {owner_names[owner_id]})"
                    for slug in slugs
                )
        if tools:
            tools.append(ToolSearchTool())
        return ConnectorTools(tuple(tools), tuple(labels))

    async def _owned_name(self, owner_id: int, kind: ConnectorKind, ref: str) -> str:
        if kind == "app":
            app = await self.app(owner_id, ref)
            if app.status != "connected":
                raise ConnectorError("Connect this app in a private chat first.")
            return app.name
        connector = await self.require_custom(owner_id, ref)
        if not connector.enabled:
            raise ConnectorError("Turn this server on before sharing it.")
        return connector.name

    async def _resolve_share(self, share: ConnectorShare) -> ConnectorShare:
        name = share.ref
        available = False
        if share.kind == "app":
            snapshot = await self.snapshot(share.owner_id)
            current = next((item for item in snapshot.apps if item.slug == share.ref), None)
            if current is not None and current.status == "connected":
                name = current.name
                available = True
            else:
                name = _app_name(share.ref, share.ref, dict(POPULAR_APPS))
        else:
            connector = await self.database.get_custom_connector(share.owner_id, share.ref)
            if connector is not None:
                name = connector.name
                available = connector.enabled
        return ConnectorShare(
            id=share.id,
            chat_id=share.chat_id,
            chat_title=share.chat_title,
            owner_id=share.owner_id,
            owner_name=share.owner_name,
            kind=share.kind,
            ref=share.ref,
            name=name,
            available=available,
            created_at=share.created_at,
        )

    async def _session_url(
        self,
        user_id: int,
        slugs: Sequence[str],
        accounts: Mapping[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        if self.client is None:
            raise ConnectorError("App connections are not configured.")
        key = "mcp:" + ",".join(sorted(slugs))
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            stored = await self.database.composio_session(user_id, key)
            if stored:
                return stored[1], stored[2]
            session_id, url, headers = await self.client.create_session(
                composio_user_key(user_id), slugs, accounts
            )
            await self.database.save_composio_session(user_id, session_id, url, key, headers)
            return url, headers


def _app_name(slug: str, fallback: str, names: Mapping[str, str]) -> str:
    if slug in names:
        return names[slug]
    if fallback and fallback.lower() != slug:
        return fallback
    return slug.replace("-", " ").replace("_", " ").title()


def _require_slug(slug: str) -> str:
    value = slug.strip().lower()
    if not SLUG.fullmatch(value):
        raise ConnectorError("Unknown app.")
    return value


def _composio_tool(
    url: str, label: str, description: str, headers: Mapping[str, str] | None = None
) -> HostedMCPTool:
    config: dict[str, Any] = {
        "type": "mcp",
        "server_label": label,
        "server_url": url,
        "server_description": description,
        "require_approval": "never",
        "defer_loading": True,
    }
    if headers:
        config["headers"] = dict(headers)
    return HostedMCPTool(tool_config=cast(Mcp, config))


def _custom_tool(connector: CustomConnector) -> HostedMCPTool:
    config: dict[str, Any] = {
        "type": "mcp",
        "server_label": mcp_label("mcp", connector.id),
        "server_url": connector.url,
        "server_description": connector.name,
        "require_approval": "never",
        "defer_loading": True,
    }
    if connector.headers:
        config["headers"] = connector.headers
    return HostedMCPTool(tool_config=cast(Mcp, config))


def is_sensitive(kind: ConnectorKind, ref: str) -> bool:
    return kind == "app" and ref in SENSITIVE_APPS


def _account_map(apps: Sequence[AppConnector]) -> dict[str, str]:
    return {item.slug: item.account_id for item in apps if item.account_id}


def connectors_keyboard(
    snapshot: ConnectorSnapshot,
    *,
    configured: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="Add app", callback_data="conn:add"),
            InlineKeyboardButton(text="Add custom MCP", callback_data="conn:new"),
        ]
    ]
    if not configured:
        rows = [[InlineKeyboardButton(text="Add custom MCP", callback_data="conn:new")]]
    rows.extend(
        [
            InlineKeyboardButton(
                text=item.name,
                callback_data=f"conn:app:{item.slug}",
            )
        ]
        for item in snapshot.apps
    )
    rows.extend(
        [
            InlineKeyboardButton(
                text=f"{item.name} · MCP",
                callback_data=f"conn:open:{item.id}",
            )
        ]
        for item in snapshot.custom
    )
    rows.append([InlineKeyboardButton(text="‹ Back", callback_data="settings:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_keyboard(apps: Sequence[AppConnector], *, search: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=item.name,
                callback_data=f"conn:app:{item.slug}",
            )
        ]
        for item in apps
        if SLUG.fullmatch(item.slug)
    ]
    if search:
        rows.append([InlineKeyboardButton(text="Search", callback_data="conn:search")])
    rows.append([InlineKeyboardButton(text="‹ Back", callback_data="conn:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def app_keyboard(
    app: AppConnector,
    *,
    link: str | None = None,
    shares: Sequence[ConnectorShare] = (),
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if app.status == "connected":
        rows.append(
            [
                InlineKeyboardButton(text="Disconnect", callback_data=f"conn:off:{app.slug}"),
                InlineKeyboardButton(text="Reconnect", callback_data=f"conn:link:{app.slug}"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="Share with a group",
                    callback_data=f"conn:pick:app:{app.slug}",
                )
            ]
        )
    elif link:
        rows.append([InlineKeyboardButton(text=f"Connect {app.name}", url=link)])
        rows.append([InlineKeyboardButton(text="Done", callback_data=f"conn:chk:{app.slug}")])
    else:
        rows.append([InlineKeyboardButton(text="Connect", callback_data=f"conn:link:{app.slug}")])
    rows.extend(
        [
            InlineKeyboardButton(
                text=f"Stop sharing · {item.chat_title[:24]}",
                callback_data=f"conn:rv:{item.id}",
            )
        ]
        for item in shares
    )
    rows.append([InlineKeyboardButton(text="‹ Back", callback_data="conn:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def custom_keyboard(
    connector: CustomConnector,
    *,
    confirm: bool = False,
    shares: Sequence[ConnectorShare] = (),
) -> InlineKeyboardMarkup:
    if confirm:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Remove", callback_data=f"conn:yes:{connector.id}"),
                    InlineKeyboardButton(text="Cancel", callback_data=f"conn:open:{connector.id}"),
                ]
            ]
        )
    rows = [
        [
            InlineKeyboardButton(text="Edit name", callback_data=f"conn:name:{connector.id}"),
            InlineKeyboardButton(text="Edit URL", callback_data=f"conn:url:{connector.id}"),
        ],
        [
            InlineKeyboardButton(text="Edit headers", callback_data=f"conn:hdr:{connector.id}"),
            InlineKeyboardButton(
                text="Turn off" if connector.enabled else "Turn on",
                callback_data=f"conn:tog:{connector.id}",
            ),
        ],
    ]
    if connector.enabled:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Share with a group",
                    callback_data=f"conn:pick:mcp:{connector.id}",
                )
            ]
        )
    rows.extend(
        [
            InlineKeyboardButton(
                text=f"Stop sharing · {item.chat_title[:24]}",
                callback_data=f"conn:rv:{item.id}",
            )
        ]
        for item in shares
    )
    rows.append([InlineKeyboardButton(text="Remove", callback_data=f"conn:del:{connector.id}")])
    rows.append([InlineKeyboardButton(text="‹ Back", callback_data="conn:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_picker_keyboard(
    kind: ConnectorKind, ref: str, groups: Sequence[KnownGroup]
) -> InlineKeyboardMarkup:
    prefix = "app" if kind == "app" else "mcp"
    rows = [
        [
            InlineKeyboardButton(
                text=item.title[:40],
                callback_data=f"conn:ask:{prefix}:{ref}:{item.chat_id}",
            )
        ]
        for item in groups
    ]
    back = f"conn:app:{ref}" if kind == "app" else f"conn:open:{ref}"
    rows.append([InlineKeyboardButton(text="‹ Back", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_share_keyboard(
    kind: ConnectorKind, ref: str, chat_id: int, *, group: bool = False
) -> InlineKeyboardMarkup:
    prefix = "app" if kind == "app" else "mcp"
    back = "conn:mine" if group else f"conn:pick:{prefix}:{ref}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Share anyway" if is_sensitive(kind, ref) else "Share",
                    callback_data=f"conn:ok:{prefix}:{ref}:{chat_id}",
                ),
                InlineKeyboardButton(text="Cancel", callback_data=back),
            ]
        ]
    )


def group_connectors_keyboard(
    shares: Sequence[ConnectorShare],
    *,
    editable: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if editable:
        rows.append([InlineKeyboardButton(text="Attach one of mine", callback_data="conn:mine")])
    rows.extend(
        [
            InlineKeyboardButton(
                text=f"{item.name} · {item.owner_name}",
                callback_data=f"conn:gsee:{item.id}",
            )
        ]
        for item in shares
    )
    rows.append([InlineKeyboardButton(text="‹ Back", callback_data="settings:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_share_keyboard(share: ConnectorShare, *, can_revoke: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_revoke:
        rows.append(
            [InlineKeyboardButton(text="Stop sharing", callback_data=f"conn:rv:{share.id}")]
        )
    rows.append([InlineKeyboardButton(text="‹ Back", callback_data="conn:ghome")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mine_keyboard(snapshot: ConnectorSnapshot) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=item.name, callback_data=f"conn:ask:app:{item.slug}:here")]
        for item in snapshot.apps
        if item.status == "connected"
    ]
    rows.extend(
        [
            InlineKeyboardButton(
                text=f"{item.name} · MCP",
                callback_data=f"conn:ask:mcp:{item.id}:here",
            )
        ]
        for item in snapshot.custom
        if item.enabled
    )
    rows.append([InlineKeyboardButton(text="‹ Back", callback_data="conn:ghome")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Save", callback_data="conn:save"),
                InlineKeyboardButton(text="Cancel", callback_data="conn:home"),
            ]
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="conn:home")]]
    )


class ConnectorPanel:
    def __init__(
        self, service: ConnectorService, rich: RichMessages, bot: Bot | None = None
    ) -> None:
        self.service = service
        self.rich = rich
        self.bot = bot

    async def show_home(
        self,
        message: Message,
        context: RequestContext,
        *,
        editable: bool = False,
        edit: bool = True,
    ) -> None:
        if context.chat_type == "private":
            snapshot = await self.service.snapshot(context.user_id)
            content = self.rich.connectors(snapshot, configured=self.service.configured)
            markup = connectors_keyboard(snapshot, configured=self.service.configured)
        else:
            shares = await self.service.group_shares(context.chat_id)
            content = self.rich.group_connectors(shares)
            markup = group_connectors_keyboard(shares, editable=editable)
        if edit:
            await self.rich.edit(message, content, reply_markup=markup)
        else:
            await self.rich.send(message, content, reply_markup=markup)

    async def handle_callback(
        self,
        message: Message,
        context: RequestContext,
        action: Sequence[str],
        state: FSMContext,
        *,
        editable: bool,
    ) -> None:
        if context.chat_type == "private":
            await self._private_callback(message, context, action, state)
            return
        await self._group_callback(message, context, action, editable)

    async def _private_callback(
        self,
        message: Message,
        context: RequestContext,
        action: Sequence[str],
        state: FSMContext,
    ) -> None:
        user_id = context.user_id
        await state.update_data(user_id=user_id, panel_message_id=message.message_id)
        if action == ["home"]:
            await state.clear()
            await self.show_home(message, context)
        elif action == ["add"]:
            apps = await self.service.catalog()
            await self.rich.edit(
                message,
                self.rich.connector_catalog(apps, configured=self.service.configured),
                reply_markup=catalog_keyboard(apps, search=self.service.configured),
            )
        elif action == ["search"]:
            if not self.service.configured:
                raise ConnectorError("App connections are not configured.")
            await state.set_state(ConnectorWizard.search)
            await self.rich.edit(
                message,
                self.rich.connector_search_prompt(),
                reply_markup=cancel_keyboard(),
            )
        elif action == ["new"]:
            await state.set_state(ConnectorWizard.mcp_name)
            await state.set_data({"user_id": user_id, "panel_message_id": message.message_id})
            await self.rich.edit(
                message,
                self.rich.connector_name_prompt(),
                reply_markup=cancel_keyboard(),
            )
        elif action == ["save"]:
            await self._save_custom(message, user_id, state)
        elif len(action) == 2 and action[0] == "app":
            await self._show_app(message, user_id, action[1])
        elif len(action) == 2 and action[0] in {"link", "chk"}:
            await self._connect_or_refresh(message, user_id, action[1], start=action[0] == "link")
        elif len(action) == 2 and action[0] == "off":
            await self.service.disconnect_app(user_id, action[1])
            await self.show_home(message, context)
        elif len(action) == 2 and action[0] == "open":
            await self._show_custom(message, user_id, action[1])
        elif len(action) == 2 and action[0] in {"name", "url", "hdr"}:
            connector = await self.service.require_custom(user_id, action[1])
            field = {"name": "name", "url": "url", "hdr": "headers"}[action[0]]
            await state.set_state(ConnectorWizard.edit)
            await state.set_data(
                {
                    "user_id": user_id,
                    "connector_id": connector.id,
                    "field": field,
                    "panel_message_id": message.message_id,
                }
            )
            await self.rich.edit(
                message,
                self.rich.connector_edit_prompt(field, connector.name),
                reply_markup=cancel_keyboard(),
            )
        elif len(action) == 2 and action[0] == "tog":
            current = await self.service.require_custom(user_id, action[1])
            connector = await self.service.update_custom(
                user_id, current.id, enabled=not current.enabled
            )
            await self._show_custom(message, user_id, connector.id)
        elif len(action) == 2 and action[0] == "del":
            connector = await self.service.require_custom(user_id, action[1])
            await self.rich.edit(
                message,
                self.rich.connector_remove_confirm(connector.name),
                reply_markup=custom_keyboard(connector, confirm=True),
            )
        elif len(action) == 2 and action[0] == "yes":
            await self.service.delete_custom(user_id, action[1])
            await self.show_home(message, context)
        elif len(action) == 3 and action[0] == "pick":
            await self._show_picker(message, user_id, _kind(action[1]), action[2])
        elif len(action) == 4 and action[0] == "ask":
            await self._show_confirm(
                message, context, _kind(action[1]), action[2], _chat_id(action[3], context)
            )
        elif len(action) == 4 and action[0] == "ok":
            await self._share(
                message, context, _kind(action[1]), action[2], _chat_id(action[3], context)
            )
        elif len(action) == 2 and action[0] == "rv":
            share = await self.service.revoke(action[1], user_id)
            if share.kind == "app":
                await self._show_app(message, user_id, share.ref)
            else:
                await self._show_custom(message, user_id, share.ref)
        else:
            raise ConnectorError("Unknown connector action.")

    async def _group_callback(
        self,
        message: Message,
        context: RequestContext,
        action: Sequence[str],
        editable: bool,
    ) -> None:
        if action in (["home"], ["ghome"]):
            await self.show_home(message, context, editable=editable)
        elif action == ["mine"]:
            if not editable:
                raise PermissionError("Only chat administrators can attach a connector here.")
            snapshot = await self.service.snapshot(context.user_id)
            await self.rich.edit(
                message,
                self.rich.connector_mine(snapshot),
                reply_markup=mine_keyboard(snapshot),
            )
        elif len(action) == 4 and action[0] == "ask":
            if not editable:
                raise PermissionError("Only chat administrators can attach a connector here.")
            await self._show_confirm(
                message, context, _kind(action[1]), action[2], _chat_id(action[3], context)
            )
        elif len(action) == 4 and action[0] == "ok":
            if not editable:
                raise PermissionError("Only chat administrators can attach a connector here.")
            await self._share(
                message, context, _kind(action[1]), action[2], _chat_id(action[3], context)
            )
        elif len(action) == 2 and action[0] == "gsee":
            share = await self.service.require_share(action[1])
            if share.chat_id != context.chat_id:
                raise ConnectorError("That share is not in this group.")
            can_revoke = share.owner_id == context.user_id or editable
            await self.rich.edit(
                message,
                self.rich.connector_share(share),
                reply_markup=group_share_keyboard(share, can_revoke=can_revoke),
            )
        elif len(action) == 2 and action[0] == "rv":
            share = await self.service.revoke(
                action[1],
                context.user_id,
                admin_chat_id=context.chat_id if editable else None,
            )
            await self.show_home(message, context, editable=editable)
        else:
            raise ConnectorError("Manage this connector in a private chat.")

    async def handle_wizard(
        self, message: Message, context: RequestContext, state: FSMContext
    ) -> None:
        if context.chat_type != "private":
            await state.clear()
            await self.rich.send(message, "Connect apps in a private chat with Skye.")
            return
        data = await state.get_data()
        if data.get("user_id") not in {None, context.user_id}:
            await state.clear()
            await self.rich.send(message, "That connector draft belongs to another chat.")
            return
        current = await state.get_state()
        text = (message.text or "").strip()
        if not text:
            await self.rich.send(message, "Send text for this step.")
            return
        try:
            if current == ConnectorWizard.search.state:
                apps = await self.service.catalog(text)
                if not apps:
                    await self.rich.send(message, "No apps matched that name.")
                    return
                await state.clear()
                await self.rich.send(
                    message,
                    self.rich.connector_catalog(apps, configured=True, query=text),
                    reply_markup=catalog_keyboard(apps, search=False),
                )
            elif current == ConnectorWizard.mcp_name.state:
                await state.update_data(name=validate_name(text), user_id=context.user_id)
                await state.set_state(ConnectorWizard.mcp_url)
                await self.rich.send(
                    message,
                    self.rich.connector_url_prompt(),
                    reply_markup=cancel_keyboard(),
                )
            elif current == ConnectorWizard.mcp_url.state:
                await state.update_data(url=await resolve_public_https(text))
                await state.set_state(ConnectorWizard.mcp_headers)
                await self.rich.send(
                    message,
                    self.rich.connector_headers_prompt(skip=True),
                    reply_markup=cancel_keyboard(),
                )
            elif current == ConnectorWizard.mcp_headers.state:
                headers = parse_headers(text)
                await state.update_data(headers=headers)
                data = await state.get_data()
                await self.rich.send(
                    message,
                    self.rich.connector_preview(
                        cast(str, data["name"]),
                        cast(str, data["url"]),
                        headers,
                    ),
                    reply_markup=preview_keyboard(),
                )
            elif current == ConnectorWizard.edit.state:
                await self._apply_edit(message, context.user_id, state, text)
            else:
                await state.clear()
        except ConnectorError as error:
            await self.rich.send(message, str(error))

    async def _connect_or_refresh(
        self, message: Message, user_id: int, slug: str, *, start: bool
    ) -> None:
        if start:
            link = await self.service.connect_link(user_id, slug)
            if link is None:
                await self._show_app(message, user_id, slug)
                return
            app = await self.service.app(user_id, slug)
            await self.rich.edit(
                message,
                self.rich.connector_app(app, connecting=True),
                reply_markup=app_keyboard(app, link=link),
            )
            return
        app = await self.service.app(user_id, slug)
        if app.status != "connected":
            raise ConnectorError("Not connected yet. Finish the page, then tap Done.")
        await self._show_app(message, user_id, slug)

    async def _save_custom(self, message: Message, user_id: int, state: FSMContext) -> None:
        data = await state.get_data()
        name = data.get("name")
        url = data.get("url")
        headers = data.get("headers")
        if not isinstance(name, str) or not isinstance(url, str) or not isinstance(headers, dict):
            raise ConnectorError("This connector draft is no longer active.")
        await self.service.add_custom(user_id, name, url, cast(dict[str, str], headers))
        await state.clear()
        await self.show_home(message, RequestContext(message.chat.id, "private", user_id))

    async def _apply_edit(
        self, message: Message, user_id: int, state: FSMContext, text: str
    ) -> None:
        data = await state.get_data()
        connector_id = data.get("connector_id")
        field = data.get("field")
        if not isinstance(connector_id, str) or field not in {"name", "url", "headers"}:
            raise ConnectorError("This edit is no longer active.")
        if text == ".":
            connector = await self.service.require_custom(user_id, connector_id)
        elif field == "name":
            connector = await self.service.update_custom(user_id, connector_id, name=text)
        elif field == "url":
            connector = await self.service.update_custom(user_id, connector_id, url=text)
        elif text == "-":
            connector = await self.service.update_custom(user_id, connector_id, headers={})
        else:
            connector = await self.service.update_custom(
                user_id, connector_id, headers=parse_headers(text)
            )
        await state.clear()
        shares = await self.service.shares_for(user_id, "custom", connector.id)
        await self.rich.send(
            message,
            self.rich.connector_custom(connector, shares=shares),
            reply_markup=custom_keyboard(connector, shares=shares),
        )

    async def _show_app(self, message: Message, user_id: int, slug: str) -> None:
        app = await self.service.app(user_id, slug)
        shares = (
            await self.service.shares_for(user_id, "app", slug) if app.status == "connected" else []
        )
        await self.rich.edit(
            message,
            self.rich.connector_app(app, shares=shares),
            reply_markup=app_keyboard(app, shares=shares),
        )

    async def _show_custom(self, message: Message, user_id: int, connector_id: str) -> None:
        connector = await self.service.require_custom(user_id, connector_id)
        shares = await self.service.shares_for(user_id, "custom", connector.id)
        await self.rich.edit(
            message,
            self.rich.connector_custom(connector, shares=shares),
            reply_markup=custom_keyboard(connector, shares=shares),
        )

    async def _show_picker(
        self, message: Message, user_id: int, kind: ConnectorKind, ref: str
    ) -> None:
        name = await self.service._owned_name(user_id, kind, ref)
        groups = await self.service.shareable_groups(user_id, kind, ref)
        await self.rich.edit(
            message,
            self.rich.connector_picker(name, groups),
            reply_markup=group_picker_keyboard(kind, ref, groups),
        )

    async def _show_confirm(
        self,
        message: Message,
        context: RequestContext,
        kind: ConnectorKind,
        ref: str,
        chat_id: int,
    ) -> None:
        await self._require_member(chat_id, context.user_id)
        name = await self.service._owned_name(context.user_id, kind, ref)
        title = await self._group_title(chat_id)
        await self.rich.edit(
            message,
            self.rich.connector_share_confirm(name, title, sensitive=is_sensitive(kind, ref)),
            reply_markup=confirm_share_keyboard(
                kind, ref, chat_id, group=context.chat_type != "private"
            ),
        )

    async def _share(
        self,
        message: Message,
        context: RequestContext,
        kind: ConnectorKind,
        ref: str,
        chat_id: int,
    ) -> None:
        await self._require_member(chat_id, context.user_id)
        await self.service.share(context.user_id, context.display_name, chat_id, kind, ref)
        if context.chat_type == "private":
            if kind == "app":
                await self._show_app(message, context.user_id, ref)
            else:
                await self._show_custom(message, context.user_id, ref)
            return
        await self.show_home(message, context, editable=True)

    async def _require_member(self, chat_id: int, user_id: int) -> None:
        if self.bot is None:
            return
        try:
            member = await self.bot.get_chat_member(chat_id, user_id)
        except Exception as error:
            log.warning("share_membership_failed", error=type(error).__name__)
            raise ConnectorError("Skye cannot see that group right now.") from error
        if member.status in {"left", "kicked"}:
            raise PermissionError("You are not in that group.")

    async def _group_title(self, chat_id: int) -> str:
        return await self.service.database.chat_title(chat_id)


def _kind(raw: str) -> ConnectorKind:
    if raw == "app":
        return "app"
    if raw == "mcp":
        return "custom"
    raise ConnectorError("Unknown connector action.")


def _chat_id(raw: str, context: RequestContext) -> int:
    if raw == "here":
        if context.chat_type == "private":
            raise ConnectorError("Share connectors with a group, not a private chat.")
        return context.chat_id
    try:
        chat_id = int(raw)
    except ValueError as error:
        raise ConnectorError("Unknown group.") from error
    if chat_id >= 0:
        raise ConnectorError("Share connectors with a group, not a private chat.")
    return chat_id
