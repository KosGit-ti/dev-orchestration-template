#!/usr/bin/env python3
"""Claude/Copilot 等のレビュー証跡 JSON を required check として検証する。"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import evidence_adapter  # noqa: E402
from scripts.ai.evidence_contract import (  # noqa: E402
    FORBIDDEN_EXIT_CODES,
    ContractViolation,
)

SCHEMA_VERSION = "1.0"
KNOWN_REVIEW_PROVIDERS = frozenset({"copilot", "claude", "codex"})
DEFAULT_ACCEPTED_REVIEW_PROVIDERS = "copilot,claude,codex"
REVIEW_REPORT_GLOB = "docs/ai/reviews/*.json"
REVIEW_REPORT_PREFIX = "docs/ai/reviews/"
REVIEW_REPORT_GIT_MODES = frozenset({"100644", "100755"})
ALLOWED_SEVERITIES = {"must", "should", "nice"}
ALLOWED_STATUSES = {"open", "resolved", "accepted_risk", "false_positive"}
ALLOWED_RESULTS = {"pass", "partial", "fail"}
REQUIRED_TEXT_FIELDS = ("role", "base_sha", "head_sha", "generated_at", "result", "summary")
REQUIRED_LENSES = {"spec", "security", "reliability"}
REQUIRED_FINDING_FIELDS = ("title", "evidence", "recommendation")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
DECISION_REF_RE = re.compile(r"^DEC-[0-9]{8}-[0-9]{3}$")
TRIGGER_EVIDENCE_RE = re.compile(
    r"^(?P<stable_ref>[A-Z][A-Z0-9_.-]{2,63}):[ \t]+(?P<detail>\S(?:.*\S)?)$"
)
PROBABLE_SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[opsur]_[A-Za-z0-9_]{36,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"sk-(?:proj-|ant-api03-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA|DSA|EC|OPENSSH) PRIVATE KEY-----"),
    re.compile(r"Bearer[ \t]+[A-Za-z0-9._~+/-]{20,}=*", re.IGNORECASE),
    re.compile(r"password[ \t]*=[ \t]*[\"'][^\"']+[\"']", re.IGNORECASE),
)
REBASE_ANCHOR_MARKER_RE = re.compile(r"\[[Pp][Rr][ \t]*#")
VALID_CONTEXT_MODES = {"diff_only", "related_context", "full_repo_agentic"}
CONTEXT_MODE_RANK = {"diff_only": 0, "related_context": 1, "full_repo_agentic": 2}
DEFAULT_REVIEW_ROUNDS = 3
EXTENSION_START_ROUND = DEFAULT_REVIEW_ROUNDS + 1
ALLOWED_EXTENSION_TRIGGER_KINDS = {
    "explicit_human_instruction",
    "immediate_blocker",
    "required_gate_failure",
    "pre_anchor_merge_contract_failure",
}
REQUIRED_ROUND_EXTENSION_FIELDS = (
    "round",
    "trigger_kind",
    "trigger_evidence",
    "authorization_ref",
    "prior_reviewed_head_sha",
    "corrected_head_sha",
    "progress_summary",
    "verification_commands",
)
EXTENSION_EVIDENCE_ONLY_EXACT_PATHS = frozenset(
    {
        "docs/ai/decision-ledger.md",
        "docs/ai/execution-ledger.md",
    }
)
EXTENSION_EVIDENCE_ONLY_PREFIXES = ("docs/ai/pre-pr-reviews/",)
ROUND_EXTENSION_LEDGER_PATH = "docs/ai/execution-ledger.md"
ROUND_EXTENSION_DECISION_LEDGER_PATH = "docs/ai/decision-ledger.md"
ROUND_EXTENSION_LEDGER_BLOCK_RE = re.compile(
    r"^```review-extension-evidence[ \t]*\n(?P<body>.*?)\n```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
REQUIRED_VERIFICATION_COMMAND_FIELDS = ("command", "exit_code", "summary")
DEFAULT_CONTEXT_BUDGET = {
    "max_related_files": 40,
    "max_context_chars": 180_000,
    "max_file_excerpt_chars": 12_000,
    "max_context_commands": 12,
}
REQUIRED_CONTEXT_BUDGET_FIELDS = tuple(DEFAULT_CONTEXT_BUDGET)
# data-only 判定（is_data_only）で data/ 前置一致に加えて許容する完全一致パス。
# 週次再学習 workflow（scheduled-train.yml・B-335）の artifact PR が data/models/** と同一コミットで
# 変更する models/metadata.json のみを対象にする（ディレクトリ丸ごとの緩和はしない・偽装防止）。
_ADDITIONAL_DATA_ONLY_EXACT_PATHS = frozenset({"models/metadata.json"})

# ---------------------------------------------------------------------------
# Evidence Schema v2（N-602A／PR-B mandatory 化）の受理境界
# ---------------------------------------------------------------------------
# 適用開始境界は 3 段で、既存 v1 証跡と過去 artifact を壊さずに fail-close だけ強める。
#
#   1. schema_version=2 の artifact は Evidence Schema v2 として共通 core で検査する
#      （docs/ai/reviews/ 直下に置いた場合と、v1 report の evidence_v2_ref から参照する
#      場合の両方）。v2 を名乗る artifact は契約違反で必ず must fail する。
#   2. v1 report は従来検証を維持する。ただし Truthfulness Rule 3（exit 126/127 は
#      role・expected list・summary に関係なく invalid）だけは schema に依存しない
#      事実なので、現 PR の証跡に対して常時 must fail へ引き上げる（PR #1297 の欠陥）。
#   3. 「新規/変更 evidence は v2 必須」は既定 must（blocking）である（PR-B・
#      ユーザー指示 2026-08-13・DEC-20260813-002 が DEC-20260813-001 の段階適用を
#      前倒し改訂）。opt-out（CLI flag／環境変数）は残さない。過去 artifact（差分外）は
#      historical read として読むだけにし、遡及判定はしない。
EVIDENCE_V2_KIND = "review"
EVIDENCE_V2_REF_FIELD = "evidence_v2_ref"


def contains_probable_secret(value: object) -> bool:
    """JSON 相当値の文字列 leaf に強い secret 指紋があるかを返す。"""
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in PROBABLE_SECRET_PATTERNS)
    if isinstance(value, list):
        return any(contains_probable_secret(item) for item in value)
    if isinstance(value, dict):
        return any(
            contains_probable_secret(key) or contains_probable_secret(item)
            for key, item in value.items()
        )
    return False


# ---------------------------------------------------------------------------
# backlog_id 実在検証（Issue #1054）
#
# accepted_risk の should finding は backlog_id + risk_reason があれば繰延を認められる
# （P-065）が、backlog_id の実在は検証していなかった（任意の文字列 1 個で通せた）。
# 対応形式は次の 2 つのみ:
#   1. `Backlog-` 始まり（大小文字不問）: docs/plan.md の `## Backlog` 系見出し配下に
#      当該 ID が存在すること。
#   2. `#<数字>` 形式: GitHub Issue として実在し、かつ open であること（gh CLI 経由）。
# それ以外の自由文字列は未対応形式として fail する（推奨どおり・穴を残さない）。
# GitHub API 到達不能、認証失敗、応答不正はいずれも既定で fail-close とする。
# gh 不在やネットワーク断だけは、ローカル実行で明示 opt-in した場合に限り
# 非ブロッキング警告へ落とせる。認証失敗と応答不正は opt-in の対象外とする。
# ---------------------------------------------------------------------------
BACKLOG_ID_STATUS_VERIFIED = "verified"
BACKLOG_ID_STATUS_MISSING = "missing"
BACKLOG_ID_STATUS_UNSUPPORTED_FORMAT = "unsupported_format"
BACKLOG_ID_STATUS_UNREACHABLE = "unreachable"
BACKLOG_ID_STATUS_LOOKUP_FAILED = "lookup_failed"

GITHUB_ISSUE_STATE_OPEN = "open"
GITHUB_ISSUE_STATE_CLOSED = "closed"
GITHUB_ISSUE_STATE_MISSING = "missing"
GITHUB_ISSUE_STATE_UNREACHABLE = "unreachable"
GITHUB_ISSUE_STATE_AUTHENTICATION_FAILED = "authentication_failed"
GITHUB_ISSUE_STATE_INVALID_RESPONSE = "invalid_response"

_ISSUE_ID_RE = re.compile(r"^#(?P<number>\d+)$")
_PLAN_BACKLOG_HEADING_RE = re.compile(r"^## Backlog")
_PLAN_RELPATH = "docs/plan.md"

GithubIssueStateFetcher = Callable[[Path, int], str]

LOW_RISK_PREFIXES = ("docs/", "data/")
LOW_RISK_SUFFIXES = (".md", ".txt")
FULL_REPO_REQUIRED_PATTERNS = (
    ".github/workflows/",
    ".github/instructions/",
    ".agents/skills/",
    ".claude/skills/",
    # 以下 2 prefix は廃止済み roster の再追加を検出する互換ガード。
    ".github/agents/",
    "scripts/hooks/",
    ".claude/hooks/",
    ".claude/agents/",
    "AGENTS.md",
    "CLAUDE.md",
    "ai/operation-policy.yml",
    "web/lib/auth/",
    "web/lib/progress/",
    "web/content/",
    "scripts/upload_db.py",
    "scripts/predict.py",
    "scripts/run_ai_review.py",
    "scripts/ai/acceptance_audit.py",
    "scripts/ai/ci_final_gate.py",
    "scripts/ai/collect_review_context.py",
    "scripts/ai/review_report_gate.py",
    "scripts/ai/run_ci_fallback_review.py",
    "prompts/ai/repo-aware-review.md",
    "docs/ai/review-result.schema.json",
    "pyproject.toml",
    "uv.lock",
    ".env",
    "configs/",
)


@dataclass(frozen=True)
class GateFinding:
    """review report gate の finding。

    ``blocking=False`` は判定不能（例: GitHub API 到達不能）を表す情報提供のみの
    finding で、PASS/PARTIAL/FAIL の判定には算入しない（ログ上で可視化するが
    ゲートは塞がない）。既定は ``True``（従来どおり判定に算入）。
    """

    id: str
    severity: str
    message: str
    report: str | None = None
    blocking: bool = True

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "severity": self.severity,
            "message": self.message,
            "blocking": self.blocking,
        }
        if self.report is not None:
            data["report"] = self.report
        return data


def _json_object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON に重複キーがあります: {key}")
        result[key] = value
    return result


def strict_json_loads(text: str) -> Any:
    """重複 object key を拒否して JSON を読む。"""
    return json.loads(text, object_pairs_hook=_json_object_without_duplicate_keys)


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        msg = f"git {' '.join(args)} failed: {stderr}"
        raise RuntimeError(msg)
    return result.stdout


def _git_success(args: list[str], cwd: Path) -> bool:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode == 0


def _safe_ref(ref: str) -> str:
    if ref.startswith("-") or any(char.isspace() for char in ref):
        msg = f"unsafe git ref: {ref!r}"
        raise ValueError(msg)
    return ref


def rev_parse(root: Path, ref: str) -> str:
    return _run_git(["rev-parse", "--verify", _safe_ref(ref)], root).strip()


def merge_base(root: Path, base_ref: str, head_ref: str) -> str:
    return _run_git(["merge-base", _safe_ref(base_ref), _safe_ref(head_ref)], root).strip()


def diff_fingerprint(root: Path, base_ref: str, head_ref: str) -> str:
    """レビュー証跡自身を除外した差分 fingerprint を返す。"""
    base = merge_base(root, base_ref, head_ref)
    diff = _run_git(["diff", "--binary", base, _safe_ref(head_ref)], root)
    chunks: list[str] = []
    keep = True
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if keep and current:
                chunks.extend(current)
            current = [line]
            paths = _diff_header_paths(line)
            keep = not paths or not all(_is_review_report_path(path) for path in paths)
            continue
        current.append(line)
    if keep and current:
        chunks.extend(current)
    return hashlib.sha256("".join(chunks).encode("utf-8")).hexdigest()


def changed_files_from_git(root: Path, base_ref: str | None, head_ref: str | None) -> list[str]:
    """差分ファイル一覧を取得する。"""
    if base_ref and head_ref:
        base = _run_git(["merge-base", _safe_ref(base_ref), _safe_ref(head_ref)], root).strip()
        output = _run_git(["diff", "--name-status", "-M", base, _safe_ref(head_ref)], root)
    else:
        output = _run_git(["diff", "--name-status", "-M", "HEAD"], root)
        untracked = _run_git(["ls-files", "--others", "--exclude-standard"], root)
        output = output + "\n" + untracked
    return paths_from_name_status(output)


def paths_from_name_status(output: str) -> list[str]:
    paths: set[str] = set()
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0].strip()
        if status.startswith(("R", "C")) and len(parts) >= 3:
            candidates = parts[1:3]
        elif len(parts) >= 2:
            candidates = [parts[1]]
        else:
            candidates = [parts[0]]
        for path in candidates:
            normalized = path.strip().replace("\\", "/")
            if normalized:
                paths.add(normalized)
    return sorted(paths)


def _strip_git_diff_path(path: str) -> str:
    normalized = path.strip().strip('"').replace("\\", "/")
    if normalized.startswith(("a/", "b/")):
        return normalized[2:]
    return normalized


def _diff_header_paths(line: str) -> list[str]:
    parts = line.strip().split()
    if len(parts) < 4:
        return []
    return [_strip_git_diff_path(parts[2]), _strip_git_diff_path(parts[3])]


def _is_review_report_path(path: str) -> bool:
    normalized = _strip_git_diff_path(path)
    if not normalized.startswith(REVIEW_REPORT_PREFIX) or not normalized.endswith(".json"):
        return False
    remainder = normalized[len(REVIEW_REPORT_PREFIX) :]
    return bool(remainder) and "/" not in remainder


def _changed_paths_between(root: Path, base_ref: str, head_ref: str) -> list[str]:
    output = _run_git(
        ["diff", "--no-renames", "--name-only", _safe_ref(base_ref), _safe_ref(head_ref)],
        root,
    )
    return [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]


def _commit_subject(root: Path, ref: str) -> str:
    return _run_git(["show", "-s", "--format=%s", _safe_ref(ref)], root).strip()


def _commit_subjects_between(root: Path, base_ref: str, head_ref: str) -> list[str]:
    output = _run_git(
        [
            "log",
            "--format=%s",
            f"{_safe_ref(base_ref)}..{_safe_ref(head_ref)}",
        ],
        root,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def _linear_commit_range_has_only_paths(
    root: Path,
    base_sha: str,
    head_sha: str,
    *,
    allowed_path: Callable[[str], bool],
    reject_anchor_marker: bool,
    require_nonempty_each_commit: bool,
    expected_commit_count: int | None = None,
) -> bool:
    """commit 列の各差分が許可 path だけで構成されることを確認する。"""
    if not _git_success(
        ["merge-base", "--is-ancestor", _safe_ref(base_sha), _safe_ref(head_sha)],
        root,
    ):
        return False
    try:
        commits = [
            line.strip()
            for line in _run_git(
                ["rev-list", "--reverse", f"{_safe_ref(base_sha)}..{_safe_ref(head_sha)}"],
                root,
            ).splitlines()
            if line.strip()
        ]
    except RuntimeError:
        return False
    if not commits:
        return False
    if expected_commit_count is not None and len(commits) != expected_commit_count:
        return False

    expected_parent = base_sha.lower()
    for commit_sha in commits:
        try:
            parents = (
                _run_git(
                    ["show", "-s", "--format=%P", _safe_ref(commit_sha)],
                    root,
                )
                .strip()
                .split()
            )
            changed_paths = _changed_paths_between(root, expected_parent, commit_sha)
            subject = _commit_subject(root, commit_sha)
        except RuntimeError:
            return False
        if parents != [expected_parent]:
            return False
        if require_nonempty_each_commit and not changed_paths:
            return False
        if any(not allowed_path(path) for path in changed_paths):
            return False
        if reject_anchor_marker and REBASE_ANCHOR_MARKER_RE.search(subject):
            return False
        expected_parent = commit_sha
    return expected_parent == head_sha.lower()


def _linear_commits_between(root: Path, base_sha: str, head_sha: str) -> list[str] | None:
    """base..head が単一親の直線履歴なら、古い順の commit 列を返す。"""
    if not _git_success(
        ["merge-base", "--is-ancestor", _safe_ref(base_sha), _safe_ref(head_sha)],
        root,
    ):
        return None
    try:
        commits = [
            line.strip()
            for line in _run_git(
                ["rev-list", "--reverse", f"{_safe_ref(base_sha)}..{_safe_ref(head_sha)}"],
                root,
            ).splitlines()
            if line.strip()
        ]
    except RuntimeError:
        return None
    if not commits:
        return None

    expected_parent = base_sha.lower()
    for commit_sha in commits:
        try:
            parents = (
                _run_git(
                    ["show", "-s", "--format=%P", _safe_ref(commit_sha)],
                    root,
                )
                .strip()
                .split()
            )
        except RuntimeError:
            return None
        if parents != [expected_parent]:
            return None
        expected_parent = commit_sha
    if expected_parent != head_sha.lower():
        return None
    return commits


def review_head_is_compatible(
    root: Path,
    report_head_sha: str,
    expected_head_sha: str,
) -> bool:
    if report_head_sha == expected_head_sha:
        return True
    if not FULL_SHA_RE.fullmatch(report_head_sha) or not FULL_SHA_RE.fullmatch(expected_head_sha):
        return False
    return _linear_commit_range_has_only_paths(
        root,
        report_head_sha,
        expected_head_sha,
        allowed_path=_is_review_report_path,
        reject_anchor_marker=False,
        require_nonempty_each_commit=False,
    )


def _parse_report_text(text: str, *, source: str) -> dict[str, Any]:
    """重複 key を拒否し、review report の root object を返す。"""
    raw = strict_json_loads(text)
    if not isinstance(raw, dict):
        msg = f"review report must be object: {source}"
        raise ValueError(msg)
    return cast("dict[str, Any]", raw)


def load_report(path: Path) -> dict[str, Any]:
    return _parse_report_text(path.read_text(encoding="utf-8"), source=str(path))


def load_report_from_git(
    root: Path,
    ref: str,
    report_path: str,
) -> dict[str, Any]:
    """検査対象 commit の通常ファイル blob から review report を読む。"""
    normalized = report_path.replace("\\", "/")
    if not _is_review_report_path(normalized):
        raise ValueError(
            f"review report は直下の docs/ai/reviews/*.json でなければなりません: {report_path}"
        )

    tree_output = _run_git(
        ["ls-tree", "-z", _safe_ref(ref), "--", normalized],
        root,
    )
    entries = [entry for entry in tree_output.split("\0") if entry]
    if len(entries) != 1:
        raise ValueError(f"検査対象 head に review report blob が1件ありません: {normalized}")
    metadata, separator, tree_path = entries[0].partition("\t")
    metadata_parts = metadata.split()
    if not separator or len(metadata_parts) != 3 or tree_path != normalized:
        raise ValueError(f"review report の git tree entry が不正です: {normalized}")
    mode, object_type, object_sha = metadata_parts
    if mode not in REVIEW_REPORT_GIT_MODES or object_type != "blob":
        raise ValueError(
            "review report は git mode 100644/100755 の通常 blob でなければなりません: "
            f"{normalized} mode={mode} type={object_type}"
        )

    text = _run_git(["cat-file", "blob", object_sha], root)
    return _parse_report_text(text, source=f"{ref}:{normalized}")


def _repo_relative_path(root: Path, path: Path) -> str:
    """symlink を解決せず、repo root からの lexical path を返す。"""
    try:
        return path.absolute().relative_to(root.absolute()).as_posix()
    except ValueError as exc:
        raise ValueError(f"report path is outside repository: {path}") from exc


def _plan_backlog_section_text(root: Path, plan_relpath: str = _PLAN_RELPATH) -> str:
    """docs/plan.md の `## Backlog` 系見出し配下のテキストを連結して返す。

    見出しが `## Backlog` で始まる節（能動 Backlog 節・
    `## Backlog（保留・人間タスクのみ）` 節の両方）を対象にする。plan.md が
    読めない場合は空文字を返す（呼び出し側で fail-close させる）。
    """
    try:
        text = (root / plan_relpath).read_text(encoding="utf-8")
    except OSError:
        return ""
    collected: list[str] = []
    capturing = False
    for line in text.splitlines():
        if line.startswith("## "):
            capturing = bool(_PLAN_BACKLOG_HEADING_RE.match(line))
        if capturing:
            collected.append(line)
    return "\n".join(collected)


def backlog_id_in_plan(root: Path, backlog_id: str, plan_relpath: str = _PLAN_RELPATH) -> bool:
    """backlog_id が docs/plan.md の Backlog 節に存在するかを判定する。

    ID 全体の一致のみを認める（前後が `-`/英数字で連続する部分一致は拒否し、
    短い ID が長い ID の部分文字列として誤ヒットしないようにする）。大小文字は
    区別しない（実運用で `Backlog-`/`BACKLOG-` 両方の表記が使われているため）。
    """
    section_text = _plan_backlog_section_text(root, plan_relpath)
    if not section_text:
        return False
    pattern = re.compile(
        r"(?<![0-9A-Za-z_-])" + re.escape(backlog_id) + r"(?![0-9A-Za-z_-])",
        re.IGNORECASE,
    )
    return bool(pattern.search(section_text))


def default_github_issue_state_fetcher(root: Path, issue_number: int, timeout: float = 10.0) -> str:
    """`gh issue view` で GitHub Issue の状態を取得する。

    戻り値は ``GITHUB_ISSUE_STATE_*`` のいずれか。到達不能、認証失敗、
    応答不正、Issue 不存在を区別する。
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--json", "state,number"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return GITHUB_ISSUE_STATE_UNREACHABLE

    if result.returncode == 0:
        try:
            payload = strict_json_loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return GITHUB_ISSUE_STATE_INVALID_RESPONSE
        if not isinstance(payload, dict):
            return GITHUB_ISSUE_STATE_INVALID_RESPONSE
        response_number = payload.get("number")
        if (
            not isinstance(response_number, int)
            or isinstance(response_number, bool)
            or response_number != issue_number
        ):
            return GITHUB_ISSUE_STATE_INVALID_RESPONSE
        raw_state = payload.get("state")
        if not isinstance(raw_state, str):
            return GITHUB_ISSUE_STATE_INVALID_RESPONSE
        state = raw_state.strip().upper()
        if state == "OPEN":
            return GITHUB_ISSUE_STATE_OPEN
        if state == "CLOSED":
            return GITHUB_ISSUE_STATE_CLOSED
        return GITHUB_ISSUE_STATE_INVALID_RESPONSE

    stderr = (result.stderr or "").lower()
    if "could not resolve to an issue" in stderr or "not found" in stderr:
        return GITHUB_ISSUE_STATE_MISSING
    authentication_markers = (
        "authentication",
        "authenticate",
        "bad credentials",
        "http 401",
        "http 403",
        "requires authorization",
        "requires authentication",
    )
    if any(marker in stderr for marker in authentication_markers):
        return GITHUB_ISSUE_STATE_AUTHENTICATION_FAILED
    offline_markers = (
        "could not resolve host",
        "connection refused",
        "connection reset",
        "network is unreachable",
        "network error",
        "timed out",
        "timeout",
        "tls handshake timeout",
    )
    if any(marker in stderr for marker in offline_markers):
        return GITHUB_ISSUE_STATE_UNREACHABLE
    return GITHUB_ISSUE_STATE_INVALID_RESPONSE


