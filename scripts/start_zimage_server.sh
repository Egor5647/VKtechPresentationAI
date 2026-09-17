#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CLI=${T2I_CLI:-$(find "$ROOT/.tools/zimage" -type f -name ZImageCLI -print -quit 2>/dev/null || true)}
if [ -z "$CLI" ]; then
  echo "Run scripts/install_zimage.sh first" >&2
  exit 2
fi
export T2I_CLI="$CLI"
export T2I_LOCAL_MODEL=${T2I_LOCAL_MODEL:-mzbac/Z-Image-Turbo-8bit}
exec "$ROOT/.venv/bin/uvicorn" vktech.t2i_server:app --host 127.0.0.1 --port "${T2I_PORT:-8002}"
