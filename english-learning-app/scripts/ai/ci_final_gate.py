#!/usr/bin/env python3
"""GitHub CI と代替 CI 証跡を `ci/final-gate` に集約する。"""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
_ROOT_TEXT = str(ROOT)
if _ROOT_TEXT not in sys.path:
    sys.path.insert(0, _ROOT_TEXT)

from scripts.ai.review_report_gate import diff_fingerprint

SCHEMA_VERSION = "1.0"
DEFAULT_FALLBACK_REPORT = Path("docs/ai/ci-fallback-review.json")
# N-432: PR ゲートを ci.yml の job へ統合（fan-out 撲滅）。required check は全て CI workflow 由来。
# security-gate は policy_check / gitleaks / pip-audit が quality-gate + secret-scan と重複するため
# required check から除外（網羅性は維持）。data-only PR は CI 内の detect-data-only + noop で
# quality-gate を供給する（passthrough〔旧 Data Passthrough CI〕は不要）。secret-scan は
# N-586（F-02 / AR-WP02）以降 data-only を含む全 PR で実際に gitleaks を実行する（noop 廃止）。
# release-pr-guard（N-586 PR-2・spec §6.4）: red-risk PR の release-manifest.yml / サイズ上限検査を
# 単独 fail で ci/final-gate を fail させるため required check へ昇格する。check 欠落は
# github_ci_unknown（fail-close・下記 classify_github_ci_state 参照）。
DEFAULT_REQUIRED_CHECKS = (
    "quality-gate",
    "secret-scan",
    "acceptance-audit",
    "review-report-gate",
    "release-pr-guard",
)
SUCCESS_CONCLUSIONS = {"success", "neutral"}
FAILURE_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required"}
EXPECTED_CHECK_WORKFLOWS = {
    "quality-gate": {"CI"},
    "secret-scan": {"CI"},
    "acceptance-audit": {"CI"},
    "review-report-gate": {"CI"},
    "release-pr-guard": {"CI"},
}
# N-432 以降は data-only も CI 内 noop で quality-gate を供給するため passthrough なし
# （secret-scan は N-586 以降 noop 対象外・全 PR で実行するため passthrough 自体が不要）。
PASSTHROUGH_CHECK_WORKFLOWS: dict[str, set[str]] = {}
INFRA_PATTERNS = (
    "billing",
    "spending limit",
    "payment",
    "runner_id=0",
    "no hosted runner",
    "no runner",
    "runner unavailable",
    "runner capacity",
    "actions outage",
    "github actions is temporarily unavailable",
    "steps=[]",
)
SECURITY_CRITICAL_CHECKS = (
    "policy_check",
    "policy check",
    "pip-audit",
    "gitleaks",
    "secret-scan",
    "security-gate",
)
REQUIRED_FALLBACK_CHECKS = (
    "uv_sync",
    "policy_check",
    "ruff_check",
    "ruff_format",
    "mypy",
    "pytest",
    "pip-audit",
    "gitleaks",
    "acceptance_audit",
    "review_report_gate",
)
RED_RISK_CHANGED_FILE_PATTERNS = (
    ".github/workflows/",
    ".github/instructions/",
    ".claude/hooks/",
    "ai/",
    "configs/",
    "scripts/hooks/",
    "web/lib/auth/",
    "web/lib/progress/",
    "web/content/",
    ".env",
    "pyproject.toml",
    "uv.lock",
    "docs/architecture.md",
    "docs/constraints.md",
    "docs/design.md",
    "docs/policies.md",
    "docs/requirements.md",
    "docs/runbook.md",
    "prompts/ai/repo-aware-review.md",
    "scripts/ai/acceptance_audit.py",
    "scripts/ai/ci_final_gate.py",
    "scripts/ai/review_report_gate.py",
    "scripts/ai/run_ci_fallback_review.py",
    "scripts/predict.py",
    "scripts/run_ai_review.py",
    "scripts/upload_db.py",
)
JOB_URL_RE = re.compile(r"/actions/runs/\d+/job/(\d+)")


@dataclass(frozen=True)
class GateFinding:
    id: str
    severity: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {"id": self.id, "severity": self.severity, "message": self.message}


def _run_command(args: list[str], *, cwd: Path = ROOT, timeout: int = 60) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        msg = f"{' '.join(args)} failed: {stderr}"
        raise RuntimeError(msg)
    return result.stdout


def _check_name_matches(name: str, required: str) -> bool:
    return name == required or name.endswith(f" / {required}") or name.endswith(f": {required}")


