#!/usr/bin/env python3
"""Stop hook: CI 失敗と全プラン安全床をブロックする。

Claude Code がセッションを終了しようとした際に、
現在のブランチにオープンな PR があり実 CI 失敗がある場合、または
全プラン完了認証の安全床が未達の場合だけ終了をブロックする。
レビュー未到着・未返信・Round 予算・review state lookup 失敗は N-371 / P-064 により
非ブロッキングのリマインドへ降格する。

これは Copilot の stop_review_guard.py の Claude Code 移植版。
出力形式を Claude Code Stop hook 形式（{"decision": "block", "reason": "..."}）に変更。

stop_hook_active について:
  Claude Code が Stop hook でブロック→エージェント続行→再度 Stop となった場合、
  2回目の入力に stop_hook_active=true が付与される。
これにより無限ループを防止する（2回目は必ず終了を許可する）。
"""

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# 同一ディレクトリの _github_api を import するため sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _block_history import (  # noqa: E402
    build_escalation_reason,
    compute_fingerprint,
    record_block_and_check_repeat,
)
from _github_api import (  # noqa: E402
    get_pr_check_runs,
    gh_available,
    gh_subprocess_env,
    has_claude_code_review_marker,
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
_TRUSTED_MARKER_LOGINS: frozenset[str] = _AI_REVIEW_LOGINS | frozenset({"github-actions[bot]"})
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from scripts.hooks import _review_loop_state  # noqa: E402


def _trusted_marker_logins(current_user: str | None = None) -> frozenset[str]:
    if current_user:
        return _TRUSTED_MARKER_LOGINS | frozenset({current_user})
    return _TRUSTED_MARKER_LOGINS


def _is_ai_review(login: object, body: object, current_user: str | None = None) -> bool:
    if isinstance(login, str) and login in _AI_REVIEW_LOGINS:
        return True
    if (
        isinstance(login, str)
        and isinstance(body, str)
        and login in _trusted_marker_logins(current_user)
    ):
        return any(marker in body for marker in _AI_REVIEW_MARKERS)
    return False


def run(
    cmd: list[str],
    timeout: int = 15,
    *,
    allow_nonzero: bool = False,
) -> str | None:
    """コマンドを実行して stdout を返す。失敗時は None。

    gh 未認証環境でも gh 呼び出しが認証されるよう gh_subprocess_env() の env
    （git credential helper 由来 token を必要時のみ注入）を渡す。
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


def fetch_reviews(pr_number: str) -> list[dict[str, object]] | None:
    reviews: list[dict[str, object]] = []
    for page in range(1, 101):
        output = run(
            [
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews?per_page=100&page={page}",
            ],
            timeout=20,
        )
        if output is None or output == "":
            return None
        try:
            page_reviews = json.loads(output)
        except json.JSONDecodeError:
            return None
        if not isinstance(page_reviews, list) or not all(
            isinstance(review, dict) for review in page_reviews
        ):
            return None
        reviews.extend(page_reviews)
        if len(page_reviews) < 100:
            return reviews
    return None


def check_ci(pr_number: str) -> list[str]:
    """CI ステータスを確認する。blocking failure だけを返す。"""
    issues: list[str] = []
    checks_json = run(
        ["gh", "pr", "checks", pr_number, "--json", "name,state"],
        allow_nonzero=True,
    )
    if not checks_json:
        return issues
    try:
        checks = json.loads(checks_json)
    except json.JSONDecodeError:
        return issues
    if not isinstance(checks, list):
        return issues

    return _ci_issues_from_checks(checks)


def _ci_issues_from_checks(checks: list[dict[str, object]]) -> list[str]:
    """gh/API 由来の check 一覧から blocking CI failure を抽出する。"""
    issues: list[str] = []
    # scripts/hooks/ci_checks.py の success_conclusions と揃える。SKIPPED / NEUTRAL は
    # 条件付き job（例: docs-site deploy は main のみ実行）の正常な終端状態であり blocking ではない
    success_states = {"SUCCESS", "SKIPPED", "NEUTRAL"}
    failure_states = {"ACTION_REQUIRED", "CANCELLED", "ERROR", "FAILURE", "STALE", "TIMED_OUT"}

    for c in checks:
        if not isinstance(c, dict):
            return issues
        name = str(c.get("name") or "?")
        raw_state = c.get("state")
        state = str(raw_state).strip().upper() if raw_state is not None else ""
        if not state:
            issues.append(f"CI '{name}' の状態が未定義")
        elif state in failure_states:
            issues.append(f"CI '{name}' が {state}")
        elif state in success_states:
            continue
        else:
            issues.append(f"CI '{name}' が {state}")
    return issues


def get_copilot_latest_review_info(
    pr_number: str,
    head_sha: str | None = None,
) -> tuple[str | None, str | None]:
    """現在headと互換な Copilot / Claude の最新レビュー情報を返す。"""
    reviews = fetch_reviews(pr_number)
    if reviews is None:
        return None, None
    current_user = run(["gh", "api", "user", "--jq", ".login"], timeout=10)
    latest_dt: datetime | None = None
    latest_date = ""
    latest_id = ""
    for review in reviews:
        user = review.get("user") or {}
        login = user.get("login") if isinstance(user, dict) else None
        if not _is_ai_review(login, review.get("body"), current_user):
            continue
        if head_sha and not _review_loop_state._review_matches_head(review, head_sha):
            continue
        review_date = review.get("submitted_at")
        if not isinstance(review_date, str) or not review_date:
            continue
        try:
            dt = datetime.fromisoformat(review_date.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest_date = review_date
            latest_id = str(review.get("id") or "")
    if not latest_id:
        return None, None
    return latest_date, latest_id


def count_copilot_reviews(pr_number: str) -> int | None:
    """異なる canonical head に対する Copilot / Claude review 数を返す。"""
    return _review_loop_state.get_ai_review_count(int(pr_number))


def count_incomplete_ai_review_threads(pr_number: str) -> int | None:
    """未解決または未返信の active AI review thread 数を返す。"""
    return _review_loop_state.count_incomplete_ai_review_threads(int(pr_number))


def _block(branch: str, pr_number: str, issues: list[str]) -> None:
    """セッション終了をブロックする（Claude Code Stop hook 形式）。"""
    reason = (
        f"【レビューループ未完了】PR #{pr_number} (ブランチ: {branch}): "
        + "; ".join(issues)
        + ".\n■ セッション終了前に必ず実施:\n"
        "1. CI 全 pass を確認\n"
        "2. Copilot レビューまたは Claude fallback を明示リクエスト\n"
        "3. AIレビュー到着を待機\n"
        "4. レビューコメントを plan.md の AC と照合して修正\n"
        "5. 【必須】各コメントに GitHub 上で返信する\n"
        "6. コミット・ push → 次ラウンドを明示リクエスト\n"
        "7. Round 3 を既定の収束点とする。"
        "非ブロッキング指摘だけでは延長せず、適格理由、実質修正、延長証跡が"
        "ある場合だけ terminal anchor 前に限って Round 4 以降へ進む。"
        "停止条件: 延長不適格・進捗ゼロ・同一指摘3回繰り返し・"
        "再トリガー3回超過・ポリシー違反・認証不能\n"
        "■ 参照: .github/instructions/review-loop.instructions.md"
    )

    try:
        fingerprint = compute_fingerprint(pr_number, other_reasons=issues)
        repeats, exceeded = record_block_and_check_repeat(pr_number, fingerprint)
        if exceeded:
            reason = build_escalation_reason(
                pr_number=pr_number,
                fingerprint=fingerprint,
                repeats=repeats,
                other_reasons=issues,
            )
    except Exception:
        # 履歴ファイル破損や書き込み失敗でも Stop hook 自体は fail-close で block する。
        pass
    # Claude Code Stop hook 形式: {"decision": "block", "reason": "..."}
    json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)


def _allow(message: str | None = None) -> None:
    """Claude Code Stop hook の allow 応答を出力する。"""
    payload: dict[str, object] = {}
    if message:
        payload["hookSpecificOutput"] = {
            "hookEventName": "Stop",
            "additionalContext": message,
        }
    json.dump(payload, sys.stdout, ensure_ascii=False)


def _full_plan_load_failure_reason(message: str) -> Callable[[], str]:
    flag_path = _REPO_ROOT / ".github" / "full-plan-execution.flag"
    return lambda: message if flag_path.exists() else ""


def _load_full_plan_completion_block_reason() -> Callable[[], str]:
    module_path = _REPO_ROOT / "scripts" / "hooks" / "full_plan_completion.py"
    spec = importlib.util.spec_from_file_location("full_plan_completion_for_claude", module_path)
    if spec is None or spec.loader is None:
        return _full_plan_load_failure_reason("全プラン完了認証モジュールを読み込めません")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        return _full_plan_load_failure_reason(
            f"全プラン完了認証モジュールの実行に失敗しました: {exc}"
        )
    return module.full_plan_completion_block_reason


full_plan_completion_block_reason = _load_full_plan_completion_block_reason()


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        json.dump({}, sys.stdout)
        return

    # 無限ループ防止: stop_hook_active が true なら終了を許可
    if input_data.get("stop_hook_active"):
        _allow()
        return

    try:
        full_plan_reason = full_plan_completion_block_reason()
    except Exception as exc:
        json.dump(
            {
                "decision": "block",
                "reason": (
                    "【全プラン完了認証エラー】全プラン完了認証モジュールの実行時エラー"
                    f" により、P-010 fail-close でブロックします。エラー: {exc}"
                ),
            },
            sys.stdout,
            ensure_ascii=False,
        )
        return
    if full_plan_reason:
        json.dump(
            {
                "decision": "block",
                "reason": f"【全プラン完了未認証】{full_plan_reason}",
            },
            sys.stdout,
            ensure_ascii=False,
        )
        return

    # main/master ブランチなら何もしない
    branch = run(["git", "branch", "--show-current"])
    if not branch or branch in ("main", "master"):
        _allow()
        return

    # Claude Code 公式リモート環境（Web SDK / claude.ai/code 等）では
    # gh CLI / GITHUB_TOKEN が無く、また MCP github ツールが利用可能。
    # PR イベントは subscribe_pr_activity による webhook 経由で受信できるため、
    # シェルポーリング前提のレビューループは適用できない。
    # → fail-open（終了許可）し、エージェントには webhook ベースの運用を案内する。
    if is_claude_code_remote():
        _allow(
            "⚠️ Stop hook (Claude Code Remote 環境): "
            f"ブランチ {branch} で作業中。"
            "この環境ではシェルベースのレビューループ自動チェックは"
            "適用されません。"
            "GitHub 操作は MCP github ツール（mcp__github__*）を使用し、"
            "PR が存在する場合は mcp__github__subscribe_pr_activity を呼び、"
            "<github-webhook-activity> イベントで CI 結果や"
            "レビューコメントを受信してください。"
            "セッション終了前に PR の状態を MCP ツールで"
            "確認することを推奨します。"
        )
        return

    # 通常ローカル環境では、認証情報が無い場合に PR 状態を確認できない。
    # N-371 以降は lookup 失敗をリマインドに留める。
    if not has_credentials():
        _allow(
            f"【N-371 reminder / 認証情報なし】ブランチ {branch}: "
            "gh CLI 未インストール かつ GITHUB_TOKEN/GH_TOKEN 未設定のため、"
            "OPEN PR の有無と CI / レビュー状態を確認できません。"
            "transient lookup 失敗は fail-open とします。"
        )
        return

    # gh CLI 不在 + token 利用可（token-only 環境）の場合:
    # OPEN PR の存在確認は API フォールバックで可能だが、CI / レビュー詳細の
    # 自動確認は gh 依存で実施できないため、リマインドに留める。
    if not gh_available():
        pr_list_token_only = list_open_prs_for_branch(branch)
        if pr_list_token_only is None:
            _allow(
                f"【N-371 reminder / PR lookup 失敗】ブランチ {branch}: "
                "OPEN な PR 一覧を取得できない（gh CLI 不在かつ API フォールバックも失敗。"
                "token 失効・API エラー・ネットワーク障害の可能性）。"
                "transient lookup 失敗は fail-open とします。"
            )
            return
        if not pr_list_token_only:
            _allow()
            return
        first_pr_token_only = pr_list_token_only[0]
        pr_num_str = (
            str(first_pr_token_only["number"])
            if isinstance(first_pr_token_only, dict) and "number" in first_pr_token_only
            else "?"
        )
        if pr_num_str.isdigit():
            check_runs = get_pr_check_runs(int(pr_num_str))
            if check_runs is None:
                _allow(
                    f"【N-371 reminder / token-only】PR #{pr_num_str}: "
                    "gh CLI 未インストールのため CI / レビュー詳細を確認できません。"
                    "transient lookup 失敗は fail-open とします。"
                )
                return
            ci_issues = _ci_issues_from_checks(check_runs)
            if ci_issues:
                _block(branch, pr_num_str, ci_issues)
                return
        else:
            _block(
                branch,
                pr_num_str,
                ["gh CLI 未インストールの token-only 環境で PR 番号を確認できません"],
            )
            return
        _allow(
            f"【N-371 reminder / token-only】PR #{pr_num_str}: OPEN PR は取得できましたが、"
            "gh CLI 未インストールのためレビュー詳細を確認できません。"
            "CI failure は GitHub API で確認済みです。必要に応じて MCP / GitHub UI で"
            "レビュー状態を確認してください。"
        )
        return

    # gh 利用可: フル検査（gh → API フォールバック）
    pr_list = list_open_prs_for_branch(branch)
    if pr_list is None:
        _allow(
            f"【N-371 reminder / PR lookup 失敗】ブランチ {branch}: "
            "OPEN な PR 一覧を取得できない（gh CLI の実行/認証失敗［未ログイン等］、"
            "token 未設定による API フォールバック不可、API エラー、またはネットワーク障害）。"
            "transient lookup 失敗は fail-open とします。"
        )
        return

    if not isinstance(pr_list, list):
        _allow(
            f"【N-371 reminder / PR lookup 失敗】ブランチ {branch}: "
            "OPEN な PR 一覧の形式が不正です。transient lookup 失敗は fail-open とします。"
        )
        return

    if not pr_list:
        # OPEN な PR が存在しない → 終了を許可
        _allow()
        return

    first_pr = pr_list[0]
    if not isinstance(first_pr, dict) or "number" not in first_pr:
        _allow(
            f"【N-371 reminder / PR lookup 失敗】ブランチ {branch}: "
            "OPEN な PR 一覧の先頭要素に number が存在しません。"
            "transient lookup 失敗は fail-open とします。"
        )
        return

    pr_number = str(first_pr["number"])
    reminders: list[str] = []

    # チェック -1: Draft 状態
    is_draft = run(["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}", "--jq", ".draft"])
    if is_draft is None:
        reminders.append("PR の Draft/Ready 状態を取得できない")
    elif is_draft == "true":
        reminders.append(
            "PR が Draft 状態。AIレビューは発火しないため、"
            "Ready for review へ変更してから Round 1 または "
            "fallback AIレビューを明示リクエストしてください"
        )

    review_count = count_copilot_reviews(pr_number)
    if review_count is None:
        reminders.append("AIレビュー数を取得できない（review state lookup 失敗は fail-open）")
    elif review_count > 3:
        reminders.append(
            f"AIレビューが {review_count} 回到達。"
            "延長ラウンドの適格理由、実質修正、修正前後 head、検証結果を確認してください。"
            "非ブロッキング指摘だけなら Backlog 化し、延長しません"
        )

    # チェック 1: CI ステータス
    ci_issues = check_ci(pr_number)
    if ci_issues:
        _block(branch, pr_number, ci_issues)
        return

    # チェック 2: Copilot レビュワーが requested_reviewers にいるか
    jq_reviewer = (
        "[.requested_reviewers[]?"
        " | select("
        '.login == "copilot-pull-request-reviewer[bot]"'
        ' or .login == "copilot-pull-request-reviewer"'
        ' or .login == "Copilot"'
        ")] | length"
    )
    reviewer_count = run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}", "--jq", jq_reviewer]
    )
    if reviewer_count is None:
        reminders.append("Copilot レビュワー情報を取得できない")

    # チェック 3: 最新コミットに対するレビューが到着しているか
    _needs_review = False
    head_sha = run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}", "--jq", ".head.sha"]
    )
    if not head_sha:
        reminders.append("レビュー状態を取得できない（head SHA 不明）")
    else:
        canonical_head = (
            _review_loop_state._canonical_reviewed_head(head_sha, _REPO_ROOT) or head_sha
        )
        commit_date_str = run(
            [
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/commits/{canonical_head}",
                "--jq",
                ".commit.committer.date",
            ]
        )
        if not commit_date_str:
            reminders.append("レビュー状態を取得できない（コミット日時不明）")
        else:
            latest_review_date, _latest_review_id = get_copilot_latest_review_info(
                pr_number,
                head_sha,
            )
            _review_after_commit = False
            if latest_review_date and latest_review_date not in ("", "null"):
                try:
                    review_dt = datetime.fromisoformat(latest_review_date.replace("Z", "+00:00"))
                    commit_dt = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
                    # identity 照合済み review の時刻は review-only R ではなく
                    # canonical E の commit 時刻と比較する。
                    _review_after_commit = review_dt.astimezone(UTC) >= commit_dt.astimezone(UTC)
                except ValueError:
                    pass
            if not _review_after_commit:
                # B-366 fallback: Claude の AI レビュー marker があれば OK
                # 詳細は plan.md の B-366 と _github_api.has_claude_code_review_marker 参照
                has_fallback_marker = (
                    has_claude_code_review_marker(int(pr_number), commit_date_str)
                    if commit_date_str
                    else False
                )
                if has_fallback_marker:
                    _review_after_commit = True
                else:
                    _needs_review = True
                    already_mentioned = any(
                        "AIレビュー" in issue or "Copilot AI レビュー" in issue
                        for issue in reminders
                    )
                    if not already_mentioned:
                        if review_count == 0:
                            trigger_hint = (
                                "Round 1 または fallback AIレビューを明示リクエストしてください"
                            )
                        else:
                            trigger_hint = (
                                "次のラウンドを明示リクエストするか、"
                                "Claude fallback AIレビューを投稿してください"
                            )
                        reminders.append(
                            f"最新コミット ({head_sha[:7]}) に対する AIレビューが未到着。"
                            f"{trigger_hint}"
                        )

    # チェック 2b: reviewer_count==0 かつ新レビューが必要な場合の明示発火案内
    if _needs_review and reviewer_count == "0":
        reminders.append(
            "Copilot AI がレビュワー未設定。"
            "自動発火は行わない運用のため、手動レビューまたは fallback AIレビューを実行してください"
        )

    # チェック 4: 未解決または未返信の active thread
    incomplete = count_incomplete_ai_review_threads(pr_number)
    if incomplete is None:
        reminders.append("未解決／未返信 thread 数を取得できない（GraphQL エラー）")
    elif incomplete > 0:
        reminders.append(f"AIレビューコメントに {incomplete} 件の未解決または未返信 thread がある")

    if reminders:
        _allow(
            f"【N-371 reminder / レビューループ未完了】PR #{pr_number}: "
            + "; ".join(reminders)
            + ". Claude Stop hook は実 CI 失敗と full-plan safety だけをブロックします。"
        )
    else:
        _allow()


if __name__ == "__main__":
    main()
