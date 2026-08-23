from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from aiohttp.test_utils import TestClient, TestServer

from skye.access import AccessService
from skye.auth import TelegramAuth
from skye.automations import (
    AutomationError,
    AutomationPanel,
    AutomationService,
    authorization_matches,
    next_cron_run,
    parse_cron,
    sanitize_webhook_body,
)
from skye.config import Settings
from skye.db import Database
from skye.models import RequestContext, Scope
from skye.projects import ProjectService
from skye.rich import RichMessages
from skye.runtime import AgentRuntime, RunOutput
from skye.telegram import TelegramApp
from skye.web import WebApp


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123:token",
        "openai_api_key": "sk-test",
        "skye_owner_ids": "1",
        "skye_web_origin": "https://chat.skye-bot.com",
        "telegram_login_client_id": "99",
        "telegram_login_client_secret": "login-secret",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
async def database(tmp_path: Path):
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


@pytest.fixture
async def automations(database: Database) -> AutomationService:
    return AutomationService(database, "https://chat.skye-bot.com")


def private_context(user_id: int = 42, thread_id: int = 0) -> RequestContext:
    return RequestContext(user_id, "private", user_id, thread_id=thread_id)


def group_context(chat_id: int = -100, user_id: int = 42, thread_id: int = 0) -> RequestContext:
    return RequestContext(chat_id, "supergroup", user_id, thread_id=thread_id)


def private_message() -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=42, type="private", first_name="Alice"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="/settings",
    )


def test_cron_next_run_is_the_following_matching_minute() -> None:
    now = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)
    stamp = next_cron_run("0 9 * * *", "UTC", now=now)
    assert datetime.fromtimestamp(stamp, UTC) == datetime(2026, 8, 23, 9, 0, tzinfo=UTC)

    later = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)
    next_stamp = next_cron_run("0 9 * * *", "UTC", now=later)
    assert datetime.fromtimestamp(next_stamp, UTC) == datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


def test_cron_next_run_honors_timezone() -> None:
    now = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)
    stamp = next_cron_run("0 12 * * *", "America/New_York", now=now)
    assert datetime.fromtimestamp(stamp, UTC) == datetime(2026, 8, 23, 16, 0, tzinfo=UTC)


def test_cron_rejects_invalid_expressions() -> None:
    with pytest.raises(AutomationError):
        parse_cron("0 9 * *")
    with pytest.raises(AutomationError):
        parse_cron("99 * * * *")
    with pytest.raises(AutomationError):
        next_cron_run("0 9 * * *", "Not/AZone")


def test_authorization_matches_exactly() -> None:
    stored = "Bearer secret-token"
    assert authorization_matches(stored, "Bearer secret-token")
    assert not authorization_matches(stored, "Bearer other")
    assert not authorization_matches(stored, None)
    assert not authorization_matches(stored, "secret-token")


def test_webhook_body_is_size_capped() -> None:
    text = sanitize_webhook_body("x" * 20_000)
    assert len(text) == 16_000


async def test_create_is_isolated_by_scope(automations: AutomationService) -> None:
    private = private_context(7)
    other = private_context(8)
    group = group_context(-100, 7)

    created = await automations.create_schedule(
        private,
        name="Briefing",
        cron="0 9 * * *",
        task="Send a short briefing.",
        now=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
    )

    assert created.scope == Scope("user", 7)
    assert await automations.listed(other.scope, other.thread_id) == []
    assert await automations.listed(group.scope, group.thread_id) == []
    listed = await automations.listed(private.scope, private.thread_id)
    assert [item.id for item in listed] == [created.id]


async def test_group_and_private_scopes_are_separate(automations: AutomationService) -> None:
    private = private_context(42)
    group = group_context(-100, 42)
    topic = group_context(-100, 42, thread_id=17)
    now = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)

    await automations.create_schedule(
        private, name="Private", cron="0 9 * * *", task="Private task.", now=now
    )
    group_item = await automations.create_schedule(
        group, name="Group", cron="0 10 * * *", task="Group task.", now=now
    )
    await automations.create_webhook(topic, name="Topic hook", task="Topic task.")

    assert [item.name for item in await automations.listed(private.scope, 0)] == ["Private"]
    assert [item.name for item in await automations.listed(group.scope, 0)] == ["Group"]
    assert [item.name for item in await automations.listed(group.scope, 17)] == ["Topic hook"]
    with pytest.raises(AutomationError, match="not found"):
        await automations.require(private, group_item.id)


async def test_delete_removes_one_automation(automations: AutomationService) -> None:
    context = private_context()
    now = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)
    first = await automations.create_schedule(
        context, name="One", cron="0 9 * * *", task="First.", now=now
    )
    second = await automations.create_schedule(
        context, name="Two", cron="0 10 * * *", task="Second.", now=now
    )

    assert await automations.delete(context, first.id)
    remaining = await automations.listed(context.scope, context.thread_id)
    assert [item.id for item in remaining] == [second.id]


