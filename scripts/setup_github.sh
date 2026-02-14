#!/usr/bin/env bash
# =============================================================================
# setup_github.sh — GitHub Labels / Milestones / Issues / Project 自動作成
# =============================================================================
# project-config.yml と docs/plan.md の内容に基づいて、
# GitHub の Labels, Milestones, Epic Issues, GitHub Project を自動作成する。
#
# 前提:
#   - gh CLI がインストール・認証済み
#   - project スコープの認可: gh auth refresh -s project,read:project
#
# 使い方:
#   bash scripts/setup_github.sh              # 全ステップ実行
#   bash scripts/setup_github.sh --labels     # Labels のみ
#   bash scripts/setup_github.sh --milestones # Milestones のみ
#   bash scripts/setup_github.sh --epics      # Epic Issues のみ
#   bash scripts/setup_github.sh --project    # Project のみ
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$REPO_ROOT/project-config.yml"

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

info()  { echo -e "\033[0;36m[INFO]\033[0m  $*"; }
warn()  { echo -e "\033[0;33m[WARN]\033[0m  $*"; }
error() { echo -e "\033[0;31m[ERROR]\033[0m $*" >&2; }
ok()    { echo -e "\033[0;32m[OK]\033[0m    $*"; }

check_gh() {
    if ! command -v gh &> /dev/null; then
        error "gh CLI がインストールされていません。"
        error "インストール: https://cli.github.com/"
        exit 1
    fi

    if ! gh auth status &> /dev/null; then
        error "gh CLI が認証されていません。"
        error "認証: gh auth login -h github.com -p https -w"
        exit 1
    fi
}

