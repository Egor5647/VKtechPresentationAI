#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENV="$ROOT/.mlx-venv"
PYTHON=${PYTHON:-"$ROOT/.venv/bin/python"}
MODEL=${MODEL_NAME:-mlx-community/Qwen3-VL-4B-Instruct-4bit}

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install -r "$ROOT/requirements-mlx.txt"
fi

exec "$VENV/bin/python" -m mlx_vlm.server \
  --host 127.0.0.1 \
  --port "${MODEL_PORT:-8001}" \
  --model "$MODEL" \
  --max-tokens 10000 \
  --max-num-seqs 1 \
  --kv-bits 8 \
  --vision-cache-size 3 \
  --log-progress-interval 100
