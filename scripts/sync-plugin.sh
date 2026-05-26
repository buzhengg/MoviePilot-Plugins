#!/usr/bin/env bash
# 在 plugins/ 与 plugins.v2/ 之间同步 moviepilotapppush（默认以 plugins.v2 为准）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/plugins.v2/moviepilotapppush"
DST="$ROOT/plugins/moviepilotapppush"
DIRECTION="${1:-v2-to-v1}"

if [[ "$DIRECTION" == "v1-to-v2" ]]; then
  SRC="$ROOT/plugins/moviepilotapppush"
  DST="$ROOT/plugins.v2/moviepilotapppush"
elif [[ "$DIRECTION" != "v2-to-v1" ]]; then
  echo "用法: $0 [v2-to-v1|v1-to-v2]（默认 v2-to-v1）" >&2
  exit 1
fi

if [[ ! -d "$SRC" ]]; then
  echo "源目录不存在: $SRC" >&2
  exit 1
fi

mkdir -p "$DST"
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "$SRC/" "$DST/"

echo "已同步: $SRC -> $DST"