def _check_run_order_key(check_run: dict[str, Any]) -> tuple[str, int]:
    timestamp = ""
    for field in ("started_at", "created_at", "completed_at", "startedAt", "completedAt"):
        value = check_run.get(field)
        if isinstance(value, str) and value:
            timestamp = value
            break
    raw_id = check_run.get("id")
    check_id = raw_id if isinstance(raw_id, int) else 0
    return timestamp, check_id


def latest_check_runs_by_name(check_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for check_run in check_runs:
        name = check_run.get("name")
        if not isinstance(name, str) or not name:
            continue
        current = latest.get(name)
        if current is None or _check_run_order_key(check_run) >= _check_run_order_key(current):
            latest[name] = check_run
    return list(latest.values())


def _workflow_name(check_run: dict[str, Any]) -> str:
    for key in ("workflowName", "workflow_name"):
        value = check_run.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _check_run_matches_workflow(
    check_run: dict[str, Any],
    expected: set[str] | None,
) -> bool:
    workflow = _workflow_name(check_run)
    return not expected or (bool(workflow) and workflow in expected)


def _check_run_matches_required_workflow(check_run: dict[str, Any], required: str) -> bool:
    return _check_run_matches_workflow(check_run, EXPECTED_CHECK_WORKFLOWS.get(required))


def _latest_check_run(check_runs: list[dict[str, Any]]) -> dict[str, Any]:
    latest = check_runs[0]
    for check_run in check_runs[1:]:
        if _check_run_order_key(check_run) >= _check_run_order_key(latest):
            latest = check_run
    return latest


def _trusted_infra_metadata_blob(check_run: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("details_url", "html_url", "runner_name", "runner_group_name"):
        value = check_run.get(key)
        if isinstance(value, str):
            chunks.append(value)
    runner_id = check_run.get("runner_id")
    if isinstance(runner_id, int):
        chunks.append(f"runner_id={runner_id}")
    steps = check_run.get("steps")
    if isinstance(steps, list) and not steps:
        chunks.append("steps=[]")
    return "\n".join(chunks).lower()


def is_infra_unavailable_check(check_run: dict[str, Any]) -> bool:
    conclusion = str(check_run.get("conclusion") or "").lower()
    if conclusion not in FAILURE_CONCLUSIONS:
        return False
    if conclusion in FAILURE_CONCLUSIONS and check_run.get("runner_id") == 0:
        return True
    steps = check_run.get("steps")
    if isinstance(steps, list) and not steps:
        return True
    metadata = _trusted_infra_metadata_blob(check_run)
    return any(pattern in metadata for pattern in INFRA_PATTERNS)


def _job_id_from_check_run(check_run: dict[str, Any]) -> str:
    for key in ("details_url", "html_url"):
        value = check_run.get(key)
        if not isinstance(value, str):
            continue
        match = JOB_URL_RE.search(value)
        if match:
            return match.group(1)
    return ""


def classify_github_ci_state(
    check_runs: list[dict[str, Any]],
    *,
    required_checks: tuple[str, ...] = DEFAULT_REQUIRED_CHECKS,
) -> dict[str, Any]:
    matched: dict[str, list[dict[str, Any]]] = {}
    for required in required_checks:
        name_matches = [
            check_run
            for check_run in check_runs
            if isinstance(check_run.get("name"), str)
            and _check_name_matches(str(check_run["name"]), required)
        ]
        primary_workflow_matches = [
            check_run
            for check_run in name_matches
            if _check_run_matches_required_workflow(check_run, required)
        ]
        passthrough_expected = PASSTHROUGH_CHECK_WORKFLOWS.get(required)
        passthrough_workflow_matches = (
            [
                check_run
                for check_run in name_matches
                if _check_run_matches_workflow(check_run, passthrough_expected)
            ]
            if passthrough_expected is not None
            else []
        )
        if primary_workflow_matches:
            selected = primary_workflow_matches
        elif passthrough_workflow_matches:
            selected = passthrough_workflow_matches
        else:
            selected = [] if required in EXPECTED_CHECK_WORKFLOWS else name_matches
        if selected:
            matched[required] = [_latest_check_run(selected)]

    missing = [name for name in required_checks if name not in matched]
    if missing:
        return {
            "state": "github_ci_unknown",
            "reason": "required checks missing: " + ", ".join(missing),
            "matched_checks": sorted(matched),
        }

    pending: list[str] = []
    infra: list[str] = []
    failed: list[str] = []
    unknown: list[str] = []
    for required, runs in matched.items():
        required_pending = False
        required_infra = False
        required_failed = False
        required_unknown = False
        for check_run in runs:
            status = str(check_run.get("status") or "").lower()
            conclusion = str(check_run.get("conclusion") or "").lower()
            if status and status != "completed":
                required_pending = True
                continue
            if conclusion in SUCCESS_CONCLUSIONS:
                continue
            if conclusion in FAILURE_CONCLUSIONS:
                if is_infra_unavailable_check(check_run):
                    required_infra = True
                else:
                    required_failed = True
                continue
            required_unknown = True
        if required_failed:
            failed.append(required)
        elif required_infra:
            infra.append(required)
        elif required_pending:
            pending.append(required)
        elif required_unknown:
            unknown.append(required)

    if failed:
        return {
            "state": "github_ci_failure",
            "reason": "required checks failed: " + ", ".join(sorted(failed)),
            "matched_checks": sorted(matched),
        }
    if pending or unknown:
        values = sorted(pending + unknown)
        return {
            "state": "github_ci_unknown",
            "reason": "required checks not completed: " + ", ".join(values),
            "matched_checks": sorted(matched),
        }
    if infra:
        return {
            "state": "github_ci_infra_unavailable",
            "reason": "required checks unavailable: " + ", ".join(sorted(infra)),
            "matched_checks": sorted(matched),
        }
    return {
        "state": "github_ci_pass",
        "reason": "required GitHub CI checks passed",
        "matched_checks": sorted(matched),
    }


def load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"JSON object expected: {path}"
        raise ValueError(msg)
    return cast("dict[str, Any]", raw)


def is_pr_controlled_fallback_report(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _require_text(
    report: dict[str, Any],
    field: str,
    findings: list[GateFinding],
) -> str:
    value = report.get(field)
    if not isinstance(value, str) or not value.strip():
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-FIELD-001",
                severity="must",
                message=f"fallback report の {field} が空です。",
            )
        )
        return ""
    return value.strip()


def _validate_checks(report: dict[str, Any], findings: list[GateFinding]) -> None:
    checks = report.get("checks")
    if not isinstance(checks, list) or not all(isinstance(item, dict) for item in checks):
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-CHECK-001",
                severity="must",
                message="fallback report の checks は object list である必要があります。",
            )
        )
        return

    present_names: set[str] = set()
    for index, raw_check in enumerate(cast("list[dict[str, Any]]", checks), start=1):
        name = raw_check.get("name")
        if isinstance(name, str) and name.strip():
            present_names.add(name.strip())
        command = str(raw_check.get("command") or raw_check.get("name") or "")
        exit_code = raw_check.get("exit_code")
        if not isinstance(exit_code, int):
            findings.append(
                GateFinding(
                    id="CFG-FALLBACK-CHECK-002",
                    severity="must",
                    message=f"checks[{index}].exit_code が int ではありません。",
                )
            )
            continue
        if exit_code == 0:
            continue
        finding_id = "CFG-FALLBACK-CHECK-003"
        if any(critical in command.lower() for critical in SECURITY_CRITICAL_CHECKS):
            finding_id = "CFG-FALLBACK-CHECK-004"
        findings.append(
            GateFinding(
                id=finding_id,
                severity="must",
                message=f"fallback check が失敗しています: {command}",
            )
        )
    missing = sorted(set(REQUIRED_FALLBACK_CHECKS) - present_names)
    if missing:
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-CHECK-005",
                severity="must",
                message="fallback report に必須チェックがありません: " + ", ".join(missing),
            )
        )


