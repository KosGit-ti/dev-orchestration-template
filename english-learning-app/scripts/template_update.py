#!/usr/bin/env python3
"""テンプレートアップデートスクリプト v2。

テンプレート（ai-dev-template、旧: dev-orchestration-template）の変更を
子リポジトリへ「選択的に」適用し、逆方向の反映（export）も行う。
決定的な処理はすべて本スクリプトが担い、どの AI アシスタント
（Claude Code / Copilot / Codex 等）でも同じ手順で扱える。

サブコマンド:
    check   テンプレート最新版と子リポジトリの適用済み版を比較する。
            exit 0=最新 / 10=更新あり / 2=エラー。
    apply   .template-update.yml に従いテンプレートの変更を適用する。
            成功時に .template-version.yml（適用状態）を書き出す。
    export  現リポジトリのテンプレート基盤（always_update / add_only）を
            テンプレートのローカルクローンへ反映する（逆方向同期）。

後方互換: サブコマンドを省略すると apply 相当で動作する（非推奨警告を表示）。

使用例:
    python scripts/template_update.py check
    python scripts/template_update.py apply --dry-run
    python scripts/template_update.py apply
    python scripts/template_update.py export --template-dir /tmp/template-clone
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from typing import Any

# PyYAML が利用できない環境への対策（簡易パーサーへフォールバック）。
# スタブ（types-PyYAML）の有無に依存せず mypy strict で安定させるため、
# 動的 import で Any 型の変数に束縛する（レビュー指摘反映）。
try:
    yaml: Any | None = import_module("yaml")
except ImportError:  # pragma: no cover - 実行環境依存
    yaml = None

# ──────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────
# テンプレート取得元 URL のフォールバック（catalog / manifest に無い場合のみ使用）。
DEFAULT_TEMPLATE_URL = "https://github.com/KosGit-Dev/dev-orchestration-template.git"
MANIFEST_FILE = ".template-update.yml"
CATALOG_FILE = "template-catalog.yml"
VERSION_FILE = ".template-version.yml"
CHANGELOG_FILE = "docs/TEMPLATE_CHANGELOG.md"
BACKUP_BRANCH_PREFIX = "backup-before-template-update"

# 走査から除外するキャッシュ・ビルド生成物のディレクトリ（テンプレートには含めない）。
NOISE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    "node_modules",
}

# カテゴリ優先度（同じ具体度でマッチが競合した場合のタイブレーク）。
CATEGORY_PRIORITY = {
    "sample_only": 4,
    "never_update": 3,
    "always_update": 2,
    "add_only": 1,
}

# 終了コード
EXIT_OK = 0
EXIT_UPDATE_AVAILABLE = 10
EXIT_ERROR = 2


# ──────────────────────────────────────────────
# 汎用ヘルパー
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


def collect_files(directory: Path) -> list[str]:
    """ディレクトリ内の全ファイルを相対パス（POSIX 形式）で返す。

    - .git 配下は除外する。
    - シンボリックリンクは辿らず、ファイルとしても列挙しない
      （.claude/skills/ 配下の外部スキルへのリンク等での重複・巡回を避ける）。
    """
    files: list[str] = []
    for root, dirnames, filenames in os.walk(directory, followlinks=False):
        # キャッシュ・ビルド生成物のディレクトリを走査対象から除外
        dirnames[:] = [d for d in dirnames if d not in NOISE_DIRS]
        root_path = Path(root)
        for name in filenames:
            if name.endswith((".pyc", ".pyo")):
                continue
            full = root_path / name
            if full.is_symlink():
                continue
            files.append(full.relative_to(directory).as_posix())
    return sorted(files)


# ──────────────────────────────────────────────
# マニフェスト / カタログ読み込み
# ──────────────────────────────────────────────
def load_manifest(manifest_path: Path) -> dict[str, Any]:
    """.template-update.yml を読み込み全体の辞書を返す。"""
    if yaml is not None:
        with manifest_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or "categories" not in data:
            print("エラー: マニフェストに 'categories' セクションがありません。")
            sys.exit(EXIT_ERROR)
        return data
    return _parse_manifest_simple(manifest_path)


def get_categories(manifest: dict[str, Any]) -> dict[str, list[str]]:
    """マニフェスト辞書から categories を取り出す。"""
    categories = manifest.get("categories")
    if not isinstance(categories, dict):
        print("エラー: マニフェストの 'categories' が不正です。")
        sys.exit(EXIT_ERROR)
    return categories


def _parse_manifest_simple(manifest_path: Path) -> dict[str, Any]:
    """PyYAML なしでマニフェストを簡易パースする。"""
    categories: dict[str, list[str]] = {}
    result: dict[str, Any] = {"categories": categories}
    current_category: str | None = None
    text = manifest_path.read_text(encoding="utf-8")

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # トップレベルの template_repository スカラー
        if stripped.startswith("template_repository:"):
            result["template_repository"] = _scalar_value(stripped.split(":", 1)[1])
            continue
        if stripped.startswith("version:") or stripped == "categories:":
            continue
        # カテゴリヘッダ検出: "  always_update:"
        if stripped.endswith(":") and not stripped.startswith("- "):
            key = stripped.rstrip(":")
            if key in CATEGORY_PRIORITY:
                current_category = key
                categories[current_category] = []
            continue
        # リスト項目: "    - path/to/file"
        if stripped.startswith("- ") and current_category is not None:
            value = stripped[2:].strip()
            if "#" in value:
                value = value[: value.index("#")].strip()
            if value:
                categories[current_category].append(value)

    return result


def load_catalog(catalog_path: Path) -> dict[str, Any] | None:
    """template-catalog.yml を読み込む。存在しなければ None。"""
    if not catalog_path.exists():
        return None
    if yaml is not None:
        with catalog_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    return _parse_catalog_simple(catalog_path)


def _scalar_value(raw: str) -> str:
    """YAML スカラー値からクォートとインラインコメントを除去する。"""
    value = raw.strip()
    if "#" in value and not (value.startswith('"') or value.startswith("'")):
        value = value[: value.index("#")].strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    return value


def _parse_inline_list(raw: str) -> list[str]:
    """インラインフローリスト（["a", "b"]）を文字列リストへ変換する。"""
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    items: list[str] = []
    for token in value.split(","):
        token = token.strip().strip("\"'")
        if token:
            items.append(token)
    return items


def _parse_catalog_simple(catalog_path: Path) -> dict[str, Any]:
    """PyYAML なしで template-catalog.yml を簡易パースする。

    template.* スカラーと features リスト（id / name / since / policy / paths）を抽出する。
    paths はインラインフローリスト記法（["a", "b"]）を前提とする。
    """
    template: dict[str, str] = {}
    features: list[dict[str, Any]] = []
    result: dict[str, Any] = {"template": template, "features": features}

    section: str | None = None  # "template" | "features" | None
    current_feature: dict[str, Any] | None = None
    text = catalog_path.read_text(encoding="utf-8")

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        stripped = raw_line.strip()

        if indent == 0:
            if stripped.startswith("schema_version:"):
                result["schema_version"] = _scalar_value(stripped.split(":", 1)[1])
                section = None
            elif stripped.startswith("template:"):
                section = "template"
            elif stripped.startswith("features:"):
                section = "features"
            else:
                section = None
            continue

        if section == "template" and ":" in stripped:
            key, _, val = stripped.partition(":")
            template[key.strip()] = _scalar_value(val)
            continue

        if section == "features":
            if stripped.startswith("- "):
                # 新しい feature の開始
                current_feature = {}
                features.append(current_feature)
                stripped = stripped[2:].strip()
                if not stripped:
                    continue
            if current_feature is not None and ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                if key == "paths":
                    current_feature[key] = _parse_inline_list(val)
                else:
                    current_feature[key] = _scalar_value(val)

    return result


# ──────────────────────────────────────────────
# 分類
# ──────────────────────────────────────────────
def _matches(rel_path: str, pattern: str) -> bool:
    """パスがパターンにマッチするか判定する。

    - パターン末尾が '/' → ディレクトリ前方一致
    - それ以外 → 完全一致
    """
    if pattern.endswith("/"):
        return rel_path.startswith(pattern) or rel_path + "/" == pattern
    return rel_path == pattern


def classify_file(rel_path: str, categories: dict[str, list[str]]) -> str:
    """ファイルパスをマニフェストのカテゴリに分類する。

    最も具体的（マッチしたパターン文字列が最長）なパターンのカテゴリを採用する。
    長さが同じ場合のみ CATEGORY_PRIORITY で決める。
    どのパターンにもマッチしない場合は 'unclassified'。
    """
    best_category = "unclassified"
    best_specificity = -1
    best_priority = 0

    for category, patterns in categories.items():
        priority = CATEGORY_PRIORITY.get(category, 0)
        for pattern in patterns:
            if not _matches(rel_path, pattern):
                continue
            specificity = len(pattern)
            if specificity > best_specificity or (
                specificity == best_specificity and priority > best_priority
            ):
                best_specificity = specificity
                best_priority = priority
                best_category = category

    return best_category


def cross_check_catalog(categories: dict[str, list[str]], catalog: dict[str, Any] | None) -> None:
    """template-catalog.yml の features[].policy と manifest 分類の整合を検査する。

    矛盾があれば警告を表示する（処理は継続する）。
    """
    if not catalog:
        return
    features = catalog.get("features") or []
    warnings: list[str] = []
    for feature in features:
        policy = feature.get("policy")
        if policy not in CATEGORY_PRIORITY:
            continue
        for path in feature.get("paths") or []:
            probe = path + "__probe__" if path.endswith("/") else path
            actual = classify_file(probe, categories)
            if actual != policy:
                warnings.append(
                    f"  - feature '{feature.get('id')}' の {path} は "
                    f"catalog:{policy} / manifest:{actual}"
                )
    if warnings:
        print("\n⚠️  catalog と manifest の分類に不整合があります:")
        for line in warnings:
            print(line)


# ──────────────────────────────────────────────
# テンプレート URL / バージョン解決
# ──────────────────────────────────────────────
def resolve_template_url(project_dir: Path, override: str | None) -> str:
    """テンプレート取得元 URL を解決する。

    優先順位: コマンドライン --template-url
      → template-catalog.yml (template.repository)
      → .template-update.yml (template_repository)
      → コード定数 DEFAULT_TEMPLATE_URL
    """
    if override:
        return override
    catalog = load_catalog(project_dir / CATALOG_FILE)
    if catalog:
        repo = (catalog.get("template") or {}).get("repository")
        if repo:
            return str(repo)
    manifest_path = project_dir / MANIFEST_FILE
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
        repo = manifest.get("template_repository")
        if repo:
            return str(repo)
    return DEFAULT_TEMPLATE_URL


def clone_template(template_url: str, dest: Path) -> str:
    """テンプレートを --depth=1 でクローンし HEAD の sha を返す。"""
    print(f"  テンプレート取得中: {template_url}")
    run_cmd(["git", "clone", "--depth=1", template_url, str(dest)])
    result = run_cmd(["git", "rev-parse", "HEAD"], cwd=dest)
    return result.stdout.strip()


def read_local_version(project_dir: Path) -> dict[str, Any]:
    """.template-version.yml を読む。無ければ空辞書（未適用）。"""
    version_path = project_dir / VERSION_FILE
    if not version_path.exists():
        return {}
    if yaml is not None:
        with version_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    # 簡易パース
    data = {}
    for line in version_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        data[key.strip()] = _scalar_value(val)
    return data


def write_version_file(
    project_dir: Path,
    *,
    template_name: str,
    template_repository: str,
    version: str,
    commit: str,
) -> None:
    """.template-version.yml を書き出す（apply 成功時の適用状態）。"""
    applied_at = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = (
        "# テンプレート適用状態（scripts/template_update.py apply が自動生成）\n"
        "# 手動編集は不要。次回 check 時にテンプレート最新版との差分判定に使う。\n"
        f'template_name: "{template_name}"\n'
        f'template_repository: "{template_repository}"\n'
        f'applied_version: "{version}"\n'
        f'applied_commit: "{commit}"\n'
        f'applied_at: "{applied_at}"\n'
    )
    (project_dir / VERSION_FILE).write_text(content, encoding="utf-8")


# ──────────────────────────────────────────────
# バージョン / CHANGELOG
# ──────────────────────────────────────────────
def parse_semver(text: str | None) -> tuple[int, ...]:
    """ "3.0.0" のような版文字列を比較可能なタプルへ変換する。失敗時は (0,)。"""
    if not text:
        return (0,)
    cleaned = str(text).strip().strip("\"'").lstrip("vV")
    parts: list[int] = []
    for token in cleaned.split("."):
        num = ""
        for ch in token:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts) if parts else (0,)


def parse_changelog(changelog_path: Path) -> list[tuple[str, str]]:
    """CHANGELOG を (version, 本文) のリストへ変換する（記載順）。

    見出し `## [x.y.z] - ...` を版の区切りとみなす。
    """
    if not changelog_path.exists():
        return []
    entries: list[tuple[str, list[str]]] = []
    current_version: str | None = None
    current_body: list[str] = []
    for line in changelog_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_version is not None:
                entries.append((current_version, current_body))
            version = _extract_heading_version(stripped)
            current_version = version
            current_body = [line]
        elif current_version is not None:
            current_body.append(line)
    if current_version is not None:
        entries.append((current_version, current_body))
    return [(v, "\n".join(body).rstrip()) for v, body in entries]


def _extract_heading_version(heading: str) -> str:
    """`## [3.0.0] - 2026-07-07` から "3.0.0" を取り出す。"""
    text = heading.lstrip("#").strip()
    if "[" in text and "]" in text:
        return text[text.index("[") + 1 : text.index("]")].strip()
    # 角括弧が無い場合は先頭トークン
    return text.split()[0] if text.split() else text


def changelog_excerpt(
    changelog_path: Path, applied_version: str | None, remote_version: str | None
) -> str:
    """applied_version より新しい版のエントリを抜粋して返す。"""
    entries = parse_changelog(changelog_path)
    if not entries:
        return "（CHANGELOG を取得できませんでした）"
    applied = parse_semver(applied_version) if applied_version else None
    selected: list[str] = []
    for version, body in entries:
        ver = parse_semver(version)
        if applied is None or ver > applied:
            selected.append(body)
    if not selected:
        # 版一致でも最新エントリを 1 件は見せる
        selected = [entries[0][1]]
    return "\n\n".join(selected)


# ──────────────────────────────────────────────
# 分析 / レポート / 適用
# ──────────────────────────────────────────────
def analyze(
    template_dir: Path,
    project_dir: Path,
    categories: dict[str, list[str]],
) -> dict[str, list[dict[str, str]]]:
    """テンプレートとプロジェクトのファイルを比較し操作計画を作る。"""
    template_files = collect_files(template_dir)
    project_files = set(collect_files(project_dir))

    plan: dict[str, list[dict[str, str]]] = {
        "update": [],
        "add": [],
        "skip_project": [],
        "skip_sample": [],
        "unclassified": [],
    }

    for rel_path in template_files:
        category = classify_file(rel_path, categories)
        exists_in_project = rel_path in project_files

        if category == "always_update":
            plan["update"].append(
                {"path": rel_path, "action": "上書き" if exists_in_project else "新規追加"}
            )
        elif category == "add_only":
            if not exists_in_project:
                plan["add"].append({"path": rel_path, "action": "新規追加"})
        elif category == "never_update":
            plan["skip_project"].append({"path": rel_path, "reason": "プロジェクト固有"})
        elif category == "sample_only":
            plan["skip_sample"].append({"path": rel_path, "reason": "サンプルファイル"})
        else:
            plan["unclassified"].append({"path": rel_path, "reason": "マニフェスト未定義"})

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

    print(f"\n[更新] always_update: {len(update_files)} ファイル")
    for item in update_files:
        print(f"   - {item['path']} [{item['action']}]")

    print(f"\n[追加] add_only: {len(add_files)} ファイル")
    for item in add_files:
        print(f"   - {item['path']}")

    print(f"\n[保護] never_update（プロジェクト固有）: {len(skip_project)} ファイル")
    for item in skip_project:
        print(f"   - {item['path']}")

    print(f"\n[除外] sample_only（サンプル）: {len(skip_sample)} ファイル")
    for item in skip_sample:
        print(f"   - {item['path']}")

    if unclassified:
        print(f"\n[未分類]: {len(unclassified)} ファイル")
        for item in unclassified:
            print(f"   - {item['path']} ({item['reason']})")

    total_changes = len(update_files) + len(add_files)
    total_skipped = len(skip_project) + len(skip_sample)
    print(f"\n{'-' * 60}")
    print(f"合計: 変更 {total_changes} / スキップ {total_skipped} / 未分類 {len(unclassified)}")
    print("-" * 60)


def apply_changes(
    template_dir: Path,
    project_dir: Path,
    plan: dict[str, list[dict[str, str]]],
) -> int:
    """操作計画に従いファイルをコピーする。"""
    count = 0
    for item in plan["update"] + plan["add"]:
        src = template_dir / item["path"]
        dst = project_dir / item["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


def create_backup_branch(project_dir: Path) -> str:
    """バックアップブランチを作成する。"""
    result = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_dir)
    current_branch = result.stdout.strip()
    ts = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d-%H%M%S")
    backup_branch = f"{BACKUP_BRANCH_PREFIX}-{ts}"
    run_cmd(["git", "branch", backup_branch], cwd=project_dir)
    print(f"  バックアップブランチ作成: {backup_branch} (from {current_branch})")
    return backup_branch


def run_post_checks(project_dir: Path) -> bool:
    """アップデート後の品質チェックを実行する。"""
    print("\nポストチェック実行中...")
    checks = [
        (["python", "-m", "ruff", "check", "."], "ruff check"),
        (["python", "-m", "ruff", "format", "--check", "."], "ruff format"),
        (["python", "ci/policy_check.py"], "policy_check"),
    ]
    all_passed = True
    for cmd, label in checks:
        try:
            result = run_cmd(cmd, cwd=project_dir, check=False)
        except FileNotFoundError:
            print(f"  [skip] {label}（実行環境に未導入）")
            continue
        status = "OK" if result.returncode == 0 else "NG"
        print(f"  [{status}] {label}")
        if result.returncode != 0:
            all_passed = False
            for line in (result.stdout or "").splitlines()[:10]:
                print(f"     {line}")
            for line in (result.stderr or "").splitlines()[:10]:
                print(f"     {line}")
    return all_passed


# ──────────────────────────────────────────────
# サブコマンド: check
# ──────────────────────────────────────────────
def cmd_check(args: argparse.Namespace, project_dir: Path) -> int:
    """テンプレート最新版と適用済み版を比較する。"""
    template_url = resolve_template_url(project_dir, args.template_url)
    local = read_local_version(project_dir)
    applied_version = local.get("applied_version")

    print("テンプレート最新版を確認中...")
    with tempfile.TemporaryDirectory() as tmpdir:
        template_dir = Path(tmpdir) / "template"
        try:
            clone_template(template_url, template_dir)
        except subprocess.CalledProcessError as exc:
            print(f"エラー: テンプレートの取得に失敗しました。\n{exc}")
            return EXIT_ERROR

        catalog = load_catalog(template_dir / CATALOG_FILE)
        if not catalog:
            print("エラー: テンプレートに template-catalog.yml がありません。")
            return EXIT_ERROR
        remote_version = str((catalog.get("template") or {}).get("version") or "")
        if not remote_version:
            print("エラー: template-catalog.yml に version がありません。")
            return EXIT_ERROR

        print("\n" + "=" * 60)
        print("テンプレート更新チェック")
        print("=" * 60)
        print(f"  適用済み版: {applied_version or '未適用'}")
        print(f"  最新版    : {remote_version}")

        remote = parse_semver(remote_version)
        current = parse_semver(applied_version) if applied_version else None

        if applied_version and current is not None and remote <= current:
            print("\n最新です。アップデートは不要です。")
            return EXIT_OK

        print("\n更新があります。")
        excerpt = changelog_excerpt(template_dir / CHANGELOG_FILE, applied_version, remote_version)
        print("\n--- 変更履歴（抜粋）" + "-" * 40)
        print(excerpt)
        print("-" * 60)
        print(
            "\n適用するには: python scripts/template_update.py apply --dry-run で確認後、"
            "\n              python scripts/template_update.py apply を実行してください。"
        )
        return EXIT_UPDATE_AVAILABLE


# ──────────────────────────────────────────────
# サブコマンド: apply
# ──────────────────────────────────────────────
def cmd_apply(args: argparse.Namespace, project_dir: Path) -> int:
    """テンプレートの変更を適用する。"""
    manifest_path = project_dir / MANIFEST_FILE
    if not manifest_path.exists():
        print(f"エラー: {MANIFEST_FILE} が見つかりません。")
        print("このスクリプトはプロジェクトルートで実行してください。")
        return EXIT_ERROR

    print("マニフェスト読み込み中...")
    manifest = load_manifest(manifest_path)
    categories = get_categories(manifest)
    cross_check_catalog(categories, load_catalog(project_dir / CATALOG_FILE))

    template_url = resolve_template_url(project_dir, args.template_url)

    with tempfile.TemporaryDirectory() as tmpdir:
        template_dir = Path(tmpdir) / "template"
        print("テンプレート取得中...")
        try:
            head_sha = clone_template(template_url, template_dir)
        except subprocess.CalledProcessError as exc:
            print(f"エラー: テンプレートの取得に失敗しました。\n{exc}")
            return EXIT_ERROR

        catalog = load_catalog(template_dir / CATALOG_FILE)
        template_meta = (catalog or {}).get("template") or {}
        remote_version = str(template_meta.get("version") or "unknown")
        template_name = str(template_meta.get("name") or "ai-dev-template")

        print("ファイル分析中...")
        plan = analyze(template_dir, project_dir, categories)
        print_report(plan)

        if plan["unclassified"]:
            print("\n⚠️  未分類ファイルがあります。")
            print("   .template-update.yml を更新して分類してください（スキップされます）。")

        if args.dry_run:
            print("\nこれは dry-run です。実際の変更は行われていません。")
            print(f"（適用時に記録される版: {remote_version}）")
            return EXIT_OK

        total_changes = len(plan["update"]) + len(plan["add"])
        if total_changes > 0:
            if not args.no_backup:
                print("\nバックアップ作成中...")
                backup_branch = create_backup_branch(project_dir)
                print(f"   復元が必要な場合: git checkout {backup_branch}")
            print("\nアップデート適用中...")
            count = apply_changes(template_dir, project_dir, plan)
            print(f"   {count} ファイルを更新しました。")
        else:
            print("\n変更対象のファイルはありません（版のみ更新します）。")

        # 適用状態を記録
        write_version_file(
            project_dir,
            template_name=template_name,
            template_repository=template_url,
            version=remote_version,
            commit=head_sha,
        )
        print(f"   適用状態を記録: {VERSION_FILE}（version {remote_version}）")

    if not args.no_post_check:
        checks_passed = run_post_checks(project_dir)
        if not checks_passed:
            print("\n⚠️  一部のチェックが失敗しています。")
            print("   使用中の AI アシスタントに品質チェックのエラー修正を依頼してください。")

    print("\nテンプレートアップデートが完了しました。")
    print("   変更内容を確認し、コミットしてください:")
    print("   git add -A && git commit -m 'chore: テンプレートアップデート適用'")
    return EXIT_OK


# ──────────────────────────────────────────────
# サブコマンド: export
# ──────────────────────────────────────────────
def cmd_export(args: argparse.Namespace, project_dir: Path) -> int:
    """現リポジトリのテンプレート基盤をテンプレートのローカルクローンへ反映する。"""
    manifest_path = project_dir / MANIFEST_FILE
    if not manifest_path.exists():
        print(f"エラー: {MANIFEST_FILE} が見つかりません。")
        return EXIT_ERROR

    template_dir = Path(args.template_dir).expanduser().resolve()
    if not template_dir.is_dir():
        print(f"エラー: --template-dir が存在しません: {template_dir}")
        return EXIT_ERROR

    # 分類元マニフェストの解決:
    # 「何がテンプレート管理か」はテンプレート側の定義を正とするため、
    # export はテンプレート側マニフェストを優先して読む。子側マニフェストは
    # apply（ローカル保護）用のカスタマイズであり、逆反映の範囲を狭めない。
    template_manifest_path = template_dir / MANIFEST_FILE
    if template_manifest_path.exists():
        manifest = load_manifest(template_manifest_path)
        catalog = load_catalog(template_dir / CATALOG_FILE)
        print("  分類元: テンプレート側マニフェスト")
    else:
        manifest = load_manifest(manifest_path)
        catalog = load_catalog(project_dir / CATALOG_FILE)
        print("  分類元: 子リポジトリ側マニフェスト（テンプレート側に未配置のため）")
    categories = get_categories(manifest)
    cross_check_catalog(categories, catalog)

    print("\n" + "=" * 60)
    print("逆方向同期（export） — 現リポジトリ → テンプレート")
    print("=" * 60)
    print(f"  反映先: {template_dir}")

    copied_update: list[str] = []
    copied_add: list[str] = []
    skipped_add: list[str] = []

    for rel_path in collect_files(project_dir):
        category = classify_file(rel_path, categories)
        if category not in ("always_update", "add_only"):
            continue
        src = project_dir / rel_path
        dst = template_dir / rel_path
        if category == "add_only":
            if dst.exists():
                skipped_add.append(rel_path)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_add.append(rel_path)
        else:  # always_update
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_update.append(rel_path)

    print(f"\n[反映] always_update: {len(copied_update)} ファイル")
    for path in copied_update:
        print(f"   - {path}")
    print(f"\n[追加] add_only（宛先に無いもののみ）: {len(copied_add)} ファイル")
    for path in copied_add:
        print(f"   - {path}")
    if skipped_add:
        print(f"\n[据置] add_only（宛先に既存のためスキップ）: {len(skipped_add)} ファイル")
        for path in skipped_add:
            print(f"   - {path}")

    print("\n" + "=" * 60)
    print("汎用化チェックリスト（反映後にテンプレート側で必ず実施）")
    print("=" * 60)
    print(
        "  1. ドメイン語の混入確認（grep 推奨。角括弧内は導入元プロジェクト固有の"
        "用語に置き換える）:\n"
        '       grep -ratinE "<ドメイン語1>|<ドメイン語2>|<ドメイン語3>" '
        "--exclude-dir=.git .\n"
        "  2. プロジェクト固有 ID の除去（N-xxx / ADR-00xx / FR-xxx / 個別プロジェクト名 等）\n"
        "  3. 入口ファイルの第一目的を汎用プレースホルダへ戻す（PROJECT_PURPOSE 等）\n"
        "  4. template-catalog.yml の template.version を更新（新機能は features に追記）\n"
        "  5. docs/TEMPLATE_CHANGELOG.md に変更エントリを追記\n"
        "  6. python scripts/template_update.py apply --dry-run で未分類 0 件を確認\n"
        "  7. ブランチを作成しコミット・push・PR 作成（PR 本文は日本語・テンプレート準拠）"
    )
    print("=" * 60)
    return EXIT_OK


# ──────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    """argparse パーサーを構築する。"""
    parser = argparse.ArgumentParser(
        description="テンプレートアップデートスクリプト v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "使用例:\n"
            "  python scripts/template_update.py check\n"
            "  python scripts/template_update.py apply --dry-run\n"
            "  python scripts/template_update.py apply\n"
            "  python scripts/template_update.py export --template-dir /tmp/template-clone\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="{check,apply,export}")

    p_check = subparsers.add_parser("check", help="テンプレート最新版と適用済み版を比較する")
    p_check.add_argument("--template-url", default=None, help="テンプレートリポジトリの URL")

    p_apply = subparsers.add_parser("apply", help="テンプレートの変更を適用する")
    p_apply.add_argument("--dry-run", action="store_true", help="変更せず計画のみ表示する")
    p_apply.add_argument("--template-url", default=None, help="テンプレートリポジトリの URL")
    p_apply.add_argument("--no-backup", action="store_true", help="バックアップブランチを作らない")
    p_apply.add_argument(
        "--no-post-check", action="store_true", help="ポストチェックをスキップする"
    )

    p_export = subparsers.add_parser(
        "export", help="現リポジトリの基盤をテンプレートのローカルクローンへ反映する"
    )
    p_export.add_argument(
        "--template-dir", required=True, help="反映先テンプレートのローカルクローンのパス"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """メインエントリポイント。"""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    subcommands = {"check", "apply", "export"}
    first_positional = next((a for a in raw_argv if not a.startswith("-")), None)
    help_requested = any(a in ("-h", "--help") for a in raw_argv)
    # 後方互換: サブコマンド無しは apply 相当（ただし --help はトップレベルの説明を表示）
    deprecated_invocation = first_positional not in subcommands and not help_requested
    if deprecated_invocation:
        raw_argv = ["apply"] + raw_argv

    parser = build_parser()
    args = parser.parse_args(raw_argv)

    if deprecated_invocation:
        print(
            "⚠️  サブコマンド無しの実行は非推奨です。'apply' として実行します。\n"
            "   今後は 'check' / 'apply' / 'export' を明示してください。\n"
        )

    project_dir = Path.cwd()

    if args.command == "check":
        return cmd_check(args, project_dir)
    if args.command == "export":
        return cmd_export(args, project_dir)
    return cmd_apply(args, project_dir)


if __name__ == "__main__":
    sys.exit(main())
