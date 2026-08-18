"""レビューループの完了判定に使う GitHub 状態取得 helper。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from typing import cast


def _find_executable(name: str) -> str:
    """コマンドの絶対パスを返す。見つからなければコマンド名そのままを返す。"""
    candidates = [f"/usr/local/bin/{name}", f"/usr/bin/{name}", f"/bin/{name}"]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    found = shutil.which(name)
    return found if found else name


_GH = _find_executable("gh")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
_COPILOT_LOGINS: frozenset[str] = frozenset(
    {
        "copilot-pull-request-reviewer[bot]",
        "copilot-pull-request-reviewer",
        "Copilot",
    }
)
_AI_REVIEW_LOGINS: frozenset[str] = _COPILOT_LOGINS | frozenset(
    {
        "chatgpt-codex-connector",
        "chatgpt-codex-connector[bot]",
        "claude",
        "claude[bot]",
        "claude-code[bot]",
    }
)
_AI_REVIEW_MARKERS: tuple[str, ...] = (
    "## AI レビュー結果",
    "### 💡 Codex Review",
    "Codex Review",
    "engine: `codex`",
    "engine: `claude`",
)
_TRUSTED_MARKER_LOGINS: frozenset[str] = _AI_REVIEW_LOGINS | frozenset({"github-actions[bot]"})
_NO_COPILOT_REVIEWS_SENTINEL = "__NO_COPILOT_REVIEWS__"
_REVIEWED_HEAD_RE = re.compile(r"reviewed_head_sha:\s*`?([0-9a-f]{7,40})`?", re.IGNORECASE)
_CODEX_REVIEWED_RE = re.compile(r"Reviewed commit:\*\*\s*`([0-9a-f]{7,40})`", re.IGNORECASE)
_MUST_COUNT_RE = re.compile(r"^\s*[-*]\s+Must:\s*(\d+)\b", re.MULTILINE)
_SHOULD_COUNT_RE = re.compile(r"^\s*[-*]\s+Should:\s*(\d+)\b", re.MULTILINE)
_REVIEW_REPORT_PREFIX = "docs/ai/reviews/"


def _run(cmd: list[str], timeout: int = 20, *, allow_nonzero: bool = False) -> str | None:
    """gh コマンドを実行し stdout を返す。失敗時は None。"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_REPO_ROOT,
        )
        if result.returncode != 0 and not allow_nonzero:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_copilot_review_count(pr_number: int) -> int | None:
    """指定 PR の Copilot レビュー数を返す。取得失敗時は None。"""
    output = _run(
        [
            _GH,
            "api",
            "--paginate",
            f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews",
            "--jq",
            (f'if length == 0 then "{_NO_COPILOT_REVIEWS_SENTINEL}" else .[] | .user.login end'),
        ],
        timeout=30,
    )
    if output is None or output == "":
        return None
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    if all(line == _NO_COPILOT_REVIEWS_SENTINEL for line in lines):
        return 0
    return sum(
        1 for line in lines if line != _NO_COPILOT_REVIEWS_SENTINEL and line in _COPILOT_LOGINS
    )


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


def _fetch_reviews(pr_number: int) -> list[dict[str, object]] | None:
    reviews: list[dict[str, object]] = []
    for page in range(1, 101):
        output = _run(
            [
                _GH,
                "api",
                f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews?per_page=100&page={page}",
            ],
            timeout=30,
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
        reviews.extend(cast("list[dict[str, object]]", page_reviews))
        if len(page_reviews) < 100:
            return reviews
    return None


def _review_body(review: dict[str, object]) -> str:
    body = review.get("body")
    return body if isinstance(body, str) else ""


def _review_commit(review: dict[str, object]) -> str:
    commit_id = review.get("commit_id")
    return commit_id if isinstance(commit_id, str) else ""


def _reviewed_head_from_body(body: str) -> str:
    for pattern in (_REVIEWED_HEAD_RE, _CODEX_REVIEWED_RE):
        match = pattern.search(body)
        if match:
            return match.group(1)
    return ""


def _is_review_report_path(path: str) -> bool:
    return path.strip().replace("\\", "/").lstrip("./").startswith(_REVIEW_REPORT_PREFIX)


def _changed_paths_between(base_sha: str, head_sha: str) -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_sha, head_sha],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=_REPO_ROOT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def _review_head_is_compatible(reviewed_sha: str, head_sha: str) -> bool:
    if head_sha.startswith(reviewed_sha) or reviewed_sha == head_sha:
        return True
    changed_paths = _changed_paths_between(reviewed_sha, head_sha)
    if not changed_paths:
        return False
    return all(_is_review_report_path(path) for path in changed_paths)


