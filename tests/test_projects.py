from io import BytesIO
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from skye.config import Settings
from skye.db import Database
from skye.models import ChatSettings, RequestContext
from skye.projects import ProjectService, file_payload
from skye.runtime import AgentRuntime, describe_tool_event, telegram_run_key, web_run_key


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


def service(database: Database, tmp_path: Path) -> ProjectService:
    return ProjectService(database, cast(Any, AsyncMock()), tmp_path / "web")


async def test_skye_project_is_created_once_and_cannot_be_deleted(
    database: Database, tmp_path: Path
) -> None:
    projects = service(database, tmp_path)
    first = await projects.ensure_skye(42)
    second = await projects.ensure_skye(42)

    assert first.id == second.id
    assert first.kind == "skye"
    assert first.name == "Skye"
    with pytest.raises(PermissionError):
        await projects.delete(42, first.id)


async def test_projects_are_isolated_by_user(database: Database, tmp_path: Path) -> None:
    projects = service(database, tmp_path)
    alice = await projects.create(1, name="Frontend", icon="code-bracket", color="blue")
    await projects.ensure_skye(2)

    assert await database.web_project(2, alice.id) is None
    listed = await projects.list(2)
    assert [item.name for item in listed] == ["Skye"]


async def test_search_is_scoped_to_the_user(database: Database, tmp_path: Path) -> None:
    projects = service(database, tmp_path)
    one = await projects.create(1, name="Secret notes")
    two = await projects.create(2, name="Secret notes")
    await projects.add_message(1, one.id, role="user", text="alpha unique token")
    await projects.add_message(2, two.id, role="user", text="alpha unique token")

    names, messages = await database.search_web(1, "alpha unique")
    assert all(project.user_id == 1 for project in names)
    assert all(message.user_id == 1 for _, message in messages)
    assert all(project.user_id == 1 for project, _ in messages)


async def test_image_files_get_cached_bounded_thumbnails(
    database: Database, tmp_path: Path
) -> None:
    projects = service(database, tmp_path)
    project = await projects.create(1, name="Images")
    source = BytesIO()
    Image.new("RGB", (1800, 1200), (40, 80, 120)).save(source, format="PNG")
    saved = await projects.save_file(
        1,
        project.id,
        filename="photo.png",
        mime="image/png",
        data=source.getvalue(),
        kind="image",
    )

    thumbnail = projects.thumbnail_bytes(1, saved.id)
    assert thumbnail is not None
    with Image.open(BytesIO(thumbnail)) as image:
        assert image.format == "WEBP"
        assert image.width <= 640
        assert image.height <= 640
    assert projects.thumbnail_bytes(1, saved.id) == thumbnail
    assert file_payload(saved)["thumbnail_url"] == f"/api/files/{saved.id}/thumbnail"


async def test_deleting_project_removes_file_bytes(
    database: Database, tmp_path: Path
) -> None:
    projects = service(database, tmp_path)
    project = await projects.create(1, name="Disposable")
    saved = await projects.save_file(
        1,
        project.id,
        filename="private.txt",
        mime="text/plain",
        data=b"private bytes",
        kind="upload",
    )
    path = tmp_path / "web" / "1" / saved.id
    assert path.is_file()

    await projects.delete(1, project.id)

    assert not path.exists()


async def test_extra_instructions_append_after_the_base_prompt() -> None:
    runtime = AgentRuntime(
        Settings(
            telegram_bot_token="123:token",
            openai_api_key="sk-test",
            skye_owner_ids="1",
            _env_file=None,
        ),  # type: ignore[call-arg]
        cast(Any, None),
        cast(Any, AsyncMock()),
        "You are Skye.",
    )
    agent = runtime._agent(
        RequestContext(1, "private", 1),
        ChatSettings("gpt-5.6-luna", "medium", memory_enabled=False),
        extra_instructions="Prefer TypeScript.",
    )
    text = cast(str, agent.instructions)
    assert text.startswith("You are Skye.")
    assert "Prefer TypeScript." in text
    assert text.index("You are Skye.") < text.index("Prefer TypeScript.")


def test_web_run_key_does_not_collide_with_telegram() -> None:
    assert telegram_run_key(1, 0) != web_run_key("abc")
    assert web_run_key("abc").startswith("web:")


def test_describe_tool_event_hides_payloads() -> None:
    event = type(
        "Event",
        (),
        {
            "name": "tool_called",
            "item": type(
                "Item",
                (),
                {
                    "title": None,
                    "raw_item": type(
                        "Raw",
                        (),
                        {"name": "remember", "call_id": "c1", "arguments": "secret memory"},
                    )(),
                },
            )(),
        },
    )()
    described = describe_tool_event(event)
    assert described is not None
    assert described.tool_label == "Saved a memory"
    assert described.tool_status == "running"
    assert "secret" not in described.tool_label
    assert described.image == b""


@pytest.mark.parametrize("tool_name", ["send_message", "send_voice"])
def test_describe_tool_event_hides_delivery_tools(tool_name: str) -> None:
    event = type(
        "Event",
        (),
        {
            "name": "tool_called",
            "item": type(
                "Item",
                (),
                {
                    "title": None,
                    "raw_item": type("Raw", (), {"name": tool_name, "call_id": "c2"})(),
                },
            )(),
        },
    )()
    assert describe_tool_event(event) is None