def resolve_backlog_id_status(
    root: Path,
    backlog_id: str,
    *,
    issue_state_fetcher: GithubIssueStateFetcher | None = None,
) -> tuple[str, str]:
    """backlog_id の実在検証結果を ``(status, detail)`` で返す。

    status は ``BACKLOG_ID_STATUS_*`` のいずれか。
    """
    value = backlog_id.strip()
    issue_match = _ISSUE_ID_RE.match(value)
    if issue_match:
        fetcher = issue_state_fetcher or default_github_issue_state_fetcher
        state = fetcher(root, int(issue_match.group("number")))
        if state == GITHUB_ISSUE_STATE_OPEN:
            return BACKLOG_ID_STATUS_VERIFIED, f"GitHub Issue {value} は open です。"
        if state == GITHUB_ISSUE_STATE_CLOSED:
            return (
                BACKLOG_ID_STATUS_MISSING,
                f"GitHub Issue {value} は closed です（繰延の追跡先として無効）。",
            )
        if state == GITHUB_ISSUE_STATE_MISSING:
            return BACKLOG_ID_STATUS_MISSING, f"GitHub Issue {value} は存在しません。"
        if state == GITHUB_ISSUE_STATE_UNREACHABLE:
            return (
                BACKLOG_ID_STATUS_UNREACHABLE,
                f"GitHub API に到達できず {value} の実在を判定できません。",
            )
        if state == GITHUB_ISSUE_STATE_AUTHENTICATION_FAILED:
            detail = f"GitHub Issue {value} の照会に必要な認証がありません。"
        else:
            detail = f"GitHub Issue {value} の照会結果が不正です。"
        return BACKLOG_ID_STATUS_LOOKUP_FAILED, detail

    if value.lower().startswith("backlog-"):
        if backlog_id_in_plan(root, value):
            return (
                BACKLOG_ID_STATUS_VERIFIED,
                f"{value} は docs/plan.md の Backlog 節に存在します。",
            )
        return (
            BACKLOG_ID_STATUS_MISSING,
            f"{value} は docs/plan.md の Backlog 節に見つかりません（宙吊り参照）。",
        )

    return (
        BACKLOG_ID_STATUS_UNSUPPORTED_FORMAT,
        f"{value} は `Backlog-` 始まりまたは `#<数字>` のいずれの形式にも一致しません。",
    )


