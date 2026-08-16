#!/usr/bin/env bash
set -euo pipefail

ROOT=/opt/skye-next
SITE_ROOT=/var/www/skye-bot.com
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
  # Keep the newest five releases.
  ls -1dt "${SITE_ROOT}/releases"/* | tail -n +6 | xargs -r rm -rf
fi

sleep 4
if ! docker inspect -f '{{.State.Running}}' skye-next | grep -qx true; then
  docker compose logs --tail 80
  echo "skye-next is not running" >&2
  exit 1
fi

docker compose ps
echo "deployed ${RELEASE}"