# 簡易 YAML パーサー
yaml_get() {
    local key="$1"
    python3 -c "
import re
def parse_yaml(filepath):
    result = {}
    with open(filepath) as f:
        current_section = ''
        for line in f:
            line = line.rstrip()
            if not line or line.lstrip().startswith('#'):
                continue
            m = re.match(r'^(\w[\w.]*):$', line)
            if m:
                current_section = m.group(1)
                continue
            m = re.match(r'^(\w[\w.]*)\s*:\s*(.+)', line)
            if m:
                result[m.group(1)] = m.group(2).strip().strip('\"').strip(\"'\")
                current_section = ''
                continue
            m = re.match(r'^  (\w[\w.]*)\s*:\s*(.+)', line)
            if m and current_section:
                key = f'{current_section}.{m.group(1)}'
                result[key] = m.group(2).strip().strip('\"').strip(\"'\")
    return result
data = parse_yaml('$CONFIG_FILE')
print(data.get('$key', ''))
" 2>/dev/null || echo ""
}

# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

create_labels() {
    info "GitHub Labels を作成中..."

    # project-config.yml の labels セクションから Python で抽出
    python3 << 'PYLABELS'
import re, subprocess, sys

labels = []
in_labels = False
with open("project-config.yml") as f:
    for line in f:
        line = line.rstrip()
        if "labels:" in line and not line.lstrip().startswith("#"):
            in_labels = True
            continue
        if in_labels:
            if line and not line.startswith(" ") and not line.startswith("\t"):
                break
            m = re.search(r'name:\s*"([^"]+)".*color:\s*"([^"]+)".*description:\s*"([^"]*)"', line)
            if m:
                labels.append((m.group(1), m.group(2), m.group(3)))

for name, color, desc in labels:
    print(f"  Creating label: {name}")
    result = subprocess.run(
        ["gh", "label", "create", name, "--color", color, "--description", desc, "--force"],
        capture_output=True, text=True
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        print(f"  Warning: {result.stderr.strip()}", file=sys.stderr)

print(f"  Created {len(labels)} labels")
PYLABELS

    ok "Labels 作成完了"
}

# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------

create_milestones() {
    info "GitHub Milestones を作成中..."

    # plan.md からフェーズ情報を読み取る
    python3 << 'PYMILESTONES'
import re, subprocess, sys

phases = []
with open("docs/plan.md") as f:
    in_roadmap = False
    for line in f:
        line = line.rstrip()
        if "ロードマップ" in line or "Roadmap" in line:
            in_roadmap = True
            continue
        if in_roadmap and line.startswith("|"):
            cols = [c.strip() for c in line.split("|")]
            if len(cols) >= 5 and cols[1].isdigit():
                phase_id = cols[1]
                name = cols[2]
                goal = cols[3]
                phases.append((phase_id, name, goal))

for phase_id, name, goal in phases:
    title = f"Phase {phase_id}: {name}"
    print(f"  Creating milestone: {title}")
    result = subprocess.run(
        ["gh", "api", "repos/{owner}/{repo}/milestones", "-f", f"title={title}", "-f", f"description={goal}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        if "already_exists" in result.stderr or "Validation Failed" in result.stderr:
            print(f"  (already exists)")
        else:
            print(f"  Warning: {result.stderr.strip()}", file=sys.stderr)

print(f"  Created {len(phases)} milestones")
PYMILESTONES

    ok "Milestones 作成完了"
}

# ---------------------------------------------------------------------------
# Epic Issues
# ---------------------------------------------------------------------------

create_epics() {
    info "Epic Issues を作成中..."

    python3 << 'PYEPICS'
import re, subprocess, sys, json

phases = []
with open("docs/plan.md") as f:
    in_roadmap = False
    for line in f:
        line = line.rstrip()
        if "ロードマップ" in line or "Roadmap" in line:
            in_roadmap = True
            continue
        if in_roadmap and line.startswith("|"):
            cols = [c.strip() for c in line.split("|")]
            if len(cols) >= 5 and cols[1].isdigit():
                phase_id = cols[1]
                name = cols[2]
                goal = cols[3]
                duration = cols[4] if len(cols) > 4 else ""
                phases.append((phase_id, name, goal, duration))

for phase_id, name, goal, duration in phases:
    title = f"[Epic] Phase {phase_id}: {name}"
    body = f"""## ゴール

{goal}

## 期間目安

{duration}

## 含まれる Story

<!-- Issue 作成後にリンクを追加 -->

## 成功条件

<!-- Phase の完了条件を記載 -->
"""
    milestone = f"Phase {phase_id}: {name}"
    print(f"  Creating epic: {title}")

    result = subprocess.run(
        ["gh", "issue", "create",
         "--title", title,
         "--body", body,
         "--label", f"epic,phase:{phase_id},priority:high"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"  → {result.stdout.strip()}")
    else:
        print(f"  Warning: {result.stderr.strip()}", file=sys.stderr)

PYEPICS

    ok "Epic Issues 作成完了"
}

# ---------------------------------------------------------------------------
# GitHub Project
# ---------------------------------------------------------------------------

create_project() {
    info "GitHub Project を作成中..."

    PROJECT_NAME=$(python3 -c "
import re
with open('project-config.yml') as f:
    for line in f:
        m = re.search(r'project_name:\s*\"([^\"]+)\"', line)
        if m:
            print(m.group(1))
            break
    else:
        print('開発ロードマップ')
")

    OWNER=$(python3 -c "
import re
with open('project-config.yml') as f:
    for line in f:
        line = line.rstrip()
        m = re.match(r'\s+owner:\s*\"([^\"]+)\"', line)
        if m:
            print(m.group(1))
            break
    else:
        print('')
")

    if [ -z "$OWNER" ]; then
        OWNER=$(gh api user -q .login 2>/dev/null || echo "")
    fi

    if [ -z "$OWNER" ]; then
        warn "GitHub ユーザー名を取得できませんでした。Project 作成をスキップします。"
        return
    fi

    info "Project 名: $PROJECT_NAME"
    info "Owner: $OWNER"

    # Project 作成
    PROJECT_URL=$(gh project create --owner "$OWNER" --title "$PROJECT_NAME" --format json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('url',''))" 2>/dev/null || echo "")

    if [ -n "$PROJECT_URL" ]; then
        ok "Project 作成完了: $PROJECT_URL"

        # 全 Issue を Project に追加
        info "Issue を Project に追加中..."
        ISSUE_NUMBERS=$(gh issue list --state all --limit 100 --json number -q '.[].number' 2>/dev/null || echo "")

        if [ -n "$ISSUE_NUMBERS" ]; then
            PROJECT_ID=$(gh project list --owner "$OWNER" --format json 2>/dev/null | python3 -c "
import json, sys
projects = json.load(sys.stdin).get('projects', [])
for p in projects:
    if p.get('title') == '$PROJECT_NAME':
        print(p.get('number', ''))
        break
" 2>/dev/null || echo "")

            if [ -n "$PROJECT_ID" ]; then
                for num in $ISSUE_NUMBERS; do
                    ITEM_URL=$(gh issue view "$num" --json url -q .url 2>/dev/null || echo "")
                    if [ -n "$ITEM_URL" ]; then
                        gh project item-add "$PROJECT_ID" --owner "$OWNER" --url "$ITEM_URL" 2>/dev/null || true
                        echo "  Added #$num"
                        sleep 1  # Rate limit 対策
                    fi
                done
            fi
        fi

        ok "全 Issue を Project に追加しました"
    else
        warn "Project の作成に失敗しました。手動で作成してください。"
        warn "必要な認可: gh auth refresh -s project,read:project"
    fi
}

# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

main() {
    check_gh

    cd "$REPO_ROOT"

    case "${1:-all}" in
        --labels)     create_labels ;;
        --milestones) create_milestones ;;
        --epics)      create_epics ;;
        --project)    create_project ;;
        all|*)
            create_labels
            create_milestones
            create_epics
            create_project
            ;;
    esac

    echo ""
    ok "=== GitHub セットアップ完了 ==="
    echo ""
    info "次のステップ："
    echo "  1. GitHub の Issues / Projects ページで作成結果を確認する"
    echo "  2. 必要に応じて Story / Task Issue を追加する"
    echo "  3. docs/plan.md の GitHub Issue/Project 対応表を更新する"
    echo ""
}

main "$@"
