#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DEST="$ROOT/.tools/zimage"
ARCHIVE="$DEST/zimage.macos.arm64.zip"
mkdir -p "$DEST"
curl -fL https://github.com/mzbac/zimage.swift/releases/latest/download/zimage.macos.arm64.zip -o "$ARCHIVE"
unzip -oq "$ARCHIVE" -d "$DEST"
CLI=$(find "$DEST" -type f -name ZImageCLI -print -quit)
test -n "$CLI"
chmod +x "$CLI"
"$CLI" -h >/dev/null
printf '%s\n' "$CLI"
