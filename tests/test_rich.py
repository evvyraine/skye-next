from aiogram.types import (
    InputMediaPhoto,
    InputRichBlockParagraph,
    InputRichBlockTable,
    InputRichBlockThinking,
)

from skye.models import (
    AccessEntry,
    AppConnector,
    ChatSettings,
    ConnectorSnapshot,
    CustomConnector,
    Memory,
    Scope,
)
from skye.rich import RichMessages


def test_output_keeps_markdown_and_embeds_images() -> None:
    output = RichMessages.output("# Result\n\n**Ready**", [b"png"])

    assert output.markdown == (
        "# Result\n\n**Ready**\n\n![Generated image 1](tg://photo?id=image_1)"
    )
    assert output.media and output.media[0].id == "image_1"
    assert isinstance(output.media[0].media, InputMediaPhoto)


def test_settings_use_a_native_rich_table() -> None:
    message = RichMessages.settings(ChatSettings(model="gpt-5.6-sol", reasoning="high"))

    assert message.blocks
    table = message.blocks[1]
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[1][1].text == "Sol"
    assert table.cells[2][1].text == "High"
    assert table.cells[3][1].text == "Skye"


def test_thinking_block_is_valid_for_drafts() -> None:
    block = InputRichBlockThinking(text="Thinking…")

    assert block.type == "thinking"


def test_access_screen_lists_entries_and_group_status() -> None:
    entries = [
        AccessEntry(Scope("user", 42), "allow", 1, "now"),
        AccessEntry(Scope("chat", -100), "ban", 1, "now"),
    ]

    message = RichMessages.access(entries, notice="Allowed user `42`.", in_group=True)

    assert message.blocks
    notice = message.blocks[1]
    status = message.blocks[3]
    table = message.blocks[4]
    assert isinstance(notice, InputRichBlockParagraph)
    assert isinstance(status, InputRichBlockParagraph)
    assert notice.text == "Allowed user `42`."
    assert status.text == "This group is not allowlisted."
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[1][0].text == "user"
    assert table.cells[1][1].text == "42"
    assert table.cells[1][2].text == "allow"


def test_access_screen_can_hide_entries() -> None:
    entries = [AccessEntry(Scope("user", 42), "allow", 1, "now")]

    message = RichMessages.access(entries, in_group=True, show_entries=False)

    assert message.blocks
    assert all(not isinstance(block, InputRichBlockTable) for block in message.blocks)
    status = message.blocks[2]
    assert isinstance(status, InputRichBlockParagraph)
    assert status.text == "This group is not allowlisted."


def test_settings_can_show_connector_count() -> None:
    message = RichMessages.settings(
        ChatSettings(model="gpt-5.6-sol", reasoning="high"),
        connector_count=2,
    )

    assert message.blocks
    table = message.blocks[1]
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[4][0].text == "Connectors"
    assert table.cells[4][1].text == "2 connected"


def test_connectors_screen_lists_apps_and_custom_servers() -> None:
    snapshot = ConnectorSnapshot(
        (AppConnector("gmail", "Gmail", "connected", account_id="ca_1"),),
        (
            CustomConnector(
                "abc",
                1,
                "Work CRM",
                "https://user:secret@example.com/mcp?token=1",
                {"Authorization": "Bearer x"},
                True,
                "now",
                "now",
            ),
        ),
    )

    message = RichMessages.connectors(snapshot, configured=True)
    custom = RichMessages.connector_custom(snapshot.custom[0])

    assert message.blocks
    table = message.blocks[2]
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[1][0].text == "Gmail"
    assert table.cells[2][0].text == "Work CRM"
    assert custom.blocks
    custom_table = custom.blocks[2]
    assert isinstance(custom_table, InputRichBlockTable)
    assert custom_table.cells[1][1].text == "https://example.com/mcp"
    assert "secret" not in custom_table.cells[1][1].text


def test_memory_screen_uses_plain_rich_cells() -> None:
    memory = Memory(1, Scope("user", 7), "preference", "Likes **literal** tea", "now", "now")

    message = RichMessages.memory([memory], enabled=True)

    assert message.blocks
    table = message.blocks[1]
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[1][2].text == "Likes **literal** tea"
