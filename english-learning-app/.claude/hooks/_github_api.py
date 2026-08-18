"""Hook 用の gh CLI フォールバック層。

設計方針:
- gh が利用可能なら従来通り gh を使う（既存挙動を壊さない）
- gh 不在で GITHUB_TOKEN/GH_TOKEN が設定されているなら urllib で GitHub REST API を直接叩く
- 両方とも利用できない場合は (auth 利用不可) を呼び出し側に通知し、
  呼び出し側はフェイルオープン（警告のみ）かフェイルクローズかを選択できる

これにより gh 未インストール環境（Claude Code Web SDK 等）でも、
GITHUB_TOKEN があれば hook が機能するようになる。

GITHUB_TOKEN が両方とも無い環境では従来どおり機能不全になるが、
呼び出し側で `has_credentials()` を確認して、誤判定で一律ブロックする代わりに
「環境的にチェック不能」と明示できるようになる。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC
from pathlib import Path
from typing import Any, cast

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from scripts.hooks import _review_loop_state  # noqa: E402

API_TIMEOUT = 15
SUBPROCESS_TIMEOUT = 10
_AI_REVIEW_MARKERS: tuple[str, ...] = (
    "## AI レビュー結果",
    "engine: `claude`",
    "## Claude Code 内部レビュー (B-366",
)
_AI_REVIEW_LOGINS: frozenset[str] = frozenset(
    {
        "copilot-pull-request-reviewer[bot]",
        "copilot-pull-request-reviewer",
        "Copilot",
        "claude",
        "claude[bot]",
        "claude-code[bot]",
    }
)
_TRUSTED_MARKER_LOGINS: frozenset[str] = _AI_REVIEW_LOGINS | frozenset({"github-actions[bot]"})
_REVIEWED_HEAD_RE = re.compile(r"reviewed_head_sha:\s*`?([0-9a-f]{7,40})`?", re.IGNORECASE)


def gh_available() -> bool:
    """gh CLI がインストール済みかどうかを返す。"""
    return shutil.which("gh") is not None


_TOKEN_RESOLVED = False
_TOKEN_CACHE: str | None = None


def _git_remote_https_url() -> str | None:
    """origin remote が `https://github.com/...` ならその URL を返す。

    credential query に `url=` として渡すと、git 自身が `credential.useHttpPath`
    を適用して path 有無を正しく扱う（手動で path を組み立てて `.git` 有無で
    不一致になる問題を避ける）。https 以外（SSH 等・credential token 非対象）や
    取得失敗時は None（host だけの query にフォールバック）。
    """
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url if url.startswith("https://github.com/") else None


def _token_from_git_credential() -> str | None:
    """git credential helper から github.com 用 token を取得する。

    gh が未認証かつ GITHUB_TOKEN/GH_TOKEN 未設定でも、git の credential helper に
    token が保存されていれば hook が GitHub を参照できるようにする（本リポジトリの
    コンテナ環境は token を env でなく git credential 経由で保持するため）。
    https remote が取れる場合は `url=` で渡して git に `credential.useHttpPath`
    の解釈を委譲する（`.git` 有無や per-repo credential を git が正しく扱う）。
    GIT_TERMINAL_PROMPT=0 で対話プロンプトを抑止し、helper 不在環境では即座に
    None を返す（ハングしない）。token は秘匿情報のためログ・出力しない（P-002）。
    """
    # 非対話を徹底する: GIT_TERMINAL_PROMPT=0 は端末プロンプトのみ抑止するため、
    # GIT_ASKPASS/SSH_ASKPASS（VS Code 等が設定）と GCM の対話も no-op 化して
    # helper が何も返さない場合に askpass 起動でハングするのを防ぐ。timeout も併用。
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "true",
        "SSH_ASKPASS": "true",
        "GCM_INTERACTIVE": "never",
    }
    url = _git_remote_https_url()
    query = f"url={url}\n\n" if url else "protocol=https\nhost=github.com\n\n"
    try:
        result = subprocess.run(
            ["git", "-c", "core.askPass=", "credential", "fill"],
            input=query,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            env=env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password=") :].strip() or None
    return None


def _resolve_token() -> str | None:
    """利用可能な GitHub token を解決する（env → git credential helper の順）。

    結果はプロセス内でキャッシュし、helper 呼び出しの繰り返しを避ける。
    """
    global _TOKEN_RESOLVED, _TOKEN_CACHE
    if _TOKEN_RESOLVED:
        return _TOKEN_CACHE
    _TOKEN_RESOLVED = True
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        token = _token_from_git_credential()
    _TOKEN_CACHE = token
    return token


_GH_AUTH_CHECKED = False
_GH_AUTH_RESULT = False


def _gh_is_authenticated() -> bool:
    """gh が **github.com に対して** 自前認証済みかを返す。

    `gh auth status` は全 known host を検査するため、GitHub Enterprise 等の別 host
    だけ認証済みでも全体の exit code が 0 になりうる。`--hostname github.com` で
    github.com 限定に判定し、別 host 認証で github.com の token 注入を抑止しない。
    結果はプロセス内でキャッシュする。gh 未インストールなら False。
    """
    global _GH_AUTH_CHECKED, _GH_AUTH_RESULT
    if _GH_AUTH_CHECKED:
        return _GH_AUTH_RESULT
    _GH_AUTH_CHECKED = True
    if not gh_available():
        _GH_AUTH_RESULT = False
        return False
    try:
        result = subprocess.run(
            ["gh", "auth", "status", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
        _GH_AUTH_RESULT = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        _GH_AUTH_RESULT = False
    return _GH_AUTH_RESULT


def gh_subprocess_env() -> dict[str, str]:
    """gh を呼ぶ subprocess 用の env を返す（読み取り系 gh 呼び出し向け）。

    gh が未認証かつ env token（GH_TOKEN/GITHUB_TOKEN）も無い場合のみ、
    git credential helper 由来の token を GH_TOKEN として注入し gh を認証させる。
    既に env token がある／gh が自前認証済み（PC1 等）の環境では何も注入せず
    既存の認証アイデンティティを尊重する（「既存挙動を壊さない」設計方針）。
    token は秘匿情報のため出力・ログに残さない（P-002）。
    """
    env = dict(os.environ)
    if env.get("GH_TOKEN") or env.get("GITHUB_TOKEN"):
        return env
    if _gh_is_authenticated():
        return env
    token = _resolve_token()
    if token:
        env["GH_TOKEN"] = token
    return env


def github_token_available() -> bool:
    """GitHub token（環境変数または git credential helper）が利用可能かを返す。"""
    return bool(_resolve_token())


def has_credentials() -> bool:
    """gh または GitHub token のいずれかが利用可能かを返す。

    両方とも利用不可の場合、hook は PR 状態を確認できないため、
    呼び出し側はフェイルオープン（警告のみ）にすることを検討する。
    """
    return gh_available() or github_token_available()


def is_claude_code_remote() -> bool:
    """Claude Code 公式リモート環境（Web SDK / claude.ai/code 等）かを判定する。

    リモート環境では `CLAUDE_CODE_REMOTE` 環境変数が設定される（ローカル CLI では未設定）。
    リモート環境の特徴:
    - gh CLI / GITHUB_TOKEN が無く、GitHub 操作は MCP github ツール経由のみ
    - PR イベントは `subscribe_pr_activity` による webhook 経由で受信できる
    - したがってシェルでの sleep ベースポーリングは不要（むしろ有害）
    """
    return bool(os.environ.get("CLAUDE_CODE_REMOTE"))


def _gh_run(
    args: list[str],
    timeout: int = SUBPROCESS_TIMEOUT,
    *,
    allow_nonzero: bool = False,
) -> str | None:
    """gh コマンドを実行して stdout を返す。失敗時は None。"""
    if not gh_available():
        return None
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=gh_subprocess_env(),
        )
        if result.returncode != 0 and not allow_nonzero:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _api_call(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> str | None:
    """GitHub REST API を urllib で直接呼ぶ。失敗時は None。

    path は "repos/{owner}/{repo}/..." 形式（先頭スラッシュ任意）。
    """
    token = _resolve_token()
    if not token:
        return None

    url = path if path.startswith("https://") else f"https://api.github.com/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "claude-code-hooks",
    }

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            return cast("str", resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError):
        return None


def get_repo_owner_name() -> tuple[str, str] | None:
    """現在のリポジトリの (owner, name) を返す。失敗時は None。

    まず gh repo view を試し、失敗時は git remote URL から推定する。
    """
    output = _gh_run(["repo", "view", "--json", "owner,name"])
    if output:
        try:
            data = json.loads(output)
            return data["owner"]["login"], data["name"]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # フォールバック: git remote URL から推定
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        remote_url = result.stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?/?$", remote_url)
        if m:
            return m.group(1), m.group(2)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def list_open_prs_for_branch(branch: str) -> list[dict[str, Any]] | None:
    """指定ブランチの OPEN な PR 一覧を返す。

    Returns:
        list[dict]: 取得成功（空リスト含む）。各 dict は {number, state, ...} を含む
        None: 取得失敗（フェイルクローズ対象）
    """
    # gh 経路
    output = _gh_run(
        [
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,state,title",
        ],
        allow_nonzero=False,
    )
    if output is not None:
        try:
            return cast("list[dict[str, Any]]", json.loads(output))
        except json.JSONDecodeError:
            pass

    # API 経路
    if not github_token_available():
        return None
    repo = get_repo_owner_name()
    if not repo:
        return None
    owner, name = repo
    response = _api_call(
        "GET",
        f"repos/{owner}/{name}/pulls"
        f"?head={urllib.parse.quote(owner)}:{urllib.parse.quote(branch)}"
        "&state=open",
    )
    if response is None:
        return None
    try:
        prs = json.loads(response)
        return [
            {
                "number": pr["number"],
                # gh は uppercase ("OPEN") を返すため合わせる
                "state": str(pr.get("state", "")).upper(),
                "title": pr.get("title", ""),
            }
            for pr in prs
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def get_pr_check_runs(pr_number: int) -> list[dict[str, Any]] | None:
    """PR のチェックラン一覧を返す。

    Returns:
        list[dict]: 各 dict は {"name": str, "state": str}
        None: 取得失敗
    """
    # gh 経路
    output = _gh_run(
        ["pr", "checks", str(pr_number), "--json", "name,state"],
        timeout=API_TIMEOUT,
        allow_nonzero=True,
    )
    if output:
        try:
            return cast("list[dict[str, Any]]", json.loads(output))
        except json.JSONDecodeError:
            pass

    # API 経路
    if not github_token_available():
        return None
    repo = get_repo_owner_name()
    if not repo:
        return None
    owner, name = repo

    # PR の head SHA を取得
    pr_response = _api_call("GET", f"repos/{owner}/{name}/pulls/{pr_number}")
    if pr_response is None:
        return None
    try:
        pr_data = json.loads(pr_response)
        head_sha = pr_data["head"]["sha"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

    # check-runs API を叩く
    check_response = _api_call(
        "GET",
        f"repos/{owner}/{name}/commits/{head_sha}/check-runs",
    )
    if check_response is None:
        return None
    try:
        check_data = json.loads(check_response)
        runs: list[dict[str, Any]] = []
        for cr in check_data.get("check_runs", []):
            status = str(cr.get("status", "")).strip().lower()
            conclusion = str(cr.get("conclusion", "") or "").strip().lower()
            # gh の state 表記に合わせる（uppercase）
            if status == "completed":
                state = (conclusion or "neutral").upper()
            elif status == "in_progress":
                state = "IN_PROGRESS"
            elif status == "queued":
                state = "QUEUED"
            elif status == "waiting":
                state = "WAITING"
            else:
                state = status.upper() if status else "UNKNOWN"
            runs.append({"name": cr.get("name", "?"), "state": state})
        return runs
    except (json.JSONDecodeError, AttributeError):
        return None


def has_claude_code_review_marker(pr_number: int, after_iso: str) -> bool:
    """B-366: PR の issue comments に AI レビュー fallback marker があるかを返す。

    判定条件:
    - 本文が Claude Code 旧 marker または `## AI レビュー結果` 等の fallback marker を含む
    - 本文に "Must 0" / "Must: 0" / "Must=0" / "Must：0" 等を含む（内部監査結果 Must 0）
    - 本文に "Should 0" / "Should: 0" / "Should=0" / "Should：0" 等を含む
    - `created_at >= after_iso`（最新コミット時刻以降のコメントのみ有効）

    Copilot Round N のレビューが遅延・422 等で発火しない場合に、Claude
    fallback レビューを Copilot review 相当に扱う。詳細は plan.md の B-366 を参照。

    Returns:
        True: 有効な marker コメントが 1 件以上ある
        False: 無い、または取得失敗
    """
    from datetime import datetime

    must_zero_pattern = re.compile(
        r"Must\s*[\s:=：]\s*(?:\*\*\s*0\s*\*\*|『\s*0\s*』|0(?![0-9]))",
        re.IGNORECASE,
    )
    should_zero_pattern = re.compile(
        r"Should\s*[\s:=：]\s*(?:\*\*\s*0\s*\*\*|『\s*0\s*』|0(?![0-9]))",
        re.IGNORECASE,
    )
    try:
        after_dt = datetime.fromisoformat(after_iso.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return False
    expected_head = _get_pr_head_sha(pr_number)
    if not expected_head:
        return False

    current_user = _gh_run(["api", "user", "--jq", ".login"], timeout=10)
    trusted_marker_logins = _TRUSTED_MARKER_LOGINS | (
        frozenset({current_user}) if current_user else frozenset()
    )

    raw = _gh_run(
        [
            "api",
            "--paginate",
            f"repos/{{owner}}/{{repo}}/issues/{pr_number}/comments",
        ],
        timeout=API_TIMEOUT,
    )
    if raw is None and github_token_available():
        repo = get_repo_owner_name()
        if repo is None:
            return False
        owner, name = repo
        raw = _api_call(
            "GET",
            f"repos/{owner}/{name}/issues/{pr_number}/comments?per_page=100",
        )
    if raw is None:
        return False

    try:
        comments = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(comments, list):
        return False
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("body", ""))
        user = comment.get("user") or {}
        login = user.get("login") if isinstance(user, dict) else None
        if login not in trusted_marker_logins:
            continue
        if not any(marker in body for marker in _AI_REVIEW_MARKERS):
            continue
        if not must_zero_pattern.search(body):
            continue
        if not should_zero_pattern.search(body):
            continue
        if not _reviewed_head_matches(body, expected_head):
            continue
        created = comment.get("created_at", "")
        try:
            created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
        if created_dt >= after_dt:
            return True
    return False


def _reviewed_head_matches(body: str, expected_head: str) -> bool:
    match = _REVIEWED_HEAD_RE.search(body)
    if not match:
        return False
    reviewed_head = match.group(1).lower()
    normalized_expected = expected_head.lower()
    if normalized_expected == reviewed_head or normalized_expected.startswith(reviewed_head):
        return True
    return _review_loop_state._review_identity_matches_head(reviewed_head, normalized_expected)


def _get_pr_head_sha(pr_number: int) -> str | None:
    raw = _gh_run(
        ["pr", "view", str(pr_number), "--json", "headRefOid", "--jq", ".headRefOid"],
        timeout=10,
    )
    if raw:
        return raw.strip()
    if not github_token_available():
        return None
    repo = get_repo_owner_name()
    if repo is None:
        return None
    owner, name = repo
    payload = _api_call("GET", f"repos/{owner}/{name}/pulls/{pr_number}")
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    head = data.get("head")
    if not isinstance(head, dict):
        return None
    sha = head.get("sha")
    return sha if isinstance(sha, str) and sha else None
