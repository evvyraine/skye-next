FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/

RUN groupadd --system --gid 10001 skye \
    && useradd --system --uid 10001 --gid skye --home-dir /nonexistent skye

WORKDIR /app

# Install third-party dependencies separately so source changes reuse this layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY BASE_PROMPT.md ./BASE_PROMPT.md
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable \
    && mkdir /data \
    && chown skye:skye /data

USER skye

CMD ["skye"]
