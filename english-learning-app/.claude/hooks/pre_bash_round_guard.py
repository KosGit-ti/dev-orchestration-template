#!/usr/bin/env python3
"""PreToolUse hook: Bash / reviewer request tool の Round 状態をリマインドする。

Claude Code の Bash ツールで git push または Copilot レビュー要求
（gh api requested_reviewers 等）、あるいは専用 reviewer request tool が
実行されようとした際に、現在のブランチの OPEN PR の review_count が 3 以上なら
N-371 / P-064 によりブロックせず、延長適格性のリマインドだけを返す。

レビューループは Round 3 を既定の収束点とする。Round 3 到達後の非ブロッキング指摘は
Backlog 化する。適格理由、実質修正、延長証跡がある場合だけ terminal anchor 前に限って
Round 4 以降へ延長する。

transient な PR / review 状態取得失敗は fail-open とする。
"""

import importlib
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# scripts/ 配下の helper import 用に repo root を追加
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 同一ディレクトリの _github_api を import するため sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent))
_command_detection = importlib.import_module("scripts.hooks._command_detection")
_review_loop_state = importlib.import_module("scripts.hooks._review_loop_state")
detect_review_loop_actions = _command_detection.detect_review_loop_actions

from _github_api import (  # noqa: E402
    gh_subprocess_env,
    has_credentials,
    is_claude_code_remote,
    list_open_prs_for_branch,
)

_COPILOT_LOGINS: frozenset[str] = frozenset(
    {
        "copilot-pull-request-reviewer[bot]",
        "copilot-pull-request-reviewer",
        "Copilot",
    }
)
_AI_REVIEW_LOGINS: frozenset[str] = _COPILOT_LOGINS | frozenset(
    {
        "claude",
        "claude[bot]",
        "claude-code[bot]",
    }
)
_AI_REVIEW_MARKERS: tuple[str, ...] = (
    "## AI レビュー結果",
    "engine: `claude`",
)


def _is_review_request_tool_name(tool_name: object) -> bool:
    """専用レビュー要求ツール名を判定する。"""
    if not isinstance(tool_name, str):
        return False
    normalized = tool_name.strip()
    return normalized in {
        "request_ai_review",
        "request_copilot_review",
        "request_claude_review",
    } or normalized.endswith(
        (
            "request_ai_review",
            "request_copilot_review",
            "request_claude_review",
        )
    )


def _run_git(cmd: list[str], timeout: int = 10) -> str | None:
    """git コマンドを実行して stdout を返す。失敗時は None。"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _run(
    cmd: list[str],
    timeout: int = 15,
    *,
    allow_nonzero: bool = False,
) -> str | None:
    """コマンドを実行して stdout を返す。失敗時は None。

    gh 未認証環境でも gh 呼び出しが認証されるよう gh_subprocess_env() の env を渡す。
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=gh_subprocess_env() if cmd[:1] == ["gh"] else None,
        )
        if result.returncode != 0 and not allow_nonzero:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _is_ai_review(login: object, body: object) -> bool:
    if isinstance(login, str) and login in _AI_REVIEW_LOGINS:
        return True
    if isinstance(body, str):
        return any(marker in body for marker in _AI_REVIEW_MARKERS)
    return False


def _count_copilot_reviews(pr_number: str) -> int | None:
    """異なる canonical head に対する Copilot / Claude review 数を返す。"""
    return _review_loop_state.get_ai_review_count(int(pr_number))


