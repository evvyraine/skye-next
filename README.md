# Skye Next

A minimal, allowlisted Telegram bot built on the OpenAI Agents SDK and Telegram Rich Messages.

## Setup

```bash
cp .env.example .env
uv sync
uv run skye
```

Required environment variables:

- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `SKYE_OWNER_IDS` — comma-separated Telegram user ids

See [ARCHITECTURE.md](ARCHITECTURE.md) for the product and system design.

## Development

```bash
uv run ruff check .
uv run mypy
uv run pytest
```
