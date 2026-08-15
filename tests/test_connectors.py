from collections.abc import Sequence
from typing import Any, cast

import pytest
from agents import HostedMCPTool, ToolSearchTool

from skye.connectors import (
    ConnectorError,
    ConnectorService,
    composio_user_key,
    mcp_label,
    parse_headers,
    validate_mcp_url,
)
from skye.db import Database
from skye.models import AppConnector, GroupMessage, RequestContext, Scope


class FakeComposio:
    def __init__(self) -> None:
        self.accounts: list[AppConnector] = []
        self.toolkits = {
            "gmail": AppConnector("gmail", "Gmail", "available"),
            "github": AppConnector("github", "GitHub", "available", no_auth=False),
            "exa": AppConnector("exa", "Exa", "available", no_auth=True),
        }
        self.deleted: list[str] = []
        self.sessions: list[tuple[str, tuple[str, ...]]] = []

    async def list_toolkits(self, query: str = "", limit: int = 8) -> list[AppConnector]:
        items = list(self.toolkits.values())
        if query:
            items = [item for item in items if query.lower() in item.name.lower()]
        return items[:limit]

    async def get_toolkit(self, slug: str) -> AppConnector:
        try:
            return self.toolkits[slug]
        except KeyError as error:
            raise ConnectorError("That app was not found.") from error

    async def list_accounts(self, user_key: str) -> list[AppConnector]:
        assert user_key.startswith("tg:")
        return list(self.accounts)

    async def create_link(self, user_key: str, slug: str) -> str:
        assert user_key.startswith("tg:")
        return f"https://connect.composio.dev/link/{slug}"

    async def delete_account(self, user_key: str, account_id: str) -> None:
        assert user_key.startswith("tg:")
        self.accounts = [item for item in self.accounts if item.account_id != account_id]
        self.deleted.append(account_id)

    async def create_session(self, user_key: str, slugs: Sequence[str]) -> tuple[str, str]:
        self.sessions.append((user_key, tuple(slugs)))
        return "sess_1", "https://backend.composio.dev/mcp/sess_1"

    async def delete_session(self, session_id: str) -> None:
        self.deleted.append(session_id)

    async def aclose(self) -> None:
        return None


@pytest.fixture
async def database(tmp_path: Any) -> Any:
    value = Database(tmp_path / "skye.db", "gpt-5.6-luna", "medium")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


def test_https_urls_are_required() -> None:
    assert validate_mcp_url("https://mcp.example.com/v1") == "https://mcp.example.com/v1"
    with pytest.raises(ConnectorError):
        validate_mcp_url("http://mcp.example.com/v1")
    with pytest.raises(ConnectorError):
        validate_mcp_url("https://localhost/mcp")
    with pytest.raises(ConnectorError):
        validate_mcp_url("https://127.0.0.1/mcp")
    with pytest.raises(ConnectorError):
        validate_mcp_url("https://user:pass@mcp.example.com/mcp")


def test_headers_are_parsed_and_filtered() -> None:
    assert parse_headers(".") == {}
    assert parse_headers("Authorization: Bearer secret") == {"Authorization": "Bearer secret"}
    assert parse_headers('{"X-Api-Key": "abc"}') == {"X-Api-Key": "abc"}
    with pytest.raises(ConnectorError):
        parse_headers("Host: evil.test")
    with pytest.raises(ConnectorError):
        parse_headers("not a header")


def test_composio_user_key_is_stable() -> None:
    assert composio_user_key(42) == "tg:42"


def test_mcp_labels_are_safe() -> None:
    assert mcp_label("mcp", "ab12cd34ef56") == "mcp_ab12cd34ef56"
    assert " " not in mcp_label("mcp", "Work CRM!")


async def test_custom_connectors_are_user_scoped(database: Database) -> None:
    service = ConnectorService(database, None)
    first = await service.add_custom(1, "Work CRM", "https://example.com/mcp", {})
    second = await service.add_custom(2, "Other", "https://example.org/mcp", {"X-Key": "1"})

    assert await database.get_custom_connector(2, first.id) is None
    assert (await service.require_custom(1, first.id)).name == "Work CRM"
    with pytest.raises(ConnectorError):
        await service.require_custom(1, second.id)
    await service.delete_custom(1, first.id)
    assert await database.get_custom_connector(1, first.id) is None


