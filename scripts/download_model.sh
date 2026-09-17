#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$ROOT/scripts/ensure_mlx_env.sh"

case "${1:-}" in
  qwen)
    REPO=mlx-community/Qwen3-VL-4B-Instruct-4bit
    REVISION=2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b
    ;;
  ministral)
    REPO=mlx-community/Ministral-3-14B-Instruct-2512-4bit
    REVISION=95b6f345475c8c8c92b7959d02f4ad86d3ba8384
    ;;
  zimage)
    REPO=mzbac/Z-Image-Turbo-8bit
    REVISION=1cc2c9cb261a04e5ad86ba0315b9e256c70fc1fa
    if ! find "$ROOT/.tools/zimage" -type f -name ZImageCLI -print -quit 2>/dev/null | grep -q .; then
      "$ROOT/scripts/install_zimage.sh"
    fi
    ;;
  *)
    echo "Usage: $0 qwen|ministral|zimage" >&2
    exit 2
    ;;
esac

echo "Downloading $REPO at pinned revision $REVISION"
"$ROOT/.mlx-venv/bin/python" - "$REPO" "$REVISION" <<'PY'
import sys
from huggingface_hub import snapshot_download

path=snapshot_download(repo_id=sys.argv[1],revision=sys.argv[2])
print(path)
PY

