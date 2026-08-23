from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiohttp import BodyPartReader, web
from openai import AsyncOpenAI

from .access import AccessService
from .attachments import (
    is_audio_upload,
    openai_file_parts,
    transcribe_audio,
    upload_openai_file,
)
from .auth import COOKIE_NAME, OIDC_COOKIE, AuthError, TelegramAuth
from .automations import (
    AutomationService,
    authorization_matches,
    sanitize_webhook_body,
)
from .billing import BillingService
from .config import Settings
from .db import Database
from .models import Automation, RequestContext, WebFile, WebSession
from .projects import (
    PROJECT_COLORS,
    PROJECT_ICONS,
    ProjectService,
    file_payload,
    message_payload,
    project_payload,
)
from .quota import AllowanceError, QuotaService
from .runtime import AgentRuntime, RunEvent, RunOutput, leftover_reply, web_run_key

log = structlog.get_logger()

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class WebApp:
    def __init__(
        self,
        config: Settings,
        database: Database,
        access: AccessService,
        runtime: AgentRuntime,
        projects: ProjectService,
        auth: TelegramAuth,
        client: AsyncOpenAI,
        automations: AutomationService | None = None,
        fire_automation: Callable[[Automation, str], None] | None = None,
    ) -> None:
        self.config = config
        self.database = database
        self.access = access
        self.runtime = runtime
        self.projects = projects
        self.auth = auth
        self.client = client
        self.automations = automations
        self.fire_automation = fire_automation
        self.billing = BillingService(database, config.telegram_bot_token)
        self.quota = QuotaService(database, self.billing, access)
        self.app = web.Application(
            client_max_size=config.skye_max_attachment_bytes + 1_000_000,
            middlewares=[self._headers],
        )
        self._routes()

    def _routes(self) -> None:
        add = self.app.router.add_route
        add("GET", "/auth/telegram", self.login_start)
        add("GET", "/auth/callback", self.login_callback)
        add("POST", "/auth/logout", self.logout)
        add("GET", "/api/health", self.health)
        add("GET", "/api/me", self.me)
        add("GET", "/api/projects", self.list_projects)
        add("POST", "/api/projects", self.create_project)
        add("GET", "/api/projects/{id}", self.get_project)
        add("PATCH", "/api/projects/{id}", self.update_project)
        add("DELETE", "/api/projects/{id}", self.delete_project)
        add("POST", "/api/projects/{id}/pin", self.pin_project)
        add("POST", "/api/projects/{id}/reset", self.reset_project)
        add("POST", "/api/projects/{id}/stop", self.stop_project)
        add("GET", "/api/projects/{id}/messages", self.list_messages)
        add("POST", "/api/projects/{id}/messages", self.send_message)
        add("GET", "/api/search", self.search)
        add("POST", "/api/transcribe", self.transcribe)
        add("GET", "/api/files/{id}", self.get_file)
        add("GET", "/api/files/{id}/thumbnail", self.get_thumbnail)
        add("GET", "/api/meta", self.meta)
        add("POST", "/automations/{id}/hook", self.automation_hook)

    @web.middleware
    async def _headers(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        response = await handler(request)
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def automation_hook(self, request: web.Request) -> web.Response:
        if self.automations is None:
            raise web.HTTPNotFound()
        item = await self.automations.get(request.match_info["id"])
        if item is None or item.kind != "webhook":
            raise web.HTTPNotFound()
        if not authorization_matches(
            item.webhook_authorization or "", request.headers.get("Authorization")
        ):
            raise web.HTTPUnauthorized()
        if not item.enabled:
            raise web.HTTPNotFound()
        body = sanitize_webhook_body(await request.read())
        if self.fire_automation is not None:
            self.fire_automation(item, body)
        return web.Response(status=202)

    async def meta(self, request: web.Request) -> web.Response:
        return web.json_response({"icons": list(PROJECT_ICONS), "colors": list(PROJECT_COLORS)})

    async def login_start(self, request: web.Request) -> web.StreamResponse:
        try:
            url, packed = self.auth.login_url(self._origin())
        except AuthError as error:
            raise web.HTTPServiceUnavailable(text=error.message) from error
        response = web.HTTPFound(url)
        response.set_cookie(
            OIDC_COOKIE,
            packed,
            httponly=True,
            secure=self._secure(),
            samesite="Lax",
            path="/",
            max_age=600,
        )
        return response

    async def login_callback(self, request: web.Request) -> web.StreamResponse:
        error = request.query.get("error")
        if error:
            raise web.HTTPBadRequest(text="Telegram login was cancelled.")
        code = request.query.get("code", "")
        state = request.query.get("state", "")
        try:
            session = await self.auth.finish(
                self._origin(), code, state, request.cookies.get(OIDC_COOKIE)
            )
        except AuthError as err:
            raise web.HTTPBadRequest(text=err.message) from err
        context = self._context(session)
        if not await self.access.allowed(context):
            response = web.HTTPFound("/?denied=1")
            response.del_cookie(OIDC_COOKIE, path="/")
            return response
        await self.projects.ensure_skye(session.user_id)
        response = web.HTTPFound("/")
        response.del_cookie(OIDC_COOKIE, path="/")
        response.set_cookie(COOKIE_NAME, session.id, **self.auth.cookie_kwargs())
        return response

    async def logout(self, request: web.Request) -> web.Response:
        await self.auth.logout(request.cookies.get(COOKIE_NAME))
        response = web.json_response({"ok": True})
        response.del_cookie(COOKIE_NAME, path="/")
        return response

    async def me(self, request: web.Request) -> web.Response:
        session = await self._session(request)
        if session is None:
            return web.json_response({"user": None}, status=401)
        context = self._context(session)
        allowed = await self.access.allowed(context)
        return web.json_response(
            {
                "user": {
                    "id": session.user_id,
                    "name": session.display_name,
                    "username": session.username,
                }
                if allowed
                else None,
                "allowed": allowed,
            }
        )

    async def list_projects(self, request: web.Request) -> web.Response:
        session = await self._require_user(request)
        projects = await self.projects.list(session.user_id)
        return web.json_response({"projects": [project_payload(item) for item in projects]})

    async def create_project(self, request: web.Request) -> web.Response:
        session = await self._require_user(request)
        body = await self._json(request)
        try:
            project = await self.projects.create(
                session.user_id,
                name=str(body.get("name") or ""),
                instructions=str(body.get("instructions") or ""),
                icon=str(body.get("icon") or "sparkles"),
                color=str(body.get("color") or "zinc"),
            )
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        return web.json_response({"project": project_payload(project)}, status=201)

    async def get_project(self, request: web.Request) -> web.Response:
        session = await self._require_user(request)
        try:
            project = await self.projects.require(session.user_id, request.match_info["id"])
        except LookupError as error:
            raise web.HTTPNotFound(text=str(error)) from error
        return web.json_response({"project": project_payload(project)})

    async def update_project(self, request: web.Request) -> web.Response:
        session = await self._require_user(request)
        body = await self._json(request)
        try:
            project = await self.projects.update(
                session.user_id,
                request.match_info["id"],
                name=self._optional_str(body, "name"),
                instructions=self._optional_str(body, "instructions"),
                icon=self._optional_str(body, "icon"),
                color=self._optional_str(body, "color"),
                pinned=body["pinned"] if isinstance(body.get("pinned"), bool) else None,
            )
        except LookupError as error:
            raise web.HTTPNotFound(text=str(error)) from error
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        return web.json_response({"project": project_payload(project)})

    async def delete_project(self, request: web.Request) -> web.Response:
        session = await self._require_user(request)
        try:
            await self.projects.delete(session.user_id, request.match_info["id"])
        except LookupError as error:
            raise web.HTTPNotFound(text=str(error)) from error
        except PermissionError as error:
            raise web.HTTPForbidden(text=str(error)) from error
        return web.json_response({"ok": True})

    async def pin_project(self, request: web.Request) -> web.Response:
        session = await self._require_user(request)
        try:
            current = await self.projects.require(session.user_id, request.match_info["id"])
            project = await self.projects.update(
                session.user_id, current.id, pinned=not current.pinned
            )
        except LookupError as error:
            raise web.HTTPNotFound(text=str(error)) from error
        return web.json_response({"project": project_payload(project)})

    async def reset_project(self, request: web.Request) -> web.Response:
        session = await self._require_user(request)
        try:
            project = await self.projects.reset(session.user_id, request.match_info["id"])
        except LookupError as error:
            raise web.HTTPNotFound(text=str(error)) from error
        return web.json_response({"project": project_payload(project)})

    async def stop_project(self, request: web.Request) -> web.Response:
        session = await self._require_user(request)
        try:
            await self.projects.require(session.user_id, request.match_info["id"])
        except LookupError as error:
            raise web.HTTPNotFound(text=str(error)) from error
        stopped = self.runtime.stop_key(web_run_key(request.match_info["id"]))
        return web.json_response({"stopped": stopped})

    async def list_messages(self, request: web.Request) -> web.Response:
        session = await self._require_user(request)
        project_id = request.match_info["id"]
        try:
            await self.projects.require(session.user_id, project_id)
        except LookupError as error:
            raise web.HTTPNotFound(text=str(error)) from error
        messages = await self.database.list_web_messages(session.user_id, project_id)
        files = await self.database.list_web_files(session.user_id, project_id)
        return web.json_response(
            {
                "messages": [message_payload(item) for item in messages],
                "files": [file_payload(item) for item in files],
            }
        )

    async def send_message(self, request: web.Request) -> web.StreamResponse:
        session = await self._require_user(request)
        project_id = request.match_info["id"]
        try:
            project = await self.projects.require(session.user_id, project_id)
        except LookupError as error:
            raise web.HTTPNotFound(text=str(error)) from error
        text, uploads = await self._read_turn(request)
        if not text and not uploads:
            raise web.HTTPBadRequest(text="Write a message or attach a file.")
        context = self._context(session)
        try:
            await self.quota.check(context)
        except AllowanceError as error:
            raise web.HTTPTooManyRequests(text=error.message) from error
        settings = await self.database.get_settings(context.scope)
        settings = await self.billing.clamp_settings(context, settings, self.access)
        content: list[dict[str, Any]] = []
        file_ids: list[str] = []
        openai_file_ids: list[str] = []
        uploaded_files: list[WebFile] = []
        preview_bits = [text] if text else []
        if text:
            content.append({"type": "input_text", "text": text})
        for filename, mime, data in uploads:
            if len(data) > self.config.skye_max_attachment_bytes:
                raise web.HTTPBadRequest(
                    text=(
                        "That file is too large "
                        f"(maximum {self.config.skye_max_attachment_bytes // 1024 // 1024} MB)."
                    )
                )
            transcript: str | None = None
            kind: str = "document"
            if is_audio_upload(filename, mime):
                transcript = await transcribe_audio(
                    self.client, self.config.skye_transcription_model, filename, data
                )
                kind = "upload"
            elif mime.startswith("image/"):
                kind = "image"
            saved = await self.projects.save_file(
                session.user_id,
                project_id,
                filename=filename,
                mime=mime or "application/octet-stream",
                data=data,
                kind="image" if kind == "image" else "upload",
            )
            file_ids.append(saved.id)
            uploaded_files.append(saved)
            openai_id = await upload_openai_file(self.client, filename, mime, data)
            if openai_id:
                openai_file_ids.append(openai_id)
            content.extend(openai_file_parts(filename, mime, data, transcript, openai_id))
            preview_bits.append(filename)
        user_message = await self.projects.add_message(
            session.user_id,
            project_id,
            role="user",
            text=text or ", ".join(preview_bits),
            file_ids=tuple(file_ids),
        )
        conversation_id = await self.projects.conversation_id(project)
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)
        for saved in uploaded_files:
            await self._sse(response, "file", file_payload(saved))
        await self._sse(response, "user", message_payload(user_message))
        assistant_file_ids: list[str] = []
        seen_tools: set[str] = set()
        sent = 0
        last_assistant: dict[str, Any] | None = None

        async def on_text(_text: str) -> None:
            return

        async def persist_assistant(text: str, file_ids: tuple[str, ...] = ()) -> None:
            nonlocal last_assistant
            assistant = await self.projects.add_message(
                session.user_id,
                project_id,
                role="assistant",
                text=text,
                file_ids=file_ids,
            )
            last_assistant = message_payload(assistant)
            await self._sse(response, "assistant", last_assistant)

        async def on_reply(text: str, _reply_to: int | None = None) -> None:
            nonlocal sent
            sent += 1
            await persist_assistant(text)

        async def on_voice(audio: bytes, _reply_to: int | None = None) -> None:
            nonlocal sent
            saved = await self.projects.save_file(
                session.user_id,
                project_id,
                filename="voice.ogg",
                mime="audio/ogg",
                data=audio,
                kind="document",
            )
            await self._sse(response, "file", file_payload(saved))
            await persist_assistant("", (saved.id,))
            sent += 1

        async def on_event(event: RunEvent) -> None:
            if event.kind == "tool":
                payload = {
                    "id": event.tool_id,
                    "name": event.tool_name,
                    "label": event.tool_label,
                    "status": event.tool_status,
                }
                await self._sse(response, "tool", payload)
                if event.tool_id not in seen_tools and event.tool_status == "done":
                    seen_tools.add(event.tool_id)
                    await self.projects.add_message(
                        session.user_id,
                        project_id,
                        role="tool",
                        text=event.tool_label,
                        tool_name=event.tool_name,
                        tool_status="done",
                    )
            elif event.kind == "image" and event.image:
                saved = await self.projects.save_file(
                    session.user_id,
                    project_id,
                    filename="image.png",
                    mime="image/png",
                    data=event.image,
                    kind="image",
                )
                assistant_file_ids.append(saved.id)
                await self._sse(response, "image", file_payload(saved))

        try:
            payload: Any = [{"role": "user", "content": content}]
            output = await self.runtime.run(
                context,
                settings,
                payload,
                on_text,
                run_key=web_run_key(project_id),
                conversation_id=conversation_id,
                extra_instructions=project.instructions,
                on_event=on_event,
                on_reply=on_reply,
                on_voice=on_voice,
                input_file_ids=tuple(openai_file_ids),
                awaiting_reply=True,
            )
            await self.quota.record(context, output.usage_tokens)
        except asyncio.CancelledError:
            if assistant_file_ids:
                await persist_assistant("", tuple(assistant_file_ids))
            await self._sse(response, "error", {"message": "Stopped."})
            await response.write_eof()
            return response
        except Exception:
            log.exception("web_run_failed", project_id=project_id)
            if assistant_file_ids:
                await persist_assistant("", tuple(assistant_file_ids))
            await self._sse(response, "error", {"message": "Something went wrong. Try again."})
            await response.write_eof()
            return response

        for generated in output.files:
            saved = await self.projects.save_file(
                session.user_id,
                project_id,
                filename=generated.filename,
                mime="application/octet-stream",
                data=generated.data,
                kind="document",
            )
            assistant_file_ids.append(saved.id)
            await self._sse(response, "file", file_payload(saved))
        if assistant_file_ids:
            await persist_assistant("", tuple(assistant_file_ids))
        elif max(output.sent, sent) == 0:
            leftover = leftover_reply(
                RunOutput(output.text, (), sent=0),
                awaiting_reply=True,
            )
            if leftover:
                await persist_assistant(leftover)
        await self._sse(response, "done", last_assistant or {"text": ""})
        await response.write_eof()
        return response

    async def search(self, request: web.Request) -> web.Response:
        session = await self._require_user(request)
        query = str(request.query.get("q") or "").strip()
        projects, messages = await self.database.search_web(session.user_id, query)
        return web.json_response(
            {
                "projects": [project_payload(item) for item in projects],
                "messages": [
                    {"project": project_payload(project), "message": message_payload(message)}
                    for project, message in messages
                ],
            }
        )

    async def transcribe(self, request: web.Request) -> web.Response:
        await self._require_user(request)
        data, filename, mime = await self._one_file(request)
        if not is_audio_upload(filename, mime):
            raise web.HTTPBadRequest(text="Send an audio file to transcribe.")
        if len(data) > self.config.skye_max_attachment_bytes:
            raise web.HTTPBadRequest(text="That recording is too large.")
        text = await transcribe_audio(
            self.client, self.config.skye_transcription_model, filename, data
        )
        return web.json_response({"text": text})

    async def get_file(self, request: web.Request) -> web.StreamResponse:
        session = await self._require_user(request)
        meta = await self.database.web_file(session.user_id, request.match_info["id"])
        if meta is None:
            raise web.HTTPNotFound(text="File not found.")
        data = self.projects.file_bytes(session.user_id, meta.id)
        if data is None:
            raise web.HTTPNotFound(text="File not found.")
        return web.Response(
            body=data,
            content_type=meta.mime,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    async def get_thumbnail(self, request: web.Request) -> web.StreamResponse:
        session = await self._require_user(request)
        meta = await self.database.web_file(session.user_id, request.match_info["id"])
        if meta is None or not meta.mime.startswith("image/"):
            raise web.HTTPNotFound(text="Thumbnail not found.")
        data = await asyncio.to_thread(self.projects.thumbnail_bytes, session.user_id, meta.id)
        if data is None:
            raise web.HTTPNotFound(text="Thumbnail not found.")
        return web.Response(
            body=data,
            content_type="image/webp",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    async def _require_user(self, request: web.Request) -> WebSession:
        session = await self._session(request)
        if session is None:
            raise web.HTTPUnauthorized(text="Please sign in with Telegram.")
        if not await self.access.allowed(self._context(session)):
            raise web.HTTPForbidden(text="This bot is private.")
        return session

    async def _session(self, request: web.Request) -> WebSession | None:
        return await self.auth.session(request.cookies.get(COOKIE_NAME))

    def _context(self, session: WebSession) -> RequestContext:
        return RequestContext(
            chat_id=session.user_id,
            chat_type="private",
            user_id=session.user_id,
            username=session.username,
            display_name=session.display_name,
        )

    def _origin(self) -> str:
        return (self.config.skye_web_origin or "http://127.0.0.1:5173").rstrip("/")

    def _secure(self) -> bool:
        return self._origin().startswith("https://")

    @staticmethod
    async def _json(request: web.Request) -> dict[str, Any]:
        if request.content_type.startswith("application/json"):
            payload = await request.json()
            return payload if isinstance(payload, dict) else {}
        return {}

    @staticmethod
    def _optional_str(body: dict[str, Any], key: str) -> str | None:
        if key not in body:
            return None
        return str(body[key])

    async def _read_turn(self, request: web.Request) -> tuple[str, list[tuple[str, str, bytes]]]:
        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            text = ""
            uploads: list[tuple[str, str, bytes]] = []
            while True:
                part = await reader.next()
                if part is None:
                    break
                if not isinstance(part, BodyPartReader):
                    continue
                name = part.name or ""
                if name == "text":
                    text = (await part.text()).strip()
                    continue
                if name == "files":
                    filename = part.filename or "file"
                    mime = part.headers.get("Content-Type", "application/octet-stream")
                    data = await part.read(decode=False)
                    uploads.append((filename, mime, data))
            return text, uploads
        body = await self._json(request)
        return str(body.get("text") or "").strip(), []

    async def _one_file(self, request: web.Request) -> tuple[bytes, str, str]:
        if not request.content_type.startswith("multipart/"):
            raise web.HTTPBadRequest(text="Send an audio file.")
        reader = await request.multipart()
        part = await reader.next()
        if not isinstance(part, BodyPartReader):
            raise web.HTTPBadRequest(text="Send an audio file.")
        filename = part.filename or "audio.webm"
        mime = part.headers.get("Content-Type", "audio/webm")
        data = await part.read(decode=False)
        return data, filename, mime

    @staticmethod
    async def _sse(response: web.StreamResponse, event: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        await response.write(f"event: {event}\ndata: {data}\n\n".encode())


async def serve_web(web_app: WebApp) -> web.AppRunner:
    runner = web.AppRunner(web_app.app)
    await runner.setup()
    site = web.TCPSite(runner, web_app.config.skye_web_host, web_app.config.skye_web_port)
    await site.start()
    log.info(
        "web_listen",
        host=web_app.config.skye_web_host,
        port=web_app.config.skye_web_port,
        origin=web_app.config.skye_web_origin,
    )
    return runner