async def test_scheduler_fires_due_row(automations: AutomationService) -> None:
    context = private_context()
    now = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)
    item = await automations.create_schedule(
        context,
        name="Due",
        cron="0 9 * * *",
        task="Do the work.",
        now=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
    )
    fired: list[str] = []

    async def fire(automation: Any) -> None:
        fired.append(automation.id)

    count = await automations.tick(fire, lambda _chat, _thread: False, now=int(now.timestamp()))

    assert count == 1
    assert fired == [item.id]
    updated = await automations.get(item.id)
    assert updated is not None
    assert updated.last_fired_at == int(now.timestamp())
    assert updated.next_run_at == int(datetime(2026, 8, 24, 9, 0, tzinfo=UTC).timestamp())


async def test_scheduler_skips_busy_chat(automations: AutomationService) -> None:
    context = private_context()
    item = await automations.create_schedule(
        context,
        name="Busy",
        cron="0 9 * * *",
        task="Do the work.",
        now=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
    )
    original = item.next_run_at
    fired: list[str] = []

    async def fire(automation: Any) -> None:
        fired.append(automation.id)

    count = await automations.tick(
        fire,
        lambda chat_id, thread_id: chat_id == context.chat_id and thread_id == 0,
        now=int(datetime(2026, 8, 23, 9, 0, tzinfo=UTC).timestamp()),
    )

    assert count == 0
    assert fired == []
    skipped = await automations.get(item.id)
    assert skipped is not None
    assert skipped.next_run_at == original


async def test_scheduler_skips_busy_once_chat(automations: AutomationService) -> None:
    context = private_context()
    item = await automations.create_schedule(
        context,
        name="Busy once",
        cron="0 9 * * *",
        task="Do it once.",
        once=True,
        now=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
    )
    original = item.next_run_at
    fired: list[str] = []

    async def fire(automation: Any) -> None:
        fired.append(automation.id)

    count = await automations.tick(
        fire,
        lambda chat_id, thread_id: chat_id == context.chat_id and thread_id == 0,
        now=int(datetime(2026, 8, 23, 9, 0, tzinfo=UTC).timestamp()),
    )

    assert count == 0
    assert fired == []
    skipped = await automations.get(item.id)
    assert skipped is not None
    assert skipped.once
    assert skipped.next_run_at == original


async def test_create_once_schedule(automations: AutomationService) -> None:
    context = private_context()
    item = await automations.create_schedule(
        context,
        name="Remind me",
        cron="0 9 * * *",
        task="Ping once.",
        once=True,
        now=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
    )

    assert item.once
    assert item.kind == "schedule"
    assert item.trigger_label == "once 0 9 * * * UTC"
    listed = await automations.listed(context.scope, context.thread_id)
    assert [row.id for row in listed] == [item.id]
    assert listed[0].once
    assert listed[0].next_run_at == int(datetime(2026, 8, 23, 9, 0, tzinfo=UTC).timestamp())


async def test_scheduler_fires_once_then_deletes(automations: AutomationService) -> None:
    context = private_context()
    now = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)
    item = await automations.create_schedule(
        context,
        name="Once",
        cron="0 9 * * *",
        task="Do it once.",
        once=True,
        now=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
    )
    fired: list[str] = []

    async def fire(automation: Any) -> None:
        fired.append(automation.id)

    count = await automations.tick(fire, lambda _chat, _thread: False, now=int(now.timestamp()))

    assert count == 1
    assert fired == [item.id]
    assert await automations.get(item.id) is None
    assert await automations.listed(context.scope, context.thread_id) == []


async def test_scheduler_once_deletes_even_if_fire_fails(
    automations: AutomationService,
) -> None:
    context = private_context()
    item = await automations.create_schedule(
        context,
        name="Failing",
        cron="0 9 * * *",
        task="This will fail.",
        once=True,
        now=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
    )

    async def fire(_automation: Any) -> None:
        raise RuntimeError("delivery failed")

    count = await automations.tick(
        fire,
        lambda _chat, _thread: False,
        now=int(datetime(2026, 8, 23, 9, 0, tzinfo=UTC).timestamp()),
    )

    assert count == 1
    assert await automations.get(item.id) is None