def _path_matches_pattern(path: str, pattern: str) -> bool:
    normalized = path.strip().replace("\\", "/")
    return normalized == pattern or normalized.startswith(pattern)


def _report_changed_files(report: dict[str, Any]) -> list[str]:
    raw_changed_files = report.get("changed_files")
    if not isinstance(raw_changed_files, list):
        return []
    return [
        item.strip().replace("\\", "/")
        for item in raw_changed_files
        if isinstance(item, str) and item.strip()
    ]


def changed_files_require_red_risk(changed_files: list[str]) -> bool:
    return any(
        _path_matches_pattern(path, pattern)
        for path in changed_files
        for pattern in RED_RISK_CHANGED_FILE_PATTERNS
    )


# ---------------------------------------------------------------------------
# release-pr-guard 独立再検証（N-586 PR-2 follow-up・独立レビュー指摘対応）
# ---------------------------------------------------------------------------
# release-pr-guard job は required check へ昇格し base-trusted checkout も適用したが、
# pull_request イベントでは ci.yml 自体が常に PR 版で実行される構造的制約があるため、
# inline の red 判定 case list は PR による改変から完全には保護できない
# （docs/specs/N-586-ci-trusted-computing-base.md §12）。本節は trusted context
# （ci-final-gate.yml・workflow_run）で実行される本スクリプトが、
# ai/operation-policy.yml の release_to_main.red_if_touches を独立に再評価し、
# red 該当時は release-manifest.yml の存在を独立に確認する（真の trusted anchor）。
# ci-final-gate.yml は本スクリプトを `python <script>` で bare 実行し依存インストールを
# 行わない trusted context のため、acceptance_audit.py / review_report_gate.py と同様に
# third-party 依存（PyYAML 含む）を追加しない。red_if_touches は最小限の行ベースパーサで
# 正本 YAML から直接読む（inline 複製を作らない）。
_RED_IF_TOUCHES_SECTION_RE = re.compile(r"^release_to_main:\s*$", re.MULTILINE)
_RED_IF_TOUCHES_KEY_RE = re.compile(r"^ {2}red_if_touches:\s*$", re.MULTILINE)
_RED_IF_TOUCHES_ITEM_RE = re.compile(r'^ {4}- "?([^"#]+?)"?\s*(?:#.*)?$')

