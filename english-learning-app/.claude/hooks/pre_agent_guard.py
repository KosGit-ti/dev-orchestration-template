#!/usr/bin/env python3
"""PreToolUse hook: release judgement 前に CI と全プラン安全床を検査する。

Claude Code の Agent ツール呼び出しをインターセプトし、
`release-judgement` skill または旧 `release-manager` agent を呼ぶ場合に
ci/final-gate 未完了または全プラン安全床未達なら実行を拒否する。
レビュー未到着・未返信・Round 予算・review state lookup 失敗は N-371 / P-064 により
非ブロッキングのリマインドへ降格する。

これは Copilot の pre_task_complete_guard.py の Claude Code 移植版。
task_complete が存在しない Claude Code では release judgement の呼び出しを
完了ゲートとして扱う。

transient な PR / review 状態取得失敗は fail-open とする。
"""

import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _github_api import gh_subprocess_env, has_claude_code_review_marker  # noqa: E402

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


def check_ci(pr_number: str) -> str | None:
    """CI ステータスを確認する。戻り値は release judgement 前の blocking failure のみ。

    戻り値: None=取得失敗, ""=blocking failure なし, 文字列=実 CI 失敗
    """
    checks_json = run(
        ["gh", "pr", "checks", pr_number, "--json", "name,state"],
        allow_nonzero=True,
    )
    if not checks_json:
        return None
    try:
        checks = json.loads(checks_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(checks, list):
        return None

    failure_states = {"FAILURE", "CANCELLED", "TIMED_OUT", "ERROR"}
    pending_states = {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}
    final_gate_state = ""

    for c in checks:
        if not isinstance(c, dict):
            return "CI 状態の形式が不正"
        name = str(c.get("name") or "?")
        raw_state = c.get("state")
        state = str(raw_state).strip().upper() if raw_state is not None else ""
        if not state:
            return f"CI '{name}' の状態が未定義"
        if name == "ci/final-gate" or name.endswith(" / ci/final-gate"):
            final_gate_state = state
            break
    if final_gate_state == "SUCCESS":
        return ""
    if final_gate_state in failure_states:
        return f"CI 'ci/final-gate' が {final_gate_state}"
    if final_gate_state in pending_states:
        return f"CI 'ci/final-gate' が未完了（{final_gate_state}）"
    if final_gate_state:
        return f"CI 'ci/final-gate' が未完了（{final_gate_state}）"

    for c in checks:
        if not isinstance(c, dict):
            return "CI 状態の形式が不正"
        name = str(c.get("name") or "?")
        state = str(c.get("state") or "").strip().upper()
        if state in failure_states:
            return f"CI '{name}' が {state}"
    if checks:
        return "CI 'ci/final-gate' が見つからない"
    return "CI 'ci/final-gate' が未作成"


def _full_plan_load_failure_reason(message: str) -> Callable[..., str]:
    flag_path = _REPO_ROOT / ".github" / "full-plan-execution.flag"
    return lambda *args, **kwargs: message if flag_path.exists() else ""


def _load_full_plan_completion_block_reason() -> Callable[..., str]:
    module_path = _REPO_ROOT / "scripts" / "hooks" / "full_plan_completion.py"
    spec = importlib.util.spec_from_file_location(
        "full_plan_completion_for_claude_pre_agent",
        module_path,
    )
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
    return cast("Callable[..., str]", module.full_plan_completion_block_reason)


full_plan_completion_block_reason = _load_full_plan_completion_block_reason()


def _full_plan_completion_reason(
    *,
    require_delivery_state: bool,
    judgement_head_ref: str | None = None,
) -> str | None:
    """旧シグネチャのテスト差し替えにも耐えて完了認証を呼ぶ。

    judgement_head_ref は対象 PR を明示解決できた場合のみ渡す。差し替え先が
    judgement_head_ref 引数を持たない旧シグネチャ（例: require_delivery_state
    だけの stub）であっても、TypeError の内容から対象引数を識別しながら
    judgement_head_ref → require_delivery_state → 引数なし、の順に段階的へ
    フォールバックする（Round 2 指摘・PR #1309: judgement_head_ref 分岐が
    require_delivery_state 非対応の旧シグネチャに遭遇すると re-raise して
    fail-close していた不備を是正）。
    """
    if judgement_head_ref is not None:
        try:
            return full_plan_completion_block_reason(
                require_delivery_state=require_delivery_state,
                judgement_head_ref=judgement_head_ref,
            )
        except TypeError as exc:
            # 差し替え先が judgement_head_ref を持たない旧シグネチャの場合、CPython は
            # 呼び出しで先に渡した kwarg（require_delivery_state）を「unexpected」として
            # 報告することがある（引数を一切持たない stub 等）。judgement_head_ref・
            # require_delivery_state のどちらの名前も現れない TypeError だけを、
            # 対象引数と無関係な例外として re-raise する。
            if "judgement_head_ref" not in str(exc) and "require_delivery_state" not in str(exc):
                raise
    try:
        return full_plan_completion_block_reason(require_delivery_state=require_delivery_state)
    except TypeError as exc:
        if "require_delivery_state" not in str(exc):
            raise
        return full_plan_completion_block_reason()


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


def _block(branch: str, pr_number: str, reasons: list[str]) -> None:
    """release judgement の呼び出しをブロックする（Claude Code 形式）。"""
    reason = (
        f"【Agent 実行ブロック】PR #{pr_number} "
        f"(ブランチ: {branch}) に未完了事項があります: " + "; ".join(reasons) + "。\n"
        "■ release-judgement skill を実行する前に必ず実施:\n"
        "1. CI 全 pass を確認 (gh pr checks)\n"
        "2. Copilot レビューまたは Claude fallback を明示リクエスト\n"
        "3. レビュー到着確認（同期 sleep ループは禁止。短い状態確認を最大20回相当）\n"
        "4. レビューコメント取得・分類 → plan.md の AC と照合 → Must/Should 修正\n"
        "5. 【必須】各コメントに GitHub 上で返信する\n"
        "6. CI 実行・検証 → コミット・ push\n"
        "7. 次ラウンドを明示リクエストして再レビュー\n"
        "8. Round 3 を既定の収束点とする。"
        "非ブロッキング指摘だけでは延長せず、適格理由、実質修正、延長証跡が"
        "ある場合だけ terminal anchor 前に限って Round 4 以降へ進む。"
        "停止条件: 延長不適格・進捗ゼロ・同一指摘3回繰り返し・"
        "再トリガー3回超過・ポリシー違反・認証不能\n"
        "■ 参照: .github/instructions/review-loop.instructions.md"
    )
    json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)


