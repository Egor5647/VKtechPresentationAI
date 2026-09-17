#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENV="$ROOT/.mlx-venv"
PYTHON=${PYTHON:-$(command -v python3.12 || command -v python3 || true)}

if [ -z "$PYTHON" ]; then
  echo "Python 3.11+ is required" >&2
  exit 2
fi
if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
fi
if ! "$VENV/bin/python" -c 'import mlx_vlm, huggingface_hub' >/dev/null 2>&1; then
  "$VENV/bin/python" -m pip install -r "$ROOT/requirements-mlx.txt"
fi

