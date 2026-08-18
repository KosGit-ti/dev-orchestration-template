#!/usr/bin/env bash
# =============================================================================
# update_agent_models.sh
# =============================================================================
# project-config.yml の ai_models 設定を読み取り、
# 各エージェントファイルの model: 行を一括更新するスクリプト。
#
# 使い方:
#   bash scripts/update_agent_models.sh
#
# 前提:
#   - yq がインストールされていること（brew install yq）
#   - エージェントファイルが agents/ または .github/agents/ にあること
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/project-config.yml"

# --- yq の存在確認 ---
if ! command -v yq &> /dev/null; then
    echo "❌ yq が見つかりません。以下のコマンドでインストールしてください："
    echo "   brew install yq"
    exit 1
fi

# --- 設定ファイルの存在確認 ---
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ $CONFIG_FILE が見つかりません"
    exit 1
fi

# --- デフォルトモデルを取得 ---
DEFAULT_MODEL=$(yq '.ai_models.default' "$CONFIG_FILE" 2>/dev/null)
if [ -z "$DEFAULT_MODEL" ] || [ "$DEFAULT_MODEL" = "null" ]; then
    echo "❌ ai_models.default が project-config.yml に設定されていません"
    exit 1
fi
echo "📋 デフォルトモデル: $DEFAULT_MODEL"

# --- エージェントファイルのディレクトリを検出 ---
AGENTS_DIR=""
if [ -d "$ROOT_DIR/.github/agents" ]; then
    AGENTS_DIR="$ROOT_DIR/.github/agents"
elif [ -d "$ROOT_DIR/agents" ]; then
    AGENTS_DIR="$ROOT_DIR/agents"
else
    echo "❌ エージェントファイルのディレクトリが見つかりません（agents/ または .github/agents/）"
    exit 1
fi
echo "📂 エージェントディレクトリ: $AGENTS_DIR"

# --- 各エージェントファイルを更新 ---
UPDATED=0
for agent_file in "$AGENTS_DIR"/*.agent.md; do
    [ -f "$agent_file" ] || continue

    # ファイル名からエージェント名を推定（例: orchestrator.agent.md → orchestrator）
    basename=$(basename "$agent_file" .agent.md)
    # ハイフン区切りをアンダースコアに変換（設定ファイル側はアンダースコア）
    agent_key=$(echo "$basename" | tr '-' '_')

    # エージェント個別のオーバーライドを確認
    OVERRIDE_MODEL=$(yq ".ai_models.overrides.$agent_key // \"\"" "$CONFIG_FILE" 2>/dev/null)

    if [ -n "$OVERRIDE_MODEL" ] && [ "$OVERRIDE_MODEL" != "null" ]; then
        MODEL="$OVERRIDE_MODEL"
    else
        MODEL="$DEFAULT_MODEL"
    fi

    # --- frontmatter 形式（---で囲まれた YAML ヘッダ）の場合 ---
    if head -1 "$agent_file" | grep -q '^---'; then
        if grep -q '^model:' "$agent_file"; then
            # 既存の model: 行を更新
            sed -i.bak "s|^model:.*|model: $MODEL|" "$agent_file"
            rm -f "${agent_file}.bak"
            echo "  ✅ $basename → $MODEL（frontmatter 更新）"
            UPDATED=$((UPDATED + 1))
        else
            # model: 行がなければ frontmatter 末尾に追加
            sed -i.bak "/^---$/a\\
model: $MODEL" "$agent_file"
            rm -f "${agent_file}.bak"
            echo "  ✅ $basename → $MODEL（frontmatter に追加）"
            UPDATED=$((UPDATED + 1))
        fi
    else
        # --- frontmatter なしの Markdown 形式の場合 ---
        # ファイル先頭に frontmatter を追加
        # macOS の sed は \n を直接扱えないため、一時ファイルを使用
        TMPFILE=$(mktemp)
        {
            echo "---"
            echo "model: $MODEL"
            echo "---"
            echo ""
            cat "$agent_file"
        } > "$TMPFILE"
        mv "$TMPFILE" "$agent_file"
        echo "  ✅ $basename → $MODEL（frontmatter を新規追加）"
        UPDATED=$((UPDATED + 1))
    fi
done

echo ""
echo "✅ 完了: $UPDATED 個のエージェントファイルを更新しました"
