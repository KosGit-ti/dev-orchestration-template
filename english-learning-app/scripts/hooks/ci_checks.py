"""CI check-runs 判定の共通ヘルパー。"""

import json
from typing import Protocol

MAX_CHECK_RUN_PAGES = 50
CHECK_RUNS_PER_PAGE = 100


class RunCommand(Protocol):
    def __call__(
        self,
        cmd: list[str],
        timeout: int = ...,
        *,
        allow_nonzero: bool = ...,
    ) -> str | None: ...


def get_pr_head_sha(run: RunCommand, pr_number: str) -> str | None:
    """PR head SHA を取得する。"""
    return run(
        [
            "gh",
            "api",
            f"repos/{{owner}}/{{repo}}/pulls/{pr_number}",
            "--jq",
            ".head.sha",
        ],
        timeout=20,
    )


def fetch_check_runs(run: RunCommand, head_sha: str) -> tuple[list[object] | None, str | None]:
    """head commit の check-runs を全ページ取得する。"""
    all_checks: list[object] = []
    total_count: int | None = None

    for page in range(1, MAX_CHECK_RUN_PAGES + 1):
        checks_json = run(
            [
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/commits/{head_sha}/check-runs"
                f"?per_page={CHECK_RUNS_PER_PAGE}&page={page}",
            ],
            timeout=20,
        )
        if checks_json is None:
            return None, f"CI check-runs API の取得に失敗しました（page={page}）"
        if not checks_json:
            return None, f"CI check-runs API のレスポンスが空です（page={page}）"
        try:
            checks_data = json.loads(checks_json)
        except json.JSONDecodeError as exc:
            return (
                None,
                f"CI check-runs API レスポンスの JSON 解析に失敗しました（page={page}: {exc.msg}）",
            )
        if not isinstance(checks_data, dict):
            return None, f"CI check-runs API レスポンス形式が不正です（page={page}: dict 以外）"

        page_checks = checks_data.get("check_runs")
        if not isinstance(page_checks, list):
            return (
                None,
                "CI check-runs API レスポンス形式が不正です"
                f"（page={page}: check_runs が list ではない）",
            )

        raw_total_count = checks_data.get("total_count")
        if isinstance(raw_total_count, int):
            total_count = raw_total_count

        all_checks.extend(page_checks)
        if total_count is not None and len(all_checks) >= total_count:
            return all_checks, None
        if len(page_checks) < CHECK_RUNS_PER_PAGE:
            return all_checks, None

    return (
        None,
        f"CI チェック数が多すぎるため全件取得できない（{len(all_checks)}件取得済み）",
    )


def check_run_sort_key(check: dict[str, object]) -> tuple[str, int]:
    """check-run の新旧比較用キーを返す。"""
    timestamps = [
        str(value)
        for value in (
            check.get("started_at"),
            check.get("completed_at"),
            check.get("created_at"),
        )
        if value
    ]
    timestamp_key = max(timestamps) if timestamps else ""

    raw_id = check.get("id")
    if isinstance(raw_id, int):
        id_key = raw_id
    elif isinstance(raw_id, str):
        try:
            id_key = int(raw_id)
        except ValueError:
            id_key = -1
    else:
        id_key = -1

    return (timestamp_key, id_key)


def latest_check_runs_by_name(checks: list[object]) -> list[dict[str, object]] | None:
    """同名 check-run が複数ある場合は最新のものだけ残す。"""
    latest: dict[str, tuple[tuple[str, int], dict[str, object]]] = {}
    for check in checks:
        if not isinstance(check, dict):
            return None
        name = str(check.get("name") or "?")
        sort_key = check_run_sort_key(check)
        current = latest.get(name)
        if current is None or sort_key >= current[0]:
            latest[name] = (sort_key, check)
    return [
        check
        for _, check in sorted(latest.values(), key=lambda item: str(item[1].get("name") or "?"))
    ]


def evaluate_check_runs_all(checks: list[object]) -> list[str]:
    """check-runs の status/conclusion から問題理由を全件返す。"""
    if not checks:
        return ["CI チェックが0件（未作成の可能性）"]

    latest_checks = latest_check_runs_by_name(checks)
    if latest_checks is None:
        return ["CI 状態の形式が不正"]

    completed_status = "COMPLETED"
    success_conclusions = {"SUCCESS", "SKIPPED", "NEUTRAL"}
    failure_conclusions = {
        "ACTION_REQUIRED",
        "CANCELLED",
        "ERROR",
        "FAILURE",
        "STALE",
        "TIMED_OUT",
    }

    issues: list[str] = []
    for check in latest_checks:
        name = str(check.get("name") or "?")
        status = str(check.get("status") or "").strip().upper()
        conclusion = str(check.get("conclusion") or "").strip().upper()
        if status != completed_status:
            issues.append(f"CI '{name}' が {status or '未定義'}")
            continue
        if conclusion in failure_conclusions:
            issues.append(f"CI '{name}' が {conclusion}")
            continue
        if conclusion in success_conclusions:
            continue
        if not conclusion:
            issues.append(f"CI '{name}' の conclusion が未定義（status={status}）")
            continue
        issues.append(f"CI '{name}' が未知の conclusion {conclusion}")
    return issues


def evaluate_check_runs(checks: list[object]) -> str:
    """check-runs の status/conclusion から最初の問題理由を返す。"""
    issues = evaluate_check_runs_all(checks)
    if issues:
        return issues[0]
    return ""