# GitHub compare API の files 配列が打ち切られる既知の上限（fail-close 判定に使用）
_COMPARE_API_FILES_LIMIT = 300


def _load_red_if_touches_patterns(root: Path) -> list[str]:
    """`ai/operation-policy.yml` の `release_to_main.red_if_touches` を読み込む。

    Parameters
    ----------
    root:
        trusted な checkout のルート（呼び出し側は module 定数 `ROOT` を渡す）。

    Returns
    -------
    list[str]
        `red_if_touches` の glob パターン一覧。

    Raises
    ------
    ValueError
        期待する YAML 構造が見つからない場合。
    OSError
        ファイル読み込みに失敗した場合。
    """
    policy_path = root / "ai" / "operation-policy.yml"
    text = policy_path.read_text(encoding="utf-8")

    section_match = _RED_IF_TOUCHES_SECTION_RE.search(text)
    if not section_match:
        msg = "ai/operation-policy.yml に release_to_main セクションが見つかりません"
        raise ValueError(msg)
    remainder = text[section_match.end() :]
    next_top_level = re.search(r"^\S", remainder, re.MULTILINE)
    section_text = remainder[: next_top_level.start()] if next_top_level else remainder

    key_match = _RED_IF_TOUCHES_KEY_RE.search(section_text)
    if not key_match:
        msg = "release_to_main.red_if_touches が見つかりません"
        raise ValueError(msg)

    patterns: list[str] = []
    item_indent: int | None = None
    for line in section_text[key_match.end() :].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            # 空行・コメント行は list の途中にも出現しうるため読み飛ばす。
            # ここで break すると以降の項目がサイレントに脱落し fail-open になる
            # （独立レビューで実測検出・trusted anchor の一点のため fail-close 必須）。
            continue
        item_match = _RED_IF_TOUCHES_ITEM_RE.match(line)
        indent = len(line) - len(line.lstrip(" "))
        if item_match is not None:
            if item_indent is None:
                item_indent = indent
            patterns.append(item_match.group(1).strip())
            continue
        if (
            item_indent is not None
            and indent < item_indent
            and re.match(r"[A-Za-z_][A-Za-z0-9_-]*:", stripped)
        ):
            # list より浅いインデントの YAML キー＝release_to_main 配下の
            # 次の兄弟キーであり、list の正常な終端。
            break
        msg = (
            "release_to_main.red_if_touches の解析で予期しない行を検出しました"
            f"（fail-close・P-010）: {line!r}"
        )
        raise ValueError(msg)

    if not patterns:
        msg = "release_to_main.red_if_touches の list 項目が空です"
        raise ValueError(msg)
    return patterns


