#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENV="$ROOT/.mlx-venv"
PYTHON=${PYTHON:-"$ROOT/.venv/bin/python"}
MODE=${MODEL_MODE:-quality}
case "$MODE" in
  fast) DEFAULT_MODEL=mlx-community/Qwen3-VL-4B-Instruct-4bit ;;
  quality) DEFAULT_MODEL=mlx-community/Ministral-3-14B-Instruct-2512-4bit ;;
  *) echo "MODEL_MODE must be fast or quality" >&2; exit 2 ;;
esac
MODEL=${MODEL_NAME:-$DEFAULT_MODEL}

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install -r "$ROOT/requirements-mlx.txt"
fi

exec "$VENV/bin/python" -m mlx_vlm.server \
  --host 127.0.0.1 \
  --port "${MODEL_PORT:-8001}" \
  --model "$MODEL" \
  --max-tokens "${MODEL_SERVER_MAX_TOKENS:-4096}" \
  --max-num-seqs 1 \
  --kv-bits 8 \
  --vision-cache-size "${MODEL_VISION_CACHE_SIZE:-1}" \
  --log-progress-interval 100