def _allow(message: str | None = None) -> None:
    """Claude Code PreToolUse の allow 応答を出力する。"""
    payload: dict[str, object] = {"decision": "allow"}
    if message:
        payload["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    json.dump(payload, sys.stdout, ensure_ascii=False)


def _is_release_judgement_invocation(tool_name: str, tool_input: object) -> bool:
    """現行 skill と旧 agent の release 判定呼び出しを認識する。"""
    if not isinstance(tool_input, dict):
        return False
    normalized_tool = tool_name.strip().lower()
    values = [
        str(tool_input.get(key, "")).strip().lower()
        for key in ("skill", "name", "description", "prompt", "subagent_type", "agentName")
    ]
    if normalized_tool == "skill":
        return any(value in {"release-judgement", "release judgement"} for value in values)
    if normalized_tool != "agent":
        return False
    return any(
        marker in value
        for value in values
        for marker in (
            "release-judgement",
            "release judgement",
            "release-manager",
            "release manager",
        )
    )


# 対象 PR 明示解決（Issue #1304）: worktree ベースで進める隊列外 PR は現在ブランチの
# OPEN PR 解決に構造的に乗らない。判定呼び出しの tool_input に対象 PR が明示されて
# いれば、それを最初の一致として採用する。抽出できない場合は None を返し、呼出元は
# 従来どおり現在ブランチの OPEN PR 解決へフォールバックする（挙動不変・fail-close 維持）。
_TARGET_PR_NUMBER_PATTERN = re.compile(
    r"--pr[ \t]+(?P<flag>\d+)" r"|\bpr=(?P<eq>\d+)" r"|\bPR[ \t]*#(?P<hash>\d+)",
    re.IGNORECASE,
)


def _iter_tool_input_values(value: object) -> Iterator[str]:
    """tool_input 内の文字列 value を再帰的に列挙する（対象 PR 抽出用）。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_tool_input_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_tool_input_values(item)


def resolve_target_pr_number(tool_input: object) -> str | None:
    """判定呼び出しの tool_input から対象 PR 番号を明示解決する。

    tool_input（ネストした dict/list を含む）の全 value を走査し、次のいずれかの
    表記のうち最初に現れたもの（大小文字は問わない）を対象 PR 番号として返す。

    - ``--pr <N>``
    - ``pr=<N>``
    - ``PR #<N>``

    抽出できない場合（該当表記が無い、または数値が続かない）は None を返す。
    """
    if not isinstance(tool_input, dict):
        return None
    combined = "\n".join(_iter_tool_input_values(tool_input))
    match = _TARGET_PR_NUMBER_PATTERN.search(combined)
    if match is None:
        return None
    return match.group("flag") or match.group("eq") or match.group("hash")


_PR_NUMBER_ONLY_PATTERN = re.compile(r"[0-9]+")


def resolve_target_pr_head(pr_number: str) -> tuple[str | None, str | None]:
    """明示解決した対象 PR の head を GitHub の PR ref 経由で fetch し、head SHA を返す。

    headRefName（PR の branch 名）は使わない。fork 由来 PR では headRefName が
    fork 側のブランチであり origin に存在しないため fetch が必ず失敗するうえ、
    `-` 始まり等の任意文字列を `git fetch` の引数へそのまま渡すことになり
    option injection の懸念もある（Round 1 指摘）。代わりに `refs/pull/<PR番号>/head`
    という GitHub 標準の固定形式参照を使う。これは same-repo PR・fork PR の
    双方に存在し、埋め込む値は `resolve_target_pr_number` が正規表現 `\\d+` で
    抽出した数字のみである（本関数自身も呼出し安全のため再検査する）ため、
    生成される引数が `-` から始まることは無い。

    fetch 成功後、`gh api` が返した head SHA が実際にローカルへ取得できたかを
    `git cat-file -e` で確認してから返す（fetch 自体の成功と、狙った SHA が
    実在することは別問題であるため）。番号形式不正・API 取得失敗・fetch 失敗・
    SHA 実在確認失敗のいずれかで ``(None, 失敗理由)`` を返す
    （P-010 fail-close。呼出元は判定呼び出しを拒否する）。
    """
    if _PR_NUMBER_ONLY_PATTERN.fullmatch(pr_number) is None:
        return None, f"対象 PR 番号の形式が不正です（数字のみを許可）: {pr_number!r}"
    head_sha = run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}", "--jq", ".head.sha"]
    )
    if not head_sha:
        return None, f"対象 PR #{pr_number} の head SHA を取得できません"
    pr_ref = f"refs/pull/{pr_number}/head"
    fetch_output = run(["git", "fetch", "origin", pr_ref], timeout=45)
    if fetch_output is None:
        return None, f"対象 PR #{pr_number} の head（{pr_ref}）を fetch できません"
    verified = run(["git", "cat-file", "-e", f"{head_sha}^{{commit}}"])
    if verified is None:
        return (
            None,
            f"対象 PR #{pr_number} の head SHA（{head_sha}）を fetch 後も確認できません",
        )
    return head_sha, None


def _current_pr_flag_note(explicit_pr_number: str) -> str:
    """明示解決した対象 PR と全プラン実行フラグの current_pr が不一致なら注記する。

    安全床自体は全プラン安全床側で対象 PR の head を使って常に評価するため
    （弱化しない）、本関数は非ブロッキングの情報提供のみを担う。フラグが
    読めない・current_pr が未記録の場合は注記なし（空文字）とする。
    """
    flag_path = _REPO_ROOT / ".github" / "full-plan-execution.flag"
    try:
        data = json.loads(flag_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    current_pr = data.get("current_pr")
    if current_pr is None:
        return ""
    if str(current_pr) == explicit_pr_number:
        return ""
    return (
        f"【対象 PR 不一致】明示解決した判定対象は PR #{explicit_pr_number} ですが、"
        f"全プラン実行フラグの current_pr は #{current_pr} です"
        "（安全床は対象 PR の head で評価済み・弱化していません）。"
    )


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        json.dump({}, sys.stdout)
        return

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if not _is_release_judgement_invocation(str(tool_name), tool_input):
        json.dump({}, sys.stdout)
        return

    # main/master ブランチなら許可
    branch = run(["git", "branch", "--show-current"])
    if not branch or branch in ("main", "master"):
        json.dump({}, sys.stdout)
        return

    # 対象 PR の明示解決（Issue #1304）: --pr <N> / pr=<N> / PR #<N> が tool_input に
    # あれば、それを対象 PR として以降の全検査（安全床の対象 head 化を含む）に使う。
    # worktree ベースで進める隊列外 PR は現在ブランチの OPEN PR 解決に構造的に乗らない
    # ため、明示解決できたときはブランチ起点の解決を経由しない。抽出できない場合は
    # 従来どおり現在ブランチの OPEN PR 解決へフォールバックする（挙動不変・fail-close 維持）。
    explicit_pr_number = resolve_target_pr_number(tool_input)
    judgement_head_ref: str | None = None
    flag_note = ""
    if explicit_pr_number:
        judgement_head_ref, head_error = resolve_target_pr_head(explicit_pr_number)
        if head_error:
            _block(branch, explicit_pr_number, [head_error])
            return
        flag_note = _current_pr_flag_note(explicit_pr_number)

    try:
        if explicit_pr_number:
            full_plan_reason = _full_plan_completion_reason(
                require_delivery_state=False,
                judgement_head_ref=judgement_head_ref,
            )
        else:
            full_plan_reason = _full_plan_completion_reason(require_delivery_state=False)
    except Exception as exc:
        _block(
            branch,
            explicit_pr_number or "未確認",
            [
                "全プラン完了認証モジュールの実行時エラーにより、"
                f"P-010 fail-close でブロックします: {exc}"
            ],
        )
        return
    if full_plan_reason:
        _block(
            branch,
            explicit_pr_number or "未確認",
            [f"全プラン完了未認証: {full_plan_reason}"],
        )
        return

    if explicit_pr_number:
        pr_number = explicit_pr_number
    else:
        # OPEN な PR を確認
        pr_json = run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"])
        if pr_json is None:
            _allow(
                f"【N-371 reminder / PR lookup エラー】ブランチ {branch} の OPEN PR 一覧を"
                "取得できません。transient lookup 失敗は fail-open とします。"
            )
            return

        try:
            pr_list = json.loads(pr_json)
        except json.JSONDecodeError:
            _allow(
                f"【N-371 reminder / PR lookup エラー】ブランチ {branch} の OPEN PR 一覧の"
                "パースに失敗しました。transient lookup 失敗は fail-open とします。"
            )
            return

        if not isinstance(pr_list, list) or not pr_list:
            # PR なし → 許可
            json.dump({}, sys.stdout)
            return

        first_pr = pr_list[0]
        if not isinstance(first_pr, dict) or "number" not in first_pr:
            _allow(
                f"【N-371 reminder / PR lookup エラー】ブランチ {branch} の OPEN PR 情報に"
                "number がありません。transient lookup 失敗は fail-open とします。"
            )
            return

        pr_number = str(first_pr["number"])

    reminders: list[str] = []
    if flag_note:
        reminders.append(flag_note)

    # チェック 0: Draft 状態
    is_draft = run(["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}", "--jq", ".draft"])
    if is_draft is None:
        reminders.append("PR の Draft/Ready 状態を取得できない")
    elif is_draft == "true":
        reminders.append(
            "PR が Draft 状態。AIレビューは発火しないため、"
            "Ready for review へ変更してから Round 1 または "
            "fallback AIレビューを明示リクエストしてください"
        )

    # チェック 1: CI ステータス
    ci_issue = check_ci(pr_number)
    if ci_issue:
        _block(branch, pr_number, [ci_issue])
        return
    elif ci_issue is None:
        reminders.append("CI 状態を取得できない（Hook では fail-open）")

    # チェック 2: レビュワーが pending
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

    # チェック 2b: Round 数（後続の発火ヒントにも使用）
    review_count = count_copilot_reviews(pr_number)
    if review_count is None:
        reminders.append("AIレビュー数を取得できない（review state lookup 失敗は fail-open）")
    elif review_count > 3:
        reminders.append(
            f"AIレビューが {review_count} 回到達。"
            "延長ラウンドの適格理由、実質修正、修正前後 head、検証結果を確認してください。"
            "非ブロッキング指摘だけなら Backlog 化し、延長しません"
        )

    # チェック 3: 最新コミットにレビューが到着しているか
    head_sha = run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}", "--jq", ".head.sha"]
    )
    if not head_sha:
        reminders.append("レビュー状態を取得できない（head SHA 不明）")
    else:
        canonical_head = (
            _review_loop_state._canonical_reviewed_head(head_sha, _REPO_ROOT) or head_sha
        )
        commit_date = run(
            [
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/commits/{canonical_head}",
                "--jq",
                ".commit.committer.date",
            ]
        )
        if not commit_date:
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
                    commit_dt = datetime.fromisoformat(commit_date.replace("Z", "+00:00"))
                    # identity 照合済み review の時刻は review-only R ではなく
                    # canonical E の commit 時刻と比較する。
                    _review_after_commit = review_dt.astimezone(UTC) >= commit_dt.astimezone(UTC)
                except ValueError:
                    pass
            if not _review_after_commit:
                # B-366 fallback: Claude の AI レビュー marker があれば OK
                has_fallback_marker = (
                    has_claude_code_review_marker(int(pr_number), commit_date)
                    if commit_date
                    else False
                )
                if has_fallback_marker:
                    _review_after_commit = True
                else:
                    already = any("AIレビュー" in r or "Copilot" in r for r in reminders)
                    if not already:
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
    # チェック 4: 未解決または未返信の active thread
    incomplete = count_incomplete_ai_review_threads(pr_number)
    if incomplete is None:
        reminders.append("未解決／未返信 thread 数を取得できない（GraphQL エラー）")
    elif incomplete > 0:
        reminders.append(f"AIレビューコメントに {incomplete} 件の未解決または未返信 thread がある")

    if reminders:
        _allow(
            f"【N-371 reminder / release judgement 前確認】PR #{pr_number}: "
            + "; ".join(reminders)
            + ". Claude pre_agent_guard は ci/final-gate と full-plan safety だけをブロックします。"
        )
    else:
        _allow()


if __name__ == "__main__":
    main()