async def test_scheduler_recurring_still_reschedules_with_once(
    automations: AutomationService,
) -> None:
    context = private_context()
    created_at = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)
    due = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)
    recurring = await automations.create_schedule(
        context, name="Daily", cron="0 9 * * *", task="Every day.", now=created_at
    )
    once = await automations.create_schedule(
        context,
        name="Once",
        cron="0 9 * * *",
        task="Just once.",
        once=True,
        now=created_at,
    )
    fired: list[str] = []

    async def fire(automation: Any) -> None:
        fired.append(automation.id)

    count = await automations.tick(fire, lambda _chat, _thread: False, now=int(due.timestamp()))

    assert count == 2
    assert set(fired) == {recurring.id, once.id}
    assert await automations.get(once.id) is None
    updated = await automations.get(recurring.id)
    assert updated is not None
    assert not updated.once
    assert updated.last_fired_at == int(due.timestamp())
    assert updated.next_run_at == int(datetime(2026, 8, 24, 9, 0, tzinfo=UTC).timestamp())


async def test_tool_create_once_schedule(automations: AutomationService) -> None:
    from agents.tool_context import ToolContext

    context = private_context(9)
    tools = {tool.name: tool for tool in automations.tools(context)}
    payload = (
        '{"name":"Alarm","cron":"0 9 * * *","task":"Say now.","timezone":"UTC","once":true}'
    )
    result = await tools["create_scheduled_automation"].on_invoke_tool(
        ToolContext(
            context=None,
            tool_name="create_scheduled_automation",
            tool_arguments=payload,
            tool_call_id="call_1",
            run_config=None,
        ),
        payload,
    )

    assert "Created scheduled automation" in result
    assert "once 0 9 * * * UTC" in result
    listed = await automations.listed(context.scope, 0)
    assert len(listed) == 1
    assert listed[0].once
    summary = await tools["list_automations"].on_invoke_tool(
        ToolContext(
            context=None,
            tool_name="list_automations",
            tool_arguments="{}",
            tool_call_id="call_2",
            run_config=None,
        ),
        "{}",
    )
    assert "once 0 9 * * * UTC" in summary


async def test_update_can_toggle_once(automations: AutomationService) -> None:
    context = private_context()
    now = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)
    item = await automations.create_schedule(
        context, name="Daily", cron="0 9 * * *", task="Every day.", now=now
    )
    assert not item.once

    updated = await automations.update(context, item.id, once=True, now=now)
    assert updated.once
    assert updated.trigger_label == "once 0 9 * * * UTC"

    recurring = await automations.update(context, item.id, once=False, now=now)
    assert not recurring.once
    assert recurring.trigger_label == "0 9 * * * UTC"


async def test_webhook_cannot_be_one_shot(automations: AutomationService) -> None:
    context = private_context()
    item = await automations.create_webhook(context, name="Hook", task="Handle it.")
    with pytest.raises(AutomationError, match="cannot be one-shot"):
        await automations.update(context, item.id, once=True)


async def test_tool_create_stays_in_current_scope(
    automations: AutomationService,
) -> None:
    from agents.tool_context import ToolContext

    private = private_context(9)
    group = group_context(-200, 9)
    tools = {tool.name: tool for tool in automations.tools(private)}
    payload = '{"name":"Ping","cron":"*/15 * * * *","task":"Say ping.","timezone":"UTC"}'
    result = await tools["create_scheduled_automation"].on_invoke_tool(
        ToolContext(
            context=None,
            tool_name="create_scheduled_automation",
            tool_arguments=payload,
            tool_call_id="call_1",
            run_config=None,
        ),
        payload,
    )

    assert "Created scheduled automation" in result
    assert await automations.listed(group.scope, 0) == []
    listed = await automations.listed(private.scope, 0)
    assert len(listed) == 1
    assert listed[0].name == "Ping"


async def test_settings_callback_deletes_one_automation(
    automations: AutomationService,
) -> None:
    context = private_context()
    keep = await automations.create_webhook(context, name="Keep", task="Keep this.")
    gone = await automations.create_webhook(context, name="Gone", task="Delete this.")
    app = object.__new__(TelegramApp)
    app.access = SimpleNamespace(allowed=AsyncMock(return_value=True))
    app._can_edit = AsyncMock(return_value=True)  # type: ignore[method-assign]
    app.rich = SimpleNamespace(edit=AsyncMock(), automations=RichMessages.automations)
    app.automation_panel = AutomationPanel(automations, app.rich)  # type: ignore[arg-type]
    callback = SimpleNamespace(
        message=private_message(),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        data=f"settings:arm:{gone.id}",
        answer=AsyncMock(),
    )

    await app.settings_callback(callback)  # type: ignore[arg-type]

    remaining = await automations.listed(context.scope, 0)
    assert [item.id for item in remaining] == [keep.id]
    callback.answer.assert_awaited()
    app.rich.edit.assert_awaited()


class FakeRuntime(AgentRuntime):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, *args: Any, **kwargs: Any) -> RunOutput:  # type: ignore[override]
        self.calls.append({"args": args, "kwargs": kwargs})
        return RunOutput("Done.", ())

    def busy(self, chat_id: int, thread_id: int) -> bool:
        return False