def _allow(message: str | None = None) -> None:
    """Claude Code PreToolUse の allow 応答を出力する。"""
    payload: dict[str, object] = {"decision": "allow"}
    if message:
        payload["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    json.dump(payload, sys.stdout, ensure_ascii=False)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        json.dump({"decision": "allow"}, sys.stdout)
        return

    tool_name = input_data.get("tool_name", input_data.get("toolName", ""))
    tool_input = input_data.get("tool_input", input_data.get("toolInput", {}))
    if not isinstance(tool_input, dict):
        tool_input = {}

    is_review_request = _is_review_request_tool_name(tool_name)
    is_push = False

    # Bash か reviewer request tool 以外は即スキップ
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if not isinstance(command, str):
            json.dump({"decision": "allow"}, sys.stdout)
            return

        # 実際のシェルコマンドとして git push / reviewer request を検出する
        detection = detect_review_loop_actions(command)
        is_push = detection.is_git_push
        is_review_request = is_review_request or detection.is_reviewer_request
    elif not is_review_request:
        json.dump({"decision": "allow"}, sys.stdout)
        return

    if not (is_push or is_review_request):
        json.dump({"decision": "allow"}, sys.stdout)
        return

    # 保護ブランチでも reviewer request は release PR を含めて検査する。
    branch = _run_git(["git", "branch", "--show-current"])
    if not branch:
        json.dump({"decision": "allow"}, sys.stdout)
        return
    if branch in ("main", "master", "develop") and is_push and not is_review_request:
        json.dump({"decision": "allow"}, sys.stdout)
        return

    # Claude Code Remote 環境は gh / GITHUB_TOKEN が無く、MCP 経路でレビューループを行う
    # git push は allow して、review request tool は通常どおり Round 予算を検査する
    if is_claude_code_remote() and is_push and not is_review_request:
        json.dump({"decision": "allow"}, sys.stdout)
        return

    if not has_credentials():
        _allow(
            f"【N-371 reminder / Round 予算ガード 認証不能】ブランチ: {branch}\n"
            "gh CLI 未インストール かつ GITHUB_TOKEN/GH_TOKEN 未設定のため、"
            "PR の review_count を確認できません。\n"
            "N-371 により transient lookup 失敗は fail-open とし、"
            "git push / AIレビュー要求はブロックしません。"
        )
        return

    # OPEN な PR を取得
    pr_list = list_open_prs_for_branch(branch)
    if pr_list is None:
        _allow(
            f"【N-371 reminder / Round 予算ガード PR lookup 失敗】ブランチ: {branch}\n"
            "PR 状態の取得に失敗しました。\n"
            "N-371 により transient lookup 失敗は fail-open とし、"
            "git push / AIレビュー要求はブロックしません。"
        )
        return

    if not pr_list:
        # OPEN な PR が存在しない場合は Round 予算の概念が無いので allow
        json.dump({"decision": "allow"}, sys.stdout)
        return

    try:
        first_pr = pr_list[0]
        pr_number = str(first_pr.get("number", "?"))
        state = str(first_pr.get("state", "")).upper()
        if state != "OPEN":
            json.dump({"decision": "allow"}, sys.stdout)
            return
    except (KeyError, IndexError, TypeError):
        _allow(
            f"【N-371 reminder / Round 予算ガード PR 情報パース失敗】ブランチ: {branch}\n"
            "PR 情報の解析に失敗しました。\n"
            "N-371 により transient lookup 失敗は fail-open とします。"
        )
        return

    # AI review_count を取得
    review_count = _count_copilot_reviews(pr_number)
    if review_count is None:
        _allow(
            f"【N-371 reminder / Round 予算ガード review_count 取得失敗】PR #{pr_number}\n"
            "AIレビュー数の取得に失敗しました。\n"
            "N-371 によりレビュー状態 lookup 失敗は fail-open とします。"
        )
        return

    if review_count >= 3:
        _allow(
            f"【N-371 reminder / 延長ラウンド候補】PR #{pr_number} / "
            f"review_count={review_count}\n"
            "AIレビューループは Round 3 が既定の収束点です。\n"
            "Hook は git push / AIレビュー要求をブロックしません。Round 4 以降へ進む前に、"
            "terminal anchor 前であり、即時ブロッカー、必須ゲート失敗、anchor 前の "
            "merge 契約 preflight 失敗、または人間オペレーターの明示指示があることを"
            "確認してください。実質修正、修正前後 head、検証結果を延長証跡へ記録します。"
            "非ブロッキング Must/Should だけなら Backlog 化し、延長しません。"
        )
        return

    # 既定 Round 3 より前なら allow
    json.dump({"decision": "allow"}, sys.stdout)


if __name__ == "__main__":
    main()
