#!/usr/bin/env bash
# Launch the Streamlit demo in an isolated uv environment.
#
# Uses --no-project so it never touches the bot's pyproject/venv, and pins a
# Python version Streamlit is known to work on (the bot runs 3.14).
#
#   ./streamlit/run.sh                 # demo mode (in-memory data)
#   SKYE_API_URL=http://127.0.0.1:8080 ./streamlit/run.sh
#
# Any extra args are forwarded to `streamlit run`, e.g. --server.port 8600.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

exec uv run --no-project --python 3.12 \
  --with 'streamlit>=1.57' \
  --with 'httpx>=0.28' \
  streamlit run app.py "$@"
