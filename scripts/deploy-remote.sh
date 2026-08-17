#!/usr/bin/env bash
set -euo pipefail

ROOT=/opt/skye-next
SITE_ROOT=/var/www/skye-bot.com
CHAT_ROOT=/var/www/chat.skye-bot.com
SHA="${GITHUB_SHA:-manual}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SHORT_SHA="$(printf '%s' "$SHA" | cut -c1-12)"
RELEASE="${SHORT_SHA}-${STAMP}"

cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "missing ${ROOT}/.env" >&2
  exit 1
fi

chown -R root:root "$ROOT"
chmod 600 .env

docker compose up -d --build --remove-orphans

if [[ -d "${ROOT}/site" ]]; then
  mkdir -p "${SITE_ROOT}/releases/${RELEASE}"
  rsync -a --delete --exclude '.DS_Store' "${ROOT}/site/" "${SITE_ROOT}/releases/${RELEASE}/"
  ln -sfn "${SITE_ROOT}/releases/${RELEASE}" "${SITE_ROOT}/current"
  ls -1dt "${SITE_ROOT}/releases"/* | tail -n +6 | xargs -r rm -rf
fi

if [[ -d "${ROOT}/web/dist" ]]; then
  mkdir -p "${CHAT_ROOT}/releases/${RELEASE}"
  rsync -a --delete --exclude '.DS_Store' "${ROOT}/web/dist/" "${CHAT_ROOT}/releases/${RELEASE}/"
  ln -sfn "${CHAT_ROOT}/releases/${RELEASE}" "${CHAT_ROOT}/current"
  ls -1dt "${CHAT_ROOT}/releases"/* | tail -n +6 | xargs -r rm -rf
fi

CADDYFILE=/etc/caddy/Caddyfile
CADDY_SNIPPET="${ROOT}/scripts/caddy-chat.skye-bot.com.caddy"
if [[ -f "$CADDY_SNIPPET" && -f "$CADDYFILE" ]]; then
  python3 - "$CADDYFILE" "$CADDY_SNIPPET" <<'PY'
from pathlib import Path
import sys

caddyfile = Path(sys.argv[1])
snippet = Path(sys.argv[2]).read_text()
begin = "# --- skye-next chat.skye-bot.com (managed) ---"
end = "# --- end skye-next chat.skye-bot.com ---"
text = caddyfile.read_text()
if begin in text and end in text:
    before, rest = text.split(begin, 1)
    _, after = rest.split(end, 1)
    text = before.rstrip() + "\n\n" + snippet.strip() + "\n" + after.lstrip("\n")
elif "chat.skye-bot.com" in text:
    raise SystemExit("chat.skye-bot.com is already in Caddyfile without managed markers")
else:
    text = text.rstrip() + "\n\n" + snippet.strip() + "\n"
caddyfile.write_text(text)
PY
  caddy validate --config "$CADDYFILE"
  systemctl reload caddy
fi

sleep 4
if ! docker inspect -f '{{.State.Running}}' skye-next | grep -qx true; then
  docker compose logs --tail 80
  echo "skye-next is not running" >&2
  exit 1
fi

docker compose ps
echo "deployed ${RELEASE}"
