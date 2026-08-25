from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardMarkup,
    InputRichBlockDetails,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichBlockUnion,
    InputRichMessage,
    Message,
    ReplyKeyboardMarkup,
    RichBlockTableCell,
    RichTextBold,
    RichTextCode,
    RichTextUnion,
    RichTextUrl,
)

from .artifacts import GeneratedFile
from .citations import sanitize_citations
from .config import Reasoning
from .models import (
    AccessEffect,
    AccessEntry,
    AppConnector,
    Automation,
    ChatSettings,
    ConnectorShare,
    ConnectorSnapshot,
    CustomConnector,
    InstalledAgent,
    KnownGroup,
    Memory,
    Scope,
    Skill,
    TelegramProject,
)
from .telegram_threads import api_thread_id, quote_reply, reply_parameters
from .ui import activity_message, decorate_keyboard


class RichMessages:
    """The single boundary for every visible message Skye sends."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(
        self,
        target: Message,
        content: str | InputRichMessage,
        reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
        *,
        reply_to: int | None = None,
    ) -> Message:
        return await self.bot.send_rich_message(
            chat_id=target.chat.id,
            message_thread_id=api_thread_id(target),
            rich_message=self._content(content),
            reply_parameters=quote_reply(reply_to),
            reply_markup=decorate_keyboard(reply_markup),
        )

    async def send_chat(
        self,
        chat_id: int,
        thread_id: int,
        content: str | InputRichMessage,
        reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
        *,
        reply_to: int | None = None,
    ) -> Message:
        return await self.bot.send_rich_message(
            chat_id=chat_id,
            message_thread_id=thread_id or None,
            rich_message=self._content(content),
            reply_parameters=quote_reply(reply_to),
            reply_markup=decorate_keyboard(reply_markup),
        )

    async def edit(
        self,
        message: Message,
        content: str | InputRichMessage,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        try:
            await self.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.message_id,
                rich_message=self._content(content),
                reply_markup=decorate_keyboard(reply_markup),
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message.lower():
                raise

    async def send_images(self, target: Message, images: Sequence[bytes]) -> None:
        await self.send_images_chat(
            target.chat.id,
            api_thread_id(target) or 0,
            images,
            reply=target,
        )

    async def send_images_chat(
        self,
        chat_id: int,
        thread_id: int,
        images: Sequence[bytes],
        reply: Message | None = None,
    ) -> None:
        for index, image in enumerate(images, start=1):
            await self.bot.send_photo(
                chat_id=chat_id,
                message_thread_id=thread_id or None,
                photo=BufferedInputFile(image, filename=f"skye-{index}.png"),
                reply_parameters=reply_parameters(reply) if reply is not None else None,
            )

    async def send_documents(self, target: Message, files: Sequence[GeneratedFile]) -> None:
        await self.send_documents_chat(
            target.chat.id,
            api_thread_id(target) or 0,
            files,
            reply=target,
        )

    async def send_documents_chat(
        self,
        chat_id: int,
        thread_id: int,
        files: Sequence[GeneratedFile],
        reply: Message | None = None,
    ) -> None:
        for item in files:
            await self.bot.send_document(
                chat_id=chat_id,
                message_thread_id=thread_id or None,
                document=BufferedInputFile(item.data, filename=item.filename),
                reply_parameters=reply_parameters(reply) if reply is not None else None,
                disable_content_type_detection=True,
            )

    async def send_voice(
        self,
        target: Message,
        audio: bytes,
        reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
        *,
        reply_to: int | None = None,
    ) -> Message:
        return await self.send_voice_chat(
            target.chat.id,
            api_thread_id(target) or 0,
            audio,
            reply_markup=decorate_keyboard(reply_markup),
            reply_to=reply_to,
        )

    async def send_voice_chat(
        self,
        chat_id: int,
        thread_id: int,
        audio: bytes,
        reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
        *,
        reply_to: int | None = None,
    ) -> Message:
        return await self.bot.send_voice(
            chat_id=chat_id,
            message_thread_id=thread_id or None,
            voice=BufferedInputFile(audio, filename="voice.ogg"),
            reply_parameters=quote_reply(reply_to),
            reply_markup=reply_markup,
        )

    async def delete(self, message: Message) -> None:
        with suppress(TelegramBadRequest):
            await self.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    async def draft(self, target: Message, text: str | None = None) -> None:
        content = self._content(text) if text else activity_message(draft=True)
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
        skill_count: int | None = None,
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
        if skill_count is not None:
            rows.append(
                [
                    cell("Skills"),
                    cell(f"{skill_count} uploaded" if skill_count else "None"),
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
    def account(
        *,
        owner: bool,
        complimentary: bool,
        plan_name: str | None,
        plan_emoji: str | None,
        status: str | None,
        notice: str | None = None,
    ) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [InputRichBlockSectionHeading(text="Account", size=2)]
        if notice:
            blocks.append(InputRichBlockParagraph(text=notice))
        if owner:
            blocks.append(InputRichBlockParagraph(text="Owner access. No Stars plan is required."))
            return InputRichMessage(blocks=blocks)
        if plan_name and plan_emoji and status:
            blocks.append(InputRichBlockSectionHeading(text=f"{plan_emoji} {plan_name}", size=3))
            blocks.append(InputRichBlockParagraph(text=status))
        elif status:
            blocks.append(InputRichBlockParagraph(text=status))
        elif complimentary:
            blocks.append(InputRichBlockParagraph(text="Complimentary access from the allowlist."))
        else:
            blocks.append(
                InputRichBlockParagraph(
                    text=(
                        "Free plan, with a basic daily message allowance. "
                        "Subscribe to Skye Plus for an expanded daily message allowance, "
                        "paid in Telegram Stars."
                    )
                )
            )
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def plan_checkout(
        *,
        name: str,
        emoji: str,
        stars: int,
        recurring: bool,
    ) -> InputRichMessage:
        if recurring:
            price = f"{stars} Telegram Stars each month"
            access = "Expanded daily message allowance while the plan is active."
        else:
            price = f"{stars} Telegram Stars, once"
            access = "Access for a limited time. This offer can be used once."
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=f"{emoji} {name}", size=2),
                InputRichBlockParagraph(text=f"{price}. {access}"),
                RichMessages._plan_details(),
            ]
        )

    @staticmethod
    def plan_terms() -> InputRichMessage:
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text="Skye plans", size=2),
                InputRichBlockParagraph(
                    text=(
                        "Paid access uses Telegram Stars. Skye Plus is the paid plan. "
                        "The free plan has a basic daily message allowance. "
                        "Creating and editing agents is on Plus."
                    )
                ),
                RichMessages._plan_details(open_by_default=True),
            ]
        )

    @staticmethod
    def _plan_details(*, open_by_default: bool = False) -> InputRichBlockDetails:
        return InputRichBlockDetails(
            summary="Plans",
            blocks=[
                InputRichBlockParagraph(
                    text=(
                        "Free includes a basic daily message allowance. "
                        "Creating agents is not included. "
                        "Skye Plus, 449 Stars each month, includes an expanded daily "
                        "message allowance and your own agents. Paid in Telegram Stars."
                    )
                ),
            ],
            is_open=True if open_by_default else None,
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
    def agents(agents: Sequence[InstalledAgent], active_agent_id: str | None) -> InputRichMessage:
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

        capabilities = ", ".join(item.title() for item in version.capabilities) or "None"
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=version.name, size=2),
                InputRichBlockParagraph(text=version.description),
                InputRichBlockTable(
                    cells=[
                        [cell("Role"), cell("Active" if active else "Specialist")],
                        [cell("Version"), cell(str(version.version))],
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
    def plus_agents() -> InputRichMessage:
        return RichMessages.prompt(
            "Agents",
            "Creating and editing agents is on Skye Plus. Open /account to upgrade.",
        )

    @staticmethod
    def agent_name_prompt(current: str | None = None) -> InputRichMessage:
        if current is None:
            body: RichTextUnion = "Send the agent name (up to 64 characters)."
        else:
            body = ["Send a new name, or ", _code("."), f" to keep “{current}”."]
        return RichMessages.prompt("Agent name", body)

    @staticmethod
    def agent_description_prompt(current: str | None = None) -> InputRichMessage:
        body: list[RichTextUnion] = ["What is this agent good at? Send 1–240 characters"]
        if current is not None:
            body.extend([", or ", _code("."), f" to keep “{current}”"])
        body.append(".")
        return RichMessages.prompt("Agent description", body)

    @staticmethod
    def projects(
        projects: Sequence[TelegramProject],
        active_id: str,
        *,
        notice: str | None = None,
    ) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text="Projects", size=2),
            InputRichBlockParagraph(
                text="Each project is its own conversation, with optional extra instructions."
            ),
        ]
        if notice:
            blocks.append(InputRichBlockParagraph(text=notice))
        active = next((item for item in projects if item.id == active_id), None)
        if active is not None:
            blocks.append(InputRichBlockParagraph(text=f"Now in {active.label}."))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def project(
        project: TelegramProject,
        active: bool,
        *,
        notice: str | None = None,
    ) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text=project.label, size=2),
        ]
        if notice:
            blocks.append(InputRichBlockParagraph(text=notice))
        status = "Active. New messages continue this conversation."
        if not active:
            status = "Inactive. Switch to continue this conversation."
        blocks.append(InputRichBlockParagraph(text=status))
        if project.instructions:
            blocks.append(InputRichBlockParagraph(text=project.instructions[:1000]))
        else:
            blocks.append(InputRichBlockParagraph(text="No extra instructions."))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def project_name_prompt(current: str | None = None) -> InputRichMessage:
        if current is None:
            body: RichTextUnion = "Send a name (up to 64 characters). You can start with an emoji."
        else:
            body = ["Send a new name, or ", _code("."), f" to keep “{current}”."]
        return RichMessages.prompt("Project name", body)

    @staticmethod
    def project_emoji_prompt() -> InputRichMessage:
        return RichMessages.prompt(
            "Project emoji",
            "Pick an emoji, or send any emoji.",
        )

    @staticmethod
    def project_instructions_prompt(keep: bool = False) -> InputRichMessage:
        body: list[RichTextUnion] = [
            "Optional extra instructions for this project. They are added to the current agent"
        ]
        if keep:
            body.extend(
                [". Send new text, ", _code("."), " to keep them, or ", _code("-"), " to clear"]
            )
        body.append(".")
        return RichMessages.prompt("Project instructions", body)

    @staticmethod
    def project_reset_confirm(project: TelegramProject) -> InputRichMessage:
        return RichMessages.prompt(
            "Reset this chat?",
            [
                "Start a new conversation in ",
                _bold(project.label),
                ". Long-term memory is kept.",
            ],
        )

    @staticmethod
    def project_delete_confirm(project: TelegramProject) -> InputRichMessage:
        return RichMessages.prompt(
            "Delete this project?",
            ["Delete ", _bold(project.label), "? The conversation will be removed."],
        )

    @staticmethod
    def agent_instructions_prompt(*, keep: bool = False) -> InputRichMessage:
        body: list[RichTextUnion] = ["Send the agent instructions as text or a Markdown file"]
        if keep:
            body.extend([", or ", _code("."), " to keep the current instructions"])
        body.append(".")
        return RichMessages.prompt("Instructions", body)

    @staticmethod
    def agent_import_usage() -> InputRichMessage:
        return RichMessages.prompt(
            "Import an agent",
            ["Use ", _code("/agents import <share link or token>"), "."],
        )

    @staticmethod
    def agent_installed(name: str, version: int, *, hint: bool = False) -> InputRichMessage:
        body: list[RichTextUnion] = ["Installed ", _bold(name), f" v{version}."]
        if hint:
            body.append(" Use /agents to select or inspect it.")
        return InputRichMessage(blocks=[InputRichBlockParagraph(text=body)])

    @staticmethod
    def agent_share_link(url: str) -> InputRichMessage:
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text="Share this version", size=2),
                InputRichBlockParagraph(text="Immutable share link for this version."),
                InputRichBlockParagraph(text=RichTextUrl(text="Install this agent", url=url)),
            ]
        )

    @staticmethod
    def prompt(title: str, body: RichTextUnion) -> InputRichMessage:
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=title, size=2),
                InputRichBlockParagraph(text=body),
            ]
        )

    @staticmethod
    def access_target(target: Scope) -> list[RichTextUnion]:
        return [f"{target.kind} ", _code(str(target.id))]

    @staticmethod
    def access_change(verb: str, target: Scope) -> list[RichTextUnion]:
        return [f"{verb} {target.kind} ", _code(str(target.id)), "."]

    @staticmethod
    def admin_prompt(action: str) -> InputRichMessage:
        if action == "allow":
            title, verb = "Allow", "allow"
        elif action == "ban":
            title, verb = "Ban", "ban"
        else:
            title, verb = "Remove", "remove"
        return RichMessages.prompt(
            title,
            f"Reply to this message with the numeric Telegram id to {verb}. "
            "Negative ids are groups.",
        )

    @staticmethod
    def access(
        entries: Sequence[AccessEntry],
        *,
        notice: RichTextUnion | None = None,
        group_effect: AccessEffect | None = None,
        in_group: bool = False,
        show_entries: bool = True,
    ) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [InputRichBlockSectionHeading(text="Access", size=2)]
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

        def cell(text: RichTextUnion, *, header: bool = False) -> RichBlockTableCell:
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
                cell(_code(str(entry.scope.id))),
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
        if groups:
            body: RichTextUnion = (
                "Pick a group. Skye only lists allowlisted groups where you have written."
            )
        else:
            body = [
                "Skye only knows groups where you have written while the bot is allowlisted. "
                "In a group, open ",
                _code("/settings"),
                " and tap Attach one of mine.",
            ]
        return RichMessages.prompt(f"Share {name}", body)

    @staticmethod
    def connector_share_confirm(name: str, group: str, *, sensitive: bool) -> InputRichMessage:
        suffix = f" with {group}? Anyone there can ask Skye to use it."
        if sensitive:
            suffix += " Replies that use this app will be visible to everyone in the group."
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=f"Share {name}", size=2),
                InputRichBlockParagraph(text=["Share ", _bold(name), suffix]),
            ]
        )

    @staticmethod
    def connector_remove_confirm(name: str) -> InputRichMessage:
        return RichMessages.prompt(
            "Remove connector",
            ["Remove ", _bold(name), "? Skye will stop using this server."],
        )

    @staticmethod
    def connector_search_prompt() -> InputRichMessage:
        return RichMessages.prompt(
            "Search apps", "Send the app name to search, then pick a result."
        )

    @staticmethod
    def connector_name_prompt() -> InputRichMessage:
        return RichMessages.prompt("Custom MCP", "Send a short name for this MCP server.")

    @staticmethod
    def connector_url_prompt() -> InputRichMessage:
        return RichMessages.prompt("Custom MCP", "Send the public https:// MCP URL.")

    @staticmethod
    def connector_headers_prompt(*, skip: bool = False) -> InputRichMessage:
        if skip:
            body: RichTextUnion = [
                "Optional headers, one ",
                _code("Name: value"),
                " per line. Send ",
                _code("."),
                " to skip.",
            ]
        else:
            body = [
                "Send headers as ",
                _code("Name: value"),
                " lines, JSON, ",
                _code("."),
                " to keep them, or ",
                _code("-"),
                " to clear them.",
            ]
        return RichMessages.prompt("Headers", body)

    @staticmethod
    def connector_edit_prompt(field: str, name: str) -> InputRichMessage:
        if field == "name":
            return RichMessages.prompt(
                "Rename",
                ["Send a new name, or ", _code("."), f" to keep “{name}”."],
            )
        if field == "url":
            return RichMessages.prompt(
                "Change URL",
                ["Send the new https:// URL, or ", _code("."), " to keep the current one."],
            )
        return RichMessages.connector_headers_prompt()

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
    def skills(skills: Sequence[Skill]) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text="Skills", size=2),
            InputRichBlockParagraph(
                text=(
                    "Hosted skills mounted in the sandbox for this chat. "
                    "Upload a zip bundle or a SKILL.md file."
                )
            ),
        ]
        if not skills:
            blocks.append(InputRichBlockParagraph(text="No skills uploaded yet."))
            return InputRichMessage(blocks=blocks)

        def cell(text: str, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="middle",
            )

        rows = [
            [cell("Skill", header=True), cell("Files", header=True)],
        ]
        rows.extend([cell(item.name), cell(str(item.file_count))] for item in skills)
        blocks.append(InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def skill(skill: Skill, *, files: Sequence[str] = ()) -> InputRichMessage:
        def cell(text: str) -> RichBlockTableCell:
            return RichBlockTableCell(text=text, align="left", valign="top")

        names = [PurePosixPath(path).name for path in files[:12]]
        listed = ", ".join(names) if names else "None"
        if len(files) > 12:
            listed += f", +{len(files) - 12} more"
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=skill.name, size=2),
                InputRichBlockParagraph(text=skill.description),
                InputRichBlockTable(
                    cells=[
                        [cell("Files"), cell(str(skill.file_count))],
                        [cell("Bundle"), cell(listed)],
                    ],
                    is_bordered=True,
                    is_striped=True,
                ),
            ]
        )

    @staticmethod
    def skill_delete_confirm(name: str) -> InputRichMessage:
        return RichMessages.prompt(
            "Delete skill",
            [
                "Delete ",
                _bold(name),
                "? This removes it from this chat and from OpenAI.",
            ],
        )

    @staticmethod
    def skill_upload_prompt() -> InputRichMessage:
        return RichMessages.prompt(
            "Add a skill",
            [
                "Send a ",
                _code(".zip"),
                " skill bundle or a ",
                _code("SKILL.md"),
                " file. Every file in the zip is stored and uploaded together.",
            ],
        )

    @staticmethod
    def memory(memories: list[Memory], enabled: bool) -> InputRichMessage:
        heading = f"Memory · {'On' if enabled else 'Off'}"
        blocks: list[InputRichBlockUnion] = [InputRichBlockSectionHeading(text=heading, size=2)]
        if not memories:
            blocks.append(InputRichBlockParagraph(text="No memories saved yet."))
            return InputRichMessage(blocks=blocks)

        def cell(text: RichTextUnion, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="top",
            )

        rows = [[cell("ID", header=True), cell("Kind", header=True), cell("Memory", header=True)]]
        rows.extend(
            [cell(_code(str(memory.id))), cell(memory.category.title()), cell(memory.content)]
            for memory in memories
        )
        blocks.append(InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def memory_clear_confirm() -> InputRichMessage:
        return RichMessages.prompt(
            "Delete all memories?",
            "This cannot be undone. Conversation history is separate.",
        )

    @staticmethod
    def automations(items: Sequence[Automation]) -> InputRichMessage:
        blocks: list[InputRichBlockUnion] = [
            InputRichBlockSectionHeading(text="Automations", size=2),
        ]
        if not items:
            blocks.append(
                InputRichBlockParagraph(
                    text="None in this chat. Ask Skye to create a scheduled or webhook automation."
                )
            )
            return InputRichMessage(blocks=blocks)

        def cell(text: str | RichTextUnion, *, header: bool = False) -> RichBlockTableCell:
            return RichBlockTableCell(
                text=text,
                is_header=header or None,
                align="left",
                valign="middle",
            )

        rows = [[cell("Name", header=True), cell("Trigger", header=True)]]
        rows.extend([cell(item.name), cell(item.trigger_label)] for item in items)
        blocks.append(InputRichBlockTable(cells=rows, is_bordered=True, is_striped=True))
        return InputRichMessage(blocks=blocks)

    @staticmethod
    def automation_item(item: Automation) -> InputRichMessage:
        state = "On" if item.enabled else "Paused"
        trigger = item.trigger_label
        return RichMessages.prompt("Automation", f"{item.name} · {trigger} · {state}")

    @staticmethod
    def automation_hook(url: str, authorization: str) -> InputRichMessage:
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text="Webhook", size=2),
                InputRichBlockParagraph(
                    text="POST to this URL. Send this Authorization header exactly."
                ),
                InputRichBlockParagraph(text=["URL: ", _code(url)]),
                InputRichBlockParagraph(text=["Authorization: ", _code(authorization)]),
            ]
        )

    @staticmethod
    def automation_delete_confirm(name: str) -> InputRichMessage:
        return RichMessages.prompt(
            "Delete this automation?",
            f"“{name}” will be removed. This cannot be undone.",
        )

    @staticmethod
    def output(markdown: str) -> InputRichMessage:
        return InputRichMessage(markdown=sanitize_citations(markdown).strip() or "Done.")

    @staticmethod
    def _content(content: str | InputRichMessage) -> InputRichMessage:
        if isinstance(content, str):
            return InputRichMessage(markdown=sanitize_citations(content))
        markdown = getattr(content, "markdown", None)
        if isinstance(markdown, str) and markdown:
            cleaned = sanitize_citations(markdown)
            if cleaned != markdown:
                return content.model_copy(update={"markdown": cleaned})
        return content


def _bold(text: str) -> RichTextBold:
    return RichTextBold(text=text)


def _code(text: str) -> RichTextCode:
    return RichTextCode(text=text)


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.split("@")[-1]
    path = parts.path or "/"
    return f"https://{host}{path}"