async def test_snapshot_merges_apps_and_custom_servers(database: Database) -> None:
    composio = FakeComposio()
    composio.accounts = [AppConnector("gmail", "gmail", "connected", account_id="ca_1")]
    service = ConnectorService(database, composio)
    await service.add_custom(7, "Docs", "https://example.com/mcp", {})
    await database.add_user_toolkit(7, "exa")

    snapshot = await service.snapshot(7)

    assert snapshot.connected_count == 3
    assert snapshot.labels == ("Exa", "Gmail", "Docs")
    assert {item.slug for item in snapshot.apps} == {"exa", "gmail"}


async def test_no_auth_app_connects_locally(database: Database) -> None:
    service = ConnectorService(database, FakeComposio())

    assert await service.connect_link(3, "exa") is None
    snapshot = await service.snapshot(3)
    assert snapshot.apps[0].slug == "exa"
    await service.disconnect_app(3, "exa")
    assert (await service.snapshot(3)).apps == ()


async def test_oauth_disconnect_deletes_only_that_users_account(database: Database) -> None:
    composio = FakeComposio()
    composio.accounts = [AppConnector("gmail", "Gmail", "connected", account_id="ca_1")]
    service = ConnectorService(database, composio)

    await service.disconnect_app(9, "gmail")

    assert composio.deleted == ["ca_1"]
    assert composio.accounts == []


async def test_hosted_tools_are_private_only(database: Database) -> None:
    composio = FakeComposio()
    composio.accounts = [AppConnector("gmail", "Gmail", "connected", account_id="ca_1")]
    service = ConnectorService(database, composio)
    await service.add_custom(4, "CRM", "https://example.com/mcp", {"Authorization": "Bearer x"})

    private = await service.hosted_tools(RequestContext(4, "private", 4))
    group = await service.hosted_tools(RequestContext(-100, "supergroup", 4))

    assert group.tools == ()
    assert private.labels == ("Gmail", "CRM")
    assert [type(tool) for tool in private.tools] == [HostedMCPTool, HostedMCPTool, ToolSearchTool]
    custom = cast(HostedMCPTool, private.tools[1])
    assert custom.tool_config["headers"] == {"Authorization": "Bearer x"}
    assert custom.tool_config["server_url"] == "https://example.com/mcp"
    assert composio.sessions == [("tg:4", ("gmail",))]


async def test_failed_composio_session_does_not_claim_apps(database: Database) -> None:
    class BrokenComposio(FakeComposio):
        async def create_session(self, user_key: str, slugs: Sequence[str]) -> tuple[str, str]:
            raise ConnectorError("Couldn't reach the connector service.")

    composio = BrokenComposio()
    composio.accounts = [AppConnector("gmail", "Gmail", "connected", account_id="ca_1")]
    service = ConnectorService(database, composio)
    await service.add_custom(5, "CRM", "https://example.com/mcp", {})

    tools = await service.hosted_tools(RequestContext(5, "private", 5))

    assert tools.labels == ("CRM",)
    assert [type(tool) for tool in tools.tools] == [HostedMCPTool, ToolSearchTool]


def test_settings_keyboard_adds_connectors_in_private() -> None:
    from skye.telegram import TelegramApp

    private = TelegramApp._settings_keyboard(True, private=True)
    group = TelegramApp._settings_keyboard(True, private=False)
    inspect = TelegramApp._settings_keyboard(False, private=False)

    assert private is not None and group is not None and inspect is not None
    private_labels = [button.text for row in private.inline_keyboard for button in row]
    group_labels = [button.text for row in group.inline_keyboard for button in row]
    inspect_labels = [button.text for row in inspect.inline_keyboard for button in row]
    assert private_labels == ["Model", "Reasoning", "Agent", "Connectors", "Memory"]
    assert group_labels == ["Model", "Reasoning", "Agent", "Connectors", "Memory"]
    assert inspect_labels == ["Connectors"]


def _group_message(chat_id: int, sender_id: int, message_id: int = 1) -> GroupMessage:
    return GroupMessage(
        chat_id,
        0,
        message_id,
        sender_id,
        "Alice",
        None,
        "hi",
        None,
        None,
        None,
        None,
        None,
        None,
        1,
    )


