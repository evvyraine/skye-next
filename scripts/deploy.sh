#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WWW_ROOT="$HOME/www"
SITE_ROOT="$WWW_ROOT/skye-bot.com"
CHAT_ROOT="$WWW_ROOT/chat.skye-bot.com"
CADDYFILE=/opt/homebrew/etc/Caddyfile
SHA="${GITHUB_SHA:-manual}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SHORT_SHA="$(printf '%s' "$SHA" | cut -c1-12)"
RELEASE="${SHORT_SHA}-${STAMP}"

cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "missing $ROOT/.env" >&2
  exit 1
fi
chmod 600 .env

if [[ -f web/package.json ]]; then
  npm --prefix web ci --no-audit --no-fund
  npm --prefix web run build
fi

if ! colima status >/dev/null 2>&1; then
  colima start
fi

docker compose up -d --build --remove-orphans

release_into() {
  local src=$1 root=$2
  mkdir -p "$root/releases/$RELEASE"
  rsync -a --delete --exclude '.DS_Store' "$src/" "$root/releases/$RELEASE/"
  ln -sfn "$root/releases/$RELEASE" "$root/current"
  ls -1dt "$root/releases"/* | tail -n +6 | xargs -r rm -rf
}

release_into "$ROOT/site" "$SITE_ROOT"
release_into "$ROOT/web/dist" "$CHAT_ROOT"

if [[ -f "$CADDYFILE" ]]; then
  python3 - "$CADDYFILE" \
    "chat.skye-bot.com=$ROOT/scripts/caddy-chat.skye-bot.com.caddy" \
    "skye-bot.com=$ROOT/scripts/caddy-skye-bot.com.caddy" <<'PY'
import re
import sys
from pathlib import Path

caddyfile = Path(sys.argv[1])
text = caddyfile.read_text()
for arg in sys.argv[2:]:
    name, snippet_path = arg.split("=", 1)
    snippet = Path(snippet_path).read_text().strip()
    begin = f"# --- skye-next {name} (managed) ---"
    end = f"# --- end skye-next {name} ---"
    if begin in text and end in text:
        before, rest = text.split(begin, 1)
        _, after = rest.split(end, 1)
        text = before.rstrip() + "\n\n" + snippet + "\n" + after.lstrip("\n")
    elif re.search(rf"(?m)^{re.escape(name)} \{{", text):
        raise SystemExit(f"{name} is already in Caddyfile without managed markers")
    else:
        text = text.rstrip() + "\n\n" + snippet + "\n"
caddyfile.write_text(text)
PY
  caddy validate --config "$CADDYFILE"
  caddy reload --config "$CADDYFILE"
fi

sleep 4
if ! docker inspect -f '{{.State.Running}}' skye-next | grep -qx true; then
  docker compose logs --tail 80
  echo "skye-next is not running" >&2
  exit 1
fi

docker compose ps
echo "deployed ${RELEASE}"