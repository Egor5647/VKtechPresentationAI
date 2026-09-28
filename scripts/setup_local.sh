#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ "$(uname -s)" = Darwin ] && command -v brew >/dev/null 2>&1; then
  brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
  brew list node >/dev/null 2>&1 || brew install node
  brew list poppler >/dev/null 2>&1 || brew install poppler
  brew list --cask libreoffice >/dev/null 2>&1 || brew install --cask libreoffice
fi

PYTHON=${PYTHON:-$(command -v python3.12 || command -v python3 || true)}
if [ -z "$PYTHON" ]; then
  echo "Python 3.11+ is required. On Debian/Ubuntu: sudo apt-get install python3 python3-venv" >&2
  exit 2
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11+ is required; select it with PYTHON=/path/to/python3." >&2
  exit 2
fi
if ! command -v "${SOFFICE:-soffice}" >/dev/null 2>&1 && [ ! -x /Applications/LibreOffice.app/Contents/MacOS/soffice ]; then
  echo "LibreOffice is required. On Debian/Ubuntu: sudo apt-get install libreoffice-impress fonts-dejavu-core" >&2
  exit 2
fi
if ! command -v "${PDFTOPPM:-pdftoppm}" >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  echo "Poppler and curl are required. On Debian/Ubuntu: sudo apt-get install poppler-utils curl" >&2
  exit 2
fi
sh "$ROOT/scripts/pnpm.sh" --version

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  if ! "$PYTHON" -m venv "$ROOT/.venv"; then
    echo "Cannot create the Python environment. On Debian/Ubuntu install python3-venv." >&2
    exit 2
  fi
fi
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT[test]"

sh "$ROOT/scripts/pnpm.sh" install --frozen-lockfile --prod=false
sh "$ROOT/scripts/pnpm.sh" run build

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  chmod 600 "$ROOT/.env"
fi
echo "Application dependencies are ready. Set POLZA_API_KEY in .env, then run make run. No local model weights are needed."
