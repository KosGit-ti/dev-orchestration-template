#!/usr/local/bin/python3.11
"""PreToolUse hook: task_complete / release-manager の安全床を検査する。

ブロック条件:
- OPEN PR があり、CI チェックに failure / cancelled / error / timed_out がある場合
- 全プラン実行フラグが存在し、plan.md の完了認証に失敗する場合

レビュー未到着・未返信・Round 予算・review state lookup 失敗は transient lookup の
fail-open 方針により非ブロッキングのリマインドへ降格する。CI API 取得失敗も fail-open。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, cast

_REPO_ROOT_FOR_IMPORT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "../..")
)
if _REPO_ROOT_FOR_IMPORT not in sys.path:
    sys.path.insert(0, _REPO_ROOT_FOR_IMPORT)
_command_detection = importlib.import_module("scripts.hooks._command_detection")
detect_review_loop_actions = _command_detection.detect_review_loop_actions
_review_loop_state = importlib.import_module("scripts.hooks._review_loop_state")

if TYPE_CHECKING:
    from collections.abc import Callable


def _find_executable(name: str) -> str:
    """コマンドの絶対パスを返す。見つからなければコマンド名そのままを返す。"""
    candidates = [f"/usr/local/bin/{name}", f"/usr/bin/{name}", f"/bin/{name}"]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    found = shutil.which(name)
    return found if found else name


_GH = _find_executable("gh")
_REPO_ROOT = _REPO_ROOT_FOR_IMPORT
_REVIEW_REQUEST_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "request_ai_review",
        "request_copilot_review",
        "request_codex_review",
        "request_claude_review",
        "mcp__github__request_ai_review",
        "mcp__github__request_copilot_review",
        "mcp__github__request_codex_review",
        "mcp__github__request_claude_review",
    }
)


def _is_review_request_tool_name(tool_name: object) -> bool:
    """専用レビュー要求ツール名を判定する。"""
    if not isinstance(tool_name, str):
        return False
    normalized = tool_name.strip()
    return normalized in _REVIEW_REQUEST_TOOL_NAMES


def _describe_review_loop_action(is_git_push: bool, is_reviewer_request: bool) -> str:
    """Round 予算メッセージ用の操作名を返す。"""
    if is_git_push and is_reviewer_request:
        return "git push / AIレビューリクエスト"
    if is_git_push:
        return "git push"
    return "AIレビューリクエスト"


def _allow(message: str | None = None) -> None:
    """PreToolUse の allow 応答を出力する。"""
    payload = {"permissionDecision": "allow"}
    if message:
        payload["message"] = message
    print(json.dumps(payload, ensure_ascii=False))


def _full_plan_flag_exists() -> bool:
    return os.path.exists(os.path.join(_REPO_ROOT, ".github", "full-plan-execution.flag"))


def _full_plan_load_failure_reason(message: str) -> Callable[..., str]:
    return lambda: message if _full_plan_flag_exists() else ""


def _load_full_plan_completion_block_reason() -> Callable[..., str]:
    module_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "full_plan_completion.py"
    )
    spec = importlib.util.spec_from_file_location("full_plan_completion", module_path)
    if spec is None or spec.loader is None:
        return _full_plan_load_failure_reason("全プラン完了認証モジュールを読み込めません")
    module = importlib.util.module_from_spec(spec)
    sys.modules["full_plan_completion"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        error_message = str(exc)
        return _full_plan_load_failure_reason(
            f"全プラン完了認証モジュールの実行に失敗しました: {error_message}"
        )
    return cast("Callable[..., str]", module.full_plan_completion_block_reason)


full_plan_completion_block_reason = _load_full_plan_completion_block_reason()


def _full_plan_completion_reason(*, require_delivery_state: bool) -> str | None:
    """旧シグネチャのテスト差し替えにも耐えて完了認証を呼ぶ。"""
    try:
        return full_plan_completion_block_reason(require_delivery_state=require_delivery_state)
    except TypeError as exc:
        if "require_delivery_state" not in str(exc):
            raise
        return full_plan_completion_block_reason()


def _get_open_pr_number_result() -> tuple[int | None, str | None]:
    """OPEN PR 番号を取得する。戻り値: (pr_number, error)。

    - (int, None): OPEN PR が存在
    - (None, None): OPEN PR なし（正常）
    - (None, str): lookup エラー（P-010 fail-close 対象）
    """
    try:
        result = subprocess.run(
            [_GH, "pr", "view", "--json", "number,state"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=_REPO_ROOT,
        )
        if result.returncode != 0:
            # returncode != 0 を分類: 既知の「PRなし」文言 vs 認証/ネットワークエラー
            combined_output = (result.stdout + result.stderr).lower()
            no_pr_indicators = [
                "no pull requests found",
                "could not find any pull request",
                "no pull request",
                "not a pull request",
            ]
            if any(indicator in combined_output for indicator in no_pr_indicators):
                # 既知の「PRなし」文言 → 正常
                return None, None
            # 認証エラー、ネットワークエラー、その他未知のエラー → fail-close
            error_output = result.stderr.strip() or result.stdout.strip() or "不明なエラー"
            return None, f"gh pr view failed (rc={result.returncode}): {error_output}"
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            return None, "PR データが dict でありません"
        number = data.get("number")
        if data.get("state") == "OPEN" and isinstance(number, int):
            return number, None
        # PR が CLOSED / MERGED 等
        return None, None
    except json.JSONDecodeError as exc:
        return None, f"PR JSON パースエラー: {exc}"
    except subprocess.TimeoutExpired:
        return None, "gh pr view がタイムアウトしました"
    except Exception as exc:
        return None, f"PR lookup エラー: {exc}"


def _get_open_pr_number() -> int | None:
    """OPEN PR 番号を返す（後方互換用 wrapper）。エラーも None に潰す。"""
    pr_number, _ = _get_open_pr_number_result()
    return pr_number


def _has_ci_failure(pr_number: int) -> bool:
    try:
        result = subprocess.run(
            [_GH, "pr", "checks", str(pr_number), "--json", "name,state,bucket"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=_REPO_ROOT,
        )
        if result.returncode != 0 and not result.stdout.strip():
            return False
        checks = json.loads(result.stdout)
        if not isinstance(checks, list):
            return False
        failure_states = {"FAILURE", "CANCELLED", "ERROR", "TIMED_OUT"}
        success_states = {"SUCCESS"}
        final_gate_state = ""
        for check in checks:
            if not isinstance(check, dict):
                continue
            name = str(check.get("name", ""))
            if name != "ci/final-gate" and not name.endswith(" / ci/final-gate"):
                continue
            final_gate_state = str(check.get("state", "")).upper()
            break
        if final_gate_state in success_states:
            return False
        if final_gate_state in failure_states:
            return True
        if final_gate_state:
            return True
        if any(
            isinstance(c, dict) and c.get("state", "").upper() in failure_states for c in checks
        ):
            return True
        return True
    except Exception:
        return False


def _get_copilot_review_count(pr_number: int) -> int | None:
    """指定 PR の AI レビュー数を返す。取得失敗時は None。"""
    return cast("int | None", _review_loop_state.get_ai_review_count(pr_number))


def _get_copilot_reviewer_count(pr_number: int) -> int | None:
    """requested_reviewers 内の Copilot レビュワー数を返す。"""
    return cast("int | None", _review_loop_state.get_copilot_reviewer_count(pr_number))


def _get_latest_copilot_review_status(
    pr_number: int,
    review_count: int | None = None,
) -> tuple[bool | None, str | None, str | None]:
    """最新コミットに AI レビューが届いたかを返す。"""
    return cast(
        "tuple[bool | None, str | None, str | None]",
        _review_loop_state.get_latest_ai_review_status(pr_number, review_count),
    )


def _count_unreplied_copilot_threads(pr_number: int) -> int | None:
    """未返信 AI レビュースレッド数を返す。"""
    return cast("int | None", _review_loop_state.count_unreplied_ai_threads(pr_number))


def main() -> None:
    # 対話端末（TTY）から直接実行された場合は即許可（EOF待ちのハング防止）
    if sys.stdin.isatty():
        print(json.dumps({"permissionDecision": "allow"}))
        return
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        print(json.dumps({"permissionDecision": "allow"}))
        return
    if not isinstance(payload, dict):
        print(json.dumps({"permissionDecision": "allow"}))
        return

    tool_name = payload.get("toolName", payload.get("tool_name", ""))
    is_task_complete = tool_name == "task_complete"
    tool_input = payload.get("toolInput", payload.get("tool_input", {}))
    if not isinstance(tool_input, dict):
        tool_input = {}
    is_release_manager = tool_name in ("runSubagent", "agent") and (
        tool_input.get("agentName") == "release-manager"
    )
    is_terminal_tool = tool_name in ("run_in_terminal", "execute")
    is_git_push = False
    is_reviewer_request = _is_review_request_tool_name(tool_name)

    # run_in_terminal / execute と専用 reviewer request tool を検査（Round 予算）
    if is_terminal_tool:
        command = tool_input.get("command", "")
        if isinstance(command, str):
            detection = detect_review_loop_actions(command)
            is_git_push = detection.is_git_push
            is_reviewer_request = is_reviewer_request or detection.is_reviewer_request

    if is_git_push or is_reviewer_request:
        pr_number, lookup_error = _get_open_pr_number_result()
        if lookup_error is not None:
            action = _describe_review_loop_action(is_git_push, is_reviewer_request)
            _allow(
                f"【レビュー状態 reminder / PR lookup エラー】{action} の Round 予算検証に"
                f"失敗しましたが、レビュー儀式ゲートの transient lookup 失敗は"
                f" fail-open とします。\nエラー: {lookup_error}"
            )
            return
        if pr_number is None:
            # PR がない場合は allow
            _allow()
            return
        review_count = _get_copilot_review_count(pr_number)
        if review_count is None:
            _allow(
                f"【レビュー状態 reminder / Round 予算検証失敗】PR #{pr_number} の"
                " AIレビュー数を取得できません。レビュー状態 lookup 失敗は"
                " fail-open とし、CI / ci/final-gate の実失敗だけを安全床として"
                "別途確認してください。"
            )
            return
        if review_count >= 3:
            action = _describe_review_loop_action(is_git_push, is_reviewer_request)
            _allow(
                f"【レビュー状態 reminder / Round 4 相当】PR #{pr_number} は既に"
                f" {review_count} 回の AIレビューを受けています。{action} は"
                "ブロックしませんが、非ブロッキング Must/Should は Backlog 化し、"
                "即時ブロッカーだけ fail-close で扱ってください。"
            )
            return

    if is_terminal_tool and not isinstance(tool_input.get("command", ""), str):
        print(json.dumps({"permissionDecision": "allow"}))
        return

    if not (is_task_complete or is_release_manager):
        print(json.dumps({"permissionDecision": "allow"}))
        return

    # P-010 fail-close: 全プラン完了認証の実行時例外を捕捉してブロック
    try:
        full_plan_reason = _full_plan_completion_reason(require_delivery_state=is_task_complete)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "permissionDecision": "deny",
                    "message": (
                        f"【全プラン完了認証エラー】全プラン完了認証モジュールの実行時エラー"
                        f" により、P-010 fail-close でブロックします。エラー: {exc}"
                    ),
                },
                ensure_ascii=False,
            )
        )
        return
    if full_plan_reason:
        print(
            json.dumps(
                {
                    "permissionDecision": "deny",
                    "message": (
                        f"【全プラン完了未認証】Nextなしだけでは完了できません。 {full_plan_reason}"
                    ),
                },
                ensure_ascii=False,
            )
        )
        return

    pr_number, lookup_error = _get_open_pr_number_result()
    if lookup_error is not None:
        _allow(
            "【レビュー状態 reminder / PR lookup エラー】task_complete / release-manager の"
            "レビュー状態確認に失敗しましたが、transient lookup 失敗は fail-open とします。\n"
            f"エラー: {lookup_error}"
        )
        return
    if pr_number is None:
        _allow()
        return

    if _has_ci_failure(pr_number):
        print(
            json.dumps(
                {
                    "permissionDecision": "deny",
                    "message": (
                        f"【CI失敗】PR #{pr_number} にCI失敗があります。"
                        " 修正してから再実行してください。"
                    ),
                }
            )
        )
        return

    reminders: list[str] = []

    review_count = _get_copilot_review_count(pr_number)
    if review_count is None:
        reminders.append("AIレビュー数を取得できない（レビュー状態 lookup 失敗は fail-open）")
        _allow(f"【レビュー状態 reminder】PR #{pr_number}: " + "; ".join(reminders))
        return
    if review_count > 3:
        reminders.append(
            f"AIレビューが {review_count} 回到達（Round 4 相当）。"
            "非ブロッキング指摘は Backlog 化してください"
        )

    latest_review_arrived, head_sha, latest_review_error = _get_latest_copilot_review_status(
        pr_number, review_count
    )
    review_required = latest_review_arrived is False
    if latest_review_error is not None:
        reminders.append(latest_review_error)
    elif review_required:
        trigger_hint = (
            "Round 1 を明示リクエスト、または fallback AIレビューを実行してください"
            if review_count == 0
            else "次のラウンドを明示リクエスト、または fallback AIレビューを実行してください"
        )
        short_sha = head_sha[:7] if head_sha else "unknown"
        reminders.append(f"最新コミット ({short_sha}) に対する AIレビューが未到着。{trigger_hint}")

    reviewer_count = _get_copilot_reviewer_count(pr_number)
    if reviewer_count is None:
        reminders.append("Copilot レビュワー情報を取得できない")
    elif review_required and reviewer_count == 0:
        reminders.append(
            "Copilot AI がレビュワー未設定。"
            "自動発火は行わない運用のため、手動レビューまたは fallback AIレビューを実行してください"
        )

    unreplied = _count_unreplied_copilot_threads(pr_number)
    if unreplied is None:
        reminders.append("未返信コメント数を取得できない（GraphQL エラー）")
    elif unreplied > 0:
        reminders.append(f"AIレビューコメントに {unreplied} 件の未返信スレッドがある")

    if reminders:
        _allow(
            f"【レビュー状態 reminder / レビューループ未完了】PR #{pr_number}: "
            + "; ".join(reminders)
            + ". CI / ci/final-gate の実失敗と full-plan safety だけをブロックします。"
        )
        return

    _allow()


if __name__ == "__main__":
    main()
