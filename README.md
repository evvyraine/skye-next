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

Optional: `COMPOSIO_API_KEY` enables hosted app connections in `/settings`.
Custom HTTPS MCP servers work without it.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the product and system design.

## Website

The static site lives in `site/`. From the repository root:

```bash
python3 -m http.server 4173 --directory site
```

Then open `http://127.0.0.1:4173`.

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

Production on vilnius lives at `/opt/skye-next` (bot, Docker Compose) and
`/var/www/skye-bot.com/current` (static site). Pushes to `master` run GitHub Actions,
which lint/test and then rsync + rebuild. The server `.env` is not in git and is not
overwritten by deploys.

## Development

```bash
uv run ruff check .
uv run mypy
uv run pytest
```
