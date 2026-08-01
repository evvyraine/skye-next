from aiogram.types import InputMediaPhoto, InputRichBlockTable, InputRichBlockThinking

from skye.models import ChatSettings, Memory, Scope
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


def test_thinking_block_is_valid_for_drafts() -> None:
    block = InputRichBlockThinking(text="Thinking…")

    assert block.type == "thinking"


def test_memory_screen_uses_plain_rich_cells() -> None:
    memory = Memory(1, Scope("user", 7), "preference", "Likes **literal** tea", "now", "now")

    message = RichMessages.memory([memory], enabled=True)

    assert message.blocks
    table = message.blocks[1]
    assert isinstance(table, InputRichBlockTable)
    assert table.cells[1][2].text == "Likes **literal** tea"
