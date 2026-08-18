#!/usr/bin/env python3
"""PostToolUse hook: git push 後にレビューループの実施をリマインドする。"""

import importlib
import json
import os
import subprocess
import sys


def _find_executable(name: str) -> str:
    """コマンドの絶対パスを返す。見つからなければコマンド名そのまま返す。"""
    import shutil

    candidates = [f"/usr/local/bin/{name}", f"/usr/bin/{name}", f"/bin/{name}"]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    found = shutil.which(name)
    return found if found else name


_GH = _find_executable("gh")
_GIT = _find_executable("git")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_command_detection = importlib.import_module("scripts.hooks._command_detection")
detect_review_loop_actions = _command_detection.detect_review_loop_actions
_review_loop_state = importlib.import_module("scripts.hooks._review_loop_state")


def _run(cmd: list[str], timeout: int = 10) -> str | None:
    """コマンドを実行して stdout を返す。失敗時は None。"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_REPO_ROOT,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _check_token_status() -> str:
    """gh トークン状態を確認し、問題があれば警告文字列を返す。"""
    try:
        import importlib.util as _ilu
        import pathlib as _pl

        validator_path = _pl.Path(__file__).parent / "gh_token_validator.py"
        spec = _ilu.spec_from_file_location("gh_token_validator", validator_path)
        if spec and spec.loader:
            module = _ilu.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            result = module.check_gh_token()
            if result.status.value == "invalid":
                return (
                    "\n⚠️ 【トークン警告】gh CLI トークンが無効または期限切れです。\n"
                    f"  詳細: {result.message}\n"
                    f"  復旧方法: {result.recovery_hint}\n"
                    "  Copilot MCP ツールが 401 の場合は "
                    "VS Code で Copilot を再認証してください。\n"
                )
            if result.status.value == "rate_limited":
                return (
                    "\n⚠️ 【レート上限警告】GitHub API レート上限に達しています。\n"
                    f"  詳細: {result.message}\n"
                    "  Copilot Chat の週次レート上限の場合は "
                    "VS Code で Copilot を再認証してください。\n"
                )
            if result.status.value == "unknown":
                return (
                    "\n⚠️ 【トークン状態不明】gh CLI トークン状態を確認できません。\n"
                    f"  詳細: {result.message}\n"
                    "  transient lookup 失敗は fail-open 方針により"
                    "Hook ではリマインドに留めます。\n"
                )
    except Exception as exc:  # noqa: BLE001
        return (
            "\n⚠️ 【トークン状態不明】gh CLI トークン検証に失敗しました。\n"
            f"  詳細: {exc}\n"
            "  transient lookup 失敗は fail-open 方針により Hook ではリマインドに留めます。\n"
        )
    return ""


def main() -> None:
    if sys.stdin.isatty():
        json.dump({}, sys.stdout)
        return

    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        json.dump({}, sys.stdout)
        return

    tool_name = input_data.get("toolName", input_data.get("tool_name", ""))
    tool_input = input_data.get("toolInput", input_data.get("tool_input", {}))
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool_name not in {"run_in_terminal", "execute"}:
        json.dump({}, sys.stdout)
        return

    raw_command = tool_input.get("command", "")
    command = raw_command if isinstance(raw_command, str) else ""
    detection = detect_review_loop_actions(command)
    if not detection.is_git_push:
        json.dump({}, sys.stdout)
        return

    branch = _run([_GIT, "branch", "--show-current"])
    if not branch or branch in ("main", "master", "develop"):
        json.dump({}, sys.stdout)
        return

    pr_json = _run(
        [
            _GH,
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,state,isDraft",
        ]
    )
    pr_list_failed = pr_json is None
    pr_list_parse_failed = False
    if pr_json is not None:
        try:
            pr_list = json.loads(pr_json)
            if not pr_list or pr_list[0].get("state") != "OPEN":
                json.dump({}, sys.stdout)
                return
        except json.JSONDecodeError:
            pr_list_failed = True
            pr_list_parse_failed = True

    pr_number = "?"
    is_draft = False
    if pr_json is not None:
        try:
            parsed = json.loads(pr_json)
            if isinstance(parsed, list) and parsed:
                first = parsed[0]
                if isinstance(first, dict):
                    pr_number = str(first.get("number", "?"))
                    is_draft = bool(first.get("isDraft", False))
        except json.JSONDecodeError:
            pass

    review_count = 0
    if pr_number != "?":
        review_count = _review_loop_state.get_ai_review_count(int(pr_number)) or 0

    token_warning = _check_token_status()

    # Round 4 相当のリマインドメッセージ（review_count >= 3）
    if review_count >= 3:
        context = (
            "【レビュー状態 reminder / Round 4 相当】\n"
            f"対象PR: #{pr_number} / 現在の AIレビュー数: {review_count}"
            f"（Round {review_count + 1} 相当）\n"
            + (
                "PR 一覧取得に失敗しましたが、transient lookup は fail-open 方針により"
                "リマインドに留めます。\n"
                if pr_list_failed
                else ""
            )
            + (
                "gh pr list の JSON パースに失敗しました。PR 番号を確定できないため "
                "手動確認してください。\n"
                if pr_list_parse_failed
                else ""
            )
            + token_warning
            + "\nレビューループは最大 3 ラウンドを目安に扱います。"
            "Hook は Round 4 相当の push / review request をブロックしません。\n"
            "\n【対応方法】\n"
            "- 非ブロッキング Must/Should: Backlog に記録し、"
            "PR コメントに Backlog ID と残リスクを返信\n"
            "- 即時ブロッカー（P-001/P-002/P-003、秘密情報、重大操作の安全、"
            "CI failure、データ破壊等）: "
            "fail-close で停止し、人間にエスカレーション\n"
            "\n詳細: .github/instructions/review-loop.instructions.md"
        )
    else:
        trigger_step = (
            "3. 【レビュー発火】Copilot レビューまたは Codex / Claude fallback を"
            "明示的にリクエストする\n"
            "   例: gh api repos/{owner}/{repo}/pulls/<PR番号>/requested_reviewers "
            "-X POST -f 'reviewers[]=copilot-pull-request-reviewer[bot]'\n"
            "4. AIレビュー到着確認（最大20回。同期 sleep ループは禁止。"
            "初回+5回ごとにレビュワー再確認）\n"
        )
        if is_draft:
            trigger_step = (
                "3. 【Draft 検出】PR が Draft のためレビューは発火しません\n"
                "   先に Ready for review に変更し、その後レビューを明示リクエストする\n"
                "4. AIレビュー到着確認（最大20回。同期 sleep ループは禁止）\n"
            )

        context = (
            "【レビュー状態 reminder / git push 検出】以下を確認してください:\n"
            f"対象PR: #{pr_number} / draft={str(is_draft).lower()} "
            f"（現在のAIレビュー数: {review_count} / Round {review_count + 1} 相当）\n"
            + (
                "PR 一覧取得に失敗しましたが、transient lookup は fail-open 方針により"
                "リマインドに留めます。\n"
                if pr_list_failed
                else ""
            )
            + (
                "gh pr list の JSON パースに失敗しました。PR 番号を確定できないため "
                "手動確認してください。\n"
                if pr_list_parse_failed
                else ""
            )
            + token_warning
            + "1. CI 通過確認（gh pr checks で全チェック success。同期 sleep ループは禁止）\n"
            + trigger_step
            + "5. レビューコメントを取得・分類し Must/Should を修正\n"
            "6. 【必須】各コメントに GitHub 上で返信する\n"
            "7. CI 実行・検証 → コミット・push\n"
            "8. 【Round 上限チェック】レビューループは最大 3 ラウンド。"
            "Round 3 到達後は非ブロッキング指摘を Backlog 化\n"
            "9. 未解決かつ未返信の AIレビュースレッドが0件 → ループ終了\n"
            "10. release-manager は未解決・未返信スレッド0件確認後にのみ呼出可\n"
            "11. task_complete 前に CI / ci/final-gate の実失敗がないことを確認\n"
            "■ 参照: .github/instructions/review-loop.instructions.md"
        )

    json.dump(
        {
            "decision": "allow",
            "reason": context,
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": context,
            },
        },
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
