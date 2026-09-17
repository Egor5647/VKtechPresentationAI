#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

if command -v brew >/dev/null 2>&1; then
  brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
  brew list node >/dev/null 2>&1 || brew install node
  brew list pnpm >/dev/null 2>&1 || brew install pnpm
  brew list poppler >/dev/null 2>&1 || brew install poppler
  brew list --cask libreoffice >/dev/null 2>&1 || brew install --cask libreoffice
fi

PYTHON=${PYTHON:-$(command -v python3.12 || command -v python3 || true)}
if [ -z "$PYTHON" ]; then
  echo "Python 3.11+ is required. On macOS install Homebrew, then run this command again." >&2
  exit 2
fi

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  "$PYTHON" -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT[test]"
"$ROOT/scripts/ensure_mlx_env.sh"

if command -v pnpm >/dev/null 2>&1; then
  (cd "$ROOT/frontend" && pnpm install --frozen-lockfile && pnpm run build)
elif command -v corepack >/dev/null 2>&1; then
  (cd "$ROOT/frontend" && corepack pnpm install --frozen-lockfile && corepack pnpm run build)
else
  echo "pnpm or Node.js corepack is required" >&2
  exit 2
fi

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
fi
echo "Application dependencies are ready. Download the three models before make run."