async def _allow_group(database: Database, user_id: int, chat_id: int, title: str) -> None:
    await database.set_access(Scope("chat", chat_id), "allow", created_by=1)
    await database.remember_chat(chat_id, title)
    await database.save_group_message(_group_message(chat_id, user_id))


async def test_share_and_revoke_are_owner_scoped(database: Database) -> None:
    composio = FakeComposio()
    composio.accounts = [AppConnector("github", "GitHub", "connected", account_id="ca_1")]
    service = ConnectorService(database, composio)
    await _allow_group(database, 7, -100, "Skye Lab")
    custom = await service.add_custom(7, "CRM", "https://example.com/mcp", {})

    app_share = await service.share(7, "Alice", -100, "app", "github")
    mcp_share = await service.share(7, "Alice", -100, "custom", custom.id)

    assert {item.ref for item in await service.group_shares(-100)} == {"github", custom.id}
    with pytest.raises(PermissionError):
        await service.revoke(app_share.id, actor_id=8)
    await service.revoke(app_share.id, actor_id=7)
    await service.revoke(mcp_share.id, actor_id=8, admin_chat_id=-100)
    assert await service.group_shares(-100) == []


async def test_group_tools_use_only_shared_connectors(database: Database) -> None:
    composio = FakeComposio()
    composio.accounts = [
        AppConnector("gmail", "Gmail", "connected", account_id="ca_1"),
        AppConnector("github", "GitHub", "connected", account_id="ca_2"),
    ]
    service = ConnectorService(database, composio)
    await _allow_group(database, 4, -100, "Skye Lab")
    await service.add_custom(4, "CRM", "https://example.com/mcp", {"Authorization": "Bearer x"})
    await service.share(4, "Alice", -100, "app", "github")

    private = await service.hosted_tools(RequestContext(4, "private", 4))
    group = await service.hosted_tools(RequestContext(-100, "supergroup", 4))
    other = await service.hosted_tools(RequestContext(-200, "supergroup", 4))

    assert "Gmail" in private.labels and "CRM" in private.labels
    assert group.labels == ("GitHub (shared by Alice)",)
    assert other.tools == ()
    assert ("tg:4", ("github",)) in composio.sessions


async def test_disconnect_and_delete_revoke_shares(database: Database) -> None:
    composio = FakeComposio()
    composio.accounts = [AppConnector("github", "GitHub", "connected", account_id="ca_1")]
    service = ConnectorService(database, composio)
    await _allow_group(database, 4, -100, "Skye Lab")
    custom = await service.add_custom(4, "CRM", "https://example.com/mcp", {})
    await service.share(4, "Alice", -100, "app", "github")
    await service.share(4, "Alice", -100, "custom", custom.id)

    await service.disconnect_app(4, "github")
    await service.delete_custom(4, custom.id)

    assert await service.group_shares(-100) == []
    assert (await service.hosted_tools(RequestContext(-100, "supergroup", 4))).tools == ()


async def test_disabled_or_missing_share_is_not_attached(database: Database) -> None:
    service = ConnectorService(database, FakeComposio())
    await _allow_group(database, 4, -100, "Skye Lab")
    custom = await service.add_custom(4, "CRM", "https://example.com/mcp", {})
    await service.share(4, "Alice", -100, "custom", custom.id)
    await service.update_custom(4, custom.id, enabled=False)

    shares = await service.group_shares(-100)
    tools = await service.hosted_tools(RequestContext(-100, "supergroup", 4))

    assert shares[0].available is False
    assert tools.tools == ()


async def test_shareable_groups_only_include_groups_the_user_wrote_in(
    database: Database,
) -> None:
    service = ConnectorService(database, FakeComposio())
    await _allow_group(database, 4, -100, "Skye Lab")
    await _allow_group(database, 9, -200, "Other")
    await service.connect_link(4, "exa")

    groups = await service.shareable_groups(4, "app", "exa")

    assert [item.chat_id for item in groups] == [-100]
    await service.share(4, "Alice", -100, "app", "exa")
    assert await service.shareable_groups(4, "app", "exa") == []
