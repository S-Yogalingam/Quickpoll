#!/usr/bin/env bash
# Run QuickPoll: one Flask process serving the API + server-rendered pages.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Load .env if present (PORT, MYSQL_*, etc.).
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
PORT="${PORT:-8090}"

# Create venv on first run.
if [ ! -d venv ]; then
  echo "==> Creating Python venv"
  python3 -m venv venv
  ./venv/bin/pip install --upgrade pip
  ./venv/bin/pip install -r requirements.txt
fi

# Ensure the schema exists (idempotent).
echo "==> Ensuring MySQL schema"
./venv/bin/python init_db.py

echo "==> Starting QuickPoll on 0.0.0.0:$PORT"
exec ./venv/bin/python app.py
