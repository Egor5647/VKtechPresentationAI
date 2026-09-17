#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

REQUESTED_MODEL_MODE=${MODEL_MODE:-}
if [ -f "$ROOT/.env" ]; then
  set -a
  . "$ROOT/.env"
  set +a
fi
[ -n "$REQUESTED_MODEL_MODE" ] && MODEL_MODE=$REQUESTED_MODEL_MODE

export DATA_DIR=${DATA_DIR:-"$ROOT/data"}
export DATABASE_URL=${DATABASE_URL:-"sqlite:///$ROOT/data/service.sqlite"}
export MODEL_PROFILE=${MODEL_PROFILE:-selection}
export MODEL_MODE=${MODEL_MODE:-quality}
export MODEL_BASE_URL=${MODEL_BASE_URL:-http://127.0.0.1:8001/v1}
export T2I_BASE_URL=${T2I_BASE_URL:-http://127.0.0.1:8002/v1}
export MODEL_MAX_TOKENS_PLANNING=${MODEL_MAX_TOKENS_PLANNING:-6500}
export SOFFICE=${SOFFICE:-$(command -v soffice || true)}
export PDFTOPPM=${PDFTOPPM:-$(command -v pdftoppm || true)}

if [ -z "$SOFFICE" ] && [ -x /Applications/LibreOffice.app/Contents/MacOS/soffice ]; then
  export SOFFICE=/Applications/LibreOffice.app/Contents/MacOS/soffice
fi
for required in "$ROOT/.venv/bin/python" "$ROOT/.mlx-venv/bin/python" "$SOFFICE" "$PDFTOPPM"; do
  if [ -z "$required" ] || [ ! -x "$required" ]; then
    echo "Dependencies are incomplete. Run: make setup" >&2
    exit 2
  fi
done

if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
  if command -v pnpm >/dev/null 2>&1; then
    (cd "$ROOT/frontend" && pnpm run build)
  else
    (cd "$ROOT/frontend" && corepack pnpm run build)
  fi
fi

T2I_CLI=${T2I_CLI:-$(find "$ROOT/.tools/zimage" -type f -name ZImageCLI -print -quit 2>/dev/null || true)}
if [ -z "$T2I_CLI" ] || [ ! -x "$T2I_CLI" ]; then
  echo "Z-Image is not installed. Run: make download-zimage" >&2
  exit 2
fi
export T2I_CLI

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

start_process text-model env MODEL_MODE="$MODEL_MODE" "$ROOT/scripts/start_mlx_model.sh"
start_process image-model "$ROOT/scripts/start_zimage_server.sh"
wait_url "$MODEL_BASE_URL/models" text-model 300
wait_url "http://127.0.0.1:8002/health" image-model 60

start_process api "$ROOT/.venv/bin/uvicorn" vktech.api:app --host 127.0.0.1 --port 8000
start_process worker "$ROOT/.venv/bin/python" -m vktech.worker
wait_url http://127.0.0.1:8000/api/health api 60

echo "Service is ready: http://127.0.0.1:8000"
echo "Logs: $LOG_DIR"
if command -v open >/dev/null 2>&1; then open http://127.0.0.1:8000; fi

while :; do
  for pid in $PIDS; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "A service process stopped. Check $LOG_DIR" >&2
      exit 1
    fi
  done
  sleep 2
done
