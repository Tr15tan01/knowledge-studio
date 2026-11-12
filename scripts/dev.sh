#!/usr/bin/env bash
# macOS/Linux equivalents of the PowerShell scripts.
#   ./scripts/dev.sh setup | setup-db | api | worker | web | test | check | create-admin <email> [ROLE]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/backend/.venv/bin/python"

need_venv() { [ -x "$PY" ] || { echo "Run ./scripts/dev.sh setup first." >&2; exit 1; }; }
need_env() { [ -f "$ROOT/.env" ] || { echo "No .env. Run ./scripts/dev.sh setup, then set MONGODB_URI and GEMINI_API_KEY." >&2; exit 1; }; }

case "${1:-}" in
  setup)
    PYTHON="${PYTHON:-python3.13}"; command -v "$PYTHON" >/dev/null || PYTHON=python3
    "$PYTHON" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
    [ -x "$PY" ] || "$PYTHON" -m venv "$ROOT/backend/.venv"
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r "$ROOT/backend/requirements-dev.txt"
    (cd "$ROOT/frontend" && npm ci --no-audit --no-fund)
    if [ ! -f "$ROOT/.env" ]; then
      SECRET="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"
      sed "s|^SESSION_SECRET=.*|SESSION_SECRET=$SECRET|" "$ROOT/.env.example" > "$ROOT/.env"
      echo "Created .env with a new SESSION_SECRET. Set MONGODB_URI and GEMINI_API_KEY."
    fi
    [ -f "$ROOT/frontend/.env.local" ] || cp "$ROOT/frontend/.env.example" "$ROOT/frontend/.env.local"
    mkdir -p "$ROOT/backend/var/storage" ;;
  setup-db) need_venv; need_env; cd "$ROOT/backend"; "$PY" -m app.cli check-config; "$PY" -m app.cli setup-db; "$PY" -m app.cli setup-search-indexes --wait ;;
  api) need_venv; need_env; cd "$ROOT/backend"; exec "$PY" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips 127.0.0.1 ;;
  worker) need_venv; need_env; cd "$ROOT/backend"; exec "$PY" -m workers.main "${@:2}" ;;
  web) cd "$ROOT/frontend"; exec npx next dev --port "${PORT:-3000}" ;;
  test) need_venv; (cd "$ROOT/backend" && "$PY" -m pytest -q); (cd "$ROOT/frontend" && npm test) ;;
  check)
    need_venv
    (cd "$ROOT/backend" && "$PY" -m ruff check . && "$PY" -m ruff format --check . && "$PY" -m mypy app && "$PY" -m pip_audit -r requirements.txt)
    (cd "$ROOT/frontend" && npm run lint && npm run typecheck && npm audit --omit=dev) ;;
  create-admin) need_venv; need_env; cd "$ROOT/backend"; "$PY" -m app.cli create-admin --email "${2:?email required}" --role "${3:-ADMIN}" ;;
  *) sed -n '2,3p' "$0"; exit 1 ;;
esac