def is_low_risk_diff_only(changed_files: list[str]) -> bool:
    if not changed_files:
        return False
    for path in changed_files:
        normalized = path.replace("\\", "/")
        if normalized.startswith(LOW_RISK_PREFIXES):
            continue
        if normalized.endswith(LOW_RISK_SUFFIXES):
            continue
        return False
    return True


def requires_full_repo_agentic(changed_files: list[str]) -> bool:
    for path in changed_files:
        normalized = path.replace("\\", "/")
        if any(normalized.startswith(pattern) for pattern in FULL_REPO_REQUIRED_PATTERNS):
            return True
        if normalized in FULL_REPO_REQUIRED_PATTERNS:
            return True
    return False


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [item.strip().replace("\\", "/") for item in value if item.strip()]


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and value > 0


def _finding(
    *,
    finding_id: str,
    report_name: str,
    message: str,
    severity: str = "must",
    blocking: bool = True,
) -> GateFinding:
    return GateFinding(
        id=finding_id,
        severity=severity,
        report=report_name,
        message=message,
        blocking=blocking,
    )


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _trigger_evidence_ref(value: object) -> str | None:
    """trigger evidence の安定参照 ID を返す。"""
    if not isinstance(value, str):
        return None
    match = TRIGGER_EVIDENCE_RE.fullmatch(value.strip())
    return match.group("stable_ref") if match else None


def _is_extension_evidence_only_path(path: str) -> bool:
    """延長ラウンドの material correction に数えない証跡専用 path かを返す。"""
    normalized = path.strip().replace("\\", "/")
    return (
        normalized in EXTENSION_EVIDENCE_ONLY_EXACT_PATHS
        or normalized.startswith(EXTENSION_EVIDENCE_ONLY_PREFIXES)
        or _is_review_report_path(normalized)
    )


def _ledger_evidence_gap_is_compatible(
    root: Path,
    corrected_head_sha: str,
    reviewed_head_sha: str,
) -> bool:
    """material correction 後の ledger-only 証跡 commit 列を許容する。

    corrected head 自身の SHA は同じ commit の ledger に書けないため、実質修正 C の
    後に C を記録する execution-ledger commit E を置き、E をレビュー対象にできる。
    terminal anchor や ledger 以外の変更が混ざる gap は受理しない。
    """
    return _linear_commit_range_has_only_paths(
        root,
        corrected_head_sha,
        reviewed_head_sha,
        allowed_path=lambda path: path == ROUND_EXTENSION_LEDGER_PATH,
        reject_anchor_marker=True,
        require_nonempty_each_commit=True,
        expected_commit_count=1,
    )


def _is_post_review_evidence_only_path(path: str) -> bool:
    """E の後から次の実質修正までに置ける review 証跡 path かを返す。"""
    return _is_review_report_path(path)


def _extension_evidence_commit(
    root: Path,
    corrected_head_sha: str,
    chain_head_sha: str,
) -> str | None:
    """C→E→R* を検証し、唯一の ledger E の SHA を返す。

    fallback workflow はレビュー後に ``docs/ai/reviews/*.json`` 専用 commit R を
    push できる。ただし次ラウンドの prior head は E に固定し、R は E→次 C の
    gap にだけ置く。C の直後の E は一意でなければならない。
    """
    commits = _linear_commits_between(root, corrected_head_sha, chain_head_sha)
    if not commits:
        return None
    evidence_commit = commits[0]
    expected_parent = corrected_head_sha.lower()
    for index, commit_sha in enumerate(commits):
        try:
            changed_paths = _changed_paths_between(root, expected_parent, commit_sha)
            subject = _commit_subject(root, commit_sha)
        except RuntimeError:
            return None
        if not changed_paths or REBASE_ANCHOR_MARKER_RE.search(subject):
            return None
        if index == 0:
            if any(path != ROUND_EXTENSION_LEDGER_PATH for path in changed_paths):
                return None
        elif any(not _is_post_review_evidence_only_path(path) for path in changed_paths):
            return None
        expected_parent = commit_sha
    return evidence_commit


def _material_correction_commit_is_compatible(
    root: Path,
    prior_reviewed_head_sha: str,
    corrected_head_sha: str,
) -> tuple[bool, list[str], list[str]]:
    """P→R*→C が review 証跡 0 件以上と最後の実質修正 C だけかを返す。"""
    commits = _linear_commits_between(root, prior_reviewed_head_sha, corrected_head_sha)
    if not commits or commits[-1].lower() != corrected_head_sha.lower():
        return False, [], []

    expected_parent = prior_reviewed_head_sha.lower()
    correction_paths: list[str] = []
    subjects: list[str] = []
    for index, commit_sha in enumerate(commits):
        try:
            changed_paths = _changed_paths_between(root, expected_parent, commit_sha)
            subject = _commit_subject(root, commit_sha)
        except RuntimeError:
            return False, [], []
        if not changed_paths or REBASE_ANCHOR_MARKER_RE.search(subject):
            return False, [], []
        subjects.append(subject)
        if index + 1 < len(commits):
            if any(not _is_post_review_evidence_only_path(path) for path in changed_paths):
                return False, [], []
        else:
            correction_paths = changed_paths
        expected_parent = commit_sha
    return True, correction_paths, subjects


def _normalized_round_extension_evidence(raw: dict[str, Any]) -> dict[str, Any]:
    """8 項目だけからなる延長証跡を比較用に正規化する。"""
    expected_fields = set(REQUIRED_ROUND_EXTENSION_FIELDS)
    if set(raw) != expected_fields:
        missing = sorted(expected_fields - set(raw))
        extra = sorted(set(raw) - expected_fields)
        details = []
        if missing:
            details.append("不足=" + ",".join(missing))
        if extra:
            details.append("余分=" + ",".join(extra))
        raise ValueError("延長証跡のフィールドが8項目と一致しません: " + "; ".join(details))
    normalized = {field: raw[field] for field in REQUIRED_ROUND_EXTENSION_FIELDS}
    for sha_field in ("prior_reviewed_head_sha", "corrected_head_sha"):
        sha = normalized[sha_field]
        if isinstance(sha, str):
            normalized[sha_field] = sha.lower()
    commands = normalized.get("verification_commands")
    if isinstance(commands, list):
        normalized_commands = []
        for command in commands:
            if not isinstance(command, dict):
                raise ValueError("verification_commands の要素は object である必要があります")
            if set(command) != set(REQUIRED_VERIFICATION_COMMAND_FIELDS):
                raise ValueError(
                    "verification_commands の要素は command / exit_code / summary "
                    "だけを持つ必要があります"
                )
            normalized_commands.append(
                {field: command[field] for field in REQUIRED_VERIFICATION_COMMAND_FIELDS}
            )
        normalized["verification_commands"] = normalized_commands
    return normalized


def _round_extension_ledger_blocks_at(
    root: Path,
    ref: str,
) -> list[dict[str, Any]]:
    """指定 commit の execution ledger から機械可読の延長証跡列を読む。"""
    object_ref = f"{_safe_ref(ref)}:{ROUND_EXTENSION_LEDGER_PATH}"
    if not _git_success(["cat-file", "-e", object_ref], root):
        return []
    content = _run_git(["show", object_ref], root)
    blocks: list[dict[str, Any]] = []
    for match in ROUND_EXTENSION_LEDGER_BLOCK_RE.finditer(content):
        try:
            raw = strict_json_loads(match.group("body"))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"execution ledger の延長証跡 JSON が不正です: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("execution ledger の延長証跡 JSON は object である必要があります")
        blocks.append(_normalized_round_extension_evidence(cast("dict[str, Any]", raw)))
    return blocks


