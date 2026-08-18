#!/usr/bin/env python3
"""PR 受入条件を Copilot 非依存で機械監査する。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai.validate_audit_evidence import (  # noqa: E402
    HEAD_MISMATCH,
    MISSING_REQUIRED_MARKER,
    MULTIPLE_MARKERS,
    resolve_head_sha,
    validate_audit_evidence,
)

SCHEMA_VERSION = "1.0"

REQUIRED_ACS = [
    "AC-001",
    "AC-010",
    "AC-020",
    "AC-030",
    "AC-040",
    "AC-050",
    "AC-060",
    "AC-070",
    "AC-080",
]

CONTROL_FILES = [
    "docs/requirements.md",
    "docs/policies.md",
    "docs/constraints.md",
    "docs/plan.md",
    "ai/operation-policy.yml",
    "ai/pre-pr-review-policy.yml",
]

# data-only 判定（is_data_only）で data/ 前置一致に加えて許容する完全一致パス。
# 週次再学習 workflow（scheduled-train.yml・B-335）の artifact PR が data/models/** と同一コミットで
# 変更する models/metadata.json のみを対象にする（ディレクトリ丸ごとの緩和はしない・偽装防止）。
_ADDITIONAL_DATA_ONLY_EXACT_PATHS = frozenset({"models/metadata.json"})

CODE_PREFIXES = ("src/", "scripts/", "ci/", "frontend/", ".claude/hooks/", ".github/workflows/")
TEST_PREFIXES = ("tests/", "frontend/__tests__/")
DOC_PREFIXES = ("docs/", "ai/", ".github/instructions/", ".github/PULL_REQUEST_TEMPLATE.md")
REVIEW_REPORT_PREFIX = "docs/ai/reviews/"
RED_RISK_PATTERNS = [
    ".github/workflows/",
    "web/lib/auth/",
    "web/lib/progress/",
    "web/content/",
    "scripts/upload_db.py",
    "scripts/predict.py",
    "pyproject.toml",
    "uv.lock",
    ".env",
    "configs/",
]

CLOSING_KEYWORD_RE = re.compile(
    r"\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#\d+",
    flags=re.IGNORECASE,
)
PATH_TOKEN_RE = re.compile(
    r"(?P<path>(?:\.github|ai|ci|configs|docs|scripts|src|tests|frontend)/[^\s`'\"),]+)"
)
LINE_SELECTOR_RE = re.compile(r"^(?P<path>.+):\d+$")


def is_review_report_path(path: str) -> bool:
    """直下の `docs/ai/reviews/*.json` だけをAIレビュー証跡として扱う。"""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized.removeprefix("./")
    if not normalized.startswith(REVIEW_REPORT_PREFIX):
        return False
    relative = normalized.removeprefix(REVIEW_REPORT_PREFIX)
    return bool(relative) and "/" not in relative and relative.endswith(".json")


@dataclass(frozen=True)
class Finding:
    """監査 finding。"""

    id: str
    severity: str
    message: str
    ac: str | None = None
    path: str | None = None

    def to_json(self) -> dict[str, str]:
        data = {
            "id": self.id,
            "severity": self.severity,
            "message": self.message,
        }
        if self.ac is not None:
            data["ac"] = self.ac
        if self.path is not None:
            data["path"] = self.path
        return data


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


def _safe_ref(ref: str) -> str:
    if ref.startswith("-") or any(char.isspace() for char in ref):
        msg = f"unsafe git ref: {ref!r}"
        raise ValueError(msg)
    return ref


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


def changed_files_from_file(path: Path) -> list[str]:
    return sorted(
        {
            line.strip().replace("\\", "/")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    )


def load_event_body(path: Path | None) -> tuple[str, dict[str, Any]]:
    """GitHub event JSON から PR body を取得する。"""
    if path is None:
        return "", {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = cast("dict[str, Any]", raw)
    pr = data.get("pull_request")
    if isinstance(pr, dict):
        body = pr.get("body")
        return str(body or ""), data
    return "", data


def load_body(body_file: Path | None, event_path: Path | None) -> str:
    if body_file is not None:
        return body_file.read_text(encoding="utf-8")
    body, _ = load_event_body(event_path)
    return body


def is_checked_ac(body: str, ac_id: str) -> bool:
    pattern = re.compile(rf"^\s*[-*]\s+\[[xX]\]\s+.*\b{re.escape(ac_id)}\b", re.MULTILINE)
    return pattern.search(body) is not None


def has_non_placeholder_value(body: str, label: str) -> bool:
    """Markdown 内の `label: value` が実値を持つかを判定する。"""
    value = label_value(body, label)
    if value is None:
        return False
    if not value:
        return False
    placeholders = {
        "-",
        "N/A",
        "n/a",
        "なし",
        "なし。",
        "未設定",
        "未確認",
        "該当なし",
        "該当なし。",
        "TBD",
        "TODO",
        "必要なら",
    }
    return value not in placeholders


def label_value(body: str, label: str) -> str | None:
    pattern = re.compile(
        rf"^(?P<indent>[^\S\r\n]*)(?:[-*][^\S\r\n]+)?{re.escape(label)}"
        rf"(?:（[^）]*）)?[^\S\r\n]*[:：][^\S\r\n]*(?P<value>.*)$"
    )
    lines = body.splitlines()
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match is None:
            continue
        value = match.group("value").strip()
        if value:
            return value
        base_indent = len(match.group("indent"))
        continuation: list[str] = []
        for next_line in lines[index + 1 :]:
            if not next_line.strip():
                if continuation:
                    continuation.append("")
                continue
            indent = len(next_line) - len(next_line.lstrip(" \t"))
            if indent <= base_indent:
                break
            continuation.append(next_line.strip())
        block_value = "\n".join(item for item in continuation if item.strip()).strip()
        return block_value or None
    return None


def contains_any_changed(changed_files: list[str], prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(prefix) for path in changed_files for prefix in prefixes)


def contains_red_risk(changed_files: list[str]) -> bool:
    return any(
        any(path.startswith(pattern) or path == pattern for pattern in RED_RISK_PATTERNS)
        for path in changed_files
    )


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


def contains_operational_safety_change(changed_files: list[str]) -> bool:
    markers = (
        "web/lib/auth/",
        "scripts/predict",
        "scripts/check_livetrade_gate.py",
        "scripts/test_kill_switch_e2e.py",
        ".github/workflows/scheduled-",
    )
    return any(path.startswith(markers) or "daily_cap" in path for path in changed_files)


def missing_validation_targets(root: Path, command_value: str | None) -> list[str]:
    if command_value is None:
        return []
    targets: set[str] = set()
    for match in PATH_TOKEN_RE.finditer(command_value):
        token = match.group("path").rstrip(".,:;")
        token = token.split("::", 1)[0].split("#", 1)[0]
        line_match = LINE_SELECTOR_RE.match(token)
        if line_match is not None:
            token = line_match.group("path")
        if "*" in token:
            continue
        targets.add(token)
    return sorted(target for target in targets if not (root / target).exists())


def body_has_justification(body: str, label: str) -> bool:
    return has_non_placeholder_value(body, label)


def audit_evidence_findings(
    *,
    root: Path,
    body: str,
    changed_files: list[str] | None = None,
    expected_head_sha: str | None = None,
) -> list[Finding]:
    """PR 本文が参照する Evidence Schema v2 監査証跡を検査する（N-602A／PR-C・P0-03 audit）。

    DEC-20260813-002（3 経路とも v2 既定 must）に従い、非 data-only PR は `kind=audit` の
    evidence-v2 marker を exactly 1 件持たなければならない。旧実装は marker 0 件を無検査で
    通していた（PR #1359 が素通り）。finding ID:

    - `AA-EV2-001`（must）: 参照 artifact が共通 validator core で不合格
      （path 不存在・sha 不一致・契約違反・path 規約／audit_target 不整合）。
    - `AA-EV2-002`（must）: kind=audit marker が 0 件。
    - `AA-EV2-003`（must）: kind=audit marker が 2 件以上。
    - `AA-EV2-004`（must）: artifact の head_sha が検査対象 head へ束縛できない
      （一致でも、evidence 専用 path だけの差分で結ばれた ancestor でもない）。

    免除の判断:

    - data-only PR（`is_data_only`・`ci/detect_data_only.py` と同一契約: 全 path が `data/`
      前置一致＋`models/metadata.json` 完全一致の併用）だけを免除する。auto-ingest／週次再学習の
      機械生成 PR は本文を持たず、`audit_acceptance` も早期 PASS するため整合する。
    - docs-only PR は **免除しない**。監査は docs PR でも AC 検証（`実行コマンド`／`結果`・
      AC-040／AC-070）を要求しており（AA-VERIFY-001／AA-DOD-001 は docs-only でも出る）、
      その実行を証跡化するのが audit artifact だからである。docs 変更の検証 command
      （lint・整合 checker・pytest の該当 file）を manifest に入れて生成する。
    - `changed_files=None` は「判定材料なし」であり、免除しない（fail-close）。
    - `expected_head_sha=None`（`--head-ref` なしのローカル実行）では head 束縛を評価しない。
      CI と fallback CI は常に `--head-ref` を渡す。

    真偽規則は `scripts/ai/evidence_contract.py` にあり、ここでは呼び出すだけである。
    """
    required = not is_data_only(changed_files or [])
    result = validate_audit_evidence(
        body=body,
        root=root,
        required=required,
        expected_head_sha=expected_head_sha,
        repo_root=root,
    )
    findings: list[Finding] = []
    for violation in result.route_violations:
        if violation.code == MISSING_REQUIRED_MARKER:
            findings.append(
                Finding(
                    id="AA-EV2-002",
                    severity="must",
                    ac="AC-040",
                    message=(
                        "PR 本文に kind=audit の evidence-v2 marker がありません"
                        "（非 data-only PR は監査証跡 exactly 1 件が必須）: " + violation.message
                    ),
                )
            )
        elif violation.code == MULTIPLE_MARKERS:
            findings.append(
                Finding(
                    id="AA-EV2-003",
                    severity="must",
                    ac="AC-040",
                    message="kind=audit の evidence-v2 marker が複数あります: " + violation.message,
                )
            )
    for failure in result.failures():
        head_violations = [v for v in failure.violations() if v.code == HEAD_MISMATCH]
        other_violations = [v for v in failure.violations() if v.code != HEAD_MISMATCH]
        if other_violations:
            findings.append(
                Finding(
                    id="AA-EV2-001",
                    severity="must",
                    ac="AC-040",
                    path=failure.path,
                    message=(
                        "PR 本文が参照する監査証跡が共通 validator で不合格です: "
                        + failure.render()
                    ),
                )
            )
        for violation in head_violations:
            findings.append(
                Finding(
                    id="AA-EV2-004",
                    severity="must",
                    ac="AC-040",
                    path=failure.path,
                    message="監査証跡の head_sha が検査対象 head へ束縛できません: "
                    + violation.message,
                )
            )
    return findings


def audit_acceptance(
    *,
    root: Path,
    body: str,
    changed_files: list[str],
    require_pr_body: bool,
    expected_head_sha: str | None = None,
) -> dict[str, Any]:
    """PR 受入条件を監査する。

    `expected_head_sha` は `--head-ref` を解決した完全 SHA で、監査証跡（kind=audit）の
    head 束縛に使う。省略時は束縛を評価しない（CI は常に渡す）。
    """
    findings: list[Finding] = []

    if is_data_only(changed_files):
        return {
            "schema_version": SCHEMA_VERSION,
            "result": "PASS",
            "data_only": True,
            "changed_files": changed_files,
            "findings": [],
        }

    for rel_path in CONTROL_FILES:
        if not (root / rel_path).exists():
            findings.append(
                Finding(
                    id="AA-CTRL-001",
                    severity="must",
                    ac="AC-001",
                    path=rel_path,
                    message=f"必須 control file が存在しません: {rel_path}",
                )
            )

    if require_pr_body and not body.strip():
        findings.append(
            Finding(
                id="AA-PR-001",
                severity="must",
                ac="AC-040",
                message="PR body が空です。AC と検証結果を記載してください。",
            )
        )

    if body.strip():
        for ac_id in REQUIRED_ACS:
            if not is_checked_ac(body, ac_id):
                findings.append(
                    Finding(
                        id="AA-AC-001",
                        severity="must",
                        ac=ac_id,
                        message=f"{ac_id} が checked checkbox として確認できません。",
                    )
                )

        if not CLOSING_KEYWORD_RE.search(body):
            findings.append(
                Finding(
                    id="AA-PR-002",
                    severity="should",
                    ac="AC-001",
                    message="PR body に Closes/Fixes/Resolves #issue がありません。",
                )
            )

        if not (
            has_non_placeholder_value(body, "実行コマンド")
            and has_non_placeholder_value(body, "結果")
        ):
            findings.append(
                Finding(
                    id="AA-VERIFY-001",
                    severity="must",
                    ac="AC-040",
                    message="検証の実行コマンドと結果が実値として確認できません。",
                )
            )

        # N-602A／PR-C: 非 data-only PR は kind=audit の v2 監査証跡 exactly 1 件を必須とし、
        # 共通 core による契約検査と検査対象 head への束縛を行う（0 件も must）。
        findings.extend(
            audit_evidence_findings(
                root=root,
                body=body,
                changed_files=changed_files,
                expected_head_sha=expected_head_sha,
            )
        )

        missing_targets = missing_validation_targets(root, label_value(body, "実行コマンド"))
        for target in missing_targets:
            findings.append(
                Finding(
                    id="AA-DOD-001",
                    severity="must",
                    ac="AC-070",
                    path=target,
                    message=f"検証コマンドの対象パスが存在しません: {target}",
                )
            )

    code_changed = contains_any_changed(changed_files, CODE_PREFIXES)
    test_changed = contains_any_changed(changed_files, TEST_PREFIXES)
    docs_changed = contains_any_changed(changed_files, DOC_PREFIXES)
    review_report_changed = any(is_review_report_path(path) for path in changed_files)
    has_body_context = body.strip() or require_pr_body

    if (
        code_changed
        and not test_changed
        and has_body_context
        and not body_has_justification(body, "テスト不要理由")
    ):
        findings.append(
            Finding(
                id="AA-TEST-001",
                severity="must",
                ac="AC-010",
                message=(
                    "コード変更がありますが tests/ または frontend/__tests__/ の変更、"
                    "もしくはテスト不要理由がありません。"
                ),
            )
        )

    if (
        code_changed
        and not docs_changed
        and has_body_context
        and not body_has_justification(body, "docs更新不要理由")
    ):
        findings.append(
            Finding(
                id="AA-DOC-001",
                severity="should",
                ac="AC-030",
                message=(
                    "コード変更がありますが docs/ai/.github instructions 更新または"
                    "不要理由がありません。"
                ),
            )
        )

    if contains_red_risk(changed_files) and body.strip():
        rollback_ok = has_non_placeholder_value(body, "ロールバック方針")
        risk_ok = "red" in body.lower() or "赤" in body or "高リスク" in body
        if not rollback_ok or not risk_ok:
            findings.append(
                Finding(
                    id="AA-RISK-001",
                    severity="must",
                    ac="AC-050",
                    message=(
                        "red-risk 変更があります。red/high-risk 表明と実値の"
                        "ロールバック方針が必要です。"
                    ),
                )
            )

    if contains_operational_safety_change(changed_files) and body.strip():
        has_jst_boundary = "JST" in body or "日本時間" in body
        has_fail_closed = "fail-closed" in body.lower() or "fail closed" in body.lower()
        has_boundary_test = "境界" in body and test_changed
        if not (has_jst_boundary and has_fail_closed and has_boundary_test):
            findings.append(
                Finding(
                    id="AA-SAFETY-001",
                    severity="must",
                    ac="AC-080",
                    message=(
                        "安全装置・運用日付関連の変更には JST、fail-closed、"
                        "境界値テストの証跡が必要です。"
                    ),
                )
            )

    if (
        code_changed
        and not review_report_changed
        and not body_has_justification(body, "AIレビュー証跡")
        and (body.strip() or require_pr_body)
    ):
        findings.append(
            Finding(
                id="AA-REVIEW-001",
                severity="should",
                ac="AC-060",
                message="コード変更がありますが AIレビュー証跡が確認できません。",
            )
        )

    result = "PASS"
    if any(f.severity == "must" for f in findings):
        result = "FAIL"
    elif findings:
        result = "PARTIAL"

    return {
        "schema_version": SCHEMA_VERSION,
        "result": result,
        "changed_files": changed_files,
        "findings": [finding.to_json() for finding in findings],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PR 受入条件監査")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    parser.add_argument("--event-path", type=Path)
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--changed-files-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-should", action="store_true")
    parser.add_argument("--no-require-pr-body", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    body = load_body(args.body_file, args.event_path)
    if args.changed_files_file is not None:
        changed_files = changed_files_from_file(args.changed_files_file)
    else:
        changed_files = changed_files_from_git(root, args.base_ref, args.head_ref)
    # --head-ref があれば完全 SHA へ解決し、監査証跡の head 束縛に使う。解決不能は
    # traceback ではなく確定した exit code とメッセージで fail-close する（PR #1378 Round 1）。
    expected_head_sha: str | None = None
    if args.head_ref:
        try:
            expected_head_sha = resolve_head_sha(root, args.head_ref)
        except (RuntimeError, ValueError) as exc:
            print(
                f"acceptance_audit: --head-ref を commit SHA へ解決できません: {exc}",
                file=sys.stderr,
            )
            raise SystemExit(2) from exc

    result = audit_acceptance(
        root=root,
        body=body,
        changed_files=changed_files,
        require_pr_body=not args.no_require_pr_body,
        expected_head_sha=expected_head_sha,
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
