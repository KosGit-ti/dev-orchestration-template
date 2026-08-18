#!/usr/bin/env python3
"""リリース判定証跡（Evidence Schema v2）を共通 validator core で検証する（N-602A）。

既存のリリース判定は `release-manager-decision:v1` の互換 wire marker
（`.agents/skills/release-judgement/SKILL.md`「互換 marker」節・`scripts/ops/pr_autopilot.sh`
が読む 1 行 HTML コメント）だけを機械検査していた。marker は「誰がどの head へ何を宣言したか」
しか表さず、判定の根拠となったコマンド実行結果を検証しない。

本 CLI は marker 検査を置き換えずに併存させ、次を追加する。

1. `artifact_kind=release` の v2 artifact を `scripts/ai/evidence_contract.py` の共通 core で
   検証する。
2. marker（pr / head / decision）と artifact（`release_pr` / `head_sha` / `release_decision`）の
   binding を exact 照合する。
3. `decision=MERGE` は artifact が契約として有効かつ `result=pass` の場合だけ許す
   （fail / inconclusive / 手書き entry は MERGE の根拠にできない・P-010）。

真偽規則は本 file に持たない。判定は必ず `scripts/ai/evidence_adapter.py` 経由で core を呼ぶ
（docs/specs/N-602-evidence-truthfulness-generation.md §Common Validator Boundary）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import evidence_adapter  # noqa: E402
from scripts.ai.evidence_contract import ContractViolation  # noqa: E402

RELEASE_KIND: Final = "release"

# scripts/ops/pr_autopilot.sh と .agents/skills/release-judgement/SKILL.md の互換 marker。
# 正規表現の正本を Python 側にも 1 か所だけ置き、行全体一致でのみ受理する。
RELEASE_DECISION_MARKER_RE: Final = re.compile(
    r"<!-- release-manager-decision:v1 pr=(?P<pr>[1-9][0-9]*) "
    r"head=(?P<head>[0-9a-f]{40}) decision=(?P<decision>MERGE|REJECT) -->"
)
MERGE_DECISION: Final = "MERGE"


@dataclass(frozen=True)
class ReleaseDecisionMarker:
    """互換 wire marker 1 件。"""

    pr: int
    head_sha: str
    decision: str

    def render(self) -> str:
        """1 行表現を返す。"""
        return f"pr={self.pr} head={self.head_sha} decision={self.decision}"


@dataclass(frozen=True)
class ReleaseEvidenceResult:
    """リリース判定証跡 1 件の検証結果。"""

    marker: ReleaseDecisionMarker | None
    adapter: evidence_adapter.AdapterResult | None
    binding_violations: tuple[ContractViolation, ...] = ()

    @property
    def ok(self) -> bool:
        """MERGE の根拠として使えるかを返す（fail-close）。"""
        if self.binding_violations:
            return False
        return self.adapter is not None and self.adapter.ok

    def violations(self) -> tuple[ContractViolation, ...]:
        """binding と core の違反を連結して返す。"""
        core = self.adapter.violations() if self.adapter is not None else ()
        return (*self.binding_violations, *core)

    def codes(self) -> frozenset[str]:
        """検出した violation code 集合を返す。"""
        return frozenset(violation.code for violation in self.violations())

    def render(self) -> str:
        """人間可読な要約を返す。"""
        lines = [f"ok={self.ok}"]
        if self.marker is not None:
            lines.append(f"  marker: {self.marker.render()}")
        if self.adapter is not None:
            lines.append(f"  artifact: {self.adapter.path}")
        lines.extend(f"  - {violation.render()}" for violation in self.violations())
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        """機械可読な結果を返す。"""
        return {
            "schema_version": 1,
            "ok": self.ok,
            "marker": None if self.marker is None else self.marker.render(),
            "artifact": None if self.adapter is None else self.adapter.path,
            "violations": [
                {"code": violation.code, "message": violation.message}
                for violation in self.violations()
            ],
        }


def parse_release_decision_markers(text: str) -> tuple[ReleaseDecisionMarker, ...]:
    """本文から互換 marker を抽出する（行全体一致のみ受理する）。"""
    markers: list[ReleaseDecisionMarker] = []
    for line in (text or "").splitlines():
        match = RELEASE_DECISION_MARKER_RE.fullmatch(line.strip())
        if match is None:
            continue
        markers.append(
            ReleaseDecisionMarker(
                pr=int(match.group("pr")),
                head_sha=match.group("head"),
                decision=match.group("decision"),
            )
        )
    return tuple(markers)


def _binding_violations(
    artifact: Mapping[str, Any] | None,
    marker: ReleaseDecisionMarker | None,
    *,
    expected_pr: int | None,
    expected_head_sha: str | None,
) -> list[ContractViolation]:
    """marker / 期待値 / artifact の binding を exact 照合する。"""
    violations: list[ContractViolation] = []
    if marker is not None:
        if expected_pr is not None and marker.pr != expected_pr:
            violations.append(
                ContractViolation(
                    code="marker_pr_mismatch",
                    message=f"marker の pr が判定対象と一致しない: {marker.pr} != {expected_pr}",
                )
            )
        if expected_head_sha is not None and marker.head_sha != expected_head_sha.lower():
            violations.append(
                ContractViolation(
                    code="marker_head_mismatch",
                    message="marker の head が判定対象 head と一致しない",
                )
            )
    if artifact is None:
        return violations

    head_sha = artifact.get("head_sha")
    if expected_head_sha is not None and head_sha != expected_head_sha.lower():
        violations.append(
            ContractViolation(
                code="artifact_head_mismatch_expected",
                message="artifact の head_sha が判定対象 head と一致しない",
            )
        )
    if expected_pr is not None and artifact.get("release_pr") != expected_pr:
        # marker 不在時（例: 互換 marker を使わない呼び出し経路）でも判定対象 PR との
        # exact 照合を行う。marker 経由の照合（artifact_pr_mismatch）とは別 code にし、
        # fail-close の未活用経路を塞ぐ（Backlog #1310）。
        violations.append(
            ContractViolation(
                code="artifact_pr_mismatch_expected",
                message="artifact の release_pr が判定対象 PR と一致しない",
            )
        )
    if marker is not None:
        if head_sha != marker.head_sha:
            violations.append(
                ContractViolation(
                    code="artifact_head_mismatch_marker",
                    message="artifact の head_sha が marker の head と一致しない",
                )
            )
        if artifact.get("release_pr") != marker.pr:
            violations.append(
                ContractViolation(
                    code="artifact_pr_mismatch",
                    message="artifact の release_pr が marker の pr と一致しない",
                )
            )
        if artifact.get("release_decision") != marker.decision:
            violations.append(
                ContractViolation(
                    code="artifact_decision_mismatch",
                    message="artifact の release_decision が marker の decision と一致しない",
                )
            )
    if artifact.get("release_decision") == MERGE_DECISION and artifact.get("result") != "pass":
        violations.append(
            ContractViolation(
                code="merge_without_pass",
                message=(
                    "release_decision=MERGE には result=pass の証跡が必要"
                    f"（result={artifact.get('result')!r}）"
                ),
            )
        )
    return violations


def validate_release_evidence(
    *,
    evidence_path: Path,
    root: Path,
    marker_text: str | None = None,
    expected_pr: int | None = None,
    expected_head_sha: str | None = None,
) -> ReleaseEvidenceResult:
    """リリース判定証跡を共通 core と marker binding の両面で検証する。

    Args:
        evidence_path: `artifact_kind=release` の Evidence Schema v2 artifact。
        root: repo root（manifest / normalized input の解決に使う）。
        marker_text: 互換 marker を含む本文。渡された場合は binding を照合する。
        expected_pr / expected_head_sha: 判定直前に取得した実値。

    Returns:
        ReleaseEvidenceResult: `ok` が真のときだけ MERGE の根拠に使える。
    """
    marker: ReleaseDecisionMarker | None = None
    binding: list[ContractViolation] = []
    if marker_text is not None:
        markers = parse_release_decision_markers(marker_text)
        if not markers:
            binding.append(
                ContractViolation(
                    code="marker_missing",
                    message="release-manager-decision:v1 marker が本文に 1 件もない",
                )
            )
        elif len({(item.pr, item.head_sha, item.decision) for item in markers}) > 1:
            binding.append(
                ContractViolation(
                    code="marker_conflict",
                    message="互いに矛盾する release-manager-decision:v1 marker がある",
                )
            )
        else:
            marker = markers[0]

    adapter = evidence_adapter.validate_evidence_file(evidence_path, kind=RELEASE_KIND, root=root)
    artifact: Mapping[str, Any] | None = None
    if "artifact_unreadable" not in adapter.codes():
        try:
            artifact = evidence_adapter.load_json_document(evidence_path)
        except evidence_adapter.EvidenceAdapterError:
            artifact = None
    binding.extend(
        _binding_violations(
            artifact,
            marker,
            expected_pr=expected_pr,
            expected_head_sha=expected_head_sha,
        )
    )
    return ReleaseEvidenceResult(marker=marker, adapter=adapter, binding_violations=tuple(binding))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口。契約違反があれば 1 を返す（fail-close）。"""
    parser = argparse.ArgumentParser(description="リリース判定証跡（Evidence Schema v2）検証")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--evidence", type=Path, required=True, help="release artifact の path")
    parser.add_argument("--marker-file", type=Path, help="互換 marker を含む本文 file")
    parser.add_argument("--pr", type=int, help="判定直前に取得した PR 番号")
    parser.add_argument("--head-sha", help="判定直前に取得した head SHA")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    marker_text: str | None = None
    if args.marker_file is not None:
        marker_text = args.marker_file.read_text(encoding="utf-8")

    result = validate_release_evidence(
        evidence_path=args.evidence,
        root=args.root.resolve(),
        marker_text=marker_text,
        expected_pr=args.pr,
        expected_head_sha=args.head_sha,
    )
    text = json.dumps(result.to_json(), indent=2, ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