def fetch_compare_changed_files(repo: str, base_sha: str, head_sha: str) -> list[str]:
    """base...head の変更ファイル一覧を GitHub compare API から取得する。

    compare API の `files` 配列は約 300 件で打ち切られる既知の制約があるが、
    release-manifest.yml の size guard が red-risk PR を 25 ファイル以内に
    制限している（`scripts/check_release_pr_size.py`）ため実運用上は問題にならない。
    rename は `previous_filename`（旧パス）も red 判定対象に含める。
    """
    output = _run_command(["gh", "api", f"repos/{repo}/compare/{base_sha}...{head_sha}"])
    data = json.loads(output)
    if not isinstance(data, dict):
        msg = "compare API response must be object"
        raise RuntimeError(msg)
    files = data.get("files")
    if not isinstance(files, list):
        msg = "compare API response missing files list"
        raise RuntimeError(msg)
    if len(files) >= _COMPARE_API_FILES_LIMIT:
        # compare API の files 配列は約 300 件で打ち切られる。上限到達時は
        # 末尾の red 対象ファイルが切り捨てられている可能性を排除できないため
        # fail-close する（red 判定回避の抜け道にしない・P-010）。
        msg = (
            "compare API files が上限"
            f"（{_COMPARE_API_FILES_LIMIT} 件）に到達＝切り捨ての可能性（fail-close）"
        )
        raise RuntimeError(msg)
    changed: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        for key in ("filename", "previous_filename"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                changed.append(value)
    return changed


def _release_manifest_present_at_ref(repo: str, ref: str) -> bool:
    """PR head SHA の tree に release-manifest.yml が存在するかを contents API で確認する。"""
    result = subprocess.run(
        # `-f` は request を POST 化してしまうため、GET の query string で渡す。
        ["gh", "api", f"repos/{repo}/contents/release-manifest.yml?ref={ref}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    return result.returncode == 0


def _changed_files_match_red_if_touches(
    changed_files: list[str], red_if_touches: list[str]
) -> bool:
    return any(
        fnmatch.fnmatch(path.strip().replace("\\", "/"), pattern.strip().replace("\\", "/"))
        for path in changed_files
        for pattern in red_if_touches
    )


def verify_release_guard_independently(
    *,
    root: Path,
    repo: str | None,
    base_sha: str | None,
    head_sha: str | None,
) -> dict[str, Any]:
    """release-pr-guard の red 判定・manifest 要件を trusted context から独立に再検証する。

    required checks が全部 success でも、本関数が `ok=False` を返せば呼び出し側
    （`evaluate_final_gate`）は gate 全体を fail にする。判定に必要な情報が不足する
    場合・API 呼び出しや YAML parse に失敗する場合はすべて `ok=False`
    （fail-close・P-010）。

    Parameters
    ----------
    root:
        trusted な base branch checkout のルート（ci-final-gate.yml では本モジュールが
        実行される gate-baseline/ 自身＝module 定数 `ROOT`）。
    repo:
        `owner/repo` 形式のリポジトリ識別子。
    base_sha:
        比較元 SHA。
    head_sha:
        比較先（PR head）SHA。

    Returns
    -------
    dict[str, Any]
        `ok`（bool）・`red`（bool | None）・`reason`（str）を持つ。
    """
    if not repo or not base_sha or not head_sha:
        return {
            "ok": False,
            "red": None,
            "reason": (
                "release guard 独立再検証: repo / base_sha / head_sha が不足しているため "
                "判定できません（fail-close）"
            ),
        }

    try:
        red_if_touches = _load_red_if_touches_patterns(root)
    except Exception as exc:  # 理由を問わず fail-close へ倒す（P-010）
        return {
            "ok": False,
            "red": None,
            "reason": (
                "release guard 独立再検証: ai/operation-policy.yml の red_if_touches "
                f"parse に失敗しました（fail-close）: {exc}"
            ),
        }

    try:
        changed_files = fetch_compare_changed_files(repo, base_sha, head_sha)
    except Exception as exc:  # 理由を問わず fail-close へ倒す（P-010）
        return {
            "ok": False,
            "red": None,
            "reason": (
                f"release guard 独立再検証: changed files 取得に失敗しました（fail-close）: {exc}"
            ),
        }

    is_red = _changed_files_match_red_if_touches(changed_files, red_if_touches)
    if not is_red:
        return {
            "ok": True,
            "red": False,
            "reason": "release guard 独立再検証: red_if_touches 非該当",
        }

    if not _release_manifest_present_at_ref(repo, head_sha):
        return {
            "ok": False,
            "red": True,
            "reason": (
                "release guard 独立再検証: red_if_touches 該当 PR に release-manifest.yml が "
                "確認できません（fail-close）"
            ),
        }
    return {
        "ok": True,
        "red": True,
        "reason": "release guard 独立再検証: red_if_touches 該当・release-manifest.yml 確認済み",
    }


def validate_fallback_report(
    report: dict[str, Any],
    *,
    expected_base_sha: str | None,
    expected_head_sha: str | None,
    expected_diff_fingerprint: str | None,
    risk_tier: str | None,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    mode = _require_text(report, "mode", findings)
    result = _require_text(report, "result", findings)
    base_sha = _require_text(report, "base_sha", findings)
    head_sha = _require_text(report, "head_sha", findings)
    context_mode = _require_text(report, "repository_context_mode", findings)
    _require_text(report, "context_fingerprint", findings)
    trusted_gate_source = _require_text(report, "trusted_gate_source", findings)

    if mode != "fallback_ci_pass_as_equivalent":
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-MODE-001",
                severity="must",
                message=(
                    "fallback report の mode が fallback_ci_pass_as_equivalent ではありません。"
                ),
            )
        )
    if result.lower() != "pass":
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-RESULT-001",
                severity="must",
                message="fallback report の result が pass ではありません。",
            )
        )
    if report.get("dry_run") is True:
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-DRYRUN-001",
                severity="must",
                message="dry-run fallback report は最終ゲートの証跡として使用できません。",
            )
        )
    if report.get("pr_body_source") != "provided":
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-PRBODY-001",
                severity="must",
                message=(
                    "fallback report の acceptance_audit は PR body を入力にする必要があります。"
                ),
            )
        )
    if expected_base_sha is not None and base_sha != expected_base_sha:
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-STALE-001",
                severity="must",
                message="fallback report の base_sha が検証対象と一致しません。",
            )
        )
    if expected_head_sha is not None and head_sha != expected_head_sha:
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-STALE-002",
                severity="must",
                message="fallback report の head_sha が検証対象と一致しません。",
            )
        )
    if (
        expected_diff_fingerprint is not None
        and report.get("diff_fingerprint") != expected_diff_fingerprint
    ):
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-STALE-003",
                severity="must",
                message="fallback report の diff_fingerprint が検証対象と一致しません。",
            )
        )
    if report.get("github_ci_state") != "github_ci_infra_unavailable":
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-STATE-001",
                severity="must",
                message=(
                    "fallback report は github_ci_infra_unavailable を根拠にする必要があります。"
                ),
            )
        )
    if context_mode == "diff_only":
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-CONTEXT-001",
                severity="must",
                message="代替 CI の repo-aware review は diff_only では完了扱いにしません。",
            )
        )
    if not isinstance(report.get("context_budget"), dict):
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-CONTEXT-002",
                severity="must",
                message="fallback report に context_budget がありません。",
            )
        )

    cli_risk = (risk_tier or "").strip().lower()
    report_risk = str(report.get("risk_tier") or "").strip().lower()
    effective_risks = {cli_risk, report_risk}
    if changed_files_require_red_risk(_report_changed_files(report)):
        effective_risks.add("red")
    if "red" in effective_risks:
        human_override = report.get("human_override") is True
        rollback_plan = report.get("rollback_plan")
        if not human_override or not isinstance(rollback_plan, str) or not rollback_plan.strip():
            findings.append(
                GateFinding(
                    id="CFG-FALLBACK-RISK-001",
                    severity="must",
                    message="red risk fallback には human_override と rollback_plan が必要です。",
                )
            )
    if (
        trusted_gate_source.startswith("bootstrap_target:")
        and report.get("human_override") is not True
    ):
        findings.append(
            GateFinding(
                id="CFG-FALLBACK-PROVENANCE-001",
                severity="must",
                message=(
                    "bootstrap target gate scripts を使う fallback には human_override が必要です。"
                ),
            )
        )

    _validate_checks(report, findings)
    return findings


