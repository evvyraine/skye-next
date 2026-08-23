from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    Chat,
    InputRichBlockDetails,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichBlockThinking,
    InputRichMessage,
    Message,
    ReplyParameters,
    RichTextBold,
    RichTextCode,
    RichTextUrl,
    User,
)

from skye.artifacts import GeneratedFile
from skye.models import (
    AccessEntry,
    AppConnector,
    ChatSettings,
    ConnectorSnapshot,
    CustomConnector,
    Memory,
    Scope,
    Skill,
)
from skye.rich import RichMessages


def test_output_keeps_markdown_without_media() -> None:
    output = RichMessages.output("# Result\n\n**Ready**")

    assert output == InputRichMessage(markdown="# Result\n\n**Ready**")


def test_output_and_content_strip_citation_tokens() -> None:
    token = "\ue200cite\ue202turn0view0\ue201"
    text = f"Paris is the capital. {token} See https://example.com/report"

    output = RichMessages.output(text)
    wrapped = RichMessages._content(text)
    already = RichMessages._content(InputRichMessage(markdown=text))

    assert output == InputRichMessage(
        markdown="Paris is the capital. See https://example.com/report"
    )
    assert wrapped == output
    assert already == output
    assert RichMessages.output(token) == InputRichMessage(markdown="Done.")
    settings = RichMessages.settings(ChatSettings(model="gpt-5.6-luna", reasoning="medium"))
    assert RichMessages._content(settings) is settings


def test_settings_use_a_native_rich_table() -> None:
    message = RichMessages.settings(ChatSettings(model="gpt-5.6-sol", reasoning="high"))

    assert message.blocks
    table = message.blocks[1]
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[1][1].text == "High"
    assert table.cells[2][1].text == "Skye"
    assert all(cell[0].text != "Model" for cell in table.cells)


def test_thinking_block_is_valid_for_drafts() -> None:
    block = InputRichBlockThinking(text="Thinking…")

    assert block.type == "thinking"


def test_access_screen_lists_entries_and_group_status() -> None:
    entries = [
        AccessEntry(Scope("user", 42), "allow", 1, "now"),
        AccessEntry(Scope("chat", -100), "ban", 1, "now"),
    ]
    target = Scope("user", 42)

    message = RichMessages.access(
        entries, notice=RichMessages.access_change("Allowed", target), in_group=True
    )

    assert message.blocks
    notice = message.blocks[1]
    status = message.blocks[2]
    table = message.blocks[3]
    assert isinstance(notice, InputRichBlockParagraph)
    assert isinstance(status, InputRichBlockParagraph)
    assert notice.text == ["Allowed user ", RichTextCode(text="42"), "."]
    assert status.text == "This group is not allowlisted."
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[1][0].text == "user"
    assert table.cells[1][1].text == RichTextCode(text="42")
    assert table.cells[1][2].text == "allow"
    assert all("Owner-only allowlist" not in getattr(block, "text", "") for block in message.blocks)


def test_connector_share_confirm_bolds_the_connector_name() -> None:
    message = RichMessages.connector_share_confirm("Work **CRM**", "Skye Lab", sensitive=True)

    assert message.blocks
    heading = message.blocks[0]
    body = message.blocks[1]
    assert isinstance(heading, InputRichBlockSectionHeading)
    assert heading.text == "Share Work **CRM**"
    assert isinstance(body, InputRichBlockParagraph)
    assert body.text == [
        "Share ",
        RichTextBold(text="Work **CRM**"),
        " with Skye Lab? Anyone there can ask Skye to use it. "
        "Replies that use this app will be visible to everyone in the group.",
    ]


def test_settings_prompts_use_native_headings_and_inline_rich_text() -> None:
    memory = RichMessages.memory_clear_confirm()
    skill = RichMessages.skill_upload_prompt()
    share = RichMessages.agent_share_link("https://t.me/skye_bot?start=agent_abc")
    admin = RichMessages.admin_prompt("allow")

    assert memory.blocks
    assert isinstance(memory.blocks[0], InputRichBlockSectionHeading)
    assert memory.blocks[0].text == "Delete all memories?"
    assert skill.blocks
    assert isinstance(skill.blocks[1], InputRichBlockParagraph)
    assert skill.blocks[1].text == [
        "Send a ",
        RichTextCode(text=".zip"),
        " skill bundle or a ",
        RichTextCode(text="SKILL.md"),
        " file. Every file in the zip is stored and uploaded together.",
    ]
    assert share.blocks
    assert isinstance(share.blocks[2], InputRichBlockParagraph)
    assert share.blocks[2].text == RichTextUrl(
        text="Install this agent", url="https://t.me/skye_bot?start=agent_abc"
    )
    assert admin.blocks
    assert isinstance(admin.blocks[0], InputRichBlockSectionHeading)
    assert admin.blocks[0].text == "Allow"


