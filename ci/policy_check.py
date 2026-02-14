"""汎用ポリシーチェッカー。

プロジェクト固有の禁止パターンと秘密情報パターンをチェックする。
project-config.yml の policies セクション、または本ファイル内の定数で設定する。

使い方:
    python ci/policy_check.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

# スキャン対象ディレクトリ（プロジェクトに合わせて変更）
SCAN_DIRS = [
    REPO_ROOT / "src",
    REPO_ROOT / "tests",
    REPO_ROOT / "scripts",
]

# スキャン対象の拡張子
SCAN_EXTENSIONS = {".py", ".ts", ".js", ".go", ".rs", ".toml", ".txt", ".yml", ".yaml"}

# スキップするディレクトリ名
SKIP_DIR_NAMES = {"__pycache__", ".git", "node_modules", ".mypy_cache", ".ruff_cache", "target"}

# ホワイトリスト（パスの相対表記）— 誤検知を除外するファイル
SKIP_FILES: set[str] = {
    "ci/policy_check.py",  # 自分自身のパターン定義は除外
}

# ---------------------------------------------------------------------------
# 禁止パターン（プロジェクトに合わせてカスタマイズ）
# ---------------------------------------------------------------------------

# 禁止 import パターン（正規表現、Python ファイルのみ適用）
# 例: 外部 HTTP ライブラリを禁止する場合
FORBIDDEN_IMPORT_PATTERNS: list[str] = [
    # r"^\s*import\s+requests",
    # r"^\s*from\s+requests\s+import",
    # r"^\s*import\s+httpx",
    # r"^\s*from\s+httpx\s+import",
]

# 秘密情報パターン（正規表現、全ファイル種別に適用）
SECRET_PATTERNS: list[str] = [
    r"AKIA[0-9A-Z]{16}",                          # AWS Access Key ID
    r"-----BEGIN\s+(RSA|DSA|EC|OPENSSH)\s+PRIVATE\s+KEY-----",  # SSH 秘密鍵
    r"ghp_[A-Za-z0-9_]{36,}",                     # GitHub Personal Access Token
    r"sk-[A-Za-z0-9]{32,}",                        # 汎用 API キー
]

# URL パターン（コード中の外部 URL 直書きを検出）
URL_PATTERN = r"https?://[^\s\"')\]>]+"

# URL ホワイトリスト（マッチしたら許可）
URL_ALLOWLIST_PATTERNS: list[str] = [
    r"example\.com",
    r"github\.com",
    r"pypi\.org",
    r"npmjs\.com",
    r"docs\.python\.org",
    r"schemas\.openapi",
    r"json-schema\.org",
    r"astral\.sh",
]

# 禁止操作パターン（言語非依存、全ファイルに適用）
FORBIDDEN_PATTERNS: list[str] = [
    # プロジェクト固有の禁止パターンをここに追加
]


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------


def git_ls_files() -> list[Path]:
    """git 管理対象のファイル一覧を取得する。"""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=True,
        )
        return [REPO_ROOT / line for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def should_skip(path: Path) -> bool:
    """スキップ対象のディレクトリに含まれるか判定する。"""
    parts = set(path.parts)
    return any(name in parts for name in SKIP_DIR_NAMES)


def is_skipped_file(path: Path) -> bool:
    """ホワイトリストに登録されたファイルか判定する。"""
    rel = path.relative_to(REPO_ROOT).as_posix()
    return rel in SKIP_FILES


def read_text_safely(path: Path) -> str | None:
    """ファイルを安全に読み込む。"""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return None


def is_url_allowlisted(line: str) -> bool:
    """URL がホワイトリストに該当するか判定する。"""
    return any(re.search(pat, line) for pat in URL_ALLOWLIST_PATTERNS)


def is_code_file(path: Path) -> bool:
    """コード系ファイルか判定する（コメント行の判定に使用）。"""
    return path.suffix.lower() in {".py", ".ts", ".js", ".go", ".rs"}


def is_comment_line(line: str, suffix: str) -> bool:
    """コメント行か判定する。"""
    stripped = line.lstrip()
    if suffix in {".py", ".ts", ".js", ".go", ".rs"}:
        if stripped.startswith("#") or stripped.startswith("//"):
            return True
    return False


# ---------------------------------------------------------------------------
# スキャン
# ---------------------------------------------------------------------------


def scan_file(path: Path) -> list[str]:
    """1 ファイルをスキャンし、問題を返す。"""
    issues: list[str] = []
    text = read_text_safely(path)
    if text is None:
        return issues

    rel = path.relative_to(REPO_ROOT)
    suffix = path.suffix.lower()

    # 禁止 import（コードファイルのみ）
    if is_code_file(path) and FORBIDDEN_IMPORT_PATTERNS:
        for pat in FORBIDDEN_IMPORT_PATTERNS:
            if re.search(pat, text, flags=re.MULTILINE):
                issues.append(f"禁止操作疑い: 禁止import検出 ({pat}) in {rel}")

    # URL 直書き（コードファイルのみ — コメント行は除外）
    if is_code_file(path):
        for lineno, line in enumerate(text.splitlines(), start=1):
            if is_comment_line(line, suffix):
                continue
            if re.search(URL_PATTERN, line) and not is_url_allowlisted(line):
                issues.append(f"外部接続疑い: URL直書き検出 in {rel}:{lineno}")

    # 秘密情報（全ファイル種別）
    for pat in SECRET_PATTERNS:
        if re.search(pat, text):
            issues.append(f"秘密情報疑い: パターン検出 ({pat}) in {rel}")

    # プロジェクト固有の禁止パターン（全ファイル種別）
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, text, flags=re.MULTILINE):
            issues.append(f"禁止パターン検出: ({pat}) in {rel}")

    return issues


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> int:
    """ポリシーチェックを実行し、違反があれば非ゼロで終了する。"""
    issues: list[str] = []

    # .env が git 管理されていないことを確認
    tracked_files = {p.relative_to(REPO_ROOT).as_posix() for p in git_ls_files()}
    if ".env" in tracked_files:
        issues.append(
            "禁止: .env がリポジトリにコミットされています。削除し、gitignore 対象にしてください。"
        )

    # 対象ファイルのスキャン
    for root in SCAN_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if should_skip(path):
                continue
            if is_skipped_file(path):
                continue
            if path.suffix.lower() not in SCAN_EXTENSIONS:
                continue
            issues.extend(scan_file(path))

    if issues:
        print("[policy_check] FAILED")
        for i, msg in enumerate(issues, start=1):
            print(f"  {i}. {msg}")
        return 1

    print("[policy_check] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
