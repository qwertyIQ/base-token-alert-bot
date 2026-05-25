#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
while true; do
  set -a
  source "$ROOT/.env"
  set +a
  /usr/bin/python3 "$ROOT/base-alert-bot.py" || true
  sleep "${SCAN_INTERVAL_SECONDS:-120}"
done