def test_settings_menus_send_native_blocks_instead_of_markdown() -> None:
    screens = [
        RichMessages.settings(ChatSettings(model="gpt-5.6-luna", reasoning="medium")),
        RichMessages.choose_reasoning("medium"),
        RichMessages.agents((), None),
        RichMessages.access(
            (), notice=RichMessages.access_change("Allowed", Scope("chat", -1002206813481))
        ),
        RichMessages.memory([], enabled=True),
        RichMessages.memory_clear_confirm(),
        RichMessages.skill_upload_prompt(),
        RichMessages.skill_delete_confirm("basic-math"),
        RichMessages.connector_share_confirm("Gmail", "Skye Lab", sensitive=False),
        RichMessages.connector_remove_confirm("Work CRM"),
        RichMessages.connector_search_prompt(),
        RichMessages.connector_edit_prompt("name", "Work CRM"),
        RichMessages.admin_prompt("allow"),
        RichMessages.agent_name_prompt("Helper"),
        RichMessages.agent_share_link("https://t.me/bot?start=agent_1"),
        RichMessages.agent_installed("Helper", 1, hint=True),
    ]
    for message in screens:
        assert message.blocks
        assert message.markdown is None
        for block in message.blocks:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                assert "**" not in text
                assert "`" not in text


def test_access_screen_can_hide_entries() -> None:
    entries = [AccessEntry(Scope("user", 42), "allow", 1, "now")]

    message = RichMessages.access(entries, in_group=True, show_entries=False)

    assert message.blocks
    assert all(not isinstance(block, InputRichBlockTable) for block in message.blocks)
    status = message.blocks[1]
    assert isinstance(status, InputRichBlockParagraph)
    assert status.text == "This group is not allowlisted."


def test_reasoning_chooser_uses_a_title_instead_of_the_settings_table() -> None:
    message = RichMessages.choose_reasoning("medium")

    assert message.blocks
    heading = message.blocks[0]
    current = message.blocks[1]
    assert isinstance(heading, InputRichBlockSectionHeading)
    assert heading.text == "Choose your reasoning"
    assert isinstance(current, InputRichBlockParagraph)
    assert current.text == "Currently Medium."
    assert all(not isinstance(block, InputRichBlockTable) for block in message.blocks)


def test_settings_can_show_connector_and_skill_counts() -> None:
    message = RichMessages.settings(
        ChatSettings(model="gpt-5.6-sol", reasoning="high"),
        connector_count=2,
        skill_count=1,
    )

    assert message.blocks
    table = message.blocks[1]
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[3][0].text == "Connectors"
    assert table.cells[3][1].text == "2 connected"
    assert table.cells[4][0].text == "Skills"
    assert table.cells[4][1].text == "1 uploaded"


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


def test_skills_screen_lists_uploaded_bundles() -> None:
    skill = Skill(
        "abc",
        Scope("user", 1),
        "skill_1",
        "basic-math",
        "Add or multiply numbers.",
        "basic-math.zip",
        2,
        1,
        "now",
    )
    listing = RichMessages.skills((skill,))
    detail = RichMessages.skill(skill, files=("basic-math/SKILL.md", "basic-math/calculate.py"))

    assert listing.blocks
    table = listing.blocks[2]
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[1][0].text == "basic-math"
    assert table.cells[1][1].text == "2"
    assert detail.blocks
    heading = detail.blocks[0]
    assert isinstance(heading, InputRichBlockSectionHeading)
    assert heading.text == "basic-math"


def test_memory_screen_uses_plain_rich_cells() -> None:
    memory = Memory(1, Scope("user", 7), "preference", "Likes **literal** tea", "now", "now")

    message = RichMessages.memory([memory], enabled=True)

    assert message.blocks
    table = message.blocks[1]
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[1][0].text == RichTextCode(text="1")
    assert table.cells[1][2].text == "Likes **literal** tea"