def _extension_ledger_evidence_issue(
    root: Path,
    *,
    prior_reviewed_head_sha: str,
    corrected_head_sha: str,
    evidence_head_sha: str,
    extension: dict[str, Any],
) -> str:
    """C→E と ledger block の追記契約を検証し、違反理由を返す。"""
    evidence_commit_sha = _extension_evidence_commit(
        root,
        corrected_head_sha,
        evidence_head_sha,
    )
    if evidence_commit_sha is None:
        return (
            "corrected head の直後に execution-ledger だけを変更する"
            "非空の evidence commit を1件置き、その後は review 証跡専用 commit "
            "だけを置く必要があります。"
        )
    try:
        prior_blocks = _round_extension_ledger_blocks_at(root, prior_reviewed_head_sha)
        corrected_blocks = _round_extension_ledger_blocks_at(root, corrected_head_sha)
        evidence_blocks = _round_extension_ledger_blocks_at(root, evidence_commit_sha)
        expected_block = _normalized_round_extension_evidence(extension)
    except (RuntimeError, ValueError) as exc:
        return str(exc)
    if corrected_blocks != prior_blocks:
        return "material correction commit で既存の延長証跡 block を変更してはいけません。"
    if (
        len(evidence_blocks) != len(corrected_blocks) + 1
        or evidence_blocks[:-1] != corrected_blocks
    ):
        return "evidence commit は既存 block を保ったまま延長証跡を1件だけ末尾追加してください。"
    if evidence_blocks[-1] != expected_block:
        return "execution ledger の追加 block が review JSON の8項目と一致しません。"
    return ""


def _authorization_ref_exists_at(root: Path, ref: str, authorization_ref: object) -> bool:
    """authorization_ref が evidence head の decision ledger に存在するかを返す。"""
    if not isinstance(authorization_ref, str) or not DECISION_REF_RE.fullmatch(authorization_ref):
        return False
    object_ref = f"{_safe_ref(ref)}:{ROUND_EXTENSION_DECISION_LEDGER_PATH}"
    if not _git_success(["cat-file", "-e", object_ref], root):
        return False
    content = _run_git(["show", object_ref], root)
    heading = re.compile(
        rf"^##[ \t]+{re.escape(authorization_ref)}:",
        re.MULTILINE,
    )
    return heading.search(content) is not None


def _valid_verification_commands(value: object) -> bool:
    """延長ラウンドの検証コマンド証跡が空でなく、すべて成功しているかを返す。"""
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if set(item) != set(REQUIRED_VERIFICATION_COMMAND_FIELDS):
            return False
        if not _nonempty_text(item.get("command")):
            return False
        exit_code = item.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != 0:
            return False
        if not _nonempty_text(item.get("summary")):
            return False
    return True


def validate_review_round_extensions(
    *,
    report: dict[str, Any],
    report_name: str,
    root: Path,
    enforce_extension_evidence: bool,
) -> list[GateFinding]:
    """Round 4 以降の適格性、進捗、anchor 前実施を検証する。

    過去の証跡には ``review_round`` が 4 以上でも延長証跡を持たないものがある。
    また rebase merge 後は PR 内 SHA が main から到達不能になる。現在 PR を検査する
    ``--changed-only`` 相当の経路では構造と commit graph を完全検証し、全履歴走査では
    明示された ``round_extensions`` の構造だけを検証する。
    """
    findings: list[GateFinding] = []
    raw_round = report.get("review_round")
    raw_extensions = report.get("round_extensions")

    if raw_round is None:
        if enforce_extension_evidence:
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-001",
                    report_name=report_name,
                    message="現在 PR のレビュー証跡には review_round が必要です。",
                )
            )
        elif raw_extensions not in (None, []):
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-001",
                    report_name=report_name,
                    message="round_extensions には正の整数 review_round が必要です。",
                )
            )
        return findings
    if isinstance(raw_round, bool) or not isinstance(raw_round, int) or raw_round < 1:
        findings.append(
            _finding(
                finding_id="RRG-ROUND-001",
                report_name=report_name,
                message="review_round は正の整数である必要があります。",
            )
        )
        return findings
    review_round = raw_round

    if enforce_extension_evidence:
        base_sha_for_ledger = report.get("base_sha")
        head_sha_for_ledger = report.get("head_sha")
        if (
            isinstance(base_sha_for_ledger, str)
            and FULL_SHA_RE.fullmatch(base_sha_for_ledger)
            and isinstance(head_sha_for_ledger, str)
            and FULL_SHA_RE.fullmatch(head_sha_for_ledger)
        ):
            try:
                base_blocks = _round_extension_ledger_blocks_at(root, base_sha_for_ledger)
                head_blocks = _round_extension_ledger_blocks_at(root, head_sha_for_ledger)
            except (RuntimeError, ValueError) as exc:
                findings.append(
                    _finding(
                        finding_id="RRG-ROUND-EXT-017",
                        report_name=report_name,
                        message=f"現在 PR の execution-ledger 延長証跡を読めません: {exc}",
                    )
                )
            else:
                if head_blocks[: len(base_blocks)] != base_blocks:
                    findings.append(
                        _finding(
                            finding_id="RRG-ROUND-EXT-017",
                            report_name=report_name,
                            message=(
                                "現在 PR で既存の execution-ledger 延長証跡を"
                                "変更または削除してはいけません。"
                            ),
                        )
                    )
                else:
                    current_pr_blocks = head_blocks[len(base_blocks) :]
                    ledger_round = DEFAULT_REVIEW_ROUNDS + len(current_pr_blocks)
                    if current_pr_blocks and review_round != ledger_round:
                        findings.append(
                            _finding(
                                finding_id="RRG-ROUND-EXT-017",
                                report_name=report_name,
                                message=(
                                    "review_round が現在 PR で追加した execution-ledger "
                                    f"延長証跡件数と一致しません（期待 Round {ledger_round}）。"
                                ),
                            )
                        )

    if review_round <= DEFAULT_REVIEW_ROUNDS:
        if raw_extensions not in (None, []):
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-001",
                    report_name=report_name,
                    message=(
                        f"Round {DEFAULT_REVIEW_ROUNDS} 以下では "
                        "round_extensions を記録できません。"
                    ),
                )
            )
        return findings

    if not enforce_extension_evidence and raw_extensions is None:
        # 2026-07-30 より前の履歴証跡を全件走査する経路だけの互換措置。
        return findings
    if not isinstance(raw_extensions, list) or not raw_extensions:
        findings.append(
            _finding(
                finding_id="RRG-ROUND-EXT-001",
                report_name=report_name,
                message=(
                    f"Round {EXTENSION_START_ROUND} 以降には空でない round_extensions が必要です。"
                ),
            )
        )
        return findings

    base_sha = report.get("base_sha")
    if not isinstance(base_sha, str) or not FULL_SHA_RE.fullmatch(base_sha):
        findings.append(
            _finding(
                finding_id="RRG-ROUND-EXT-014",
                report_name=report_name,
                message="Round 4 以降の証跡には検証済みの 40 桁 base_sha が必要です。",
            )
        )
        base_sha = None

    expected_extension_count = review_round - DEFAULT_REVIEW_ROUNDS
    if len(raw_extensions) != expected_extension_count:
        findings.append(
            _finding(
                finding_id="RRG-ROUND-EXT-002",
                report_name=report_name,
                message=(
                    "round_extensions は Round 4 から review_round までを"
                    " 1 件ずつ連続して記録する必要があります。"
                ),
            )
        )

    trigger_counts: dict[str, int] = {}
    for raw_extension in raw_extensions:
        if not isinstance(raw_extension, dict):
            continue
        trigger_kind = raw_extension.get("trigger_kind")
        trigger_ref = _trigger_evidence_ref(raw_extension.get("trigger_evidence"))
        if not isinstance(trigger_kind, str) or trigger_ref is None:
            continue
        trigger_counts[trigger_ref] = trigger_counts.get(trigger_ref, 0) + 1
    if any(count > 3 for count in trigger_counts.values()):
        findings.append(
            _finding(
                finding_id="RRG-ROUND-EXT-015",
                report_name=report_name,
                message=(
                    "同じ stable trigger ID による延長は3回までです。"
                    "4回目以降は停止して successor PR または人間判断へ切り替えてください。"
                ),
            )
        )

    previous_corrected_head: str | None = None
    final_corrected_head: str | None = None
    valid_extensions: list[tuple[str, str, dict[str, Any]]] = []
    for index, raw_extension in enumerate(raw_extensions):
        expected_round = EXTENSION_START_ROUND + index
        if not isinstance(raw_extension, dict):
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-003",
                    report_name=report_name,
                    message=f"round_extensions[{index}] は object である必要があります。",
                )
            )
            continue
        extension = cast("dict[str, Any]", raw_extension)
        if contains_probable_secret(extension):
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-016",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}] に秘密情報らしい値があります。"
                        "公開証跡へ保存せず、値を失効・除去してください。"
                    ),
                )
            )
        unexpected_fields = sorted(set(extension) - set(REQUIRED_ROUND_EXTENSION_FIELDS))
        missing_fields = [
            field for field in REQUIRED_ROUND_EXTENSION_FIELDS if field not in extension
        ]
        if missing_fields or unexpected_fields:
            details = []
            if missing_fields:
                details.append("不足: " + ", ".join(missing_fields))
            if unexpected_fields:
                details.append("余分: " + ", ".join(unexpected_fields))
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-003",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}] は定義済み8項目だけを持つ必要があります（"
                        + " / ".join(details)
                        + "）。"
                    ),
                )
            )

        extension_round = extension.get("round")
        if (
            isinstance(extension_round, bool)
            or not isinstance(extension_round, int)
            or extension_round != expected_round
        ):
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-002",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}].round は {expected_round} "
                        "である必要があります。"
                    ),
                )
            )

        trigger_kind = extension.get("trigger_kind")
        if not isinstance(trigger_kind, str) or trigger_kind not in ALLOWED_EXTENSION_TRIGGER_KINDS:
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-004",
                    report_name=report_name,
                    message=(f"round_extensions[{index}].trigger_kind は適格理由ではありません。"),
                )
            )
        for field in (
            "trigger_evidence",
            "authorization_ref",
            "progress_summary",
        ):
            if not _nonempty_text(extension.get(field)):
                findings.append(
                    _finding(
                        finding_id="RRG-ROUND-EXT-003",
                        report_name=report_name,
                        message=f"round_extensions[{index}].{field} が空です。",
                    )
                )
        if _trigger_evidence_ref(extension.get("trigger_evidence")) is None:
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-003",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}].trigger_evidence は "
                        "`STABLE-ID: 具体的根拠` 形式である必要があります。"
                    ),
                )
            )
        authorization_ref = extension.get("authorization_ref")
        if not isinstance(authorization_ref, str) or not DECISION_REF_RE.fullmatch(
            authorization_ref
        ):
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-013",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}].authorization_ref は "
                        "DEC-YYYYMMDD-NNN 形式である必要があります。"
                    ),
                )
            )
        if not _valid_verification_commands(extension.get("verification_commands")):
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-005",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}].verification_commands は"
                        " command / exit_code=0 / summary を持つ"
                        "空でない object list である必要があります。"
                    ),
                )
            )

        prior_head = extension.get("prior_reviewed_head_sha")
        corrected_head = extension.get("corrected_head_sha")
        if not isinstance(prior_head, str) or not FULL_SHA_RE.fullmatch(prior_head):
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-006",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}].prior_reviewed_head_sha は"
                        " 40 桁 SHA である必要があります。"
                    ),
                )
            )
            continue
        if not isinstance(corrected_head, str) or not FULL_SHA_RE.fullmatch(corrected_head):
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-006",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}].corrected_head_sha は"
                        " 40 桁 SHA である必要があります。"
                    ),
                )
            )
            continue

        # 全履歴走査では rebase merge 後に PR 内 SHA が到達不能になる。構造検証は
        # 維持し、commit graph と ledger blob の検証は現在 PR の changed-only 経路
        # および provider 実行前検証だけで行う。
        if not enforce_extension_evidence:
            continue

        if previous_corrected_head is not None:
            prior_evidence_commit = _extension_evidence_commit(
                root,
                previous_corrected_head,
                prior_head,
            )
            if prior_evidence_commit is None or prior_evidence_commit.lower() != prior_head.lower():
                findings.append(
                    _finding(
                        finding_id="RRG-ROUND-EXT-007",
                        report_name=report_name,
                        message=(
                            f"round_extensions[{index}] の prior reviewed head は"
                            "直前延長ラウンドの corrected head の直後に置いた "
                            "ledger evidence commit と一致する必要があります。"
                        ),
                    )
                )
        previous_corrected_head = corrected_head
        final_corrected_head = corrected_head

        correction_is_compatible, changed_paths, correction_subjects = (
            _material_correction_commit_is_compatible(
                root,
                prior_head,
                corrected_head,
            )
        )
        if prior_head == corrected_head or not correction_is_compatible:
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-008",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}] の corrected head は"
                        " prior reviewed head 後の review 証跡専用 commit 0 件以上と、"
                        "最後の非空 material correction 1 commit からなる必要があります。"
                    ),
                )
            )
            continue

        try:
            prior_subject = _commit_subject(root, prior_head)
        except RuntimeError as exc:
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-008",
                    report_name=report_name,
                    message=f"延長ラウンドの commit 差分を検証できません: {exc}",
                )
            )
            continue

        material_paths = [
            path for path in changed_paths if not _is_extension_evidence_only_path(path)
        ]
        if not material_paths:
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-009",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}] に review 証跡以外の"
                        " material correction がありません。"
                    ),
                )
            )
        if REBASE_ANCHOR_MARKER_RE.search(prior_subject) or any(
            REBASE_ANCHOR_MARKER_RE.search(subject) for subject in correction_subjects
        ):
            findings.append(
                _finding(
                    finding_id="RRG-ROUND-EXT-010",
                    report_name=report_name,
                    message=(
                        f"round_extensions[{index}] は terminal anchor 作成後に"
                        "開始されています。anchor 後の変更は successor PR が必要です。"
                    ),
                )
            )
        valid_extensions.append((prior_head, corrected_head, extension))

    report_head_sha = report.get("head_sha")
    valid_report_head = isinstance(report_head_sha, str) and bool(
        FULL_SHA_RE.fullmatch(report_head_sha)
    )
    if (
        enforce_extension_evidence
        and final_corrected_head is not None
        and (
            not valid_report_head
            or not _ledger_evidence_gap_is_compatible(
                root,
                final_corrected_head,
                cast("str", report_head_sha),
            )
        )
    ):
        findings.append(
            _finding(
                finding_id="RRG-ROUND-EXT-011",
                report_name=report_name,
                message=(
                    "report.head_sha は40桁 SHAで、最後の "
                    "round_extensions.corrected_head_sha の直後に置いた"
                    " ledger-only evidence commit である必要があります。"
                ),
            )
        )
    if (
        enforce_extension_evidence
        and valid_report_head
        and len(valid_extensions) == len(raw_extensions)
    ):
        report_head_sha = cast("str", report_head_sha)
        for index, (prior_head, corrected_head, extension) in enumerate(valid_extensions):
            if index + 1 < len(valid_extensions):
                evidence_head = valid_extensions[index + 1][0]
            else:
                evidence_head = report_head_sha
            evidence_issue = _extension_ledger_evidence_issue(
                root,
                prior_reviewed_head_sha=prior_head,
                corrected_head_sha=corrected_head,
                evidence_head_sha=evidence_head,
                extension=extension,
            )
            if evidence_issue:
                findings.append(
                    _finding(
                        finding_id="RRG-ROUND-EXT-012",
                        report_name=report_name,
                        message=(
                            f"round_extensions[{index}] の ledger 証跡が不正です: {evidence_issue}"
                        ),
                    )
                )
            if not _authorization_ref_exists_at(
                root,
                evidence_head,
                extension.get("authorization_ref"),
            ):
                findings.append(
                    _finding(
                        finding_id="RRG-ROUND-EXT-013",
                        report_name=report_name,
                        message=(
                            f"round_extensions[{index}].authorization_ref は "
                            "DEC-YYYYMMDD-NNN 形式で、evidence head の "
                            "docs/ai/decision-ledger.md に存在する必要があります。"
                        ),
                    )
                )
        first_prior_head = valid_extensions[0][0]
        if base_sha is not None:
            if not _git_success(
                [
                    "merge-base",
                    "--is-ancestor",
                    _safe_ref(base_sha),
                    _safe_ref(first_prior_head),
                ],
                root,
            ):
                findings.append(
                    _finding(
                        finding_id="RRG-ROUND-EXT-014",
                        report_name=report_name,
                        message=(
                            "base_sha は最初の prior reviewed head の祖先である必要があります。"
                        ),
                    )
                )
            else:
                try:
                    prior_subjects = _commit_subjects_between(
                        root,
                        base_sha,
                        first_prior_head,
                    )
                except RuntimeError as exc:
                    findings.append(
                        _finding(
                            finding_id="RRG-ROUND-EXT-014",
                            report_name=report_name,
                            message=f"base から prior head までを検証できません: {exc}",
                        )
                    )
                else:
                    if any(REBASE_ANCHOR_MARKER_RE.search(subject) for subject in prior_subjects):
                        findings.append(
                            _finding(
                                finding_id="RRG-ROUND-EXT-010",
                                report_name=report_name,
                                message=(
                                    "terminal anchor 後に作られた prior reviewed head から"
                                    "延長を開始できません。successor PR が必要です。"
                                ),
                            )
                        )
    return findings


