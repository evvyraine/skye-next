"""Capture full model traffic at the HTTP transport and attach it to a run.

The OpenAI client is the only thing behind this transport, so it records chat
completions, images, and audio without touching the runtime. Request and
response bodies are scrubbed: inline base64 media becomes a file on disk plus a
readable marker, and credentials never reach the store.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from .images import sniff_extension, sniff_mime
from .ops import CapturedMedia, OpsStore, TraceRecord, monotonic_ms, redact_headers

_DATA_URL = re.compile(r"^data:(?P<mime>[^;,]+)?(?:;[^,]*)?;base64,(?P<data>.+)$", re.DOTALL)
_BASE64 = re.compile(r"^[A-Za-z0-9+/=]+$")
_FILENAME = re.compile(r'filename="([^"]*)"')
_FIELD = re.compile(r'name="([^"]*)"')
_TEXT_CONTENT = ("json", "text", "xml", "javascript", "event-stream", "x-ndjson")
_MAX_BODY_BYTES = 40 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class OpsContext:
    run_id: str
    run_key: str | None = None
    transport: str | None = None
    label: str | None = None
    chat_id: int | None = None
    user_id: int | None = None
    thread_id: int | None = None


_current: ContextVar[OpsContext | None] = ContextVar("skye_ops_context", default=None)


def current_context() -> OpsContext | None:
    return _current.get()


@contextmanager
def ops_run(context: OpsContext) -> Iterator[None]:
    token = _current.set(context)
    try:
        yield
    finally:
        _current.reset(token)


def bind_context(context: OpsContext) -> Token[OpsContext | None]:
    return _current.set(context)


def clear_context(token: Token[OpsContext | None]) -> None:
    _current.reset(token)


def new_run_id() -> str:
    return uuid4().hex


@dataclass(slots=True)
class _MediaSink:
    media: list[CapturedMedia] = field(default_factory=list)
    request_count: int = 0
    response_count: int = 0

    def next_name(self, where: str) -> int:
        if where == "request":
            self.request_count += 1
            return self.request_count
        self.response_count += 1
        return self.response_count


class CapturingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, store: OpsStore) -> None:
        self._inner = inner
        self._store = store

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self._store.capture_payloads:
            return await self._inner.handle_async_request(request)
        trace_id = uuid4().hex
        body = b""
        try:
            body = await request.aread()
            request.stream = httpx.ByteStream(body)
        except Exception:
            body = b""
        started = time.monotonic()
        try:
            response = await self._inner.handle_async_request(request)
        except BaseException as error:
            self._complete(trace_id, request, body, None, b"", started, error)
            raise
        stream = response.stream
        if response.is_stream_consumed or "_content" in response.__dict__:
            raw = bytes(response.content)
            response.stream = httpx.ByteStream(raw)
            self._complete(trace_id, request, body, response, raw, started, None)
        elif isinstance(stream, httpx.AsyncByteStream):
            response.stream = _TeeStream(
                stream,
                lambda raw, error: self._complete(
                    trace_id, request, body, response, raw, started, error
                ),
            )
        else:
            raw = b""
            try:
                raw = await response.aread()
                response.stream = httpx.ByteStream(raw)
            except Exception:
                raw = b""
            self._complete(trace_id, request, body, response, raw, started, None)
        return response

    def _complete(
        self,
        trace_id: str,
        request: httpx.Request,
        request_body: bytes,
        response: httpx.Response | None,
        response_body: bytes,
        started: float,
        error: BaseException | None,
    ) -> None:
        store = self._store
        context = current_context()
        sink = _MediaSink()
        request_headers = redact_headers(request.headers)
        response_headers = redact_headers(response.headers) if response is not None else {}
        request_type = request.headers.get("content-type", "")
        response_type = response_headers.get("content-type", "")
        request_payload = _parse_request(request_type, request_body, sink, store)
        response_payload = _parse_response(
            response_type, response_body, response, sink, store
        )
        model = _find_model(request_payload) or _find_model(response_payload)
        tokens = _find_tokens(response_payload)
        status = response.status_code if response is not None else 0
        message = None
        if error is not None:
            message = f"{type(error).__name__}: {error}"
        elif status >= 400:
            message = _error_message(response_payload) or f"HTTP {status}"
        trace = TraceRecord(
            id=trace_id,
            ts=_now_iso(),
            run_id=context.run_id if context else None,
            run_key=context.run_key if context else None,
            transport=context.transport if context else None,
            label=context.label if context else None,
            chat_id=context.chat_id if context else None,
            user_id=context.user_id if context else None,
            thread_id=context.thread_id if context else None,
            method=request.method,
            url=str(request.url.copy_with(query=b"")),
            host=request.url.host,
            status=status,
            ok=error is None and status < 400,
            duration_ms=monotonic_ms(started),
            model=model,
            stream=_looks_streaming(response_type),
            request_bytes=len(request_body),
            response_bytes=len(response_body),
            request_content_type=_short_type(request_type),
            response_content_type=_short_type(response_type),
            request_headers=request_headers,
            response_headers=response_headers,
            request_body=request_payload,
            response_body=response_payload,
            error=message,
            tokens=tokens,
            media=sink.media[:40],
        )
        store.record_trace(trace)


class _TeeStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, on_done: Any) -> None:
        self._inner = inner
        self._on_done = on_done
        self._buffer = bytearray()
        self._done = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._inner:
                if len(self._buffer) < _MAX_BODY_BYTES:
                    self._buffer.extend(chunk[: _MAX_BODY_BYTES - len(self._buffer)])
                yield chunk
        finally:
            self._finish(None)

    async def aclose(self) -> None:
        try:
            await self._inner.aclose()
        finally:
            self._finish(None)

    def _finish(self, error: BaseException | None) -> None:
        if self._done:
            return
        self._done = True
        with suppress(Exception):
            self._on_done(bytes(self._buffer), error)


def _parse_request(content_type: str, body: bytes, sink: _MediaSink, store: OpsStore) -> Any:
    if not body:
        return None
    if content_type.startswith("multipart/"):
        return _parse_multipart(content_type, body, sink, "request")
    return _decode_body(content_type, body, sink, "request", store)


def _parse_response(
    content_type: str,
    body: bytes,
    response: httpx.Response | None,
    sink: _MediaSink,
    store: OpsStore,
) -> Any:
    if response is None or not body:
        return None
    lowered = content_type.lower()
    if lowered.startswith("image/"):
        return {"__media__": _capture_bytes(body, content_type, "response", sink, "image")}
    if lowered.startswith(("audio/", "video/")):
        return {"__media__": _capture_bytes(body, content_type, "response", sink, "media")}
    if lowered.startswith("text/event-stream"):
        return _parse_sse(body, sink)
    return _decode_body(content_type, body, sink, "response", store)


def _decode_body(
    content_type: str, body: bytes, sink: _MediaSink, where: str, store: OpsStore
) -> Any:
    if len(body) > store.max_body_bytes * 3:
        return {"__truncated__": len(body)}
    lowered = content_type.lower()
    text: str | None = None
    if any(marker in lowered for marker in _TEXT_CONTENT) or not lowered:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = None
    if text is None:
        return {"__media__": _capture_bytes(body, content_type, where, sink, "media")}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    return _scrub(payload, sink, where)


def _parse_sse(body: bytes, sink: _MediaSink) -> Any:
    events: list[dict[str, Any]] = []
    for block in body.decode("utf-8", "replace").split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        name = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if not data or data == "[DONE]":
            continue
        parsed: Any
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = data
        events.append({"event": name, "data": _scrub(parsed, sink, "response")})
    return {"__stream__": True, "events": events[:400], "count": len(events)}


def _scrub(value: Any, sink: _MediaSink, where: str) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key == "b64_json" and isinstance(item, str):
                result[key] = _from_base64(item, "image/png", sink, where, "output image")
            elif key == "image_url":
                result[key] = _handle_image_url(item, sink, where)
            elif key == "file_data" and isinstance(item, str):
                result[key] = _handle_data_url(item, sink, where, "file")
            elif key == "input_audio" and isinstance(item, dict):
                result[key] = _handle_audio(item, sink, where)
            else:
                result[key] = _scrub(item, sink, where)
        return result
    if isinstance(value, list):
        return [_scrub(item, sink, where) for item in value]
    if isinstance(value, str):
        handled = _handle_data_url(value, sink, where, "media")
        if handled is not None:
            return handled
        return value
    return value


def _handle_image_url(value: Any, sink: _MediaSink, where: str) -> Any:
    if isinstance(value, str):
        handled = _handle_data_url(value, sink, where, "image")
        return handled if handled is not None else value
    if isinstance(value, dict):
        return _scrub(value, sink, where)
    return value


def _handle_audio(value: dict[str, Any], sink: _MediaSink, where: str) -> Any:
    data = value.get("data")
    if not isinstance(data, str):
        return _scrub(value, sink, where)
    audio_format = value.get("format")
    mime = "audio/wav"
    if isinstance(audio_format, str) and audio_format:
        mime = f"audio/{audio_format}"
    copy = dict(value)
    copy["data"] = _from_base64(data, mime, sink, where, "input audio")
    return copy


def _handle_data_url(value: str, sink: _MediaSink, where: str, detail: str) -> Any | None:
    if not value.startswith("data:"):
        return None
    match = _DATA_URL.match(value)
    if match is None:
        return None
    mime = match.group("mime") or "application/octet-stream"
    return _from_base64(match.group("data"), mime, sink, where, detail)


def _from_base64(text: str, mime_hint: str, sink: _MediaSink, where: str, detail: str) -> Any:
    compact = "".join(text.split())
    marker = {"__base64__": f"{len(compact)} chars", "mime": mime_hint}
    if len(compact) < 64 or not _BASE64.match(compact):
        return marker
    try:
        data = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return marker
    if not data or len(data) > _MAX_BODY_BYTES:
        return marker
    return _capture_bytes(data, mime_hint, where, sink, detail)


def _capture_bytes(
    data: bytes, mime_hint: str, where: str, sink: _MediaSink, detail: str
) -> dict[str, Any]:
    mime = _sniff(data, mime_hint)
    extension = sniff_extension(mime)
    index = sink.next_name(where)
    name = f"{where}-{index}.{extension}"
    kind = "image" if mime.startswith("image/") else mime.split("/", 1)[0]
    sink.media.append(
        CapturedMedia(name=name, mime=mime, kind=kind, data=data, where=where, detail=detail)
    )
    return {"__media__": name, "mime": mime, "bytes": len(data)}


def _sniff(data: bytes, mime_hint: str) -> str:
    if mime_hint and mime_hint != "application/octet-stream":
        return mime_hint
    try:
        return sniff_mime(data)
    except Exception:
        return mime_hint or "application/octet-stream"


def _parse_multipart(content_type: str, body: bytes, sink: _MediaSink, where: str) -> Any:
    boundary = None
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "boundary":
            boundary = value.strip().strip('"').encode()
    if not boundary:
        return {"__multipart__": len(body)}
    parts: list[dict[str, Any]] = []
    for chunk in body.split(b"--" + boundary):
        if chunk in (b"", b"--", b"--\r\n") or b"\r\n\r\n" not in chunk:
            continue
        header_blob, _, payload = chunk.partition(b"\r\n\r\n")
        payload = payload.rstrip(b"\r\n")
        headers = _multipart_headers(header_blob)
        disposition = headers.get("content-disposition", "")
        part_type = headers.get("content-type", "application/octet-stream")
        filename = _filename(disposition)
        info: dict[str, Any] = {"name": _field_name(disposition), "size": len(payload)}
        if filename:
            info["filename"] = filename
            info["content_type"] = part_type
        if filename or part_type.startswith(("image/", "audio/", "video/")):
            info["media"] = _capture_bytes(payload, part_type, where, sink, filename or "part")
        elif len(payload) < 8_000:
            with suppress(UnicodeDecodeError):
                info["value"] = payload.decode("utf-8")
        parts.append(info)
    return {"__multipart__": True, "parts": parts}


def _multipart_headers(blob: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in blob.decode("utf-8", "replace").split("\r\n"):
        key, _, value = line.partition(":")
        if value:
            headers[key.strip().lower()] = value.strip()
    return headers


def _filename(disposition: str) -> str | None:
    match = _FILENAME.search(disposition)
    return match.group(1) or None if match else None


def _field_name(disposition: str) -> str | None:
    match = _FIELD.search(disposition)
    return match.group(1) or None if match else None


def _find_model(payload: Any) -> str | None:
    if isinstance(payload, dict):
        model = payload.get("model")
        if isinstance(model, str) and model:
            return model
    return None


def _find_tokens(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    if isinstance(total, int):
        return total
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        return input_tokens + output_tokens
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if isinstance(prompt, int) or isinstance(completion, int):
        return int(prompt or 0) + int(completion or 0)
    return None


def _error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
        code = error.get("code")
        if isinstance(code, str) and code:
            return code
    if isinstance(error, str) and error:
        return error
    return None


def _looks_streaming(content_type: str) -> bool:
    return "event-stream" in content_type.lower()


def _short_type(content_type: str) -> str | None:
    if not content_type:
        return None
    return content_type.split(";")[0].strip()[:120]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
