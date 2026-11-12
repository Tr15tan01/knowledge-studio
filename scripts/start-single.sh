#!/usr/bin/env bash
# Single-service deployment: the API (with the background worker inside it) and the web app
# run side by side in one service. The web app listens on $PORT and proxies /api/* to the API.
# See docs/deployment.md, "One free service".
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python}"

export RUN_WORKER_IN_API=1
# On Render the public address is known; use it unless set explicitly.
if [ -n "${RENDER_EXTERNAL_URL:-}" ]; then
  export APP_PUBLIC_URL="${APP_PUBLIC_URL:-$RENDER_EXTERNAL_URL}"
  export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-$RENDER_EXTERNAL_URL}"
fi

cd "$ROOT/backend"
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1 &
API=$!
cd "$ROOT/frontend"
npx next start --port "${PORT:-3000}" &
WEB=$!

stop() { kill "$API" "$WEB" 2>/dev/null || true; }
trap 'stop; wait; exit 0' TERM INT
# If either process exits, stop the other so the platform restarts the whole service.
wait -n "$API" "$WEB" || true
stop
wait || true
exit 1
