#!/usr/bin/env python3
"""release 判定証跡 bundle（判定 comment 同梱方式）を生成・描画・解析・検証する。

背景（外部再監査 2026-08-15 P0-03 release 経路）:

- DEC-20260813-002 と `docs/specs/N-602-evidence-truthfulness-generation.md` は release 経路も
  Evidence Schema v2 を既定 must と定めるが、`scripts/ai/validate_release_evidence.py` の呼出元は
  tests だけで、`release-manager-decision:v1` marker 単独の MERGE 経路
  （`scripts/ops/pr_autopilot.sh` と手順書）が現存していた。
- `.github/instructions/review-loop.instructions.md` は terminal anchor 後の commit 追加を
  禁じるため、判定対象 head H の release artifact を PR branch の H へ commit することは
  原理的にできない。

そこで本 module は「release evidence bundle = 判定 comment 同梱」方式を実装する。
release-judgement は判定直前に `artifact_kind=release` の v2 artifact を
`scripts/ai/evidence_generator.py` で生成し、判定 comment に次を同梱する。

1. 互換 marker:
   ``<!-- release-manager-decision:v1 pr=<N> head=<40hex> decision=<MERGE|REJECT> -->``
2. 参照 marker: ``<!-- evidence-v2 kind=release path=docs/ai/evidence/<日付>-pr<N>-release-v2.json
   sha256=<artifact raw sha256> -->``（実際は 1 行）
3. bundle marker: ``<!-- release-evidence-bundle:v1 pr=<N> head=<40hex> -->`` に続く fenced ```json
   ブロック 2 つ（artifact 本体・command manifest 本体）

検証側（`scripts/ai/validate_rebase_merge_contract.py`・`scripts/ops/pr_autopilot.sh`）は comment
本文から bundle を厳密 parse → 一時 root へ materialize（artifact sha256 と参照 marker、
manifest sha256 と `command_manifest_sha256` の一致検査）→
`validate_release_evidence.validate_release_evidence()` で共通 core と marker binding を検証する。
marker 単独／bundle 不在／不整合／`result!=pass` の MERGE はすべて fail-close で MERGE の
根拠にならない。

真偽規則は本 module に持たない。判定は必ず `validate_release_evidence` 経由で共通 core を呼ぶ
（§Common Validator Boundary）。bundle は merge 後の次 PR で同じ path へ archive できる
（``--archive-root`` は検証 pass 後にだけ materialize する）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import evidence_adapter  # noqa: E402
from scripts.ai import evidence_generator as eg  # noqa: E402
from scripts.ai import validate_release_evidence as vre  # noqa: E402
from scripts.ai.evidence_contract import (  # noqa: E402
    CanonicalContentError,
    ContractViolation,
    canonical_content_bytes,
    sha256_bytes,
)

BUNDLE_MARKER_RE: Final = re.compile(
    r"<!-- release-evidence-bundle:v1 pr=(?P<pr>[1-9][0-9]*) head=(?P<head>[0-9a-f]{40}) -->"
)
# archive 先の path 規約。判定日（artifact の generated_at）と PR 番号で一意にする。
RELEASE_EVIDENCE_DIR: Final = "docs/ai/evidence"
RELEASE_EVIDENCE_PATH_RE: Final = re.compile(
    r"^docs/ai/evidence/(?P<date>\d{4}-\d{2}-\d{2})-pr(?P<pr>[1-9][0-9]*)-release-v2\.json$"
)
MANIFEST_REF_RE: Final = re.compile(r"^docs/ai/evidence-manifests/(?P<sha>[0-9a-f]{64})\.json$")
SHA40_RE: Final = re.compile(r"^[0-9a-fA-F]{40}$")
FENCE_OPEN: Final = "```json"
FENCE_CLOSE: Final = "```"
# GitHub issue comment の上限（65536 文字）より手前で fail-close する。
MAX_COMMENT_CHARS: Final = 65_000
GENERATE_MANIFEST_ID_TEMPLATE: Final = "pr{pr}-release-judgement"
# bundle の構造（parse / materialize / 判定対象 binding）に起因する violation code。
# REJECT 判定は verification の fail を正直に記録するため core の result 由来で ok=False に
# なり得るが、これらの構造違反がある comment はどの decision でも投稿対象にしない。
BUNDLE_STRUCTURE_CODES: Final[frozenset[str]] = frozenset(
    {
        "expected_head_invalid",
        "release_bundle_missing",
        "bundle_pr_mismatch",
        "bundle_head_mismatch",
        "bundle_marker_duplicate",
        "evidence_reference_missing",
        "evidence_reference_conflict",
        "bundle_block_missing",
        "bundle_layout_invalid",
        "bundle_block_empty",
        "bundle_block_unterminated",
        "bundle_block_invalid_json",
        "bundle_block_not_object",
        "evidence_path_invalid",
        "bundle_artifact_sha_mismatch",
        "manifest_ref_invalid",
        "manifest_ref_sha_mismatch",
        "bundle_manifest_sha_mismatch",
        "materialize_conflict",
    }
)


class BundleError(ValueError):
    """bundle の描画・解析・materialize に失敗した typed error。``code`` を持つ。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class BundleRenderError(BundleError):
    """artifact / manifest から comment を描画できない。"""