def evaluate_final_gate(
    *,
    github_ci_state: str,
    github_ci_reason: str,
    fallback_report: dict[str, Any] | None,
    expected_base_sha: str | None = None,
    expected_head_sha: str | None = None,
    expected_diff_fingerprint: str | None = None,
    risk_tier: str | None = None,
    release_guard_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    findings: list[GateFinding] = []
    mode = github_ci_state
    reason = github_ci_reason

    if github_ci_state == "github_ci_pass":
        result = "pass"
    elif github_ci_state == "github_ci_failure":
        result = "fail"
        findings.append(
            GateFinding(
                id="CFG-GITHUB-FAILURE-001",
                severity="must",
                message="GitHub CI の実 failure は fallback で上書きできません。",
            )
        )
    elif github_ci_state == "github_ci_infra_unavailable":
        if fallback_report is None:
            result = "fail"
            findings.append(
                GateFinding(
                    id="CFG-FALLBACK-MISSING-001",
                    severity="must",
                    message="GitHub CI infra unavailable ですが fallback 証跡がありません。",
                )
            )
        else:
            findings.extend(
                validate_fallback_report(
                    fallback_report,
                    expected_base_sha=expected_base_sha,
                    expected_head_sha=expected_head_sha,
                    expected_diff_fingerprint=expected_diff_fingerprint,
                    risk_tier=risk_tier,
                )
            )
            result = "fail" if findings else "pass"
            if not findings:
                mode = "fallback_ci_pass_as_equivalent"
                reason = "GitHub CI infra unavailable; fallback CI evidence passed"
    else:
        result = "fail"
        findings.append(
            GateFinding(
                id="CFG-GITHUB-UNKNOWN-001",
                severity="must",
                message="GitHub CI 状態が unknown のため fail-close します。",
            )
        )

    # N-586 PR-2 follow-up（独立レビュー指摘対応）: required checks が全部 success でも、
    # release-pr-guard の独立再検証（verify_release_guard_independently）が ok=False なら
    # 全体を fail にする。PR checkout の inline red 判定を trusted context から上書きする
    # 唯一の層であり、他の分岐結果（github_ci_pass 等）より優先する。
    if release_guard_check is not None and release_guard_check.get("ok") is not True:
        result = "fail"
        findings.append(
            GateFinding(
                id="CFG-RELEASE-GUARD-001",
                severity="must",
                message=str(
                    release_guard_check.get("reason") or "release guard 独立再検証に失敗しました。"
                ),
            )
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "result": result,
        "mode": mode,
        "github_ci_state": github_ci_state,
        "reason": reason,
        "expected_base_sha": expected_base_sha,
        "expected_head_sha": expected_head_sha,
        "expected_diff_fingerprint": expected_diff_fingerprint,
        "risk_tier": risk_tier,
        "release_guard_check": release_guard_check,
        "findings": [finding.to_json() for finding in findings],
    }


def fetch_pr_metadata(repo: str, pr_number: int) -> dict[str, str]:
    # gh pr view --json baseRefOid は新しめの gh CLI でしか使えず、self-hosted runner の
    # 古い gh では "Unknown JSON field" で失敗する。REST(gh api) はバージョン非依存のため
    # こちらでメタデータを取得する。
    output = _run_command(
        [
            "gh",
            "api",
            f"repos/{repo}/pulls/{pr_number}",
        ]
    )
    data = json.loads(output)
    if not isinstance(data, dict):
        raise RuntimeError("gh api pulls output must be object")
    base_raw = data.get("base")
    base = base_raw if isinstance(base_raw, dict) else {}
    head_raw = data.get("head")
    head = head_raw if isinstance(head_raw, dict) else {}
    return {
        "base_sha": str(base.get("sha") or ""),
        "head_sha": str(head.get("sha") or ""),
        "base_ref": str(base.get("ref") or ""),
        "head_ref": str(head.get("ref") or ""),
    }


def fetch_check_runs(repo: str, head_sha: str) -> list[dict[str, Any]]:
    check_runs: list[dict[str, Any]] = []
    page = 1
    while True:
        output = _run_command(
            [
                "gh",
                "api",
                f"repos/{repo}/commits/{head_sha}/check-runs?per_page=100&page={page}",
            ]
        )
        data = json.loads(output)
        if not isinstance(data, dict):
            raise RuntimeError("check-runs response must be object")
        page_runs = data.get("check_runs")
        if not isinstance(page_runs, list):
            raise RuntimeError("check-runs response must contain list")
        check_runs.extend(cast("list[dict[str, Any]]", page_runs))
        if len(page_runs) < 100:
            break
        page += 1
    for check_run in check_runs:
        job_id = _job_id_from_check_run(check_run)
        if not job_id:
            continue
        try:
            job_payload = _run_command(
                ["gh", "api", f"repos/{repo}/actions/jobs/{job_id}"],
                timeout=30,
            )
        except RuntimeError:
            continue
        job = json.loads(job_payload)
        if not isinstance(job, dict):
            continue
        for field in ("steps", "runner_id", "runner_name", "runner_group_name", "workflow_name"):
            if field in job:
                check_run[field] = job[field]
    return check_runs


def post_commit_status(repo: str, sha: str, result: dict[str, Any]) -> None:
    state = "success" if result["result"] == "pass" else "failure"
    description = str(result.get("reason") or "ci/final-gate")[:140]
    _run_command(
        [
            "gh",
            "api",
            f"repos/{repo}/statuses/{sha}",
            "-f",
            f"state={state}",
            "-f",
            "context=ci/final-gate",
            "-f",
            f"description={description}",
        ]
    )


def parse_required_checks(value: str) -> tuple[str, ...]:
    checks = tuple(item.strip() for item in value.split(",") if item.strip())
    return checks or DEFAULT_REQUIRED_CHECKS


def main() -> int:
    parser = argparse.ArgumentParser(description="CI final gate")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repo")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--github-ci-state")
    parser.add_argument("--github-ci-reason", default="")
    parser.add_argument("--github-checks-json", type=Path)
    parser.add_argument("--fallback-report", type=Path, default=DEFAULT_FALLBACK_REPORT)
    parser.add_argument("--allow-pr-controlled-fallback-report", action="store_true")
    parser.add_argument("--expected-base-sha")
    parser.add_argument("--expected-head-sha")
    parser.add_argument("--expected-diff-fingerprint")
    parser.add_argument("--risk-tier")
    parser.add_argument("--required-checks", default=",".join(DEFAULT_REQUIRED_CHECKS))
    parser.add_argument("--post-status", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    expected_base_sha = args.expected_base_sha
    expected_head_sha = args.expected_head_sha
    if args.repo and args.pr and (not expected_base_sha or not expected_head_sha):
        metadata = fetch_pr_metadata(args.repo, args.pr)
        expected_base_sha = expected_base_sha or metadata["base_sha"]
        expected_head_sha = expected_head_sha or metadata["head_sha"]

    if args.github_ci_state:
        github_ci = {
            "state": args.github_ci_state,
            "reason": args.github_ci_reason or args.github_ci_state,
        }
    else:
        if args.github_checks_json:
            check_payload = json.loads(args.github_checks_json.read_text(encoding="utf-8"))
            if not isinstance(check_payload, list):
                raise SystemExit("--github-checks-json は check run list JSON である必要があります")
            check_runs = cast("list[dict[str, Any]]", check_payload)
        elif args.repo and expected_head_sha:
            check_runs = fetch_check_runs(args.repo, expected_head_sha)
        else:
            check_runs = []
        github_ci = classify_github_ci_state(
            check_runs,
            required_checks=parse_required_checks(args.required_checks),
        )

    fallback_path = args.fallback_report
    if not fallback_path.is_absolute():
        fallback_path = root / fallback_path
    fallback_report = None
    if fallback_path.exists():
        if is_pr_controlled_fallback_report(root, fallback_path) and (
            not args.allow_pr_controlled_fallback_report
        ):
            raise SystemExit(
                "fallback report is inside PR-controlled checkout; use a trusted artifact path"
            )
        fallback_report = load_json(fallback_path)
    expected_diff_fingerprint = args.expected_diff_fingerprint
    if (
        fallback_report is not None
        and expected_diff_fingerprint is None
        and expected_base_sha
        and expected_head_sha
    ):
        expected_diff_fingerprint = diff_fingerprint(root, expected_base_sha, expected_head_sha)

    # N-586 PR-2 follow-up（独立レビュー指摘対応）: release-pr-guard の判定を trusted context
    # （本スクリプトが実行される base branch checkout＝module 定数 ROOT）から独立に
    # 再検証する。--root（PR checkout）ではなく ROOT を使うのは、ai/operation-policy.yml の
    # red_if_touches を PR に改変されない正本から読むため。
    release_guard_check = verify_release_guard_independently(
        root=ROOT,
        repo=args.repo,
        base_sha=expected_base_sha,
        head_sha=expected_head_sha,
    )

    result = evaluate_final_gate(
        github_ci_state=str(github_ci["state"]),
        github_ci_reason=str(github_ci.get("reason") or ""),
        fallback_report=fallback_report,
        expected_base_sha=expected_base_sha,
        expected_head_sha=expected_head_sha,
        expected_diff_fingerprint=expected_diff_fingerprint,
        risk_tier=args.risk_tier,
        release_guard_check=release_guard_check,
    )

    if args.post_status:
        if not args.repo or not expected_head_sha:
            raise SystemExit("--post-status には --repo と head SHA が必要です")
        post_commit_status(args.repo, expected_head_sha, result)

    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
