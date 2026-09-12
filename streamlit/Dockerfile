# Streamlit beta frontend (variant A: thin client over the bot's /api).
#
# Build context is the repo root so the root .dockerignore applies:
#   docker build -f streamlit/Dockerfile -t skye-next-beta .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/beta

WORKDIR /app

# Only what the demo needs. Keep the ranges loose; the app targets Streamlit's
# CCv2 API (>=1.57) and httpx for the API + SSE client.
RUN pip install --no-cache-dir "streamlit>=1.57,<2" "httpx>=0.28,<1"

COPY streamlit/ ./

RUN useradd --system --uid 10002 --create-home --home-dir /home/beta beta \
    && chown -R beta:beta /app
USER beta

EXPOSE 8501

# Overridden by compose to add --server.baseUrlPath=beta for the /beta mount.
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
