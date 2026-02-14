#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — プロジェクト初期化スクリプト
# =============================================================================
# テンプレートリポジトリからプロジェクトを初期化する。
# project-config.yml の設定を読み取り、テンプレート内のプレースホルダーを置換する。
#
# 前提:
#   - bash 4+
#   - Python 3.8+ (YAML パース用)
#   - git
#
# 使い方:
#   bash scripts/bootstrap.sh
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

# Python で YAML から値を取得する（外部ライブラリ不要、簡易パース）
yaml_get() {
    local key="$1"
    python3 -c "
import sys, re

# 簡易 YAML パーサー（PyYAML 不要）
def parse_yaml(filepath):
    result = {}
    with open(filepath) as f:
        current_section = ''
        for line in f:
            line = line.rstrip()
            if not line or line.lstrip().startswith('#'):
                continue
            # セクションヘッダ
            m = re.match(r'^(\w[\w.]*):$', line)
            if m:
                current_section = m.group(1)
                continue
            # キー: 値（トップレベル）
            m = re.match(r'^(\w[\w.]*)\s*:\s*(.+)', line)
            if m:
                result[m.group(1)] = m.group(2).strip().strip('\"').strip(\"'\")
                current_section = ''
                continue
            # インデントされたキー: 値
            m = re.match(r'^  (\w[\w.]*)\s*:\s*(.+)', line)
            if m and current_section:
                key = f'{current_section}.{m.group(1)}'
                result[key] = m.group(2).strip().strip('\"').strip(\"'\")

    return result

data = parse_yaml('$CONFIG_FILE')
key = '$key'
print(data.get(key, ''))
" 2>/dev/null || echo ""
}

# ---------------------------------------------------------------------------
# 設定読み込み
# ---------------------------------------------------------------------------

if [ ! -f "$CONFIG_FILE" ]; then
    error "project-config.yml が見つかりません: $CONFIG_FILE"
    exit 1
fi

info "project-config.yml を読み込み中..."

PROJECT_NAME=$(yaml_get "project.name")
DISPLAY_NAME=$(yaml_get "project.display_name")
DESCRIPTION=$(yaml_get "project.description")
OWNER=$(yaml_get "project.owner")
PACKAGE_NAME=$(yaml_get "source.package_name")
SRC_DIR=$(yaml_get "source.src_dir")
LANGUAGE=$(yaml_get "toolchain.language")
VERSION=$(yaml_get "toolchain.version")
PKG_MANAGER=$(yaml_get "toolchain.package_manager")
LINTER=$(yaml_get "toolchain.linter")
FORMATTER=$(yaml_get "toolchain.formatter")
TYPE_CHECKER=$(yaml_get "toolchain.type_checker")
TEST_RUNNER=$(yaml_get "toolchain.test_runner")
RUN_PREFIX=$(yaml_get "toolchain.run_prefix")

# デフォルト値
PROJECT_NAME="${PROJECT_NAME:-my-project}"
DISPLAY_NAME="${DISPLAY_NAME:-My Project}"
DESCRIPTION="${DESCRIPTION:-プロジェクトの説明}"
PACKAGE_NAME="${PACKAGE_NAME:-my_package}"
SRC_DIR="${SRC_DIR:-src}"
RUN_PREFIX="${RUN_PREFIX:-}"

info "プロジェクト名: $PROJECT_NAME"
info "表示名: $DISPLAY_NAME"
info "パッケージ名: $PACKAGE_NAME"
info "言語: $LANGUAGE"

# ---------------------------------------------------------------------------
# ディレクトリ作成
# ---------------------------------------------------------------------------

info "ディレクトリ構造を作成中..."

dirs=(
    "$SRC_DIR"
    "tests"
    "configs"
    "outputs"
    "data"
    "notebooks"
    "scripts"
    "ci"
)

for dir in "${dirs[@]}"; do
    mkdir -p "$REPO_ROOT/$dir"
done

# .gitkeep を配置（空ディレクトリの保持）
for dir in outputs data configs; do
    touch "$REPO_ROOT/$dir/.gitkeep"
done

ok "ディレクトリ構造を作成しました"

# ---------------------------------------------------------------------------
# パッケージ構造の作成（Python の場合）
# ---------------------------------------------------------------------------

if [ "$LANGUAGE" = "python" ]; then
    info "Python パッケージ構造を作成中..."

    # メインパッケージ
    PACKAGE_DIR="$REPO_ROOT/$SRC_DIR/$PACKAGE_NAME"
    mkdir -p "$PACKAGE_DIR"
    [ -f "$PACKAGE_DIR/__init__.py" ] || echo '"""'"$DISPLAY_NAME"'パッケージ。"""' > "$PACKAGE_DIR/__init__.py"

    # py.typed マーカー
    touch "$PACKAGE_DIR/py.typed"

    # pyproject.toml の生成
    if [ ! -f "$REPO_ROOT/pyproject.toml" ]; then
        cat > "$REPO_ROOT/pyproject.toml" << PYPROJECT
[project]
name = "$PROJECT_NAME"
version = "0.1.0"
description = "$DESCRIPTION"
requires-python = ">=$VERSION"
dependencies = []

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.5",
    "mypy>=1.10",
]

[tool.ruff]
line-length = 100
target-version = "py${VERSION//./}"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM"]

[tool.mypy]
strict = true
warn_return_any = true
warn_unused_configs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["$SRC_DIR"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.backends"
PYPROJECT
        ok "pyproject.toml を生成しました"
    fi

    # conftest.py
    if [ ! -f "$REPO_ROOT/tests/conftest.py" ]; then
        cat > "$REPO_ROOT/tests/conftest.py" << 'CONFTEST'
"""テスト共通フィクスチャ。"""
CONFTEST
        ok "tests/conftest.py を生成しました"
    fi

    # スモークテスト
    if [ ! -f "$REPO_ROOT/tests/test_smoke.py" ]; then
        cat > "$REPO_ROOT/tests/test_smoke.py" << SMOKE
"""スモークテスト — パッケージが import できることを確認する。"""


def test_import() -> None:
    """パッケージの import が成功する。"""
    import ${PACKAGE_NAME}  # noqa: F401
SMOKE
        ok "tests/test_smoke.py を生成しました"
    fi
fi

# ---------------------------------------------------------------------------
# テンプレート変数の置換（docs 内）
# ---------------------------------------------------------------------------

info "テンプレート変数を置換中..."

# runbook.md のプレースホルダーを置換
if [ -f "$REPO_ROOT/docs/runbook.md" ]; then
    if command -v sed > /dev/null 2>&1; then
        sed -i.bak \
            -e "s|{{PROJECT_NAME}}|$PROJECT_NAME|g" \
            -e "s|{{RUN_PREFIX}}|$RUN_PREFIX|g" \
            -e "s|{{LINTER}}|$LINTER|g" \
            -e "s|{{FORMATTER}}|$FORMATTER|g" \
            -e "s|{{TYPE_CHECKER}}|$TYPE_CHECKER|g" \
            -e "s|{{TEST_RUNNER}}|$TEST_RUNNER|g" \
            "$REPO_ROOT/docs/runbook.md"
        rm -f "$REPO_ROOT/docs/runbook.md.bak"
    fi
fi

# architecture.md のプレースホルダーを置換
if [ -f "$REPO_ROOT/docs/architecture.md" ]; then
    if command -v sed > /dev/null 2>&1; then
        sed -i.bak \
            -e "s|{{PACKAGE_NAME}}|$PACKAGE_NAME|g" \
            "$REPO_ROOT/docs/architecture.md"
        rm -f "$REPO_ROOT/docs/architecture.md.bak"
    fi
fi

ok "テンプレート変数を置換しました"

# ---------------------------------------------------------------------------
# CI ワークフローの設定（言語に応じて）
# ---------------------------------------------------------------------------

info "CI ワークフローを設定中..."

CI_FILE="$REPO_ROOT/.github/workflows/ci.yml"

if [ -f "$CI_FILE" ] && [ "$LANGUAGE" = "python" ]; then
    # Python 用の CI ワークフローを生成
    cat > "$CI_FILE" << 'CIFILE'
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  quality-gate:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "PYTHON_VERSION"

      - name: Install uv
        run: python -m pip install --upgrade pip && python -m pip install uv

      - name: Cache uv dependencies
        uses: actions/cache@v4
        with:
          path: ~/.cache/uv
          key: uv-${{ runner.os }}-${{ hashFiles('pyproject.toml') }}
          restore-keys: |
            uv-${{ runner.os }}-

      - name: Sync dependencies
        run: uv sync --all-extras --dev

      - name: Policy check
        run: uv run python ci/policy_check.py

      - name: Lint (ruff check)
        run: uv run ruff check .

      - name: Format check (ruff format)
        run: uv run ruff format --check .

      - name: Type check (mypy)
        run: uv run mypy src/

      - name: Test (pytest)
        run: uv run pytest -q --tb=short
CIFILE
    sed -i.bak "s|PYTHON_VERSION|$VERSION|g" "$CI_FILE"
    rm -f "$CI_FILE.bak"
    ok "Python 用 CI ワークフローを生成しました"
fi

# ---------------------------------------------------------------------------
# .env.example の作成
# ---------------------------------------------------------------------------

if [ ! -f "$REPO_ROOT/.env.example" ]; then
    cat > "$REPO_ROOT/.env.example" << 'ENVEXAMPLE'
# 環境変数の例（値は設定しないこと。変数名のみ記載する）
# .env にコピーして値を設定する: cp .env.example .env
# ⚠️ .env は絶対にコミットしない
ENVEXAMPLE
    ok ".env.example を作成しました"
fi

# ---------------------------------------------------------------------------
# 完了
# ---------------------------------------------------------------------------

echo ""
ok "=== プロジェクト初期化完了 ==="
echo ""
info "次のステップ："
echo "  1. docs/ 配下のテンプレートをプロジェクトに合わせて編集する"
echo "  2. GitHub Labels/Milestones/Issues を作成する場合:"
echo "     bash scripts/setup_github.sh"
echo "  3. git add -A && git commit -m 'chore: プロジェクト初期化'"
echo ""