async def _web_client(
    database: Database, tmp_path: Path, automations: AutomationService
) -> tuple[TestClient, list[tuple[Any, str]]]:
    fired: list[tuple[Any, str]] = []

    def fire(item: Any, body: str) -> None:
        fired.append((item, body))

    config = settings()
    projects = ProjectService(database, AsyncMock(), tmp_path / "web-files")
    auth = TelegramAuth(config, database, projects)
    web_app = WebApp(
        config,
        database,
        AccessService(database, frozenset({1})),
        FakeRuntime(),  # type: ignore[arg-type]
        projects,
        auth,
        cast(Any, AsyncMock()),
        automations,
        fire,
    )
    client = TestClient(TestServer(web_app.app))
    await client.start_server()
    return client, fired


@pytest.mark.asyncio
async def test_webhook_auth_success_and_failure(
    database: Database, automations: AutomationService, tmp_path: Path
) -> None:
    item = await automations.create_webhook(
        private_context(1), name="Hook", task="Handle the event."
    )
    client, fired = await _web_client(database, tmp_path, automations)
    try:
        missing = await client.post(f"/automations/{item.id}/hook", data=b'{"ok":true}')
        assert missing.status == 401

        wrong = await client.post(
            f"/automations/{item.id}/hook",
            data=b'{"ok":true}',
            headers={"Authorization": "Bearer wrong"},
        )
        assert wrong.status == 401

        unknown = await client.post(
            "/automations/missing/hook",
            data=b"{}",
            headers={"Authorization": item.webhook_authorization or ""},
        )
        assert unknown.status == 404

        ok = await client.post(
            f"/automations/{item.id}/hook",
            data=b'{"ok":true}',
            headers={"Authorization": item.webhook_authorization or ""},
        )
        assert ok.status == 202
        assert len(fired) == 1
        assert fired[0][0].id == item.id
        assert fired[0][1] == '{"ok":true}'
    finally:
        await client.close()


async def test_fire_uses_runtime_and_created_by() -> None:
    from skye.models import ChatSettings

    app = object.__new__(TelegramApp)
    app.access = SimpleNamespace(
        allowed=AsyncMock(return_value=True),
        billed_user_id=AsyncMock(return_value=7),
    )
    app.quota = SimpleNamespace(check=AsyncMock(), record=AsyncMock())
    app.runtime = FakeRuntime()
    app.database = SimpleNamespace(
        get_settings=AsyncMock(return_value=ChatSettings("gpt-5.6-luna", "medium"))
    )
    app.billing = SimpleNamespace(
        clamp_settings=AsyncMock(side_effect=lambda _context, current, _access: current)
    )
    placeholder = private_message()
    app.rich = SimpleNamespace(
        send_chat=AsyncMock(return_value=placeholder),
        edit=AsyncMock(),
        send=AsyncMock(),
        send_images=AsyncMock(),
        send_documents=AsyncMock(),
        output=RichMessages.output,
    )
    app.telegram_projects = SimpleNamespace(
        active=AsyncMock(return_value=SimpleNamespace(instructions="")),
        conversation_id=AsyncMock(return_value="conv_1"),
    )
    app._can_edit = AsyncMock(return_value=True)  # type: ignore[method-assign]
    app.groups = SimpleNamespace(mark_seen=AsyncMock())
    app._automation_tasks = set()
    item = SimpleNamespace(
        id="auto1",
        scope=Scope("user", 7),
        thread_id=0,
        created_by=7,
        name="Briefing",
        prompt="Summarize the day.",
        kind="schedule",
        chat_id=7,
        chat_type="private",
    )

    await app.fire_automation(item)  # type: ignore[arg-type]

    assert app.runtime.calls
    context = app.runtime.calls[0]["args"][0]
    user_input = app.runtime.calls[0]["args"][2]
    assert context.user_id == 7
    assert context.chat_id == 7
    assert "Briefing" in user_input
    assert "Summarize the day." in user_input
    assert app.runtime.calls[0]["kwargs"]["manage_automations"] is True
    assert app.runtime.calls[0]["kwargs"]["conversation_id"] == "conv_1"


async def test_fire_skips_when_chat_is_not_allowed() -> None:
    app = object.__new__(TelegramApp)
    app.access = SimpleNamespace(allowed=AsyncMock(return_value=False))
    app.runtime = FakeRuntime()
    item = SimpleNamespace(
        id="auto1",
        scope=Scope("chat", -100),
        thread_id=0,
        created_by=7,
        name="Nightly",
        prompt="Post a recap.",
        kind="schedule",
        chat_id=-100,
        chat_type="supergroup",
    )

    await app.fire_automation(item)  # type: ignore[arg-type]

    assert app.runtime.calls == []