def _has_blocking_fallback_findings(body: str) -> bool:
    if "## AI レビュー結果" not in body:
        return False
    must = _MUST_COUNT_RE.search(body)
    should = _SHOULD_COUNT_RE.search(body)
    return (must is not None and int(must.group(1)) > 0) or (
        should is not None and int(should.group(1)) > 0
    )


def _review_matches_head(review: dict[str, object], head_sha: str) -> bool:
    body = _review_body(review)
    reviewed_head = _reviewed_head_from_body(body)
    if reviewed_head:
        return _review_head_is_compatible(reviewed_head, head_sha)
    commit_id = _review_commit(review)
    if commit_id:
        return _review_head_is_compatible(commit_id, head_sha)
    return "## AI レビュー結果" not in body


def get_ai_review_count(pr_number: int) -> int | None:
    """Copilot / Codex / Claude のレビュー数を返す。取得失敗時は None。"""
    reviews = _fetch_reviews(pr_number)
    if reviews is None:
        return None
    if not reviews:
        return 0
    current_user = _run([_GH, "api", "user", "--jq", ".login"], timeout=10)
    count = 0
    for review in reviews:
        user = review.get("user") or {}
        login = user.get("login") if isinstance(user, dict) else None
        if _is_ai_review(login, review.get("body"), current_user):
            count += 1
    return count


def get_copilot_reviewer_count(pr_number: int) -> int | None:
    """requested_reviewers 内の Copilot レビュワー数を返す。"""
    jq_expr = (
        "[.requested_reviewers[]?"
        " | select("
        '.login == "copilot-pull-request-reviewer[bot]"'
        ' or .login == "copilot-pull-request-reviewer"'
        ' or .login == "Copilot"'
        ")] | length"
    )
    output = _run(
        [_GH, "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}", "--jq", jq_expr],
        timeout=20,
    )
    if output is None or output == "":
        return None
    try:
        return int(output)
    except ValueError:
        return None


