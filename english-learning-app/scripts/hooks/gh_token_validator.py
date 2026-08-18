"""gh_token_validator.py — GitHub API トークン有効性検証モジュール（フック共有）

全プラン実行モードでの自動化パイプライン中、PR 操作の前後に呼び出して
トークン期限切れ（401）を事前検知する。

hooks/ 配下の他のスクリプトから import して使う。

PR レビュー対応は最大 3 ラウンド。Round 3 後の非ブロッキング Must/Should は Backlog 化、
即時ブロッカーは fail-close。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import Enum


class TokenStatus(Enum):
    """トークン有効性の状態。"""

    VALID = "valid"
    INVALID = "invalid"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"  # 将来拡張用。到達時は呼び出し側でフェイルクローズする。


@dataclass(frozen=True)
class TokenCheckResult:
    """トークン検証の結果。"""

    status: TokenStatus
    remaining_calls: int | None
    message: str
    recovery_hint: str


# ────────────────────────────────────────────────
# 定数
# ────────────────────────────────────────────────
_GH_EXEC: str = ""  # 遅延初期化


def _get_gh() -> str:
    """gh コマンドの絶対パスを返す（遅延初期化）。"""
    global _GH_EXEC
    if _GH_EXEC:
        return _GH_EXEC
    import shutil

    candidates = ["/usr/local/bin/gh", "/usr/bin/gh"]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            _GH_EXEC = p
            return _GH_EXEC
    found = shutil.which("gh")
    _GH_EXEC = found or "gh"
    return _GH_EXEC


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """コマンドを実行して (returncode, stdout, stderr) を返す。"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", "command not found"


# ────────────────────────────────────────────────
# トークン検証
# ────────────────────────────────────────────────
def check_gh_token() -> TokenCheckResult:
    """gh CLI トークンの有効性を検証する。

    Returns:
        TokenCheckResult: 検証結果。status が VALID 以外の場合は中断を推奨。
    """
    gh = _get_gh()

    # 1. gh auth status 確認
    rc, out, err = _run([gh, "auth", "status"])
    if rc != 0:
        return TokenCheckResult(
            status=TokenStatus.INVALID,
            remaining_calls=None,
            message=f"gh auth status 失敗: {err or out}",
            recovery_hint=(
                "GH_TOKEN が無効または期限切れです。\n"
                "  1. https://github.com/settings/tokens で新しい PAT を生成\n"
                "  2. shell 履歴に残らない投入手順を docs/runbook.md で確認\n"
                "  3. または VS Code で Copilot を再認証してください"
            ),
        )

    # 2. API 疎通確認（rate_limit エンドポイント使用）
    rc2, remaining_str, err2 = _run([gh, "api", "/rate_limit", "--jq", ".rate.remaining"])
    if rc2 != 0:
        return TokenCheckResult(
            status=TokenStatus.INVALID,
            remaining_calls=None,
            message=f"GitHub API 疎通失敗: {err2}",
            recovery_hint=(
                "トークンは gh auth に登録されていますが、API 呼び出しで 401 が発生しました。\n"
                "  1. GH_TOKEN 環境変数が設定されているかだけ確認してください\n"
                "  2. トークンスコープ確認: repo, workflow, read:org, project が必要\n"
                "  3. gh auth refresh --hostname github.com を試してください"
            ),
        )

    # 3. レート残数確認
    try:
        remaining = int(remaining_str)
    except (ValueError, TypeError):
        return TokenCheckResult(
            status=TokenStatus.UNKNOWN,
            remaining_calls=None,
            message=f"GitHub API レート残数の解析に失敗しました: {remaining_str!r}",
            recovery_hint=(
                "gh api /rate_limit の応答を手動確認してください。判断不能のため処理は中断します。"
            ),
        )

    if remaining is not None and remaining == 0:
        # reset 時刻を取得
        _, reset_str, _ = _run([gh, "api", "/rate_limit", "--jq", ".rate.reset"])
        return TokenCheckResult(
            status=TokenStatus.RATE_LIMITED,
            remaining_calls=0,
            message=f"GitHub API レート上限に達しました。リセット Unix 時刻: {reset_str}",
            recovery_hint=(
                "GitHub REST API レート上限（通常 5000/h）に達しました。\n"
                f"  リセット時刻: {reset_str}\n"
                "  注意: Copilot Chat の週次レート上限は別管理です。\n"
                "  Copilot MCP ツールが 401 の場合:\n"
                "    VS Code: Ctrl+Shift+P → 'GitHub Copilot: Sign out' → 'Sign in'"
            ),
        )

    return TokenCheckResult(
        status=TokenStatus.VALID,
        remaining_calls=remaining,
        message=f"トークン有効 (API 残: {remaining} リクエスト)",
        recovery_hint="",
    )


def is_token_valid() -> bool:
    """トークンが有効かどうかを bool で返す（簡易版）。"""
    result = check_gh_token()
    return result.status == TokenStatus.VALID


def check_and_warn(context: str = "PR 操作") -> bool:
    """トークン検証を実行し、問題があれば stderr に警告を出す。

    Args:
        context: 操作名（ログ出力用）

    Returns:
        True: トークン有効、False: トークン無効または上限超過
    """
    import sys

    result = check_gh_token()

    if result.status == TokenStatus.VALID:
        print(
            f"[gh_token_validator] {context} 前チェック OK (残: {result.remaining_calls})",
            file=sys.stderr,
        )
        return True

    print(
        f"[gh_token_validator][WARN] {context}: トークン問題を検知",
        file=sys.stderr,
    )
    print(f"  状態: {result.status.value}", file=sys.stderr)
    print(f"  詳細: {result.message}", file=sys.stderr)
    print(f"  復旧方法:\n{result.recovery_hint}", file=sys.stderr)

    if result.status == TokenStatus.INVALID:
        return False

    if result.status == TokenStatus.RATE_LIMITED:
        print(
            "[gh_token_validator][WARN] レート上限到達。"
            "操作を中止してリセット後に再実行してください。",
            file=sys.stderr,
        )
        return False

    return False  # UNKNOWN は判断不能としてフェイルクローズ


# ────────────────────────────────────────────────
# Copilot MCP セッション向け注意事項
# ────────────────────────────────────────────────
COPILOT_MCP_NOTE = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【Copilot MCP セッショントークンについて】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

mcp_io_github_git_* ツール（VS Code Copilot MCP）が 401 を返す場合、
gh CLI トークンとは別の Copilot セッショントークンが期限切れです。

復旧手順:
  1. VS Code: Ctrl+Shift+P → 'GitHub Copilot: Sign out'
  2. 'GitHub Copilot: Sign in' で再認証
  3. または VS Code 自体を再起動

週次レート上限（100% 使用）の場合:
  - GitHub Copilot の週次 API コール上限に達しています
  - リセットは毎週月曜 12:00 AM UTC
  - 上限解除まで mcp_io_github_git_* の代わりに gh CLI を使用

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def main() -> int:
    """CLI エントリポイント。終了コードは shell ヘルパーと揃える。"""
    result = check_gh_token()
    print(f"Status: {result.status.value}")
    print(f"Message: {result.message}")
    if result.recovery_hint:
        print(f"Recovery:\n{result.recovery_hint}")
    if result.status == TokenStatus.INVALID:
        print(COPILOT_MCP_NOTE)
        return 1
    if result.status == TokenStatus.RATE_LIMITED:
        return 2
    if result.status == TokenStatus.UNKNOWN:
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
