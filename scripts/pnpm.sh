#!/bin/sh
# Use the project's pinned pnpm without global shims or Corepack's cache.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js 22.13+ and npm are required. If using nvm, run: nvm use 24" >&2
  exit 2
fi
if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 13) ? 0 : 1)'; then
  echo "Node.js 22.13+ is required; found $(node --version)." >&2
  exit 2
fi

PNPM_VERSION=$(node -e '
  const value = require(process.argv[1]).packageManager;
  if (!/^pnpm@\d+\.\d+\.\d+$/.test(value || "")) {
    console.error("frontend/package.json must pin an exact pnpm version");
    process.exit(2);
  }
  process.stdout.write(value.slice(5));
' "$ROOT/frontend/package.json")
PNPM_DIR="$ROOT/.tools/pnpm/$PNPM_VERSION"
PNPM_ENTRY="$PNPM_DIR/node_modules/pnpm/bin/pnpm.mjs"
if [ ! -f "$PNPM_ENTRY" ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required to install the project-local pnpm $PNPM_VERSION." >&2
    exit 2
  fi
  echo "Installing project-local pnpm $PNPM_VERSION (no Corepack or sudo)." >&2
  npm install --prefix "$PNPM_DIR" --no-save --package-lock=false \
    --ignore-scripts --no-audit --no-fund "pnpm@$PNPM_VERSION"
fi

cd "$ROOT/frontend"
exec node "$PNPM_ENTRY" "$@"
