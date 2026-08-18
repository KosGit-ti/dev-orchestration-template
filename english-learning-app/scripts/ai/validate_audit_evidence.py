#!/usr/bin/env python3
"""受入監査の証跡（Evidence Schema v2）を共通 validator core で検証する（N-602A／PR-C）。

`scripts/ai/acceptance_audit.py` は PR 本文の checkbox とラベル行だけを見ており、
「実行コマンド」「結果」の値が実際の実行に対応するかを検査していなかった。本 module は
PR 本文の `evidence-v2` marker が参照する `artifact_kind=audit` の artifact を
`scripts/ai/evidence_contract.py` の共通 core で検証する。

audit 経路の規約（外部再監査 2026-08-15 P0-03・DEC-20260813-002「3 経路とも v2 既定 must」）:

- 非 data-only PR は `kind=audit` の marker を **exactly 1 件** 持つ。0 件は
  `missing_required_marker`、2 件以上は `multiple_markers`（いずれも must）。marker 0 件で
  `ok=True` になる旧挙動（PR #1359 が素通りした穴）は廃止した。
- artifact path は `docs/ai/evidence/<YYYY-MM-DD>-pr<N>-audit-v2.json`、`audit_target` は
  `pr:<N>`（path の N と一致）。
- artifact の `head_sha` は検査対象 head と一致するか、その ancestor で ancestor→head の差分が
  evidence 専用 path（`AUDIT_HEAD_EVIDENCE_PREFIXES` と `docs/ai/reviews/*.json`）だけの場合に
  限り束縛できる（`head_mismatch`）。head の判定は `evidence_adapter.head_is_compatible()` に
  一本化し、review 経路と考え方を揃える。

真偽規則は本 file に持たず、必ず `scripts/ai/evidence_adapter.py` 経由で core を呼ぶ
（§Common Validator Boundary）。判断不能は必ず違反にする（P-010）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import evidence_adapter  # noqa: E402
from scripts.ai.evidence_contract import ContractViolation  # noqa: E402

AUDIT_KIND: Final = "audit"

# audit artifact の path 規約と kind 固有 field 規約。
AUDIT_ARTIFACT_PATH_RE: Final = re.compile(
    r"^docs/ai/evidence/\d{4}-\d{2}-\d{2}-pr(?P<pr>[1-9]\d*)-audit-v2\.json$"
)
AUDIT_TARGET_RE: Final = re.compile(r"^pr:(?P<pr>[1-9]\d*)$")

# head 束縛で ancestor→head 差分に許容する evidence 専用 path。
AUDIT_HEAD_EVIDENCE_PREFIXES: Final[tuple[str, ...]] = (
    "docs/ai/evidence/",
    "docs/ai/evidence-manifests/",
    "docs/ai/evidence-normalized-inputs/",
)
REVIEW_REPORT_PREFIX: Final = "docs/ai/reviews/"

# 経路（route）レベルの違反 code。artifact 単位ではなく PR 本文全体に対して出す。
MISSING_REQUIRED_MARKER: Final = "missing_required_marker"
MULTIPLE_MARKERS: Final = "multiple_markers"
# artifact 単位で足す adapter 違反 code。
HEAD_MISMATCH: Final = "head_mismatch"
AUDIT_PATH_CONVENTION: Final = "audit_path_convention"
AUDIT_TARGET_INVALID: Final = "audit_target_invalid"
AUDIT_TARGET_MISMATCH: Final = "audit_target_mismatch"


def is_review_report_path(path: str) -> bool:
    """`docs/ai/reviews/*.json` 直下だけを review 証跡 path として扱う（下位 dir は対象外）。"""
    normalized = path.replace("\\", "/")
    if not normalized.startswith(REVIEW_REPORT_PREFIX) or not normalized.endswith(".json"):
        return False
    remainder = normalized[len(REVIEW_REPORT_PREFIX) :]
    return bool(remainder) and "/" not in remainder


def is_audit_head_evidence_path(path: str) -> bool:
    """head 束縛で許容する evidence 専用 path かを返す。"""
    normalized = path.strip().replace("\\", "/")
    return normalized.startswith(AUDIT_HEAD_EVIDENCE_PREFIXES) or is_review_report_path(normalized)


@dataclass(frozen=True)
class AuditEvidenceResult:
    """PR 本文が参照した監査証跡の検証結果。"""

    results: tuple[evidence_adapter.AdapterResult, ...]
    route_violations: tuple[ContractViolation, ...] = ()
    required: bool = True
    expected_head_sha: str | None = None

    @property
    def referenced(self) -> bool:
        """v2 artifact 参照が 1 件でもあったかを返す。"""
        return bool(self.results)

    @property
    def ok(self) -> bool:
        """経路規約を満たし、参照した全 artifact が契約として有効かを返す（fail-close）。"""
        if self.route_violations:
            return False
        return all(result.ok for result in self.results)

    def failures(self) -> tuple[evidence_adapter.AdapterResult, ...]:
        """不合格 artifact だけを返す。"""
        return tuple(result for result in self.results if not result.ok)

    def route_codes(self) -> frozenset[str]:
        """経路レベルの violation code 集合を返す。"""
        return frozenset(violation.code for violation in self.route_violations)

    def render(self) -> str:
        """人間可読な要約を返す。"""
        lines = [f"  - {violation.render()}" for violation in self.route_violations]
        lines.extend(result.render() for result in self.results)
        if not lines:
            return "evidence-v2 marker なし（audit 参照は要求されていない）"
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        """機械可読な結果を返す。"""
        return {
            "schema_version": 2,
            "required": self.required,
            "expected_head_sha": self.expected_head_sha,
            "referenced": self.referenced,
            "ok": self.ok,
            "route_violations": [
                {"code": violation.code, "message": violation.message}
                for violation in self.route_violations
            ],
            "artifacts": [
                {
                    "path": result.path,
                    "kind": result.kind,
                    "ok": result.ok,
                    "violations": [
                        {"code": violation.code, "message": violation.message}
                        for violation in result.violations()
                    ],
                }
                for result in self.results
            ],
        }


def _audit_path_pr_number(path: str) -> int | None:
    """path 規約に一致すれば PR 番号を返す。"""
    match = AUDIT_ARTIFACT_PATH_RE.fullmatch(path.replace("\\", "/"))
    return int(match.group("pr")) if match else None


def _audit_target_checks(reference_path: str) -> evidence_adapter.KindFieldChecks:
    """`audit_target` が `pr:<N>` で path の N と一致することを検査する extra_checks を返す。"""

    def _checks(artifact: Mapping[str, Any]) -> Sequence[ContractViolation]:
        target = artifact.get("audit_target")
        if not isinstance(target, str):
            # 欠損・型不正は adapter の kind_field 検査が既に違反にする（二重計上しない）。
            return ()
        match = AUDIT_TARGET_RE.fullmatch(target.strip())
        if match is None:
            return (
                ContractViolation(
                    code=AUDIT_TARGET_INVALID,
                    message=f"audit_target が pr:<N> 形式でない: {target!r}",
                ),
            )
        path_pr = _audit_path_pr_number(reference_path)
        if path_pr is not None and int(match.group("pr")) != path_pr:
            return (
                ContractViolation(
                    code=AUDIT_TARGET_MISMATCH,
                    message=(
                        f"audit_target の PR 番号 {match.group('pr')} が artifact path の "
                        f"pr{path_pr} と一致しない"
                    ),
                ),
            )
        return ()

    return _checks


def _with_violation(
    result: evidence_adapter.AdapterResult, violation: ContractViolation
) -> evidence_adapter.AdapterResult:
    """adapter 違反を 1 件追加した AdapterResult を返す。"""
    return replace(result, adapter_violations=(*result.adapter_violations, violation))


def _artifact_head_sha(path: Path | None) -> str | None:
    """artifact file の `head_sha` を読む。読めない・型不正は ``None``。"""
    if path is None or not path.is_file():
        return None
    try:
        document = evidence_adapter.load_json_document(path)
    except evidence_adapter.EvidenceAdapterError:
        return None
    head = document.get("head_sha")
    return head if isinstance(head, str) else None


def _bind_head(
    result: evidence_adapter.AdapterResult,
    artifact_path: Path | None,
    *,
    repo_root: Path,
    expected_head_sha: str,
) -> evidence_adapter.AdapterResult:
    """artifact の head_sha を検査対象 head へ束縛できなければ `head_mismatch` を足す。

    artifact が読めない／`head_sha` を取れない場合は `head_mismatch` を付けない。それらは
    既に adapter／core の違反（reference_path_missing・artifact_unreadable・contract 違反）と
    して報告されており、二重に head 不適合と診断すると原因を誤らせる（PR #1378 Round 1）。
    `head_mismatch` は「artifact が読めて head_sha は取れたが束縛できない」場合に限る。
    """
    artifact_head = _artifact_head_sha(artifact_path)
    if artifact_head is None:
        return result
    if evidence_adapter.head_is_compatible(
        repo_root,
        expected_head_sha,
        artifact_head,
        allowed_path=is_audit_head_evidence_path,
    ):
        return result
    return _with_violation(
        result,
        ContractViolation(
            code=HEAD_MISMATCH,
            message=(
                f"artifact の head_sha {artifact_head} は検査対象 head {expected_head_sha} と"
                "一致せず、evidence 専用 path だけの差分で結ばれた ancestor でもない"
            ),
        ),
    )


def validate_audit_evidence(
    *,
    body: str,
    root: Path,
    required: bool = True,
    expected_head_sha: str | None = None,
    repo_root: Path | None = None,
) -> AuditEvidenceResult:
    """PR 本文の `evidence-v2` marker が参照する監査証跡を検証する。

    `kind=audit` の marker だけを対象にする。review / release の marker は各経路の
    validator が扱うため、ここでは無視する（二重判定を避ける）。

    Args:
        body: PR 本文。
        root: artifact・manifest を解決する repo root。
        required: 真なら marker 0 件を `missing_required_marker` として違反にする。
            data-only PR の免除は呼び出し側（`acceptance_audit`）が判断して偽を渡す。
        expected_head_sha: 検査対象 head の完全 SHA。``None`` のときは head 束縛を評価しない
            （CI は常に `--head-ref` を渡す。ローカルの ref なし実行だけがこの経路）。
        repo_root: head 束縛の git を実行する root。省略時は ``root``。

    Returns:
        AuditEvidenceResult: ``ok`` が真のときだけ gate pass の根拠に使える。
    """
    references = [
        reference
        for reference in evidence_adapter.extract_evidence_v2_references(body)
        if reference.kind == AUDIT_KIND
    ]
    route_violations: list[ContractViolation] = []
    if required and not references:
        route_violations.append(
            ContractViolation(
                code=MISSING_REQUIRED_MARKER,
                message=(
                    "PR 本文に kind=audit の evidence-v2 marker がない"
                    "（非 data-only PR は監査証跡 exactly 1 件が必須）"
                ),
            )
        )
    if len(references) > 1:
        route_violations.append(
            ContractViolation(
                code=MULTIPLE_MARKERS,
                message=(
                    f"kind=audit の evidence-v2 marker が {len(references)} 件ある"
                    "（exactly 1 件でなければならない）"
                ),
            )
        )

    results: list[evidence_adapter.AdapterResult] = []
    for reference in references:
        result = evidence_adapter.validate_evidence_reference(
            reference, root=root, extra_checks=_audit_target_checks(reference.path)
        )
        if _audit_path_pr_number(reference.path) is None:
            result = _with_violation(
                result,
                ContractViolation(
                    code=AUDIT_PATH_CONVENTION,
                    message=(
                        "audit artifact の path が規約 "
                        "docs/ai/evidence/<YYYY-MM-DD>-pr<N>-audit-v2.json に一致しない: "
                        f"{reference.path}"
                    ),
                ),
            )
        if expected_head_sha is not None:
            result = _bind_head(
                result,
                evidence_adapter.safe_repo_path(root, reference.path),
                repo_root=repo_root or root,
                expected_head_sha=expected_head_sha.strip().lower(),
            )
        results.append(result)
    return AuditEvidenceResult(
        results=tuple(results),
        route_violations=tuple(route_violations),
        required=required,
        expected_head_sha=None if expected_head_sha is None else expected_head_sha.strip().lower(),
    )


def resolve_head_sha(repo_root: Path, ref: str) -> str:
    """git ref を完全 commit SHA へ解決する。解決できなければ例外（fail-close）。"""
    if ref.startswith("-") or any(char.isspace() for char in ref):
        msg = f"unsafe git ref: {ref!r}"
        raise ValueError(msg)
    output = evidence_adapter.git_stdout(repo_root, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if output is None or not evidence_adapter.FULL_SHA_RE.fullmatch(output.strip().lower()):
        msg = f"git ref を commit SHA へ解決できない: {ref!r}"
        raise RuntimeError(msg)
    return output.strip().lower()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口。経路規約違反または参照 artifact 不合格なら 1 を返す（fail-close）。

    `--body-file` を渡した場合は marker exactly 1 件を要求する（opt-out flag は置かない）。
    `--evidence` だけを渡した場合は artifact 直接検証で、marker は要求しない。
    `--head-ref`／`--expected-head-sha` を渡すと、どちらの経路でも head 束縛を評価する。
    """
    parser = argparse.ArgumentParser(description="受入監査証跡（Evidence Schema v2）検証")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--body-file", type=Path, help="PR 本文 file（evidence-v2 marker 抽出元）")
    parser.add_argument("--evidence", type=Path, help="監査 artifact を直接指定する")
    parser.add_argument(
        "--head-ref",
        help="検査対象 head の git ref（--root で解決して head 束縛を評価する）",
    )
    parser.add_argument(
        "--expected-head-sha",
        help="検査対象 head の完全 SHA（--head-ref の代わりに直接渡す）",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.body_file is None and args.evidence is None:
        parser.error("--body-file か --evidence のいずれかが必要です")
    if args.head_ref is not None and args.expected_head_sha is not None:
        parser.error("--head-ref と --expected-head-sha は同時に指定できません")

    root = args.root.resolve()
    expected_head_sha: str | None = None
    if args.head_ref is not None:
        try:
            expected_head_sha = resolve_head_sha(root, args.head_ref)
        except (RuntimeError, ValueError) as exc:
            parser.error(str(exc))
    elif args.expected_head_sha is not None:
        declared_head = str(args.expected_head_sha).strip().lower()
        if not evidence_adapter.FULL_SHA_RE.fullmatch(declared_head):
            parser.error("--expected-head-sha は 40 桁 hex でなければなりません")
        expected_head_sha = declared_head

    results: list[evidence_adapter.AdapterResult] = []
    route_violations: tuple[ContractViolation, ...] = ()
    required = args.body_file is not None
    if args.body_file is not None:
        body = args.body_file.read_text(encoding="utf-8")
        body_result = validate_audit_evidence(
            body=body, root=root, required=True, expected_head_sha=expected_head_sha
        )
        results.extend(body_result.results)
        route_violations = body_result.route_violations
    if args.evidence is not None:
        direct = evidence_adapter.validate_evidence_file(args.evidence, kind=AUDIT_KIND, root=root)
        if expected_head_sha is not None:
            direct = _bind_head(
                direct, args.evidence, repo_root=root, expected_head_sha=expected_head_sha
            )
        results.append(direct)

    result = AuditEvidenceResult(
        results=tuple(results),
        route_violations=route_violations,
        required=required,
        expected_head_sha=expected_head_sha,
    )
    text = json.dumps(result.to_json(), indent=2, ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
