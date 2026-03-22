#!/usr/bin/env python3
"""テンプレートアップデートスクリプト。

.template-update.yml マニフェストに従い、テンプレートリポジトリの変更を
子プロジェクトに「選択的に」適用する。

Usage:
    # dry-run（何が起きるか確認するだけ）
    python scripts/template_update.py --dry-run

    # 本実行
    python scripts/template_update.py

    # テンプレートリポジトリを指定
    python scripts/template_update.py --template-url https://github.com/KosGit-ti/dev-orchestration-template.git
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# PyYAML が利用できない環境への対策
try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ──────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────
DEFAULT_TEMPLATE_URL = (
    "https://github.com/KosGit-ti/dev-orchestration-template.git"
)
MANIFEST_FILE = ".template-update.yml"
BACKUP_BRANCH_PREFIX = "backup-before-template-update"

# カテゴリ優先度（高い数値 = 高い優先度）
CATEGORY_PRIORITY = {
    "sample_only": 4,
    "never_update": 3,
    "always_update": 2,
    "add_only": 1,
}


# ──────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────
def run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """サブプロセスを実行する。"""
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        capture_output=capture,
        text=True,
    )


def load_manifest(manifest_path: Path) -> dict[str, list[str]]:
    """マニフェスト YAML をパースしてカテゴリ辞書を返す。"""
    if yaml is None:
        # PyYAML が無い場合は簡易パーサーで対応
        return _parse_manifest_simple(manifest_path)

    with manifest_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "categories" not in data:
        print("エラー: マニフェストに 'categories' セクションがありません。")
        sys.exit(1)

    return data["categories"]


def _parse_manifest_simple(manifest_path: Path) -> dict[str, list[str]]:
    """PyYAML なしでマニフェストを簡易パースする。"""
    categories: dict[str, list[str]] = {}
    current_category: str | None = None
    text = manifest_path.read_text(encoding="utf-8")

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        # コメント・空行をスキップ
        if not stripped or stripped.startswith("#"):
            continue
        # 'version:' 行をスキップ
        if stripped.startswith("version:") or stripped == "categories:":
            continue

        # カテゴリヘッダ検出: "  always_update:" のようなパターン
        if stripped.endswith(":") and not stripped.startswith("- "):
            key = stripped.rstrip(":")
            if key in CATEGORY_PRIORITY:
                current_category = key
                categories[current_category] = []
            continue

        # リスト項目: "    - path/to/file"
        if stripped.startswith("- ") and current_category is not None:
            # インラインコメントを除去
            value = stripped[2:].strip()
            if "#" in value:
                value = value[: value.index("#")].strip()
            if value:
                categories[current_category].append(value)

    return categories


def classify_file(
    rel_path: str, categories: dict[str, list[str]]
) -> str:
    """ファイルパスをマニフェストのカテゴリに分類する。

    優先度: sample_only > never_update > always_update > add_only
    どのカテゴリにもマッチしない場合は 'unclassified' を返す。
    """
    best_category = "unclassified"
    best_priority = 0

    for category, patterns in categories.items():
        priority = CATEGORY_PRIORITY.get(category, 0)
        for pattern in patterns:
            if _matches(rel_path, pattern) and priority > best_priority:
                best_category = category
                best_priority = priority

    return best_category


def _matches(rel_path: str, pattern: str) -> bool:
    """パスがパターンにマッチするか判定する。

    - パターン末尾が '/' → ディレクトリプレフィクスマッチ
    - それ以外 → 完全一致
    """
    if pattern.endswith("/"):
        return rel_path.startswith(pattern) or rel_path + "/" == pattern
    return rel_path == pattern


def collect_files(directory: Path) -> list[str]:
    """ディレクトリ内の全ファイルを相対パスのリストで返す。"""
    files: list[str] = []
    for p in sorted(directory.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            files.append(str(p.relative_to(directory)).replace("\\", "/"))
    return files


def create_backup_branch(project_dir: Path) -> str:
    """バックアップブランチを作成する。"""
    # 現在のブランチ名を取得
    result = run_cmd(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_dir
    )
    current_branch = result.stdout.strip()

    # タイムスタンプ付きバックアップブランチ名
    import datetime

    ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_branch = f"{BACKUP_BRANCH_PREFIX}-{ts}"

    run_cmd(
        ["git", "branch", backup_branch], cwd=project_dir
    )
    print(f"  バックアップブランチ作成: {backup_branch} (from {current_branch})")
    return backup_branch


def clone_template(template_url: str, dest: Path) -> None:
    """テンプレートリポジトリを一時ディレクトリにクローンする。"""
    print(f"  テンプレート取得中: {template_url}")
    run_cmd(
        ["git", "clone", "--depth=1", template_url, str(dest)],
    )


# ──────────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────────
def analyze(
    template_dir: Path,
    project_dir: Path,
    categories: dict[str, list[str]],
) -> dict[str, list[dict[str, str]]]:
    """テンプレートとプロジェクトのファイルを比較し、操作計画を作成する。"""
    template_files = collect_files(template_dir)
    project_files = set(collect_files(project_dir))

    plan: dict[str, list[dict[str, str]]] = {
        "update": [],      # always_update: 上書きするファイル
        "add": [],         # add_only: 新規追加するファイル
        "skip_project": [],  # never_update: スキップ（プロジェクト固有）
        "skip_sample": [],   # sample_only: スキップ（サンプル）
        "unclassified": [],  # 未分類
    }

    for rel_path in template_files:
        category = classify_file(rel_path, categories)
        exists_in_project = rel_path in project_files

        if category == "always_update":
            plan["update"].append({
                "path": rel_path,
                "action": "上書き" if exists_in_project else "新規追加",
            })
        elif category == "add_only":
            if not exists_in_project:
                plan["add"].append({
                    "path": rel_path,
                    "action": "新規追加",
                })
            # 既存ファイルはスキップ（ログ不要）
        elif category == "never_update":
            plan["skip_project"].append({
                "path": rel_path,
                "reason": "プロジェクト固有",
            })
        elif category == "sample_only":
            plan["skip_sample"].append({
                "path": rel_path,
                "reason": "サンプルファイル",
            })
        else:
            plan["unclassified"].append({
                "path": rel_path,
                "reason": "マニフェスト未定義",
            })

    return plan


def print_report(plan: dict[str, list[dict[str, str]]]) -> None:
    """操作計画のレポートを表示する。"""
    print("\n" + "=" * 60)
    print("テンプレートアップデート — 操作計画レポート")
    print("=" * 60)

    update_files = plan["update"]
    add_files = plan["add"]
    skip_project = plan["skip_project"]
    skip_sample = plan["skip_sample"]
    unclassified = plan["unclassified"]

    print(f"\n✅ 更新対象 (always_update): {len(update_files)} ファイル")
    for item in update_files:
        print(f"   {'📝' if item['action'] == '上書き' else '➕'} {item['path']} [{item['action']}]")

    print(f"\n➕ 新規追加 (add_only): {len(add_files)} ファイル")
    for item in add_files:
        print(f"   ➕ {item['path']}")

    print(f"\n🔒 スキップ — プロジェクト固有 (never_update): {len(skip_project)} ファイル")
    for item in skip_project:
        print(f"   🔒 {item['path']}")

    print(f"\n📦 スキップ — サンプル (sample_only): {len(skip_sample)} ファイル")
    for item in skip_sample:
        print(f"   📦 {item['path']}")

    if unclassified:
        print(f"\n⚠️  未分類: {len(unclassified)} ファイル")
        for item in unclassified:
            print(f"   ⚠️  {item['path']} ({item['reason']})")

    total_changes = len(update_files) + len(add_files)
    total_skipped = len(skip_project) + len(skip_sample)
    print(f"\n{'─' * 60}")
    print(f"合計: 変更 {total_changes} / スキップ {total_skipped} / 未分類 {len(unclassified)}")
    print("─" * 60)


def apply_changes(
    template_dir: Path,
    project_dir: Path,
    plan: dict[str, list[dict[str, str]]],
) -> int:
    """操作計画に従いファイルをコピーする。"""
    count = 0

    for item in plan["update"]:
        src = template_dir / item["path"]
        dst = project_dir / item["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1

    for item in plan["add"]:
        src = template_dir / item["path"]
        dst = project_dir / item["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1

    return count


def run_post_checks(project_dir: Path) -> bool:
    """アップデート後の品質チェックを実行する。"""
    print("\n🔍 ポストチェック実行中...")
    checks = [
        (["python", "-m", "ruff", "check", "."], "ruff check"),
        (["python", "-m", "ruff", "format", "--check", "."], "ruff format"),
        (["python", "ci/policy_check.py"], "policy_check"),
    ]
    all_passed = True
    for cmd, label in checks:
        result = run_cmd(cmd, cwd=project_dir, check=False)
        status = "✅" if result.returncode == 0 else "❌"
        print(f"  {status} {label}")
        if result.returncode != 0:
            all_passed = False
            if result.stdout:
                # 最初の10行だけ表示
                for line in result.stdout.splitlines()[:10]:
                    print(f"     {line}")
            if result.stderr:
                for line in result.stderr.splitlines()[:10]:
                    print(f"     {line}")

    return all_passed


def main() -> int:
    """メインエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="テンプレートアップデートスクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 何が起きるか確認する（安全）
  python scripts/template_update.py --dry-run

  # 実際にアップデートを適用する
  python scripts/template_update.py

  # テンプレート URL を指定する
  python scripts/template_update.py --template-url https://github.com/user/template.git
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際の変更は行わず、何が起きるかのレポートのみ表示する",
    )
    parser.add_argument(
        "--template-url",
        default=DEFAULT_TEMPLATE_URL,
        help=f"テンプレートリポジトリの URL（デフォルト: {DEFAULT_TEMPLATE_URL}）",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="バックアップブランチの作成をスキップする",
    )
    parser.add_argument(
        "--no-post-check",
        action="store_true",
        help="ポストチェック（lint / policy_check）をスキップする",
    )
    args = parser.parse_args()

    # プロジェクトルートを検出
    project_dir = Path.cwd()
    manifest_path = project_dir / MANIFEST_FILE

    if not manifest_path.exists():
        print(f"エラー: {MANIFEST_FILE} が見つかりません。")
        print("このスクリプトはプロジェクトルートで実行してください。")
        print("テンプレートを最新版にアップデートすると、マニフェストが含まれます。")
        sys.exit(1)

    # マニフェスト読み込み
    print("📋 マニフェスト読み込み中...")
    categories = load_manifest(manifest_path)

    # テンプレートをクローン
    with tempfile.TemporaryDirectory() as tmpdir:
        template_dir = Path(tmpdir) / "template"
        print("📥 テンプレート取得中...")
        clone_template(args.template_url, template_dir)

        # 分析
        print("🔍 ファイル分析中...")
        plan = analyze(template_dir, project_dir, categories)

        # レポート表示
        print_report(plan)

        # 未分類ファイルの警告
        if plan["unclassified"]:
            print("\n⚠️  未分類ファイルがあります。")
            print("   .template-update.yml を更新して分類してください。")
            print("   未分類ファイルはスキップされます。")

        if args.dry_run:
            print("\n📎 これは dry-run です。実際の変更は行われていません。")
            print("   アップデートを実行するには --dry-run を外して再実行してください。")
            return 0

        # 変更がなければ終了
        total_changes = len(plan["update"]) + len(plan["add"])
        if total_changes == 0:
            print("\n✨ アップデートする変更はありません。すでに最新です。")
            return 0

        # バックアップブランチ作成
        if not args.no_backup:
            print("\n💾 バックアップ作成中...")
            backup_branch = create_backup_branch(project_dir)
            print(f"   復元が必要な場合: git checkout {backup_branch}")

        # 適用
        print("\n🚀 アップデート適用中...")
        count = apply_changes(template_dir, project_dir, plan)
        print(f"   {count} ファイルを更新しました。")

    # ポストチェック
    if not args.no_post_check:
        checks_passed = run_post_checks(project_dir)
        if not checks_passed:
            print("\n⚠️  一部のチェックが失敗しています。")
            print("   Copilot に「品質チェックのエラーを修正して」と伝えてください。")

    print("\n✅ テンプレートアップデートが完了しました。")
    print("   変更内容を確認し、コミットしてください:")
    print("   git add -A && git commit -m 'chore: テンプレートアップデート適用'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
