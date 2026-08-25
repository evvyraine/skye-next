import json

import httpx

from skye.providers import OpenRouterTransport, _normalize


def test_openrouter_server_tool_outputs_are_normalized_for_openai_sdk() -> None:
    assert _normalize(
        {
            "type": "openrouter:web_fetch",
            "id": "fetch_1",
            "status": "completed",
            "url": "https://example.com",
        }
    )["action"] == {"type": "open_page", "url": "https://example.com"}
    assert _normalize(
        {
            "type": "openrouter:image_generation",
            "status": "completed",
            "imageUrl": "https://example.com/image.png",
        }
    ) == {
        "type": "image_generation_call",
        "status": "completed",
        "imageUrl": "https://example.com/image.png",
        "id": "openrouter_image_generation",
        "result": "https://example.com/image.png",
    }
    shell = _normalize(
        {
            "type": "openrouter:shell",
            "status": "completed",
            "id": "shell_1",
            "call_id": None,
            "action": {"commands": ["pwd"]},
        }
    )
    assert shell["type"] == "shell_call"
    assert shell["call_id"] == "shell_1"


def test_openrouter_server_tool_definitions_are_normalized_in_response_echo() -> None:
    assert _normalize(
        {
            "type": "openrouter:shell",
            "parameters": {"environment": {"type": "container_auto"}},
        }
    ) == {"type": "shell", "environment": {"type": "container_auto"}}


async def test_transport_normalizes_streamed_server_tool_items() -> None:
    async def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/responses"
        assert json.loads(request.content)["tools"] == [
            {"type": "openrouter:web_search", "parameters": {"search_context_size": "medium"}},
            {"type": "mcp", "server_label": "connector"},
        ]
        event = {
            "type": "response.output_item.done",
            "item": {
                "type": "openrouter:web_search",
                "id": "search_1",
                "status": "completed",
                "action": {"type": "search", "query": "Skye"},
            },
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode(),
        )

    transport = OpenRouterTransport(httpx.MockTransport(upstream))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/responses",
            json={
                "tools": [
                    {
                        "type": "openrouter:web_search",
                        "server_label": "openrouter_web_search",
                        "parameters": {"search_context_size": "medium"},
                    },
                    {"type": "mcp", "server_label": "connector"},
                ]
            },
        )

    assert '"type":"web_search_call"' in response.text
    assert "data: [DONE]" in response.text
