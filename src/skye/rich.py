from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichBlockThinking,
    InputRichBlockUnion,
    InputRichMessage,
    InputRichMessageMedia,
    Message,
    RichBlockTableCell,
)

from .config import MODELS, ModelId, Reasoning
from .models import (
    AccessEffect,
    AccessEntry,
    AppConnector,
    ChatSettings,
    ConnectorShare,
    ConnectorSnapshot,
    CustomConnector,
    InstalledAgent,
    KnownGroup,
    Memory,
)
from .telegram_threads import api_thread_id, reply_parameters


class RichMessages:
    """The single boundary for every visible message Skye sends."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(
        self,
        target: Message,
        content: str | InputRichMessage,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        return await self.bot.send_rich_message(
            chat_id=target.chat.id,
            message_thread_id=api_thread_id(target),
            rich_message=self._content(content),
            reply_parameters=reply_parameters(target),
            reply_markup=reply_markup,
        )

    async def edit(
        self,
        message: Message,
        content: str | InputRichMessage,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        await self.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.message_id,
            rich_message=self._content(content),
            reply_markup=reply_markup,
        )

    async def draft(self, target: Message, text: str | None = None) -> None:
        content = (
            InputRichMessage(markdown=text)
            if text
            else InputRichMessage(blocks=[InputRichBlockThinking(text="Thinking…")])
        )
        await self.bot.send_rich_message_draft(
            chat_id=target.chat.id,
            message_thread_id=api_thread_id(target),
            draft_id=target.message_id,
            rich_message=content,
        )

    @staticmethod
    def settings(
        settings: ChatSettings,
        agent_name: str = "Skye",
        *,
        connector_count: int | None = None,
    ) -> InputRichMessage:
        def cell(text: str, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="middle",
            )

        rows = [
            [cell("Option", header=True), cell("Selected", header=True)],
            [cell("Model"), cell(MODELS[settings.model])],
            [cell("Reasoning"), cell(settings.reasoning.title())],
            [cell("Agent"), cell(agent_name)],
        ]
        if connector_count is not None:
            rows.append(
                [
                    cell("Connectors"),
                    cell(f"{connector_count} connected" if connector_count else "None"),
                ]
            )
        rows.append([cell("Memory"), cell("On" if settings.memory_enabled else "Off")])
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text="Settings", size=2),
                InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True),
            ]
        )

    @staticmethod
    def choose_model(model: ModelId) -> InputRichMessage:
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text="Choose your model", size=2),
                InputRichBlockParagraph(text=f"Currently {MODELS[model]}."),
            ]
        )

    @staticmethod
    def choose_reasoning(reasoning: Reasoning) -> InputRichMessage:
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text="Choose your reasoning", size=2),
                InputRichBlockParagraph(text=f"Currently {reasoning.title()}."),
            ]
        )

    @staticmethod
    def agents(
        agents: Sequence[InstalledAgent], active_agent_id: str | None
    ) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text="Agents", size=2),
            InputRichBlockParagraph(
                text=(
                    "Skye is active. Installed agents remain available as specialists."
                    if active_agent_id is None
                    else "The selected agent leads this chat; the others remain specialists."
                )
            ),
        ]
        if not agents:
            blocks.append(InputRichBlockParagraph(text="No custom agents installed yet."))
            return InputRichMessage(blocks=blocks)

        def cell(text: str, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="top",
            )

        rows = [
            [cell("Agent", header=True), cell("Version", header=True), cell("Role", header=True)]
        ]
        rows.extend(
            [
                cell(item.version.name),
                cell(str(item.version.version)),
                cell("Active" if item.profile.id == active_agent_id else "Specialist"),
            ]
            for item in agents
        )
        blocks.append(InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def agent(installed: InstalledAgent, active: bool) -> InputRichMessage:
        version = installed.version

        def cell(text: str) -> RichBlockTableCell:
            return RichBlockTableCell(text=text, align="left", valign="top")

        model = MODELS[version.model] if version.model else "Chat default"
        capabilities = ", ".join(item.title() for item in version.capabilities) or "None"
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=version.name, size=2),
                InputRichBlockParagraph(text=version.description),
                InputRichBlockTable(
                    cells=[
                        [cell("Role"), cell("Active" if active else "Specialist")],
                        [cell("Version"), cell(str(version.version))],
                        [cell("Model"), cell(model)],
                        [cell("Capabilities"), cell(capabilities)],
                    ],
                    is_bordered=True,
                    is_striped=True,
                ),
                InputRichBlockSectionHeading(text="Instructions", size=3),
                InputRichBlockParagraph(text=version.instructions[:4000]),
            ]
        )

    @staticmethod
    def agent_preview(name: str, description: str, instructions: str) -> InputRichMessage:
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=f"Preview · {name}", size=2),
                InputRichBlockParagraph(text=description),
                InputRichBlockSectionHeading(text="Instructions", size=3),
                InputRichBlockParagraph(text=instructions[:4000]),
            ]
        )

    @staticmethod
    def access(
        entries: Sequence[AccessEntry],
        *,
        notice: str | None = None,
        group_effect: AccessEffect | None = None,
        in_group: bool = False,
        show_entries: bool = True,
    ) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text="Access", size=2)
        ]
        if notice:
            blocks.append(InputRichBlockParagraph(text=notice))
        if in_group:
            if group_effect == "allow":
                status = "This group is allowlisted."
            elif group_effect == "ban":
                status = "This group is banned."
            else:
                status = "This group is not allowlisted."
            blocks.append(InputRichBlockParagraph(text=status))
        if not show_entries:
            return InputRichMessage(blocks=blocks)
        if not entries:
            blocks.append(InputRichBlockParagraph(text="The allowlist is empty."))
            return InputRichMessage(blocks=blocks)

        def cell(text: str, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="middle",
            )

        rows = [
            [
                cell("Kind", header=True),
                cell("Id", header=True),
                cell("Effect", header=True),
            ]
        ]
        rows.extend(
            [
                cell(entry.scope.kind),
                cell(str(entry.scope.id)),
                cell(entry.effect),
            ]
            for entry in entries
        )
        blocks.append(InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def connectors(snapshot: ConnectorSnapshot, *, configured: bool) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text="Connectors", size=2),
            InputRichBlockParagraph(
                text=(
                    "Apps and custom MCP servers Skye can use in this private chat. "
                    "Each person has their own list."
                )
            ),
        ]
        if not configured:
            blocks.append(
                InputRichBlockParagraph(
                    text="Hosted apps need COMPOSIO_API_KEY. Custom MCP still works."
                )
            )
        if not snapshot.apps and not snapshot.custom:
            blocks.append(InputRichBlockParagraph(text="No connectors yet."))
            return InputRichMessage(blocks=blocks)

        def cell(text: str, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="middle",
            )

        rows = [
            [cell("Connector", header=True), cell("Kind", header=True), cell("Status", header=True)]
        ]
        rows.extend(
            [cell(item.name), cell("App"), cell(item.status.title())] for item in snapshot.apps
        )
        rows.extend(
            [
                cell(item.name),
                cell("Custom MCP"),
                cell("On" if item.enabled else "Off"),
            ]
            for item in snapshot.custom
        )
        blocks.append(InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def connector_catalog(
        apps: Sequence[AppConnector], *, configured: bool, query: str | None = None
    ) -> InputRichMessage:
        heading = f"Apps matching “{query}”" if query else "Add an app"
        body = (
            "Search by name, or pick a popular one."
            if configured
            else "Hosted apps are not configured on this bot."
        )
        if query and not apps:
            body = "No apps matched that name."
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=heading, size=2),
                InputRichBlockParagraph(text=body),
            ]
        )

    @staticmethod
    def connector_app(
        app: AppConnector,
        *,
        connecting: bool = False,
        shares: Sequence[ConnectorShare] = (),
    ) -> InputRichMessage:
        if connecting:
            status = (
                "Open the secure page to connect your account. "
                "Credentials stay with Composio, not in Telegram."
            )
        elif app.status == "connected":
            status = "Connected. Skye can use this app when you ask in this private chat."
        elif app.no_auth:
            status = "This app does not need a sign-in. Tap Connect to enable it."
        else:
            status = "Not connected yet."
        description = app.description[:400] if app.description else ""
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text=app.name, size=2),
            InputRichBlockParagraph(text=status),
        ]
        if description:
            blocks.append(InputRichBlockParagraph(text=description))
        if shares:
            names = ", ".join(item.chat_title for item in shares)
            blocks.append(InputRichBlockParagraph(text=f"Shared with: {names}."))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def connector_custom(
        connector: CustomConnector, *, shares: Sequence[ConnectorShare] = ()
    ) -> InputRichMessage:
        header_names = ", ".join(connector.headers) if connector.headers else "None"

        def cell(text: str) -> RichBlockTableCell:
            return RichBlockTableCell(text=text, align="left", valign="top")

        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text=connector.name, size=2),
            InputRichBlockParagraph(
                text=(
                    "Custom MCP. Skye will call this HTTPS server when it is on."
                    if connector.enabled
                    else "This custom MCP is off."
                )
            ),
            InputRichBlockTable(
                cells=[
                    [cell("Status"), cell("On" if connector.enabled else "Off")],
                    [cell("URL"), cell(_safe_url(connector.url))],
                    [cell("Headers"), cell(header_names)],
                ],
                is_bordered=True,
                is_striped=True,
            ),
        ]
        if shares:
            names = ", ".join(item.chat_title for item in shares)
            blocks.append(InputRichBlockParagraph(text=f"Shared with: {names}."))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def group_connectors(shares: Sequence[ConnectorShare]) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text="Connectors", size=2),
            InputRichBlockParagraph(
                text=(
                    "Connectors shared with this group. Anyone here can ask Skye to use them. "
                    "Set them up in a private chat, then attach or share."
                )
            ),
        ]
        if not shares:
            blocks.append(InputRichBlockParagraph(text="Nothing is shared with this group yet."))
            return InputRichMessage(blocks=blocks)

        def cell(text: str, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="middle",
            )

        rows = [
            [
                cell("Connector", header=True),
                cell("Shared by", header=True),
                cell("Status", header=True),
            ]
        ]
        rows.extend(
            [
                cell(item.name),
                cell(item.owner_name),
                cell("On" if item.available else "Unavailable"),
            ]
            for item in shares
        )
        blocks.append(InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def connector_picker(name: str, groups: Sequence[KnownGroup]) -> InputRichMessage:
        body = (
            "Pick a group. Skye only lists allowlisted groups where you have written."
            if groups
            else (
                "Skye only knows groups where you have written while the bot is allowlisted. "
                "In a group, open /settings and tap Attach one of mine."
            )
        )
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=f"Share {name}", size=2),
                InputRichBlockParagraph(text=body),
            ]
        )

    @staticmethod
    def connector_share_confirm(name: str, group: str, *, sensitive: bool) -> InputRichMessage:
        body = f"Share **{name}** with {group}? Anyone there can ask Skye to use it."
        if sensitive:
            body += " Replies that use this app will be visible to everyone in the group."
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=f"Share {name}", size=2),
                InputRichBlockParagraph(text=body),
            ]
        )

    @staticmethod
    def connector_share(share: ConnectorShare) -> InputRichMessage:
        status = (
            f"Shared by {share.owner_name}. Anyone in this group can ask Skye to use it."
            if share.available
            else (
                f"Shared by {share.owner_name}, but it is no longer connected. "
                "Stop sharing to remove it from this group."
            )
        )
        kind = "Custom MCP" if share.kind == "custom" else "App"
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=share.name, size=2),
                InputRichBlockParagraph(text=status),
                InputRichBlockParagraph(text=f"{kind} · {share.chat_title}"),
            ]
        )

    @staticmethod
    def connector_mine(snapshot: ConnectorSnapshot) -> InputRichMessage:
        body = (
            "Attach one of your connected apps or custom servers to this group."
            if snapshot.apps or snapshot.custom
            else "Connect an app or custom MCP in a private chat first."
        )
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text="Attach one of yours", size=2),
                InputRichBlockParagraph(text=body),
            ]
        )

    @staticmethod
    def connector_preview(name: str, url: str, headers: Mapping[str, str]) -> InputRichMessage:
        header_names = ", ".join(headers) if headers else "None"
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=f"Preview · {name}", size=2),
                InputRichBlockParagraph(text=_safe_url(url)),
                InputRichBlockParagraph(text=f"Headers: {header_names}"),
            ]
        )

    @staticmethod
    def memory(memories: list[Memory], enabled: bool) -> InputRichMessage:
        heading = f"Memory · {'On' if enabled else 'Off'}"
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text=heading, size=2)
        ]
        if not memories:
            blocks.append(InputRichBlockParagraph(text="No memories saved yet."))
            return InputRichMessage(blocks=blocks)

        def cell(text: str, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="top",
            )

        rows = [[cell("ID", header=True), cell("Kind", header=True), cell("Memory", header=True)]]
        rows.extend(
            [cell(str(memory.id)), cell(memory.category.title()), cell(memory.content)]
            for memory in memories
        )
        blocks.append(InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def output(markdown: str, images: Sequence[bytes] = ()) -> InputRichMessage:
        media = [
            InputRichMessageMedia(
                id=f"image_{index}",
                media=InputMediaPhoto(
                    media=BufferedInputFile(image, filename=f"skye-{index}.png")
                ),
            )
            for index, image in enumerate(images, start=1)
        ]
        image_blocks = "\n\n".join(
            f"![Generated image {index}](tg://photo?id=image_{index})"
            for index in range(1, len(media) + 1)
        )
        body = "\n\n".join(part for part in (markdown.strip(), image_blocks) if part) or "Done."
        return InputRichMessage(markdown=body, media=media or None)

    @staticmethod
    def _content(content: str | InputRichMessage) -> InputRichMessage:
        if isinstance(content, InputRichMessage):
            return content
        return InputRichMessage(markdown=content)


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.split("@")[-1]
    path = parts.path or "/"
    return f"https://{host}{path}"
