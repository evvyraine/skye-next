from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class OpenRouterTransport(httpx.AsyncBaseTransport):
    """Normalize OpenRouter server-tool items to the OpenAI Responses wire types."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        is_responses_request = request.url.path.rstrip("/").endswith("/responses")
        if is_responses_request:
            request = _strip_openrouter_tool_labels(request)
        response = await self._transport.handle_async_request(request)
        if not is_responses_request:
            return response
        headers = [
            (name, value)
            for name, value in response.headers.raw
            if name.lower() not in {b"content-length", b"content-encoding"}
        ]
        return httpx.Response(
            response.status_code,
            headers=headers,
            stream=_NormalizedResponseStream(response),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._transport.aclose()


class _NormalizedResponseStream(httpx.AsyncByteStream):
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        content_type = self.response.headers.get("content-type", "")
        if "text/event-stream" not in content_type:
            body = b"".join([chunk async for chunk in self.response.aiter_bytes()])
            yield _normalize_json_bytes(body)
            return
        buffer = b""
        async for chunk in self.response.aiter_bytes():
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                yield _normalize_sse_line(line) + b"\n"
        if buffer:
            yield _normalize_sse_line(buffer)

    async def aclose(self) -> None:
        await self.response.aclose()


def _strip_openrouter_tool_labels(request: httpx.Request) -> httpx.Request:
    try:
        payload = json.loads(request.content)
    except (RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
        return request
    if not isinstance(payload, dict) or not isinstance(payload.get("tools"), list):
        return request

    changed = False
    tools: list[Any] = []
    for tool in payload["tools"]:
        if isinstance(tool, dict) and str(tool.get("type", "")).startswith("openrouter:"):
            tool = dict(tool)
            changed = tool.pop("server_label", None) is not None or changed
        tools.append(tool)
    if not changed:
        return request

    payload["tools"] = tools
    headers = dict(request.headers)
    headers.pop("content-length", None)
    return httpx.Request(
        request.method,
        request.url,
        headers=headers,
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        extensions=request.extensions,
    )


def _normalize_sse_line(line: bytes) -> bytes:
    if not line.startswith(b"data:"):
        return line
    prefix, payload = line.split(b":", 1)
    stripped = payload.strip()
    if stripped == b"[DONE]":
        return line
    return prefix + b": " + _normalize_json_bytes(stripped)


def _normalize_json_bytes(payload: bytes) -> bytes:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError, UnicodeDecodeError:
        return payload
    return json.dumps(_normalize(value), ensure_ascii=False, separators=(",", ":")).encode()


def _normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {key: _normalize(item) for key, item in value.items()}
    kind = normalized.get("type")
    status = normalized.get("status")
    if kind == "openrouter:web_search":
        if status is not None:
            normalized["type"] = "web_search_call"
            normalized.setdefault("id", "openrouter_web_search")
            normalized.setdefault("action", {"type": "search", "query": None})
        else:
            normalized = {"type": "web_search", **normalized.get("parameters", {})}
    elif kind == "openrouter:web_fetch":
        if status is not None:
            normalized = {
                **normalized,
                "type": "web_search_call",
                "id": normalized.get("id") or "openrouter_web_fetch",
                "action": {"type": "open_page", "url": normalized.get("url")},
            }
        else:
            normalized = {"type": "web_search"}
    elif kind == "openrouter:image_generation":
        if status is not None:
            normalized["type"] = "image_generation_call"
            normalized.setdefault("id", "openrouter_image_generation")
            normalized["result"] = (
                normalized.get("result") or normalized.get("imageB64") or normalized.get("imageUrl")
            )
        else:
            normalized = {"type": "image_generation", **normalized.get("parameters", {})}
    elif kind == "openrouter:shell":
        if status is not None:
            normalized["type"] = "shell_call"
            normalized.setdefault("id", "openrouter_shell")
            normalized["call_id"] = normalized.get("call_id") or normalized["id"]
            normalized.setdefault("action", {"commands": []})
        else:
            parameters = normalized.get("parameters", {})
            normalized = {
                "type": "shell",
                "environment": parameters.get("environment", {"type": "container_auto"}),
            }
    return normalized
