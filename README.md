# Skye NEXT

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

## Docker

Create the configuration file and start Skye in the background:

```bash
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, and SKYE_OWNER_IDS in .env.
docker compose up -d --build
```

The SQLite database is stored in the persistent `skye-data` Docker volume. Compose restarts the
bot after a failure and after a host reboot, unless it was stopped manually.

Common operations:

```bash
docker compose logs -f skye
docker compose ps
docker compose restart skye
docker compose stop
docker compose start
docker compose down
```

`docker compose down` removes the container but keeps the database volume. To deploy a new version,
pull the changes and run `docker compose up -d --build` again.

## Development

```bash
uv run ruff check .
uv run mypy
uv run pytest
```
