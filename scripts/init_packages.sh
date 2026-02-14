#!/usr/bin/env bash
# =============================================================================
# init_packages.sh — パッケージ構造初期化（Python 向け）
# =============================================================================
# src/ 配下のディレクトリに __init__.py が存在しない場合、自動生成する。
#
# 使い方:
#   bash scripts/init_packages.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_DIR="$REPO_ROOT/src"

if [ ! -d "$SRC_DIR" ]; then
    echo "src/ ディレクトリが見つかりません"
    exit 1
fi

count=0
find "$SRC_DIR" -type d | while read -r dir; do
    init_file="$dir/__init__.py"
    if [ ! -f "$init_file" ]; then
        touch "$init_file"
        echo "Created: ${init_file#$REPO_ROOT/}"
        ((count++)) || true
    fi
done

echo "Done. Created $count __init__.py files."