class BundleParseError(BundleError):
    """comment 本文に bundle marker はあるが構造が契約どおりでない。"""


class BundleMaterializeError(BundleError):
    """bundle の内容が自己整合しない（sha / path / manifest 参照）。"""


@dataclass(frozen=True)
class Bundle:
    """comment から厳密 parse した release evidence bundle。"""

    pr: int
    head_sha: str
    reference: evidence_adapter.EvidenceReference
    artifact_text: str
    manifest_text: str
    artifact: dict[str, Any]
    manifest: dict[str, Any]

    @property
    def artifact_bytes(self) -> bytes:
        """materialize / sha256 照合に使う artifact の raw bytes。"""
        return self.artifact_text.encode("utf-8")

    @property
    def manifest_bytes(self) -> bytes:
        """materialize / sha256 照合に使う manifest の raw bytes。"""
        return self.manifest_text.encode("utf-8")


# ---------------------------------------------------------------------------
# 描画
# ---------------------------------------------------------------------------


def _load_object(path: Path, *, label: str) -> tuple[bytes, dict[str, Any]]:
    """JSON object file を読み、canonical bytes であることを確認して返す。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BundleRenderError("file_unreadable", f"{label} を読めない: {path}: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleRenderError(
            "file_invalid_json", f"{label} が JSON として読めない: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise BundleRenderError("file_not_object", f"{label} が JSON object でない: {path}")
    # fenced block は行単位で復元するため、file bytes は改行を含まない canonical bytes
    # （generator の write_evidence_artifact / CommandManifest.write が書く形式）に限る。
    # そうでなければ archive 時に sha256 が一致しなくなるので描画時点で拒否する。
    try:
        canonical = canonical_content_bytes(value)
    except CanonicalContentError as exc:
        raise BundleRenderError(
            "file_not_canonical", f"{label} を canonical 化できない: {path}: {exc}"
        ) from exc
    if canonical != raw:
        raise BundleRenderError(
            "file_not_canonical",
            f"{label} は canonical-content-v1 bytes（改行なし・RFC 8785）で書かれている必要がある: "
            f"{path}",
        )
    return raw, value


def evidence_relative_path(artifact: Mapping[str, Any]) -> str:
    """artifact の generated_at と release_pr から archive 先 path を導出する。"""
    generated_at = artifact.get("generated_at")
    pr = artifact.get("release_pr")
    if not isinstance(generated_at, str) or len(generated_at) < 10:
        raise BundleRenderError("artifact_invalid", "generated_at が RFC3339 でない")
    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        raise BundleRenderError("artifact_invalid", "release_pr が正の整数でない")
    path = f"{RELEASE_EVIDENCE_DIR}/{generated_at[:10]}-pr{pr}-release-v2.json"
    if RELEASE_EVIDENCE_PATH_RE.fullmatch(path) is None:
        raise BundleRenderError("artifact_invalid", f"archive path を導出できない: {path}")
    return path


def render_bundle_comment(artifact_path: Path, manifest_path: Path, pr: int, head: str) -> str:
    """判定 comment 本文（互換 marker + evidence-v2 参照 + bundle）を描画する。

    artifact 側の ``release_pr`` / ``head_sha`` / ``release_decision`` と引数の binding を
    描画前に exact 照合し、``command_manifest_ref`` / ``command_manifest_sha256`` と manifest
    file の raw sha256 の一致も検査する（不一致は typed error・fail-close）。
    """
    if not SHA40_RE.fullmatch(head):
        raise BundleRenderError("head_invalid", f"head は 40 桁 SHA で指定する: {head!r}")
    head = head.lower()
    artifact_raw, artifact = _load_object(artifact_path, label="artifact")
    manifest_raw, _manifest = _load_object(manifest_path, label="manifest")

    if artifact.get("artifact_kind") != vre.RELEASE_KIND:
        raise BundleRenderError(
            "artifact_kind_mismatch",
            f"artifact_kind が release でない: {artifact.get('artifact_kind')!r}",
        )
    if artifact.get("release_pr") != pr:
        raise BundleRenderError(
            "artifact_pr_mismatch", f"artifact の release_pr {artifact.get('release_pr')!r} != {pr}"
        )
    if artifact.get("head_sha") != head:
        raise BundleRenderError(
            "artifact_head_mismatch", "artifact の head_sha が引数 head と一致しない"
        )
    decision = artifact.get("release_decision")
    if decision not in evidence_adapter.RELEASE_DECISIONS:
        raise BundleRenderError(
            "artifact_decision_invalid", f"release_decision が MERGE/REJECT でない: {decision!r}"
        )
    manifest_sha = sha256_bytes(manifest_raw)
    ref = artifact.get("command_manifest_ref")
    ref_match = MANIFEST_REF_RE.fullmatch(ref) if isinstance(ref, str) else None
    if ref_match is None:
        raise BundleRenderError(
            "manifest_ref_invalid",
            "command_manifest_ref は docs/ai/evidence-manifests/<sha256>.json の形式で必要: "
            f"{ref!r}",
        )
    if (
        artifact.get("command_manifest_sha256") != manifest_sha
        or ref_match.group("sha") != manifest_sha
    ):
        raise BundleRenderError(
            "manifest_sha_mismatch",
            "manifest file の sha256 が artifact の command_manifest_ref / "
            "command_manifest_sha256 と一致しない",
        )

    evidence_path = evidence_relative_path(artifact)
    artifact_sha = sha256_bytes(artifact_raw)
    lines = [
        f"<!-- release-manager-decision:v1 pr={pr} head={head} decision={decision} -->",
        f"<!-- evidence-v2 kind=release path={evidence_path} sha256={artifact_sha} -->",
        f"<!-- release-evidence-bundle:v1 pr={pr} head={head} -->",
        FENCE_OPEN,
        artifact_raw.decode("utf-8"),
        FENCE_CLOSE,
        FENCE_OPEN,
        manifest_raw.decode("utf-8"),
        FENCE_CLOSE,
    ]
    comment = "\n".join(lines) + "\n"
    if len(comment) > MAX_COMMENT_CHARS:
        raise BundleRenderError(
            "comment_too_large",
            f"comment が {MAX_COMMENT_CHARS} 文字を超える（{len(comment)}）。"
            "command 数か excerpt を減らす",
        )
    return comment


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def _read_fenced_json_block(lines: Sequence[str], start: int, *, ordinal: int) -> tuple[str, int]:
    """``start`` 以降の最初の非空行から ```json ブロックを読み、(本文, 次 index) を返す。"""
    index = start
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        raise BundleParseError("bundle_block_missing", f"fenced json ブロック {ordinal} がない")
    if lines[index].strip() != FENCE_OPEN:
        raise BundleParseError(
            "bundle_layout_invalid",
            "bundle marker の後には ```json ブロックだけを置く"
            f"（{ordinal} 番目の直前に他の行がある）",
        )
    index += 1
    body: list[str] = []
    while index < len(lines):
        if lines[index].strip() == FENCE_CLOSE:
            content = "\n".join(body)
            if not content.strip():
                raise BundleParseError("bundle_block_empty", f"fenced json ブロック {ordinal} が空")
            return content, index + 1
        body.append(lines[index])
        index += 1
    raise BundleParseError(
        "bundle_block_unterminated", f"fenced json ブロック {ordinal} が閉じていない"
    )