def validate_repository_context(
    report: dict[str, Any],
    report_name: str,
    actual_changed_files: list[str] | None = None,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    raw_mode = report.get("repository_context_mode")
    if not isinstance(raw_mode, str) or raw_mode not in VALID_CONTEXT_MODES:
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-001",
                report_name=report_name,
                message="repository_context_mode が不正または空です。",
            )
        )
        return findings
    mode = raw_mode

    changed_files = _string_list(report.get("changed_files"))
    if changed_files is None or not changed_files:
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-002",
                report_name=report_name,
                message="changed_files は空でない string list である必要があります。",
            )
        )
        changed_files = []
    actual_files = sorted(
        {
            normalized
            for path in (actual_changed_files or [])
            if (normalized := path.strip().replace("\\", "/"))
            and not _is_review_report_path(normalized)
        }
    )
    if actual_files and changed_files:
        missing_actual_files = sorted(set(actual_files) - set(changed_files))
        if missing_actual_files:
            findings.append(
                _finding(
                    finding_id="RRG-CONTEXT-026",
                    report_name=report_name,
                    message=(
                        "changed_files が実際の PR 差分を網羅していません: "
                        + ", ".join(missing_actual_files)
                    ),
                )
            )
    policy_changed_files = actual_files or changed_files

    diff_only_reason = report.get("diff_only_reason")
    if mode == "diff_only":
        if not isinstance(diff_only_reason, str) or not diff_only_reason.strip():
            findings.append(
                _finding(
                    finding_id="RRG-CONTEXT-003",
                    report_name=report_name,
                    message="diff_only には diff_only_reason が必要です。",
                )
            )
        if not is_low_risk_diff_only(policy_changed_files):
            findings.append(
                _finding(
                    finding_id="RRG-CONTEXT-004",
                    report_name=report_name,
                    message="diff_only は docs/data/typo 等の低リスク変更だけ許可されます。",
                )
            )

    diff_only_allowed = (
        mode == "diff_only"
        and is_low_risk_diff_only(policy_changed_files)
        and not requires_full_repo_agentic(policy_changed_files)
        and isinstance(diff_only_reason, str)
        and bool(diff_only_reason.strip())
    )
    required_mode = (
        "full_repo_agentic"
        if requires_full_repo_agentic(policy_changed_files)
        else "related_context"
    )
    if (
        policy_changed_files
        and not diff_only_allowed
        and CONTEXT_MODE_RANK[mode] < CONTEXT_MODE_RANK[required_mode]
    ):
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-005",
                report_name=report_name,
                message=f"この変更には repository_context_mode={required_mode} 以上が必要です。",
            )
        )

    scanned_paths = _string_list(report.get("scanned_paths"))
    if scanned_paths is None:
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-006",
                report_name=report_name,
                message="scanned_paths は string list である必要があります。",
            )
        )
        scanned_paths = []
    missing_scans = sorted(set(policy_changed_files) - set(scanned_paths))
    if missing_scans:
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-007",
                report_name=report_name,
                message="scanned_paths に changed_files が含まれていません: "
                + ", ".join(missing_scans),
            )
        )

    raw_related_files = report.get("related_files")
    if not isinstance(raw_related_files, list) or not all(
        isinstance(item, dict) for item in raw_related_files
    ):
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-008",
                report_name=report_name,
                message="related_files は object list である必要があります。",
            )
        )
        related_files: list[dict[str, Any]] = []
    else:
        related_files = cast("list[dict[str, Any]]", raw_related_files)
        for index, item in enumerate(related_files, start=1):
            if not isinstance(item.get("path"), str) or not item["path"].strip():
                findings.append(
                    _finding(
                        finding_id="RRG-CONTEXT-009",
                        report_name=report_name,
                        message=f"related_files[{index}].path が空です。",
                    )
                )
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                findings.append(
                    _finding(
                        finding_id="RRG-CONTEXT-009",
                        report_name=report_name,
                        message=f"related_files[{index}].reason が空です。",
                    )
                )

    raw_commands = report.get("commands_run")
    if not isinstance(raw_commands, list) or not all(
        isinstance(item, dict) for item in raw_commands
    ):
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-010",
                report_name=report_name,
                message="commands_run は object list である必要があります。",
            )
        )
        commands_run: list[dict[str, Any]] = []
    else:
        commands_run = cast("list[dict[str, Any]]", raw_commands)
        if mode != "diff_only" and not commands_run:
            findings.append(
                _finding(
                    finding_id="RRG-CONTEXT-011",
                    report_name=report_name,
                    message="related_context 以上では commands_run が必要です。",
                )
            )
        for index, item in enumerate(commands_run, start=1):
            if not isinstance(item.get("command"), str) or not item["command"].strip():
                findings.append(
                    _finding(
                        finding_id="RRG-CONTEXT-012",
                        report_name=report_name,
                        message=f"commands_run[{index}].command が空です。",
                    )
                )
            if not isinstance(item.get("exit_code"), int):
                findings.append(
                    _finding(
                        finding_id="RRG-CONTEXT-012",
                        report_name=report_name,
                        message=f"commands_run[{index}].exit_code が int ではありません。",
                    )
                )
            if not isinstance(item.get("summary"), str) or not item["summary"].strip():
                findings.append(
                    _finding(
                        finding_id="RRG-CONTEXT-012",
                        report_name=report_name,
                        message=f"commands_run[{index}].summary が空です。",
                    )
                )

    if mode == "full_repo_agentic":
        related_paths = {
            str(item.get("path", "")).replace("\\", "/")
            for item in related_files
            if isinstance(item.get("path"), str)
        }
        outside_changed = sorted(related_paths - set(policy_changed_files))
        if not outside_changed:
            findings.append(
                _finding(
                    finding_id="RRG-CONTEXT-024",
                    report_name=report_name,
                    message=(
                        "full_repo_agentic には changed_files 外の関連ファイル探索証跡が必要です。"
                    ),
                )
            )
        command_texts = [
            str(item.get("command", "")) for item in commands_run if isinstance(item, dict)
        ]
        has_repo_search = any(
            command.startswith("git ls-files") or command.startswith("rg ")
            for command in command_texts
        )
        if not has_repo_search:
            findings.append(
                _finding(
                    finding_id="RRG-CONTEXT-025",
                    report_name=report_name,
                    message="full_repo_agentic には git ls-files または rg の探索証跡が必要です。",
                )
            )

    fingerprint = report.get("context_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-013",
                report_name=report_name,
                message="context_fingerprint が空です。",
            )
        )

    raw_budget = report.get("context_budget")
    if not isinstance(raw_budget, dict):
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-014",
                report_name=report_name,
                message="context_budget は object である必要があります。",
            )
        )
        budget: dict[str, int] = DEFAULT_CONTEXT_BUDGET.copy()
    else:
        budget = {}
        for field in REQUIRED_CONTEXT_BUDGET_FIELDS:
            value = raw_budget.get(field)
            if not _positive_int(value):
                findings.append(
                    _finding(
                        finding_id="RRG-CONTEXT-015",
                        report_name=report_name,
                        message=f"context_budget.{field} は正の int である必要があります。",
                    )
                )
                budget[field] = DEFAULT_CONTEXT_BUDGET[field]
            else:
                budget[field] = cast("int", value)

    override_reason = report.get("context_budget_override_reason")
    has_budget_override = any(
        budget[field] > DEFAULT_CONTEXT_BUDGET[field] for field in REQUIRED_CONTEXT_BUDGET_FIELDS
    )
    if has_budget_override and (
        not isinstance(override_reason, str) or not override_reason.strip()
    ):
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-016",
                report_name=report_name,
                message="context budget 拡張には context_budget_override_reason が必要です。",
            )
        )

    if len(related_files) > budget["max_related_files"]:
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-017",
                report_name=report_name,
                message="related_files が context_budget.max_related_files を超えています。",
            )
        )
    if len(commands_run) > budget["max_context_commands"]:
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-018",
                report_name=report_name,
                message="commands_run が context_budget.max_context_commands を超えています。",
            )
        )

    excerpt_total = 0
    for item in related_files:
        excerpt = item.get("excerpt")
        if not isinstance(excerpt, str):
            continue
        excerpt_total += len(excerpt)
        if len(excerpt) > budget["max_file_excerpt_chars"]:
            findings.append(
                _finding(
                    finding_id="RRG-CONTEXT-019",
                    report_name=report_name,
                    message="related_files の excerpt が max_file_excerpt_chars を超えています。",
                )
            )
    if excerpt_total > budget["max_context_chars"]:
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-020",
                report_name=report_name,
                message="related_files の excerpt 合計が max_context_chars を超えています。",
            )
        )

    context_truncated = report.get("context_truncated")
    if not isinstance(context_truncated, bool):
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-021",
                report_name=report_name,
                message="context_truncated は bool である必要があります。",
            )
        )
    truncated_reason = report.get("context_truncated_reason")
    if context_truncated is True and (
        not isinstance(truncated_reason, str) or not truncated_reason.strip()
    ):
        findings.append(
            _finding(
                finding_id="RRG-CONTEXT-022",
                report_name=report_name,
                message="context_truncated=true には context_truncated_reason が必要です。",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Evidence Schema v2 受理経路（N-602A・Common Validator Boundary）
# ---------------------------------------------------------------------------


def is_evidence_v2_report(report: Mapping[str, Any]) -> bool:
    """レビュー証跡 JSON が Evidence Schema v2 artifact かを返す。"""
    return evidence_adapter.is_evidence_v2_document(report)


def is_correction_report(report: Mapping[str, Any], report_name: str) -> bool:
    """レビュー証跡 JSON が correction artifact（別 schema）かを返す。

    `docs/ai/reviews/*.json` には review-result schema と correction schema が同居する。
    命名規約（``*.correction-v2.json``）と ``correction_schema_version`` field の
    いずれかで判別し、通常 schema 検査の誤適用を避ける（無視はしない）。
    """
    return evidence_adapter.is_correction_document(report) or evidence_adapter.is_correction_path(
        report_name
    )


def validate_correction_report(
    *, report: Mapping[str, Any], report_name: str, root: Path
) -> list[GateFinding]:
    """correction artifact を Correction Contract と共通 core で検証する。"""
    result = evidence_adapter.validate_correction_artifact(report, root=root, path=report_name)
    if result.ok:
        return []
    return [
        GateFinding(
            id="RRG-EV2-004",
            severity="must",
            report=report_name,
            message=("訂正証跡（correction artifact）が契約検査で不合格です: " + result.render()),
        )
    ]


def _review_kind_field_checks(artifact: Mapping[str, Any]) -> Sequence[ContractViolation]:
    """review 経路固有の field 契約を検査する（真偽規則は core が持つ）。"""
    violations: list[ContractViolation] = []
    provider = artifact.get("review_provider")
    if isinstance(provider, str) and provider.strip().lower() not in KNOWN_REVIEW_PROVIDERS:
        violations.append(
            ContractViolation(
                code="kind_field_invalid",
                message=f"未知の review provider です: {provider.strip().lower()}",
            )
        )
    return violations


def validate_evidence_v2_report(
    *, report: Mapping[str, Any], report_name: str, root: Path
) -> tuple[list[GateFinding], str | None]:
    """Evidence Schema v2 の review artifact を共通 core で検証する。

    Returns:
        (findings, provider): ``provider`` は契約として有効な artifact のときだけ返す。
        無効 artifact の provider は充足根拠にしない（fail-close）。
    """
    result = evidence_adapter.validate_kind_evidence(
        report,
        kind=EVIDENCE_V2_KIND,
        root=root,
        path=report_name,
        extra_checks=_review_kind_field_checks,
    )
    if not result.ok:
        return (
            [
                GateFinding(
                    id="RRG-EV2-001",
                    severity="must",
                    report=report_name,
                    message=(
                        "Evidence Schema v2 のレビュー証跡が共通 validator で不合格です: "
                        + result.render()
                    ),
                )
            ],
            None,
        )
    provider = report.get("review_provider")
    normalized = provider.strip().lower() if isinstance(provider, str) else None
    return [], normalized


def validate_evidence_v2_link(
    *, report: Mapping[str, Any], report_name: str, root: Path
) -> list[GateFinding]:
    """v1 report が宣言した v2 artifact 参照（``evidence_v2_ref``）を検証する。

    参照は移行用の受理経路である。参照がある以上は共通 core で検査し、
    不正な参照は must fail にする（宣言だけで通過させない）。
    """
    raw = report.get(EVIDENCE_V2_REF_FIELD)
    if raw is None:
        return []
    if not isinstance(raw, Mapping):
        return [
            GateFinding(
                id="RRG-EV2-003",
                severity="must",
                report=report_name,
                message=f"{EVIDENCE_V2_REF_FIELD} は path と sha256 を持つ object が必要です。",
            )
        ]
    ref_path = raw.get("path")
    ref_sha = raw.get("sha256")
    if not isinstance(ref_path, str) or not isinstance(ref_sha, str):
        return [
            GateFinding(
                id="RRG-EV2-003",
                severity="must",
                report=report_name,
                message=f"{EVIDENCE_V2_REF_FIELD}.path / .sha256 が string ではありません。",
            )
        ]
    reference = evidence_adapter.EvidenceReference(
        kind=EVIDENCE_V2_KIND, path=ref_path, sha256=ref_sha
    )
    result = evidence_adapter.validate_evidence_reference(
        reference, root=root, extra_checks=_review_kind_field_checks
    )
    if result.ok:
        return []
    return [
        GateFinding(
            id="RRG-EV2-003",
            severity="must",
            report=report_name,
            message=(
                "参照した Evidence Schema v2 artifact が共通 validator で不合格です: "
                + result.render()
            ),
        )
    ]


def validate_v1_command_truthfulness(
    *, report: Mapping[str, Any], report_name: str
) -> list[GateFinding]:
    """v1 report の commands_run から実行不能 exit code を検出する。

    Truthfulness Rule 3（exit 126/127 は role・expected list・summary に関係なく invalid）は
    schema 版に依存しない事実なので、v1 report にも常時適用する（PR #1297 の false pass）。
    exit code 定数は共通 core（``FORBIDDEN_EXIT_CODES``）を import して使い、複製しない。
    メッセージの ``commands_run[n]`` は 0 始まりで、correction contract の ``field_path``
    （例: ``commands_run[2]``）と同じ list index 基準に揃える（Round 1 是正）。
    """
    findings: list[GateFinding] = []
    commands = report.get("commands_run")
    if not isinstance(commands, list):
        return findings
    for index, item in enumerate(commands):
        if not isinstance(item, Mapping):
            continue
        exit_code = item.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            continue
        if exit_code in FORBIDDEN_EXIT_CODES:
            findings.append(
                GateFinding(
                    id="RRG-EV2-002",
                    severity="must",
                    report=report_name,
                    message=(
                        f"commands_run[{index}] の exit_code={exit_code} は"
                        "コマンドを実行できなかったことを表すため、証跡の根拠にできません"
                        "（N-602 Truthfulness Rule 3）。実行可能なコマンドで再測定してください。"
                    ),
                )
            )
    return findings


def validate_report(
    *,
    report: dict[str, Any],
    path: Path,
    root: Path | None = None,
    expected_fingerprint: str | None,
    expected_base_sha: str | None,
    expected_head_sha: str | None = None,
    actual_changed_files: list[str] | None = None,
    enforce_extension_evidence: bool | None = None,
    github_issue_state_fetcher: GithubIssueStateFetcher | None = None,
    allow_offline_backlog_validation: bool = False,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    report_name = path.as_posix()
    extension_evidence_required = (
        actual_changed_files is not None
        if enforce_extension_evidence is None
        else enforce_extension_evidence
    )

    # Truthfulness Rule 3 は schema 版に依存しない事実なので、現 PR の証跡へは v1 でも適用する。
    # 過去 artifact（差分外）は historical read として読むだけにし、遡及判定はしない
    # （PR #1297 の既存 evidence は correction artifact で訂正する・AC-07）。
    if extension_evidence_required:
        findings.extend(validate_v1_command_truthfulness(report=report, report_name=report_name))
    # 移行用の参照経路。宣言があれば必ず共通 core で検査する。
    findings.extend(
        validate_evidence_v2_link(report=report, report_name=report_name, root=root or ROOT)
    )
    if extension_evidence_required and EVIDENCE_V2_REF_FIELD not in report:
        # 「新規/変更 evidence は v2 必須」（PR-B mandatory 化・DEC-20260813-002）。
        # 過去 artifact（差分外）は historical read のまま遡及判定しない。
        findings.append(
            GateFinding(
                id="RRG-EV2-010",
                severity="must",
                report=report_name,
                message=(
                    "現在 PR のレビュー証跡が schema v1 形式です。"
                    "Evidence Schema v2 artifact を生成し、証跡本体または "
                    f"{EVIDENCE_V2_REF_FIELD} で参照してください（N-602A）。"
                ),
                blocking=True,
            )
        )

    if extension_evidence_required and contains_probable_secret(report):
        findings.append(
            GateFinding(
                id="RRG-SECRET-001",
                severity="must",
                report=report_name,
                message=(
                    "現在 PR のレビュー証跡に秘密情報らしい値があります。"
                    "公開証跡へ保存せず、値を失効・除去してください。"
                ),
            )
        )

    if report.get("schema_version") != SCHEMA_VERSION:
        findings.append(
            GateFinding(
                id="RRG-SCHEMA-001",
                severity="must",
                report=report_name,
                message=f"schema_version must be {SCHEMA_VERSION}",
            )
        )

    provider = report.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        findings.append(
            GateFinding(
                id="RRG-PROVIDER-001",
                severity="must",
                report=report_name,
                message="provider が空です。",
            )
        )
    elif provider.strip().lower() not in KNOWN_REVIEW_PROVIDERS:
        findings.append(
            GateFinding(
                id="RRG-PROVIDER-001",
                severity="must",
                report=report_name,
                message=f"未知の review provider です: {provider.strip().lower()}",
            )
        )

    if expected_fingerprint is not None and report.get("diff_fingerprint") != expected_fingerprint:
        findings.append(
            GateFinding(
                id="RRG-DIFF-001",
                severity="must",
                report=report_name,
                message="diff_fingerprint が現在差分と一致しません。",
            )
        )

    if expected_base_sha is not None and report.get("base_sha") != expected_base_sha:
        findings.append(
            GateFinding(
                id="RRG-BASE-001",
                severity="must",
                report=report_name,
                message="base_sha が検証対象 base ref と一致しません。",
            )
        )

    report_head_sha = report.get("head_sha")
    if (
        expected_head_sha is not None
        and isinstance(report_head_sha, str)
        and report_head_sha.strip()
        and not review_head_is_compatible(root or ROOT, report_head_sha.strip(), expected_head_sha)
    ):
        findings.append(
            GateFinding(
                id="RRG-HEAD-001",
                severity="must",
                report=report_name,
                message=(
                    "head_sha が検証対象 head ref と一致せず、"
                    "後続差分もレビュー証跡 JSON のみではありません。"
                ),
            )
        )

    for field in REQUIRED_TEXT_FIELDS:
        value = report.get(field)
        if not isinstance(value, str) or not value.strip():
            findings.append(
                GateFinding(
                    id="RRG-FIELD-001",
                    severity="must",
                    report=report_name,
                    message=f"{field} が空です。",
                )
            )

    findings.extend(
        validate_review_round_extensions(
            report=report,
            report_name=report_name,
            root=root or ROOT,
            enforce_extension_evidence=extension_evidence_required,
        )
    )
    findings.extend(validate_repository_context(report, report_name, actual_changed_files))

    result = report.get("result")
    if isinstance(result, str) and result.strip().lower() not in ALLOWED_RESULTS:
        findings.append(
            GateFinding(
                id="RRG-RESULT-001",
                severity="must",
                report=report_name,
                message="result が不正です。",
            )
        )
    if isinstance(result, str) and result.strip().lower() == "fail":
        findings.append(
            GateFinding(
                id="RRG-RESULT-002",
                severity="must",
                report=report_name,
                message="review report result が fail です。",
            )
        )

    raw_lenses = report.get("lenses")
    if not isinstance(raw_lenses, list) or not all(isinstance(item, str) for item in raw_lenses):
        findings.append(
            GateFinding(
                id="RRG-LENS-001",
                severity="must",
                report=report_name,
                message="lenses は string list である必要があります。",
            )
        )
    else:
        present_lenses = {item.strip().lower() for item in raw_lenses}
        missing_lenses = sorted(REQUIRED_LENSES - present_lenses)
        if missing_lenses:
            findings.append(
                GateFinding(
                    id="RRG-LENS-002",
                    severity="must",
                    report=report_name,
                    message="必須 lens が不足しています: " + ", ".join(missing_lenses),
                )
            )

    raw_findings = report.get("findings")
    if not isinstance(raw_findings, list):
        findings.append(
            GateFinding(
                id="RRG-FINDINGS-001",
                severity="must",
                report=report_name,
                message="findings は list である必要があります。",
            )
        )
        return findings

    for index, raw_finding in enumerate(raw_findings, start=1):
        if not isinstance(raw_finding, dict):
            findings.append(
                GateFinding(
                    id="RRG-FINDING-001",
                    severity="must",
                    report=report_name,
                    message=f"finding #{index} が object ではありません。",
                )
            )
            continue
        finding = cast("dict[str, Any]", raw_finding)
        severity = str(finding.get("severity", "")).lower()
        status = str(finding.get("status", "")).lower()
        finding_id = str(finding.get("id") or f"finding #{index}")

        for field in REQUIRED_FINDING_FIELDS:
            value = finding.get(field)
            if not isinstance(value, str) or not value.strip():
                findings.append(
                    GateFinding(
                        id="RRG-FINDING-FIELD-001",
                        severity="must",
                        report=report_name,
                        message=f"{finding_id}: {field} が空です。",
                    )
                )

        ac_mapping = finding.get("ac_mapping")
        if not isinstance(ac_mapping, list) or not all(
            isinstance(item, str) and item.strip() for item in ac_mapping
        ):
            findings.append(
                GateFinding(
                    id="RRG-FINDING-AC-001",
                    severity="must",
                    report=report_name,
                    message=f"{finding_id}: ac_mapping は string list である必要があります。",
                )
            )

        if severity not in ALLOWED_SEVERITIES:
            findings.append(
                GateFinding(
                    id="RRG-SEVERITY-001",
                    severity="must",
                    report=report_name,
                    message=f"{finding_id}: severity が不正です。",
                )
            )
            continue
        if status not in ALLOWED_STATUSES:
            findings.append(
                GateFinding(
                    id="RRG-STATUS-001",
                    severity="must",
                    report=report_name,
                    message=f"{finding_id}: status が不正です。",
                )
            )
            continue

        if severity == "must" and status == "open":
            findings.append(
                GateFinding(
                    id="RRG-MUST-001",
                    severity="must",
                    report=report_name,
                    message=f"{finding_id}: Must finding が open のままです。",
                )
            )
        if severity == "must" and status == "accepted_risk":
            findings.append(
                GateFinding(
                    id="RRG-MUST-002",
                    severity="must",
                    report=report_name,
                    message=f"{finding_id}: Must finding は accepted_risk にできません。",
                )
            )
        if severity == "should" and status == "open":
            findings.append(
                GateFinding(
                    id="RRG-SHOULD-001",
                    severity="should",
                    report=report_name,
                    message=f"{finding_id}: Should finding が open のままです。",
                )
            )
        if severity == "should" and status == "accepted_risk":
            raw_backlog_id = finding.get("backlog_id")
            backlog_id = raw_backlog_id.strip() if isinstance(raw_backlog_id, str) else ""
            risk_reason = finding.get("risk_reason")
            if not backlog_id or not risk_reason:
                findings.append(
                    GateFinding(
                        id="RRG-SHOULD-002",
                        severity="must",
                        report=report_name,
                        message=(
                            f"{finding_id}: accepted_risk には backlog_id と "
                            "risk_reason が必要です。"
                        ),
                    )
                )
            else:
                # Issue #1054: backlog_id は非空であるだけでなく実在する必要がある
                # （宙吊り参照で P-065 の繰延要件を空洞化させない）。
                backlog_status, backlog_detail = resolve_backlog_id_status(
                    root or ROOT,
                    backlog_id,
                    issue_state_fetcher=github_issue_state_fetcher,
                )
                if backlog_status == BACKLOG_ID_STATUS_MISSING:
                    findings.append(
                        GateFinding(
                            id="RRG-SHOULD-003",
                            severity="must",
                            report=report_name,
                            message=(
                                f"{finding_id}: backlog_id={backlog_id!r} が実在しません。"
                                f"{backlog_detail}"
                            ),
                        )
                    )
                elif backlog_status == BACKLOG_ID_STATUS_UNSUPPORTED_FORMAT:
                    findings.append(
                        GateFinding(
                            id="RRG-SHOULD-004",
                            severity="must",
                            report=report_name,
                            message=(
                                f"{finding_id}: backlog_id={backlog_id!r} は未対応形式です。"
                                f"{backlog_detail}"
                            ),
                        )
                    )
                elif backlog_status == BACKLOG_ID_STATUS_UNREACHABLE:
                    offline_allowed = allow_offline_backlog_validation
                    findings.append(
                        GateFinding(
                            id="RRG-SHOULD-005",
                            severity="should" if offline_allowed else "must",
                            report=report_name,
                            message=(
                                f"{finding_id}: backlog_id={backlog_id!r} の実在検証が"
                                "オフラインのため完了しませんでした。"
                                + (
                                    "ローカル限定の明示例外として警告に留めます。"
                                    if offline_allowed
                                    else "既定の fail-close によりゲートを停止します。"
                                )
                                + backlog_detail
                            ),
                            blocking=not offline_allowed,
                        )
                    )
                elif backlog_status == BACKLOG_ID_STATUS_LOOKUP_FAILED:
                    findings.append(
                        GateFinding(
                            id="RRG-SHOULD-006",
                            severity="must",
                            report=report_name,
                            message=(
                                f"{finding_id}: backlog_id={backlog_id!r} の実在検証に"
                                f"失敗しました。{backlog_detail}"
                            ),
                        )
                    )
                # BACKLOG_ID_STATUS_VERIFIED は finding を追加しない。

    return findings


def collect_reports(root: Path, pattern: str) -> list[Path]:
    paths = glob.glob(str(root / pattern))
    return sorted(Path(path) for path in paths)


def collect_changed_reports(root: Path, changed_files: list[str]) -> list[Path]:
    return sorted(root / path for path in changed_files if _is_review_report_path(path))


def is_data_only(changed_files: list[str]) -> bool:
    """全変更ファイルが data-only 扱いかどうかを判定する（ci/detect_data_only.py と同一契約）。

    data/ 前置一致に加え `_ADDITIONAL_DATA_ONLY_EXACT_PATHS` への完全一致も許容する
    （週次再学習 artifact PR の `models/metadata.json` 用・2026-07-10 拡張）。ただし
    完全一致パス単独では False＝data/ 配下ファイルとの併用時のみ許容（監査バイパス防止・
    少なくとも 1 件は data/ 前置一致が必要）。
    判定はファイルパスの全数一致のみで行い、PR タイトルは参照しない（偽装防止）。
    """
    return bool(changed_files) and (
        all(
            path.startswith("data/") or path in _ADDITIONAL_DATA_ONLY_EXACT_PATHS
            for path in changed_files
        )
        and any(path.startswith("data/") for path in changed_files)
    )


def provider_set(reports: list[dict[str, Any]]) -> set[str]:
    providers: set[str] = set()
    for report in reports:
        provider = report.get("provider")
        if isinstance(provider, str) and provider.strip():
            normalized = provider.strip().lower()
            providers.add(normalized)
    return providers


def evaluate_reports(
    *,
    root: Path,
    report_paths: list[Path],
    required_providers: set[str],
    expected_fingerprint: str | None,
    expected_base_sha: str | None = None,
    expected_head_sha: str | None = None,
    actual_changed_files: list[str] | None = None,
    enforce_extension_evidence: bool | None = None,
    github_issue_state_fetcher: GithubIssueStateFetcher | None = None,
    report_ref: str | None = None,
    allow_offline_backlog_validation: bool = False,
) -> dict[str, Any]:
    findings: list[GateFinding] = []
    reports: list[dict[str, Any]] = []
    report_entries: list[tuple[str, dict[str, Any]]] = []
    report_names: list[str] = []
    evidence_v2_names: list[str] = []
    evidence_v2_providers: set[str] = set()
    correction_names: list[str] = []

    if not report_paths:
        findings.append(
            GateFinding(
                id="RRG-REPORT-001",
                severity="must",
                message="レビュー証跡 JSON が存在しません。",
            )
        )

    for path in report_paths:
        rel_path = path.as_posix()
        try:
            rel_path = _repo_relative_path(root, path)
            report_names.append(rel_path)
            if report_ref is None:
                report = load_report(path)
            else:
                report = load_report_from_git(root, report_ref, rel_path)
        except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            findings.append(
                GateFinding(
                    id="RRG-LOAD-001",
                    severity="must",
                    report=rel_path,
                    message=f"レビュー証跡を読み込めません: {exc}",
                )
            )
            continue
        if is_correction_report(report, rel_path):
            # correction artifact は review-result schema ではない。専用契約で検証する。
            findings.extend(
                validate_correction_report(report=report, report_name=rel_path, root=root)
            )
            correction_names.append(rel_path)
            continue
        if is_evidence_v2_report(report):
            # Evidence Schema v2 artifact は v1 report 検証へ流さず、共通 core で検査する。
            v2_findings, v2_provider = validate_evidence_v2_report(
                report=report, report_name=rel_path, root=root
            )
            findings.extend(v2_findings)
            evidence_v2_names.append(rel_path)
            if v2_provider is not None:
                evidence_v2_providers.add(v2_provider)
            continue
        reports.append(report)
        report_entries.append((rel_path, report))
        findings.extend(
            validate_report(
                report=report,
                path=Path(rel_path),
                root=root,
                expected_fingerprint=expected_fingerprint,
                expected_base_sha=expected_base_sha,
                expected_head_sha=expected_head_sha,
                actual_changed_files=actual_changed_files,
                enforce_extension_evidence=enforce_extension_evidence,
                github_issue_state_fetcher=github_issue_state_fetcher,
                allow_offline_backlog_validation=allow_offline_backlog_validation,
            )
        )

    # 契約として有効な v2 artifact の provider だけを充足根拠へ加える（fail-close）。
    present = provider_set(reports) | evidence_v2_providers
    provider_fingerprints: dict[tuple[str, str], str] = {}
    for rel_path, report in report_entries:
        provider = report.get("provider")
        fingerprint = report.get("context_fingerprint")
        if not isinstance(provider, str) or not isinstance(fingerprint, str) or not fingerprint:
            continue
        key = (provider.strip().lower(), fingerprint)
        if key in provider_fingerprints:
            findings.append(
                GateFinding(
                    id="RRG-CONTEXT-023",
                    severity="must",
                    report=rel_path,
                    message=(
                        "同一 provider / context_fingerprint のレビュー証跡が重複しています: "
                        f"{provider_fingerprints[key]}"
                    ),
                )
            )
            continue
        provider_fingerprints[key] = rel_path

    # N-613: required_providers は互換引数名で、意味は「受理可能な provider 集合」。
    # そのいずれか 1 つの有効なレビュー証跡が present なら充足する。
    # accepted と auto-trigger は別であり、Codex を accepted に含めても executor は起動しない。
    # 証跡の包括性は
    # validate_report（fingerprint / head_sha / commands_run / mode 等）が担保する。
    unknown_required = required_providers - KNOWN_REVIEW_PROVIDERS
    if not required_providers or unknown_required:
        detail = "空集合" if not required_providers else ", ".join(sorted(unknown_required))
        findings.append(
            GateFinding(
                id="RRG-PROVIDER-003",
                severity="must",
                message=f"accepted_review_providers の設定が不正です: {detail}",
            )
        )
    elif not (present & required_providers):
        findings.append(
            GateFinding(
                id="RRG-PROVIDER-002",
                severity="must",
                message=(
                    "受理可能なハーネスのレビュー証跡が 1 つもありません"
                    f"（いずれか 1 つ必要）: {', '.join(sorted(required_providers))}"
                ),
            )
        )

    # blocking=False はローカルで明示した offline backlog 検証例外だけに用いる。
    blocking_findings = [f for f in findings if f.blocking]
    result = "PASS"
    if any(f.severity == "must" for f in blocking_findings):
        result = "FAIL"
    elif blocking_findings:
        result = "PARTIAL"

    return {
        "schema_version": SCHEMA_VERSION,
        "result": result,
        "accepted_review_providers": sorted(required_providers),
        "required_providers": sorted(required_providers),
        "present_providers": sorted(present),
        "diff_fingerprint": expected_fingerprint,
        "reports": report_names,
        "evidence_v2_reports": evidence_v2_names,
        "correction_reports": correction_names,
        "findings": [finding.to_json() for finding in findings],
    }


def parse_providers(value: str) -> set[str]:
    providers = {part.strip().lower() for part in value.split(",") if part.strip()}
    if not providers:
        raise ValueError("accepted_review_providers は 1 件以上必要です")
    unknown = providers - KNOWN_REVIEW_PROVIDERS
    if unknown:
        raise ValueError("未知の review provider です: " + ", ".join(sorted(unknown)))
    return providers


def _running_in_ci() -> bool:
    """一般 CI と GitHub Actions のどちらでも CI 実行を検出する。"""
    false_values = {"", "0", "false", "no", "off"}
    return any(
        os.environ.get(name, "").strip().lower() not in false_values
        for name in ("CI", "GITHUB_ACTIONS")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="AI review report gate")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--reports", default=REVIEW_REPORT_GLOB)
    parser.add_argument(
        "--accepted-providers",
        "--required-providers",
        dest="required_providers",
        default=DEFAULT_ACCEPTED_REVIEW_PROVIDERS,
        help=(
            "受理可能な review provider（カンマ区切り）。いずれか 1 つの有効証跡で充足する。"
            " --required-providers は互換 alias。"
        ),
    )
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    parser.add_argument("--expected-diff-fingerprint")
    parser.add_argument("--changed-only", action="store_true")
    parser.add_argument("--no-fingerprint-check", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-should", action="store_true")
    parser.add_argument(
        "--allow-offline-backlog-validation",
        action="store_true",
        help=(
            "ローカルで GitHub に到達できない場合だけ Issue 実在検証を警告へ落とす。"
            "CI では使用できず、認証失敗・応答不正も許容しない。"
        ),
    )
    args = parser.parse_args()
    try:
        accepted_providers = parse_providers(args.required_providers)
    except ValueError as exc:
        parser.error(str(exc))
    if args.allow_offline_backlog_validation and _running_in_ci():
        parser.error("--allow-offline-backlog-validation は CI 環境では使用できません")

    root = args.root.resolve()
    expected = args.expected_diff_fingerprint
    expected_base_sha = None
    expected_head_sha = None
    if not args.no_fingerprint_check and expected is None and args.base_ref and args.head_ref:
        expected = diff_fingerprint(root, args.base_ref, args.head_ref)
    if not args.no_fingerprint_check and args.base_ref and args.head_ref:
        expected_base_sha = merge_base(root, args.base_ref, args.head_ref)
        expected_head_sha = rev_parse(root, args.head_ref)

    actual_changed_files: list[str] | None = None
    report_ref: str | None = None
    if args.base_ref and args.head_ref:
        actual_changed_files = changed_files_from_git(root, args.base_ref, args.head_ref)

    if args.changed_only:
        report_ref = rev_parse(root, args.head_ref or "HEAD")
        changed_files = actual_changed_files or changed_files_from_git(
            root, args.base_ref, args.head_ref
        )
        # base/head ref を省略したローカル changed-only でも、現在差分の延長証跡契約と
        # changed_files 網羅性を CI 経路と同じく検証する。
        actual_changed_files = changed_files
        if is_data_only(changed_files):
            result = {
                "schema_version": SCHEMA_VERSION,
                "result": "PASS",
                "data_only": True,
                "accepted_review_providers": sorted(accepted_providers),
                "required_providers": sorted(accepted_providers),
                "present_providers": [],
                "diff_fingerprint": expected,
                "reports": [],
                "evidence_v2_reports": [],
                "correction_reports": [],
                "findings": [],
            }
            text = json.dumps(result, indent=2, ensure_ascii=False)
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(text + "\n", encoding="utf-8")
            print(text)
            return 0
        report_paths = collect_changed_reports(root, changed_files)
    else:
        report_paths = collect_reports(root, args.reports)
    result = evaluate_reports(
        root=root,
        report_paths=report_paths,
        required_providers=accepted_providers,
        expected_fingerprint=expected,
        expected_base_sha=expected_base_sha,
        expected_head_sha=expected_head_sha,
        actual_changed_files=actual_changed_files,
        enforce_extension_evidence=args.changed_only,
        report_ref=report_ref,
        allow_offline_backlog_validation=args.allow_offline_backlog_validation,
    )
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)

    if result["result"] == "FAIL":
        return 1
    if result["result"] == "PARTIAL" and not args.allow_should:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