def get_copilot_latest_review_info(pr_number: int) -> tuple[str | None, str | None]:
    """Copilot bot の最新レビュー日時と review id を返す。"""
    jq_expr = (
        "[.[] | select("
        '(.user.login == "copilot-pull-request-reviewer[bot]"'
        ' or .user.login == "copilot-pull-request-reviewer"'
        ' or .user.login == "Copilot")'
        " and .submitted_at != null"
        r')] | .[] | "\(.id)|\(.submitted_at)"'
    )
    output = _run(
        [
            _GH,
            "api",
            "--paginate",
            f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews",
            "--jq",
            jq_expr,
        ],
        timeout=20,
    )
    if not output:
        return None, None
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None, None
    latest_dt: datetime | None = None
    latest_date = ""
    latest_id = ""
    for line in lines:
        review_id, sep, review_date = line.partition("|")
        if not sep:
            continue
        try:
            review_dt = datetime.fromisoformat(review_date.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
        if latest_dt is None or review_dt > latest_dt:
            latest_dt = review_dt
            latest_date = review_date
            latest_id = review_id
    if not latest_id:
        return None, None
    return latest_date, latest_id


def get_ai_latest_review_info(pr_number: int) -> tuple[str | None, str | None]:
    """Copilot / Codex / Claude の最新レビュー日時と review id を返す。"""
    reviews = _fetch_reviews(pr_number)
    if reviews is None:
        return None, None
    current_user = _run([_GH, "api", "user", "--jq", ".login"], timeout=10)

    latest_dt: datetime | None = None
    latest_date = ""
    latest_id = ""
    for review in reviews:
        user = review.get("user") or {}
        login = user.get("login") if isinstance(user, dict) else None
        if not _is_ai_review(login, review.get("body"), current_user):
            continue
        review_date = review.get("submitted_at")
        if not isinstance(review_date, str) or not review_date:
            continue
        try:
            review_dt = datetime.fromisoformat(review_date.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
        if latest_dt is None or review_dt > latest_dt:
            latest_dt = review_dt
            latest_date = review_date
            latest_id = str(review.get("id") or "")
    if not latest_date:
        return None, None
    return latest_date, latest_id


def get_latest_copilot_review_status(
    pr_number: int,
    review_count: int | None = None,
) -> tuple[bool | None, str | None, str | None]:
    """最新コミットに Copilot レビューが届いたかを返す。"""
    head_sha = _run(
        [_GH, "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}", "--jq", ".head.sha"],
        timeout=20,
    )
    if not head_sha:
        return None, None, "レビュー状態を取得できない（head SHA 不明）"

    commit_date = _run(
        [
            _GH,
            "api",
            f"repos/{{owner}}/{{repo}}/commits/{head_sha}",
            "--jq",
            ".commit.committer.date",
        ],
        timeout=20,
    )
    if not commit_date:
        return None, head_sha, "レビュー状態を取得できない（コミット日時不明）"

    latest_review_date, _latest_review_id = get_copilot_latest_review_info(pr_number)
    if latest_review_date in (None, "", "null"):
        effective_review_count = review_count
        if effective_review_count is None:
            effective_review_count = get_copilot_review_count(pr_number)
        if effective_review_count is None:
            return None, head_sha, "レビュー状態を取得できない（Copilot レビュー日時不明）"
        if effective_review_count == 0:
            return False, head_sha, None
        return None, head_sha, "レビュー状態を取得できない（Copilot レビュー日時不明）"

    assert latest_review_date is not None
    try:
        review_dt = datetime.fromisoformat(latest_review_date.replace("Z", "+00:00")).astimezone(
            UTC
        )
        commit_dt = datetime.fromisoformat(commit_date.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None, head_sha, "レビュー状態を取得できない（レビューまたはコミット日時不正）"

    return review_dt >= commit_dt, head_sha, None


def get_latest_ai_review_status(
    pr_number: int,
    review_count: int | None = None,
) -> tuple[bool | None, str | None, str | None]:
    """最新コミットに Copilot / Codex / Claude レビューが届いたかを返す。"""
    head_sha = _run(
        [_GH, "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}", "--jq", ".head.sha"],
        timeout=20,
    )
    if not head_sha:
        return None, None, "レビュー状態を取得できない（head SHA 不明）"

    commit_date = _run(
        [
            _GH,
            "api",
            f"repos/{{owner}}/{{repo}}/commits/{head_sha}",
            "--jq",
            ".commit.committer.date",
        ],
        timeout=20,
    )
    if not commit_date:
        return None, head_sha, "レビュー状態を取得できない（コミット日時不明）"

    reviews = _fetch_reviews(pr_number)
    if reviews is None:
        return None, head_sha, "レビュー状態を取得できない（AIレビュー一覧取得失敗）"
    current_user = _run([_GH, "api", "user", "--jq", ".login"], timeout=10)
    latest_review: dict[str, object] | None = None
    latest_dt: datetime | None = None
    for review in reviews:
        user = review.get("user") or {}
        login = user.get("login") if isinstance(user, dict) else None
        if not _is_ai_review(login, review.get("body"), current_user):
            continue
        review_date = review.get("submitted_at")
        if not isinstance(review_date, str) or not review_date:
            continue
        try:
            review_dt = datetime.fromisoformat(review_date.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
        if latest_dt is None or review_dt > latest_dt:
            latest_dt = review_dt
            latest_review = review

    if latest_review is None or latest_dt is None:
        effective_review_count = review_count
        if effective_review_count is None:
            effective_review_count = get_ai_review_count(pr_number)
        if effective_review_count is None:
            return None, head_sha, "レビュー状態を取得できない（AIレビュー日時不明）"
        if effective_review_count == 0:
            return False, head_sha, None
        return None, head_sha, "レビュー状態を取得できない（AIレビュー日時不明）"

    try:
        commit_dt = datetime.fromisoformat(commit_date.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None, head_sha, "レビュー状態を取得できない（レビューまたはコミット日時不正）"

    if _has_blocking_fallback_findings(_review_body(latest_review)):
        return False, head_sha, "最新 fallback AI review に Must/Should 指摘が残っています"
    if not _review_matches_head(latest_review, head_sha):
        return False, head_sha, "最新 AI review の対象 SHA が head SHA と一致しません"
    return latest_dt >= commit_dt, head_sha, None


def count_unreplied_copilot_threads(pr_number: int) -> int | None:
    """Copilot の未返信スレッド数を返す。取得失敗時は None。"""
    current_user = _run([_GH, "api", "user", "--jq", ".login"], timeout=10)
    if not current_user:
        return None
    repo_info = _run([_GH, "repo", "view", "--json", "owner,name"], timeout=10)
    if not repo_info:
        return None
    try:
        repo_data = json.loads(repo_info)
        owner = repo_data["owner"]["login"]
        repo_name = repo_data["name"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

    unreplied = 0
    cursor: str | None = None
    for _ in range(100):
        cursor_arg = f', after: "{cursor}"' if cursor else ""
        query = (
            "{"
            f'  repository(owner: "{owner}", name: "{repo_name}") {{'
            f"    pullRequest(number: {pr_number}) {{"
            f"      reviewThreads(first: 100{cursor_arg}) {{"
            "        pageInfo { hasNextPage endCursor }"
            "        nodes {"
            "          isResolved isOutdated"
            "          comments(first: 50) {"
            "            pageInfo { hasNextPage }"
            "            nodes { author { login } }"
            "          }"
            "        }"
            "      }"
            "    }"
            "  }"
            "}"
        )
        output = _run([_GH, "api", "graphql", "-f", f"query={query}"], timeout=20)
        if not output:
            return None
        try:
            data = json.loads(output)
            if data.get("errors"):
                return None
            review_threads_data = data["data"]["repository"]["pullRequest"]["reviewThreads"]
            if not isinstance(review_threads_data, dict):
                return None
            page_nodes = review_threads_data.get("nodes")
            if not isinstance(page_nodes, list):
                return None
            page_info = review_threads_data.get("pageInfo", {})
            if not isinstance(page_info, dict):
                return None

            for thread in page_nodes:
                if not isinstance(thread, dict):
                    return None
                if thread.get("isResolved") or thread.get("isOutdated"):
                    continue
                comments_data = thread.get("comments", {})
                if not isinstance(comments_data, dict):
                    return None
                comments_page_info = comments_data.get("pageInfo", {})
                if not isinstance(comments_page_info, dict):
                    return None
                if comments_page_info.get("hasNextPage"):
                    return None
                comments = comments_data.get("nodes", [])
                if not isinstance(comments, list):
                    return None
                if not comments:
                    continue

                has_copilot = False
                has_reply = False
                for comment in comments:
                    if not isinstance(comment, dict):
                        return None
                    author = comment.get("author") or {}
                    if not isinstance(author, dict):
                        return None
                    login = author.get("login")
                    has_copilot = has_copilot or login in _COPILOT_LOGINS
                    has_reply = has_reply or login == current_user
                if has_copilot and not has_reply:
                    unreplied += 1

            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                return None
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
    else:
        return None

    return unreplied


def count_unreplied_ai_threads(pr_number: int) -> int | None:
    """Copilot / Codex / Claude の未返信スレッド数を返す。取得失敗時は None。"""
    current_user = _run([_GH, "api", "user", "--jq", ".login"], timeout=10)
    if not current_user:
        return None
    repo_info = _run([_GH, "repo", "view", "--json", "owner,name"], timeout=10)
    if not repo_info:
        return None
    try:
        repo_data = json.loads(repo_info)
        owner = repo_data["owner"]["login"]
        repo_name = repo_data["name"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

    unreplied = 0
    cursor: str | None = None
    for _ in range(100):
        cursor_arg = f', after: "{cursor}"' if cursor else ""
        query = (
            "{"
            f'  repository(owner: "{owner}", name: "{repo_name}") {{'
            f"    pullRequest(number: {pr_number}) {{"
            f"      reviewThreads(first: 100{cursor_arg}) {{"
            "        pageInfo { hasNextPage endCursor }"
            "        nodes {"
            "          isResolved isOutdated"
            "          comments(first: 50) {"
            "            pageInfo { hasNextPage }"
            "            nodes { author { login } body }"
            "          }"
            "        }"
            "      }"
            "    }"
            "  }"
            "}"
        )
        output = _run([_GH, "api", "graphql", "-f", f"query={query}"], timeout=20)
        if not output:
            return None
        try:
            data = json.loads(output)
            if data.get("errors"):
                return None
            review_threads_data = data["data"]["repository"]["pullRequest"]["reviewThreads"]
            if not isinstance(review_threads_data, dict):
                return None
            page_nodes = review_threads_data.get("nodes")
            if not isinstance(page_nodes, list):
                return None
            page_info = review_threads_data.get("pageInfo", {})
            if not isinstance(page_info, dict):
                return None

            for thread in page_nodes:
                if not isinstance(thread, dict):
                    return None
                if thread.get("isResolved") or thread.get("isOutdated"):
                    continue
                comments_data = thread.get("comments", {})
                if not isinstance(comments_data, dict):
                    return None
                comments_page_info = comments_data.get("pageInfo", {})
                if not isinstance(comments_page_info, dict):
                    return None
                if comments_page_info.get("hasNextPage"):
                    return None
                comments = comments_data.get("nodes", [])
                if not isinstance(comments, list):
                    return None
                if not comments:
                    continue

                has_ai_review = False
                has_reply = False
                for comment in comments:
                    if not isinstance(comment, dict):
                        return None
                    author = comment.get("author") or {}
                    if not isinstance(author, dict):
                        return None
                    login = author.get("login")
                    has_ai_review = has_ai_review or _is_ai_review(
                        login,
                        comment.get("body"),
                        current_user,
                    )
                    has_reply = has_reply or login == current_user
                if has_ai_review and not has_reply:
                    unreplied += 1

            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                return None
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
    else:
        return None

    return unreplied