async def test_send_documents_uploads_container_files() -> None:
    bot = AsyncMock()
    incoming = Message(
        message_id=17,
        date=0,
        chat=Chat(id=42, type="private", first_name="Alice"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="Hello",
    )

    await RichMessages(bot).send_documents(
        incoming, (GeneratedFile("Архитектура.md", b"# architecture"),)
    )

    kwargs = bot.send_document.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["disable_content_type_detection"] is True
    assert kwargs["reply_parameters"] == ReplyParameters(
        message_id=17,
        allow_sending_without_reply=True,
    )
    document = kwargs["document"]
    assert isinstance(document, BufferedInputFile)
    assert document.filename == "Архитектура.md"


async def test_send_images_uploads_each_image_as_a_photo() -> None:
    bot = AsyncMock()
    incoming = Message(
        message_id=17,
        date=0,
        chat=Chat(id=42, type="private", first_name="Alice"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="Hello",
    )

    await RichMessages(bot).send_images(incoming, (b"first", b"second"))

    assert bot.send_photo.await_count == 2
    first = bot.send_photo.await_args_list[0].kwargs
    second = bot.send_photo.await_args_list[1].kwargs
    assert isinstance(first["photo"], BufferedInputFile)
    assert first["photo"].filename == "skye-1.png"
    assert second["photo"].filename == "skye-2.png"
    assert first["reply_parameters"] == ReplyParameters(
        message_id=17,
        allow_sending_without_reply=True,
    )


async def test_send_is_standalone_by_default() -> None:
    bot = AsyncMock()
    incoming = Message(
        message_id=17,
        date=0,
        chat=Chat(id=42, type="private", first_name="Alice"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="Hello",
    )

    await RichMessages(bot).send(incoming, "Hi.")

    kwargs = bot.send_rich_message.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["message_thread_id"] is None
    assert kwargs["rich_message"] == InputRichMessage(markdown="Hi.")
    assert kwargs["reply_parameters"] is None


async def test_send_quotes_when_reply_to_is_set() -> None:
    bot = AsyncMock()
    incoming = Message(
        message_id=17,
        date=0,
        chat=Chat(id=42, type="private", first_name="Alice"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="Hello",
    )

    await RichMessages(bot).send(incoming, "Hi.", reply_to=123)

    kwargs = bot.send_rich_message.await_args.kwargs
    assert kwargs["reply_parameters"] == ReplyParameters(
        message_id=123,
        allow_sending_without_reply=True,
    )


async def test_send_keeps_forum_topic_without_quoting() -> None:
    bot = AsyncMock()
    incoming = Message(
        message_id=20,
        date=0,
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="Hello",
        message_thread_id=9,
        is_topic_message=True,
    )

    await RichMessages(bot).send(incoming, "Hi.")

    kwargs = bot.send_rich_message.await_args.kwargs
    assert kwargs["message_thread_id"] == 9
    assert kwargs["reply_parameters"] is None


async def test_send_ignores_reply_only_thread_id_without_quoting() -> None:
    bot = AsyncMock()
    incoming = Message(
        message_id=21,
        date=0,
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="Hello",
        message_thread_id=19,
    )

    await RichMessages(bot).send(incoming, "Hi.")

    kwargs = bot.send_rich_message.await_args.kwargs
    assert kwargs["message_thread_id"] is None
    assert kwargs["reply_parameters"] is None


async def test_edit_ignores_unchanged_content() -> None:
    bot = AsyncMock()
    bot.edit_message_text.side_effect = TelegramBadRequest(
        method=AsyncMock(),
        message=(
            "Bad Request: message is not modified: specified new message content "
            "and reply markup are exactly the same as a current content and reply "
            "markup of the message"
        ),
    )
    incoming = Message(
        message_id=8,
        date=0,
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="Thinking…",
    )

    await RichMessages(bot).edit(incoming, "Thinking…")


async def test_edit_still_raises_other_bad_requests() -> None:
    bot = AsyncMock()
    bot.edit_message_text.side_effect = TelegramBadRequest(
        method=AsyncMock(),
        message="Bad Request: message to edit not found",
    )
    incoming = Message(
        message_id=8,
        date=0,
        chat=Chat(id=-100, type="supergroup", title="Skye Lab"),
        from_user=User(id=42, is_bot=False, first_name="Alice"),
        text="Thinking…",
    )

    with pytest.raises(TelegramBadRequest):
        await RichMessages(bot).edit(incoming, "Done.")


def test_account_screen_shows_renewal_status() -> None:
    message = RichMessages.account(
        owner=False,
        complimentary=False,
        plan_name="Skye Plus",
        plan_emoji="🌙",
        status="18 days left. Renews automatically. Telegram Stars will be charged again "
        "at the end of this period.",
    )
    assert message.blocks
    heading = message.blocks[1]
    status = message.blocks[2]
    assert isinstance(heading, InputRichBlockSectionHeading)
    assert heading.text == "🌙 Skye Plus"
    assert isinstance(status, InputRichBlockParagraph)
    assert "Terra" not in str(status.text)
    assert "Luna" not in str(status.text)
    assert "Renews automatically" in str(status.text)


def test_plus_agents_prompt_points_to_account() -> None:
    message = RichMessages.plus_agents()
    assert message.blocks
    heading = message.blocks[0]
    body = message.blocks[1]
    assert isinstance(heading, InputRichBlockSectionHeading)
    assert heading.text == "Agents"
    assert isinstance(body, InputRichBlockParagraph)
    blob = str(body.text)
    assert "Skye Plus" in blob
    assert "/account" in blob
    assert "token" not in blob.lower()
    assert "Luna" not in blob


def test_plan_checkout_has_a_collapsed_plans_details_block() -> None:
    message = RichMessages.plan_checkout(
        name="Skye Plus",
        emoji="🌙",
        stars=449,
        recurring=True,
    )
    assert message.blocks
    assert isinstance(message.blocks[0], InputRichBlockSectionHeading)
    assert message.blocks[0].text == "🌙 Skye Plus"
    details = message.blocks[-1]
    assert isinstance(details, InputRichBlockDetails)
    assert details.summary == "Plans"
    assert details.is_open is not True
    blob = str(details.blocks[0].text)
    assert "expanded daily message allowance" in blob.lower()
    assert "your own agents" in blob.lower()
    assert "Luna" not in blob
    assert "token" not in blob.lower()
