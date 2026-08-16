import asyncio
import logging
from importlib.resources import files
from pathlib import Path

import structlog
from agents import set_default_openai_client, set_tracing_disabled
from aiogram import Bot, Dispatcher
from openai import AsyncOpenAI
from pydantic import ValidationError

from .access import AccessService
from .attachments import AttachmentService
from .config import Settings
from .connectors import ComposioClient, ConnectorService
from .conversations import ConversationService
from .custom_agents import CustomAgentService
from .db import Database
from .group_context import GroupContextService
from .memory import MemoryService
from .runtime import OPENAI_MAX_RETRIES, AgentRuntime
from .skills import SkillService
from .telegram import COMMANDS, TelegramApp, UpdateMiddleware, replay_pending

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

    client = AsyncOpenAI(api_key=config.openai_api_key, max_retries=OPENAI_MAX_RETRIES)
    set_default_openai_client(client, use_for_tracing=config.skye_tracing)
    set_tracing_disabled(not config.skye_tracing)

    bot = Bot(config.telegram_bot_token)
    dispatcher = Dispatcher()
    conversations = ConversationService(database, client)
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
    attachments = AttachmentService(config, bot, client)
    access = AccessService(database, config.skye_owner_ids)
    skills = SkillService(database, client, config.skye_max_attachment_bytes)
    runtime = AgentRuntime(
        config,
        conversations,
        memory,
        load_base_prompt(config.skye_base_prompt_path),
        custom_agents,
        connectors,
        client,
        skills,
    )
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
        attachments,
        runtime,
        skills,
    )
    dispatcher.update.outer_middleware(UpdateMiddleware(database, groups))
    dispatcher.include_router(telegram.router)

    try:
        bot_info = await bot.get_me()
        if bot_info.can_join_groups and not bot_info.can_read_all_group_messages:
            structlog.get_logger().warning(
                "group_privacy_enabled",
                hint="Disable Group Privacy in BotFather or make the bot a group administrator.",
            )
        await bot.set_my_commands(COMMANDS)
        await replay_pending(dispatcher, bot, database)
        await dispatcher.start_polling(
            bot,
            allowed_updates=sorted(
                set(dispatcher.resolve_used_update_types()) | {"message", "edited_message"}
            ),
            handle_signals=True,
        )
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
