#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ -f "$ROOT/.env" ]; then
  set -a
  . "$ROOT/.env"
  set +a
fi

export DATA_DIR=${DATA_DIR:-"$ROOT/data"}
export DATABASE_URL=${DATABASE_URL:-"sqlite:///$ROOT/data/service.sqlite"}
export MODEL_MAX_TOKENS_PLANNING=${MODEL_MAX_TOKENS_PLANNING:-6500}
# .env may contain command names (SOFFICE=soffice), not absolute paths.
SOFFICE=$(command -v "${SOFFICE:-soffice}" || true)
PDFTOPPM=$(command -v "${PDFTOPPM:-pdftoppm}" || true)
export SOFFICE PDFTOPPM

if [ -z "$SOFFICE" ] && [ -x /Applications/LibreOffice.app/Contents/MacOS/soffice ]; then
  export SOFFICE=/Applications/LibreOffice.app/Contents/MacOS/soffice
fi
for required in "$ROOT/.venv/bin/python" "$SOFFICE" "$PDFTOPPM"; do
  if [ -z "$required" ] || [ ! -x "$required" ]; then
    echo "Dependencies are incomplete. Run: make setup" >&2
    exit 2
  fi
done
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required. On Debian/Ubuntu: sudo apt-get install curl" >&2
  exit 2
fi

if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
  sh "$ROOT/scripts/pnpm.sh" install --frozen-lockfile --prod=false
  sh "$ROOT/scripts/pnpm.sh" run build
fi

"$ROOT/.venv/bin/python" -c 'from vktech.model import model_endpoint; import sys; sys.exit(0 if model_endpoint().configured else "Set POLZA_API_KEY in .env before starting the application")'

LOG_DIR="$ROOT/.local/logs"
mkdir -p "$LOG_DIR" "$DATA_DIR"
PIDS=""
cleanup() {
  trap - INT TERM EXIT
  [ -n "$PIDS" ] && kill $PIDS 2>/dev/null || true
  wait $PIDS 2>/dev/null || true
}
trap cleanup INT TERM EXIT

start_process() {
  name=$1
  shift
  "$@" >"$LOG_DIR/$name.log" 2>&1 &
  pid=$!
  PIDS="$PIDS $pid"
  echo "$name: $pid"
}

wait_url() {
  url=$1
  name=$2
  attempts=${3:-180}
  while [ "$attempts" -gt 0 ]; do
    if curl -fsS "$url" >/dev/null 2>&1; then return 0; fi
    attempts=$((attempts-1))
    sleep 1
  done
  echo "$name did not start; see $LOG_DIR/$name.log" >&2
  return 1
}

start_process api "$ROOT/.venv/bin/uvicorn" vktech.api:app --host 127.0.0.1 --port 8000
start_process worker "$ROOT/.venv/bin/python" -m vktech.worker
wait_url http://127.0.0.1:8000/api/health api 60

echo "Service is ready: http://127.0.0.1:8000"
echo "Logs: $LOG_DIR"

while :; do
  for pid in $PIDS; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "A service process stopped. Check $LOG_DIR" >&2
      exit 1
    fi
  done
  sleep 2
done