def _parse_object(text: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BundleParseError(
            "bundle_block_invalid_json", f"{label} ブロックが JSON として読めない: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise BundleParseError("bundle_block_not_object", f"{label} ブロックが JSON object でない")
    return value


def parse_bundle_comment(text: str) -> Bundle | None:
    """comment 本文から bundle を厳密 parse する。

    Returns:
        bundle marker が 1 件もなければ ``None``（bundle 不在）。marker はあるが構造が
        契約どおりでない場合は ``BundleParseError``（``None`` にはしない・fail-close）。
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    marker_indexes = [
        index for index, line in enumerate(lines) if BUNDLE_MARKER_RE.fullmatch(line.strip())
    ]
    if not marker_indexes:
        return None
    if len(marker_indexes) > 1:
        raise BundleParseError(
            "bundle_marker_duplicate", "release-evidence-bundle:v1 marker が複数ある"
        )
    marker_index = marker_indexes[0]
    marker_match = BUNDLE_MARKER_RE.fullmatch(lines[marker_index].strip())
    assert marker_match is not None
    pr = int(marker_match.group("pr"))
    head_sha = marker_match.group("head")

    references = [
        reference
        for reference in evidence_adapter.extract_evidence_v2_references(normalized)
        if reference.kind == vre.RELEASE_KIND
    ]
    if not references:
        raise BundleParseError(
            "evidence_reference_missing", "kind=release の evidence-v2 marker がない"
        )
    if len(references) > 1:
        raise BundleParseError(
            "evidence_reference_conflict", "kind=release の evidence-v2 marker が複数ある"
        )

    artifact_text, next_index = _read_fenced_json_block(lines, marker_index + 1, ordinal=1)
    manifest_text, _ = _read_fenced_json_block(lines, next_index, ordinal=2)
    artifact = _parse_object(artifact_text, label="artifact")
    manifest = _parse_object(manifest_text, label="manifest")
    return Bundle(
        pr=pr,
        head_sha=head_sha,
        reference=references[0],
        artifact_text=artifact_text,
        manifest_text=manifest_text,
        artifact=artifact,
        manifest=manifest,
    )


# ---------------------------------------------------------------------------
# materialize
# ---------------------------------------------------------------------------


def _write_exact(target: Path, data: bytes, *, label: str) -> None:
    """既存 file と bytes が異なる場合は上書きせず fail-close する（archive の冪等性）。"""
    if target.exists():
        if target.is_file() and target.read_bytes() == data:
            return
        raise BundleMaterializeError(
            "materialize_conflict", f"{label} の書き出し先に内容の異なる file が既にある: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def materialize_bundle(bundle: Bundle, tmp_root: Path) -> Path:
    """bundle を ``tmp_root`` 配下へ書き出し、artifact / manifest の自己整合を検査する。

    - 参照 marker の path は ``docs/ai/evidence/<日付>-pr<N>-release-v2.json`` で PR 番号が
      bundle marker と一致すること
    - artifact bytes の sha256 == 参照 marker の sha256
    - ``command_manifest_ref`` == ``docs/ai/evidence-manifests/<command_manifest_sha256>.json``
    - manifest bytes の sha256 == ``command_manifest_sha256``

    Returns:
        書き出した artifact の path（``validate_release_evidence`` の ``evidence_path``）。
    """
    path_match = RELEASE_EVIDENCE_PATH_RE.fullmatch(bundle.reference.path)
    if path_match is None or int(path_match.group("pr")) != bundle.pr:
        raise BundleMaterializeError(
            "evidence_path_invalid",
            "evidence-v2 marker の path は "
            "docs/ai/evidence/<日付>-pr<bundle の PR>-release-v2.json でなければならない: "
            f"{bundle.reference.path}",
        )
    # 参照 marker の path は artifact 自身（generated_at／release_pr）から導出される規約 path と
    # exact 一致しなければならない（日付だけの改ざんも拒否・PR #1382 Round 2）。
    try:
        derived_path = evidence_relative_path(bundle.artifact)
    except BundleRenderError as exc:
        raise BundleMaterializeError(
            "evidence_path_invalid",
            f"artifact から規約 path を導出できない: {exc}",
        ) from exc
    if derived_path != bundle.reference.path:
        raise BundleMaterializeError(
            "evidence_path_mismatch",
            "evidence-v2 marker の path が artifact の generated_at／release_pr から導出される "
            f"規約 path と一致しない（marker {bundle.reference.path} / 導出 {derived_path}）",
        )
    artifact_sha = sha256_bytes(bundle.artifact_bytes)
    if artifact_sha != bundle.reference.sha256:
        raise BundleMaterializeError(
            "bundle_artifact_sha_mismatch",
            "artifact ブロックの sha256 が evidence-v2 marker と一致しない"
            f"（marker {bundle.reference.sha256} / 実測 {artifact_sha}）",
        )
    ref = bundle.artifact.get("command_manifest_ref")
    ref_match = MANIFEST_REF_RE.fullmatch(ref) if isinstance(ref, str) else None
    if ref_match is None:
        raise BundleMaterializeError(
            "manifest_ref_invalid",
            "command_manifest_ref は docs/ai/evidence-manifests/<sha256>.json の形式で必要: "
            f"{ref!r}",
        )
    declared_sha = bundle.artifact.get("command_manifest_sha256")
    if declared_sha != ref_match.group("sha"):
        raise BundleMaterializeError(
            "manifest_ref_sha_mismatch",
            "command_manifest_ref の sha256 と command_manifest_sha256 が一致しない",
        )
    manifest_sha = sha256_bytes(bundle.manifest_bytes)
    if manifest_sha != declared_sha:
        raise BundleMaterializeError(
            "bundle_manifest_sha_mismatch",
            "manifest ブロックの sha256 が artifact の command_manifest_sha256 と一致しない"
            f"（宣言 {declared_sha!r} / 実測 {manifest_sha}）",
        )
    assert isinstance(ref, str)
    evidence_target = evidence_adapter.safe_repo_path(tmp_root, bundle.reference.path)
    manifest_target = evidence_adapter.safe_repo_path(tmp_root, ref)
    if evidence_target is None or manifest_target is None:
        raise BundleMaterializeError(
            "evidence_path_invalid", "root 外へ解決される path は書き出さない"
        )
    _write_exact(manifest_target, bundle.manifest_bytes, label="manifest")
    _write_exact(evidence_target, bundle.artifact_bytes, label="artifact")
    return evidence_target


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------


def _single_marker(text: str) -> vre.ReleaseDecisionMarker | None:
    markers = vre.parse_release_decision_markers(text)
    if len({(item.pr, item.head_sha, item.decision) for item in markers}) == 1:
        return markers[0]
    return None


def _failed_result(text: str, violations: Sequence[ContractViolation]) -> vre.ReleaseEvidenceResult:
    """core を呼ぶ前に確定した fail を ReleaseEvidenceResult に畳む（adapter なし = ok False）。"""
    return vre.ReleaseEvidenceResult(
        marker=_single_marker(text), adapter=None, binding_violations=tuple(violations)
    )


def validate_bundle_comment(
    text: str, *, expected_pr: int, expected_head_sha: str
) -> vre.ReleaseEvidenceResult:
    """comment 本文の bundle を materialize し、共通 core と marker binding で検証する。

    ``ok`` が真のときだけ、この comment は MERGE の根拠として使える。marker のみ、
    bundle 不在、sha 不一致、manifest 欠落、head/pr 不一致、``result!=pass`` の MERGE は
    すべて偽になる（fail-close）。
    """
    if not SHA40_RE.fullmatch(expected_head_sha or ""):
        return _failed_result(
            text,
            [
                ContractViolation(
                    code="expected_head_invalid",
                    message=f"判定対象 head は 40 桁 SHA で指定する: {expected_head_sha!r}",
                )
            ],
        )
    expected_head = expected_head_sha.lower()
    try:
        bundle = parse_bundle_comment(text)
    except BundleError as exc:
        return _failed_result(text, [ContractViolation(code=exc.code, message=exc.message)])
    if bundle is None:
        return _failed_result(
            text,
            [
                ContractViolation(
                    code="release_bundle_missing",
                    message=(
                        "release-evidence-bundle:v1 が comment にない"
                        "（release-manager-decision:v1 marker 単独は MERGE の根拠にならない）"
                    ),
                )
            ],
        )
    extra: list[ContractViolation] = []
    if bundle.pr != expected_pr:
        extra.append(
            ContractViolation(
                code="bundle_pr_mismatch",
                message=f"bundle marker の pr が判定対象と一致しない: {bundle.pr} != {expected_pr}",
            )
        )
    if bundle.head_sha != expected_head:
        extra.append(
            ContractViolation(
                code="bundle_head_mismatch",
                message="bundle marker の head が判定対象 head と一致しない",
            )
        )
    with tempfile.TemporaryDirectory(prefix="release-evidence-bundle-") as tmpdir:
        tmp_root = Path(tmpdir)
        try:
            evidence_path = materialize_bundle(bundle, tmp_root)
        except BundleError as exc:
            extra.append(ContractViolation(code=exc.code, message=exc.message))
            return _failed_result(text, extra)
        result = vre.validate_release_evidence(
            evidence_path=evidence_path,
            root=tmp_root,
            marker_text=text,
            expected_pr=expected_pr,
            expected_head_sha=expected_head,
        )
    # PR #1382 Round 3 suppressed 指摘: materialize 先は with を抜けると削除されるため、
    # 出力の `artifact` path が実在しない一時 path になる。参照 marker の repo 相対 path
    # （materialize_bundle が bundle marker と exact 一致を検査済み）へ戻して返す。
    adapter = result.adapter
    if adapter is not None:
        adapter = replace(adapter, path=bundle.reference.path)
    return vre.ReleaseEvidenceResult(
        marker=result.marker,
        adapter=adapter,
        binding_violations=(*extra, *result.binding_violations),
    )


def bundle_structure_ok(result: vre.ReleaseEvidenceResult) -> bool:
    """bundle の構造違反（``BUNDLE_STRUCTURE_CODES``）が 1 件もないかを返す。

    ``ok`` とは別の弱い性質で、REJECT 判定の comment が投稿可能かの判断に使う。MERGE の
    根拠には使わない（MERGE は ``ok`` が必要）。
    """
    return not (result.codes() & BUNDLE_STRUCTURE_CODES)


# ---------------------------------------------------------------------------
# 生成（generator の薄い CLI。真偽規則は持たない）
# ---------------------------------------------------------------------------


def _spec_from_mapping(entry: Mapping[str, Any]) -> eg.CommandSpec:
    """spec file の 1 entry を CommandSpec へ写す（型検査は generator 側の契約に委ねる）。"""
    argv = entry.get("argv", [])
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise BundleRenderError(
            "spec_invalid", f"argv は string 配列で書く: {entry.get('command_id')!r}"
        )
    exit_codes = entry.get("expected_exit_codes", [0])
    if not isinstance(exit_codes, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in exit_codes
    ):
        raise BundleRenderError(
            "spec_invalid", f"expected_exit_codes は int 配列で書く: {entry.get('command_id')!r}"
        )
    timeout_ms = entry.get("timeout_ms", eg.DEFAULT_TIMEOUT_MS)
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        raise BundleRenderError(
            "spec_invalid", f"timeout_ms は int で書く: {entry.get('command_id')!r}"
        )
    optional: dict[str, str | None] = {}
    for name in ("executable", "claim_template", "free_text_claim"):
        value = entry.get(name)
        if value is not None and not isinstance(value, str):
            raise BundleRenderError(
                "spec_invalid", f"{name} は string で書く: {entry.get('command_id')!r}"
            )
        optional[name] = value
    return eg.CommandSpec(
        command_id=str(entry.get("command_id", "")),
        role=str(entry.get("role", "verification")),
        claim_type=str(entry.get("claim_type", "exit_success")),
        argv=tuple(argv),
        script=str(entry.get("script", "")),
        execution_mode=str(entry.get("execution_mode", "argv")),
        expected_exit_codes=tuple(exit_codes),
        timeout_ms=timeout_ms,
        executable=optional["executable"],
        claim_template=optional["claim_template"],
        free_text_claim=optional["free_text_claim"],
    )


def generate_release_evidence(
    *,
    spec_path: Path,
    pr: int,
    head_sha: str,
    decision: str,
    out_root: Path,
    cwd: Path,
    repository: str = eg.DEFAULT_REPOSITORY,
) -> dict[str, Any]:
    """spec file の command を generator で実行し、release artifact と manifest を書き出す。

    spec file は ``{"manifest_id"?: str, "commands": [CommandSpec 相当], "pass_env"?: [str]}``。
    ``pass_env`` は sanitized environment へ追加する環境変数名で、認証情報らしい名前は
    generator が拒否する（gh 依存 command は ``HOME`` の file auth に限る）。

    Returns:
        書き出し結果の要約（path・raw sha256・result）。``usable`` は宣言 decision の根拠に
        使えるか（REJECT は契約有効で可、MERGE は result=pass が必要）。
    """
    if not SHA40_RE.fullmatch(head_sha):
        raise BundleRenderError("head_invalid", f"head は 40 桁 SHA で指定する: {head_sha!r}")
    if decision not in evidence_adapter.RELEASE_DECISIONS:
        raise BundleRenderError("decision_invalid", f"decision は MERGE/REJECT: {decision!r}")
    try:
        spec_doc = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleRenderError(
            "spec_invalid", f"spec file を読めない: {spec_path}: {exc}"
        ) from exc
    if not isinstance(spec_doc, dict) or not isinstance(spec_doc.get("commands"), list):
        raise BundleRenderError("spec_invalid", "spec file は {commands: [...]} の object で書く")
    pass_env = spec_doc.get("pass_env", [])
    if not isinstance(pass_env, list) or not all(isinstance(item, str) for item in pass_env):
        raise BundleRenderError("spec_invalid", "pass_env は string 配列で書く")
    env = {name: os.environ[name] for name in pass_env if name in os.environ}
    # commands の要素は全て object でなければならない。非 object を黙って除外すると spec の
    # 記述ミスで検証コマンドが欠落した弱い証跡が生成されうるため fail-close（PR #1382 Round 1）。
    invalid_entries = [
        index for index, entry in enumerate(spec_doc["commands"]) if not isinstance(entry, dict)
    ]
    if invalid_entries:
        raise BundleRenderError(
            "spec_invalid",
            f"commands の要素は object で書く（非 object の index: {invalid_entries}）",
        )
    specs = [_spec_from_mapping(entry) for entry in spec_doc["commands"]]
    manifest_id = str(spec_doc.get("manifest_id") or GENERATE_MANIFEST_ID_TEMPLATE.format(pr=pr))

    manifest = eg.build_command_manifest(specs, manifest_id=manifest_id)
    generated = eg.generate_evidence_artifact(
        artifact_kind=vre.RELEASE_KIND,
        manifest=manifest,
        head_sha=head_sha.lower(),
        repository=repository,
        cwd=cwd,
        env=env,
        extra_fields={"release_pr": pr, "release_decision": decision},
    )
    manifest_path = manifest.write(out_root / eg.DEFAULT_MANIFEST_REF_PREFIX)
    artifact_rel = evidence_relative_path(generated.artifact)
    artifact_path = eg.write_evidence_artifact(generated, out_root / artifact_rel)
    usable = generated.validation.contract_valid and (
        decision != vre.MERGE_DECISION or generated.validation.ok
    )
    return {
        "artifact": artifact_path.as_posix(),
        "artifact_relative_path": artifact_rel,
        "artifact_sha256": sha256_bytes(artifact_path.read_bytes()),
        "manifest": manifest_path.as_posix(),
        "manifest_sha256": manifest.sha256,
        "result": generated.artifact.get("result"),
        "validation": generated.validation.render(),
        "decision": decision,
        "usable": usable,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="release 判定証跡 bundle（comment 同梱方式）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--render", action="store_true", help="artifact/manifest から comment を描画する"
    )
    mode.add_argument(
        "--generate",
        action="store_true",
        help="spec file の command を実行して artifact を生成する",
    )
    parser.add_argument(
        "--comment-file", type=Path, help="検証する comment 本文 file（既定モード）"
    )
    parser.add_argument("--pr", type=int, required=True, help="判定対象 PR 番号")
    parser.add_argument(
        "--head-sha", "--head", dest="head_sha", required=True, help="判定対象 head SHA"
    )
    parser.add_argument("--artifact", type=Path, help="--render: release artifact の path")
    parser.add_argument("--manifest", type=Path, help="--render: command manifest の path")
    parser.add_argument("--spec-file", type=Path, help="--generate: command spec JSON")
    parser.add_argument(
        "--decision", choices=sorted(evidence_adapter.RELEASE_DECISIONS), help="--generate: 判定"
    )
    parser.add_argument(
        "--out-root", type=Path, default=Path("."), help="--generate: 書き出し root"
    )
    parser.add_argument(
        "--cwd", type=Path, default=ROOT, help="--generate: command の作業ディレクトリ"
    )
    parser.add_argument(
        "--repository", default=eg.DEFAULT_REPOSITORY, help="--generate: repository 名"
    )
    parser.add_argument(
        "--archive-root",
        type=Path,
        help="検証 pass 後に bundle を書き出す repo root（merge 後の archive 用）",
    )
    parser.add_argument("--output", type=Path, help="結果の書き出し先")
    return parser


def _emit(text: str, output: Path | None) -> None:
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口。検証 fail・描画不能・生成不能はすべて 1（fail-close）。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.render:
        if args.artifact is None or args.manifest is None:
            parser.error("--render には --artifact と --manifest が必要")
        try:
            comment = render_bundle_comment(args.artifact, args.manifest, args.pr, args.head_sha)
        except BundleError as exc:
            print(f"render に失敗: {exc}", file=sys.stderr)
            return 1
        _emit(comment, args.output)
        return 0

    if args.generate:
        if args.spec_file is None or args.decision is None:
            parser.error("--generate には --spec-file と --decision が必要")
        try:
            summary = generate_release_evidence(
                spec_path=args.spec_file,
                pr=args.pr,
                head_sha=args.head_sha,
                decision=args.decision,
                out_root=args.out_root.resolve(),
                cwd=args.cwd.resolve(),
                repository=args.repository,
            )
        except (BundleError, eg.EvidenceGenerationError) as exc:
            print(f"generate に失敗: {exc}", file=sys.stderr)
            return 1
        _emit(json.dumps(summary, indent=2, ensure_ascii=False), args.output)
        return 0 if summary["usable"] else 1

    if args.comment_file is None:
        parser.error("--comment-file（または --render / --generate）を指定する")
    try:
        text = args.comment_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"comment file を読めない: {exc}", file=sys.stderr)
        return 1
    result = validate_bundle_comment(text, expected_pr=args.pr, expected_head_sha=args.head_sha)
    payload = result.to_json()
    payload["decision"] = None if result.marker is None else result.marker.decision
    payload["bundle_structure_ok"] = bundle_structure_ok(result)
    if result.ok and args.archive_root is not None:
        bundle = parse_bundle_comment(text)
        assert bundle is not None
        try:
            archived = materialize_bundle(bundle, args.archive_root.resolve())
        except BundleError as exc:
            print(f"archive に失敗: {exc}", file=sys.stderr)
            return 1
        payload["archived"] = archived.as_posix()
    _emit(json.dumps(payload, indent=2, ensure_ascii=False), args.output)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
