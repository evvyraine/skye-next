from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from importlib.resources import files
from pathlib import Path

import httpx
import structlog
from agents import set_default_openai_client, set_tracing_disabled
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommandScopeAllPrivateChats
from openai import AsyncOpenAI
from pydantic import ValidationError

from .access import AccessService, ChatAdministrator
from .attachments import AttachmentService
from .auth import TelegramAuth
from .automations import AutomationService
from .billing import BillingService
from .config import Settings
from .connectors import ComposioClient, ConnectorService
from .conversations import ConversationService
from .custom_agents import CustomAgentService
from .db import Database
from .group_context import GroupContextService
from .media_groups import MediaGroupService
from .memory import MemoryService
from .projects import ProjectService
from .providers import OpenRouterTransport
from .runtime import OPENAI_MAX_RETRIES, AgentRuntime
from .skills import SkillService
from .telegram import COMMANDS, PRIVATE_COMMANDS, TelegramApp, UpdateMiddleware
from .telegram_projects import TelegramProjectService
from .web import WebApp, serve_web

log = structlog.get_logger()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )


def load_base_prompt(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return files("skye").joinpath("BASE_PROMPT.md").read_text(encoding="utf-8")


async def run() -> None:
    configure_logging()
    config = load_settings()
    database = Database(
        config.skye_database_path,
        config.skye_default_model,
        config.skye_default_reasoning,
    )
    await database.open()

    http_client = (
        httpx.AsyncClient(
            transport=OpenRouterTransport(),
            timeout=httpx.Timeout(600, connect=10),
            follow_redirects=True,
        )
        if config.provider == "openrouter"
        else None
    )
    client = AsyncOpenAI(
        api_key=config.provider_api_key,
        base_url=config.provider_base_url,
        max_retries=OPENAI_MAX_RETRIES,
        http_client=http_client,
    )
    trace_with_openai = config.skye_tracing and config.provider == "openai"
    set_default_openai_client(client, use_for_tracing=trace_with_openai)
    set_tracing_disabled(not trace_with_openai)

    bot = Bot(config.telegram_bot_token)
    dispatcher = Dispatcher()
    remote_conversations = config.provider == "openai"
    conversations = ConversationService(database, client, remote=remote_conversations)
    memory = MemoryService(database)
    custom_agents = CustomAgentService(database)
    composio = ComposioClient(config.composio_api_key) if config.composio_api_key else None
    if config.composio_api_key:
        log.info(
            "composio_configured",
            key_prefix=config.composio_api_key.split("_", 1)[0],
            key_length=len(config.composio_api_key),
        )
    connectors = ConnectorService(database, composio)
    groups = GroupContextService(config, database, bot)
    media_groups = MediaGroupService(config, database)
    attachments = AttachmentService(config, bot, client)

    async def list_chat_administrators(
        chat_id: int,
    ) -> tuple[ChatAdministrator, ...] | None:
        try:
            members = await bot.get_chat_administrators(chat_id)
        except TelegramAPIError:
            log.warning("chat_admins_failed", chat_id=chat_id)
            return None
        return tuple(
            ChatAdministrator(
                user_id=member.user.id,
                is_creator=member.status == "creator",
                is_bot=member.user.is_bot,
            )
            for member in members
        )

    access = AccessService(
        database, config.skye_owner_ids, list_administrators=list_chat_administrators
    )
    billing = BillingService(database, config.telegram_bot_token)
    skills = SkillService(
        database, client, config.skye_max_attachment_bytes, provider=config.provider
    )
    automations = AutomationService(database, config.skye_web_origin)
    runtime = AgentRuntime(
        config,
        conversations,
        memory,
        load_base_prompt(config.skye_base_prompt_path),
        custom_agents,
        connectors,
        client,
        skills,
        automations,
    )
    projects = ProjectService(
        database,
        client,
        config.skye_web_files_path,
        remote_conversations=remote_conversations,
    )
    telegram_projects = TelegramProjectService(
        database, client, remote_conversations=remote_conversations
    )
    auth = TelegramAuth(config, database, projects)
    telegram = TelegramApp(
        config,
        bot,
        database,
        access,
        conversations,
        memory,
        custom_agents,
        connectors,
        groups,
        media_groups,
        attachments,
        runtime,
        skills,
        telegram_projects,
        billing,
        automations,
    )
    web_app = WebApp(
        config,
        database,
        access,
        runtime,
        projects,
        auth,
        client,
        automations,
        telegram.enqueue_automation,
    )
    dispatcher.update.outer_middleware(UpdateMiddleware(database, groups, media_groups))
    dispatcher.include_router(telegram.router)

    try:
        bot_info = await bot.get_me()
        if bot_info.can_join_groups and not bot_info.can_read_all_group_messages:
            structlog.get_logger().warning(
                "group_privacy_enabled",
                hint="Disable Group Privacy in BotFather or make the bot a group administrator.",
            )
        await bot.set_my_commands(COMMANDS)
        await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        dropped = await database.drop_pending_updates()
        if dropped:
            log.info("pending_updates_dropped", count=dropped)
        await bot.delete_webhook(drop_pending_updates=True)
        runner = await serve_web(web_app)
        scheduler = asyncio.create_task(
            automations.run_loop(telegram.fire_automation, runtime.busy)
        )
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        polling = asyncio.create_task(
            dispatcher.start_polling(
                bot,
                allowed_updates=sorted(
                    set(dispatcher.resolve_used_update_types()) | {"message", "edited_message"}
                ),
                handle_signals=False,
            )
        )
        try:
            await stop.wait()
        finally:
            polling.cancel()
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await polling
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
            await runner.cleanup()
    finally:
        await connectors.aclose()
        await client.close()
        await bot.session.close()
        await database.close()


def main() -> None:
    asyncio.run(run())


def load_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        fields = ", ".join(".".join(map(str, item["loc"])) for item in error.errors())
        raise SystemExit(
            f"Invalid configuration: {fields}. Check .env against .env.example."
        ) from None
