#!/usr/bin/env python3
"""S1₂ の root に載せる claim closure artifact を決定的に組み立てる CLI。

DEC-20260815-003（B+・決定 2／5／6）と HRAI-REAUDIT-20260815 P0-01（PR-F0）の実装である。
post-S2 回復プログラムの再 closeout（T₂→S1₂→R₂→S2₂）で、owner claim ごとの
`schema:claim-closure/v2`／`schema:gated-residual/v2` artifact を **手書きさせずに**
組み立てることだけを担う。

## 本 CLI がすること／しないこと

- する: 期待 slot の導出（`closeout_expectation.derive_expected_slots` が唯一の正本）、
  root_sequence と evidence ID の事前導出（writer と同じ canonical 規則）、artifact 本体の
  組み立て、`claim_closure.validate_claim_closure_artifact` による自己検証、artifact の
  append-only 書き込み、writer `--roots` 用 descriptor の出力。
- しない: test の実行（`test_results` の observed は `--claim-command-manifest` が指す
  **実行済み artifact** から機械抽出する。実行は `scripts/ai/generate_ac_test_evidence.py`
  ／別工程が担う）。registry entry の登録（R₂ の仕事）。closure event の生成
  （`scripts/ai/claim_closure_events.py`）。plan／checkpoint／flag への書き込み。
  R-025 の outcome／verdict の参照。

## fail-close

期待 slot・前提 token・claim binding のいずれかが解決できない場合、artifact を **1 件も
書かない**（部分生成を作らない）。`observed`／`raw_artifact_sha256` を入力へ手書きすると
拒否する（observed は実行済み artifact から、SHA は実 file から機械的に埋める）。
allowlist 外 claim への `gated-residual/v2`、既存 artifact の上書き、`--out-dir` の外への
書き込みも拒否する。

## evidence ID の一致

writer（`scripts/ai/write_task_closeout_event.py`）は `--roots` の descriptor を
`task_closeout_event.build_evidence_roots` へ渡し、canonical 順（descriptor projection の
UTF-8 byte 昇順）で `root_sequence` を 1..N に割り当ててから
`task_closeout_event.derive_evidence_id` で ID を作る。projection の JCS key 順は
`artifact_path` が先頭であり artifact path は batch 内で一意なので、**順序は artifact path
の canonical byte 順だけで決まる**（`assign_root_sequences`）。本 CLI はこの規則で ID を
事前導出し、artifact 本体の `evidence_id` へ埋める。writer 側と一致することは
`tests/ai/test_build_claim_closure_artifacts.py` が `build_evidence_roots` との照合で固定
する。

## 入力

`--ac-evidence-index`（evidence index）::

    schema: claim-closure-input/evidence-index/v1
    entries:
      - subject_id: ac:N-594B:AC-01
        evidence_type: evidence-type:ac_leaf_result
        phase_or_context_id: task:N-594B
        evidence_id: EV-<64 hex>
        artifact_path: docs/ai/evidence/ac/N-594B/ac-01-2026-08-15-v2.json
        binding_digest: <64 hex>
        tested_head: <40 hex>
        evaluation_head: <40 hex>

batch の非 claim slot（task terminal／scope AC leaf／global leaf）は slot tuple で、claim の
前提 token（`dependencies ∪ ac_refs ∪ {release_gate}` のうち batch 外のもの）は `subject_id`
で引く（同一 subject が複数 entry にあると ambiguous として拒否する）。

`--claim-command-manifest`（claim binding）::

    schema: claim-closure-input/claim-command-manifest/v1
    claims:
      COMPUTE-REQ-001:
        release_gate_evidence_tokens: [ac:N-594B:AC-02]
        tests:
          - polarity: positive
            test: <manifest claim の positive_tests 全文>
            command_id: pytest-n-594b-ac-02
            verification_id: pytest-n-594b-ac-02
            parser_id: pytest-ra-stdout-v1
            derivation_id: evidence-v2-observed-value/v1
            expected: {type: integer, value: 0}
            observed_source:
              artifact_path: docs/ai/evidence/ac/N-594B/ac-02-2026-08-15-v2.json
              pointer: /commands/0/observed_exit_code
      COMPUTE-REQ-003:
        residual:
          gated_by: [gate:R025-FREEZE-07]
          owner: <人間 owner>
          review_due: 2026-09-15T00:00:00Z
          blocker_truth_snapshot: [...]
          attempts: [...]
          minimal_containment: {...}

`observed` は書けない（あれば拒否）。`raw_artifact_sha256` も書けない（実 file から実測
する）。`release_gate_evidence_tokens` は claim の `release_gate` を展開した leaf 集合の
部分集合でなければならない（gate 外の証跡で release gate を pass にしない）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import claim_closure  # noqa: E402
from scripts.ai import closeout_expectation as expectation  # noqa: E402
from scripts.ai.task_closeout_event import (  # noqa: E402
    TRACKED_PATH_RE,
    canonical_content_bytes,
    derive_evidence_id,
    sha256_bytes,
)

MANIFEST_REL: Final = "docs/audits/audit-materialization-manifest-2026-08-12.yml"
DEFAULT_CLOSEOUT_ROLE: Final = "task_terminal"
DEFAULT_CLOSEOUT_SEQUENCE: Final = 2
DEFAULT_OUT_DIR_TEMPLATE: Final = "docs/ai/evidence/closeout/{task_id}/{merged_pr}"

EVIDENCE_INDEX_SCHEMA: Final = "claim-closure-input/evidence-index/v1"
CLAIM_BINDING_SCHEMA: Final = "claim-closure-input/claim-command-manifest/v1"
BUILD_PLAN_SCHEMA: Final = "claim-closure-build-plan/v1"

EVIDENCE_INDEX_ENTRY_FIELDS: Final = (
    "subject_id",
    "evidence_type",
    "phase_or_context_id",
    "evidence_id",
    "artifact_path",
    "binding_digest",
    "tested_head",
    "evaluation_head",
)
TEST_BINDING_FIELDS: Final = (
    "polarity",
    "test",
    "command_id",
    "verification_id",
    "parser_id",
    "derivation_id",
    "expected",
    "observed_source",
)
OBSERVED_SOURCE_FIELDS: Final = ("artifact_path", "pointer")
CLAIM_BINDING_FIELDS: Final = ("release_gate_evidence_tokens", "tests", "residual")
RESIDUAL_BINDING_FIELDS: Final = (
    "gated_by",
    "owner",
    "review_due",
    "blocker_truth_snapshot",
    "attempts",
    "minimal_containment",
)
BLOCKER_BINDING_FIELDS: Final = (
    "token",
    "evaluator_id",
    "evaluation_head",
    "evaluated_at",
    "raw_artifact_path",
)
ATTEMPT_BINDING_FIELDS: Final = (
    "kind",
    "reference",
    "exit_status",
    "attempted_at",
    "raw_artifact_path",
)
CONTAINMENT_BINDING_FIELDS: Final = (
    "containment",
    "verification_command_id",
    "verification_result",
    "raw_artifact_path",
)

# 入力へ手書きさせない field 名（observed は実行結果から、SHA は実 file から埋める）。
FORBIDDEN_INPUT_KEYS: Final = frozenset({"observed", "raw_artifact_sha256"})

EXIT_OK: Final = 0
EXIT_INCOMPLETE: Final = 1
EXIT_INPUT_ERROR: Final = 2

_HEX40_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_ID_RE: Final = re.compile(r"^EV-[0-9a-f]{64}$")
_CLAIM_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TASK_ID_RE: Final = re.compile(r"^(?:[A-Z]+-[0-9]+[A-Z]?|R-[0-9]{3})$")
_OUT_DIR_RE: Final = re.compile(r"^docs/[A-Za-z0-9][A-Za-z0-9._/\-]*$")


class BuildError(ValueError):
    """入力契約違反（fail-close で exit 2・artifact を 1 件も書かない）。"""


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------


def _utf8_sorted(items: Sequence[str]) -> list[str]:
    return sorted(items, key=lambda item: item.encode("utf-8"))


def _closed_mapping(value: object, fields: Sequence[str], *, label: str) -> dict[str, Any]:
    """closed object（exact field 集合）を検査して dict を返す。"""
    if not isinstance(value, Mapping):
        raise BuildError(f"{label} が object ではない")
    keys = set(value)
    expected = set(fields)
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    if missing:
        raise BuildError(f"{label}: 必須 field 欠落: {missing}")
    if extra:
        raise BuildError(f"{label}: 未知 field（closed schema）: {extra}")
    return dict(value)


def _reject_forbidden_keys(node: object, *, path: str) -> None:
    """入力 document から `observed`／`raw_artifact_sha256` の手書きを再帰的に拒否する。"""
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}/{key}"
            if isinstance(key, str) and key in FORBIDDEN_INPUT_KEYS:
                raise BuildError(
                    f"{child}: {key} は入力へ手書きできない"
                    "（observed は実行済み artifact から、SHA は実 file から機械的に埋める）"
                )
            _reject_forbidden_keys(value, path=child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_forbidden_keys(item, path=f"{path}[{index}]")


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BuildError(f"{label} を読めない: {path}: {exc}") from exc
    document, issues = claim_closure.load_closure_document(raw, fmt="yaml")
    if issues:
        raise BuildError(f"{label} を parse できない: {path}: {'; '.join(issues)}")
    if not isinstance(document, Mapping):
        raise BuildError(f"{label} の root が mapping ではない: {path}")
    return dict(document)


def resolve_json_pointer(document: object, pointer: str) -> object:
    """RFC 6901 の JSON Pointer を解決する（未解決は BuildError）。"""
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise BuildError(f"JSON Pointer は空文字列か / 始まりが必要である: {pointer!r}")
    node: object = document
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping):
            if key not in node:
                raise BuildError(f"JSON Pointer {pointer} の {key!r} が解決しない")
            node = node[key]
        elif isinstance(node, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key) or int(key) >= len(node):
                raise BuildError(f"JSON Pointer {pointer} の配列 index {key!r} が解決しない")
            node = node[int(key)]
        else:
            raise BuildError(f"JSON Pointer {pointer} が scalar を辿ろうとしている")
    return node


def _repo_file_bytes(repo_root: Path, artifact_path: object, *, label: str) -> bytes:
    """repo 追跡 path（`docs/`／`tests/`／`scripts/` 配下）の raw bytes を読む。"""
    if not isinstance(artifact_path, str) or not artifact_path:
        raise BuildError(f"{label}: artifact_path が非空文字列ではない")
    if ".." in artifact_path.split("/") or artifact_path.startswith("/"):
        raise BuildError(f"{label}: artifact_path に traversal がある: {artifact_path}")
    if not re.match(r"^(?:docs|tests|scripts)/", artifact_path):
        raise BuildError(
            f"{label}: artifact_path は docs/／tests/／scripts/ 配下が必要である: {artifact_path}"
        )
    target = (repo_root / artifact_path).resolve()
    if not target.is_relative_to(repo_root.resolve()):
        raise BuildError(f"{label}: artifact_path が repo 外へ解決される: {artifact_path}")
    if not target.is_file():
        raise BuildError(f"{label}: artifact が存在しない: {artifact_path}")
    return target.read_bytes()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BuildError(message)


# ---------------------------------------------------------------------------
# evidence index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceBinding:
    """1 件の証跡束縛（slot と subject token の両方から引ける）。"""

    subject_id: str
    evidence_type: str
    phase_or_context_id: str | None
    evidence_id: str
    artifact_path: str
    binding_digest: str
    tested_head: str
    evaluation_head: str

    @property
    def slot(self) -> expectation.SlotKey:
        return (self.subject_id, self.evidence_type, self.phase_or_context_id)


@dataclass(frozen=True)
class EvidenceIndex:
    """evidence index（slot 索引と subject 索引）。"""

    by_slot: Mapping[expectation.SlotKey, EvidenceBinding]
    by_subject: Mapping[str, tuple[EvidenceBinding, ...]]

    def subject(self, token: str) -> EvidenceBinding:
        candidates = self.by_subject.get(token, ())
        if not candidates:
            raise BuildError(f"evidence index に前提 token {token} の entry がない")
        if len(candidates) > 1:
            raise BuildError(
                f"evidence index の前提 token {token} が {len(candidates)} 件に解決する"
                "（ambiguous）"
            )
        return candidates[0]


def load_evidence_index(path: Path) -> EvidenceIndex:
    """evidence index YAML を closed schema で読む。"""
    document = _load_yaml_mapping(path, label="evidence index")
    _reject_forbidden_keys(document, path="evidence-index")
    _require(
        document.get("schema") == EVIDENCE_INDEX_SCHEMA,
        f"evidence index の schema が {EVIDENCE_INDEX_SCHEMA} ではない: {document.get('schema')!r}",
    )
    entries = document.get("entries")
    _require(
        isinstance(entries, list) and bool(entries), "evidence index の entries が非空配列ではない"
    )
    assert isinstance(entries, list)
    by_slot: dict[expectation.SlotKey, EvidenceBinding] = {}
    by_subject: dict[str, list[EvidenceBinding]] = {}
    for index, item in enumerate(entries):
        label = f"evidence index entries[{index}]"
        obj = _closed_mapping(item, EVIDENCE_INDEX_ENTRY_FIELDS, label=label)
        subject = obj["subject_id"]
        evidence_type = obj["evidence_type"]
        phase = obj["phase_or_context_id"]
        _require(
            isinstance(subject, str) and ":" in subject, f"{label}: subject_id が token ではない"
        )
        _require(
            isinstance(evidence_type, str) and evidence_type.startswith("evidence-type:"),
            f"{label}: evidence_type が evidence-type: token ではない",
        )
        _require(
            phase is None or (isinstance(phase, str) and bool(phase)),
            f"{label}: phase_or_context_id が文字列でも null でもない",
        )
        for name, pattern in (
            ("evidence_id", _EVIDENCE_ID_RE),
            ("binding_digest", _HEX64_RE),
            ("tested_head", _HEX40_RE),
            ("evaluation_head", _HEX40_RE),
        ):
            value = obj[name]
            _require(
                isinstance(value, str) and bool(pattern.match(value)),
                f"{label}: {name} が規定の形式ではない: {value!r}",
            )
        artifact_path = obj["artifact_path"]
        _require(
            isinstance(artifact_path, str) and bool(TRACKED_PATH_RE.match(artifact_path)),
            f"{label}: artifact_path が docs/ 配下の追跡 path ではない: {artifact_path!r}",
        )
        binding = EvidenceBinding(
            subject_id=str(subject),
            evidence_type=str(evidence_type),
            phase_or_context_id=None if phase is None else str(phase),
            evidence_id=str(obj["evidence_id"]),
            artifact_path=str(artifact_path),
            binding_digest=str(obj["binding_digest"]),
            tested_head=str(obj["tested_head"]),
            evaluation_head=str(obj["evaluation_head"]),
        )
        if binding.slot in by_slot:
            raise BuildError(f"evidence index に同じ slot が重複している: {binding.slot!r}")
        by_slot[binding.slot] = binding
        by_subject.setdefault(binding.subject_id, []).append(binding)
    paths = [binding.artifact_path for binding in by_slot.values()]
    _require(len(set(paths)) == len(paths), "evidence index に同じ artifact_path が重複している")
    return EvidenceIndex(
        by_slot=by_slot,
        by_subject={key: tuple(value) for key, value in by_subject.items()},
    )


# ---------------------------------------------------------------------------
# claim binding（command manifest）
# ---------------------------------------------------------------------------


def load_claim_bindings(path: Path) -> dict[str, dict[str, Any]]:
    """claim command manifest YAML を closed schema で読む（observed 手書きは拒否）。"""
    document = _load_yaml_mapping(path, label="claim command manifest")
    _reject_forbidden_keys(document, path="claim-command-manifest")
    _require(
        document.get("schema") == CLAIM_BINDING_SCHEMA,
        f"claim command manifest の schema が {CLAIM_BINDING_SCHEMA} ではない:"
        f" {document.get('schema')!r}",
    )
    claims = document.get("claims")
    _require(isinstance(claims, Mapping), "claim command manifest の claims が mapping ではない")
    assert isinstance(claims, Mapping)
    bindings: dict[str, dict[str, Any]] = {}
    for claim_id, entry in claims.items():
        _require(
            isinstance(claim_id, str) and bool(_CLAIM_ID_RE.match(claim_id)),
            f"claim command manifest の claim key が claim ID ではない: {claim_id!r}",
        )
        _require(
            isinstance(entry, Mapping),
            f"claim command manifest の {claim_id} が object ではない",
        )
        assert isinstance(entry, Mapping)
        unknown = sorted(set(entry) - set(CLAIM_BINDING_FIELDS))
        _require(
            not unknown, f"claim command manifest の {claim_id} に未知 field がある: {unknown}"
        )
        bindings[str(claim_id)] = dict(entry)
    return bindings


# ---------------------------------------------------------------------------
# root_sequence と evidence ID
# ---------------------------------------------------------------------------


def assign_root_sequences(
    paths_by_slot: Mapping[expectation.SlotKey, str],
) -> dict[expectation.SlotKey, int]:
    """artifact path の canonical byte 昇順で `root_sequence` を 1..N へ割り当てる。

    writer（`task_closeout_event.build_evidence_roots`）は descriptor projection
    （`ROOT_DESCRIPTOR_FIELDS`）の canonical byte 順で並べる。JCS の key 順は
    `artifact_path` が先頭であり path は batch 内で一意なので、順序は path の canonical
    byte 順だけで決まる。
    """
    paths = list(paths_by_slot.values())
    if len(set(paths)) != len(paths):
        raise BuildError(
            "同じ artifact_path を持つ slot がある（root tuple は path 重複を拒否する）"
        )
    ordered = sorted(paths_by_slot.items(), key=lambda item: canonical_content_bytes(item[1]))
    return {slot: index for index, (slot, _path) in enumerate(ordered, start=1)}


def derive_slot_evidence_ids(
    paths_by_slot: Mapping[expectation.SlotKey, str],
    *,
    task_id: str,
    merged_pr: int,
    closeout_role: str,
    closeout_sequence: int,
) -> dict[expectation.SlotKey, str]:
    """batch 全 slot の evidence ID を writer と同じ canonical 規則で事前導出する。"""
    sequences = assign_root_sequences(paths_by_slot)
    return {
        slot: derive_evidence_id(
            task_id=task_id,
            merged_pr=merged_pr,
            closeout_role=closeout_role,
            closeout_sequence=closeout_sequence,
            subject_id=slot[0],
            evidence_type=slot[1],
            phase_or_context_id=slot[2],
            root_sequence=sequences[slot],
        )
        for slot in paths_by_slot
    }


# ---------------------------------------------------------------------------
# build plan
# ---------------------------------------------------------------------------


@dataclass
class BuildPlan:
    """期待 slot と入力充足状況（dry-run 出力の元）。"""

    task_id: str
    merged_pr: int
    closeout_sequence: int
    slots: list[expectation.ExpectedSlot] = field(default_factory=list)
    claim_slots: list[expectation.ExpectedSlot] = field(default_factory=list)
    out_dir_rel: str = ""
    missing_index_slots: list[expectation.SlotKey] = field(default_factory=list)
    missing_prerequisite_tokens: list[str] = field(default_factory=list)
    missing_claim_bindings: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not (
            self.issues
            or self.missing_index_slots
            or self.missing_prerequisite_tokens
            or self.missing_claim_bindings
        )

    def kind_counts(self) -> dict[str, int]:
        counts = {kind: 0 for kind in expectation.SLOT_KINDS}
        for slot in self.slots:
            counts[slot.kind] = counts.get(slot.kind, 0) + 1
        return counts

    def to_descriptor(self) -> dict[str, Any]:
        counts = self.kind_counts()
        return {
            "schema": BUILD_PLAN_SCHEMA,
            "task_id": self.task_id,
            "merged_pr": self.merged_pr,
            "closeout_sequence": self.closeout_sequence,
            "out_dir": self.out_dir_rel,
            "expected_slot_count": len(self.slots),
            "slot_kind_counts": counts,
            "claim_slot_count": len(self.claim_slots),
            "claim_slots": [
                {
                    "claim_id": slot.claim_id,
                    "kind": slot.kind,
                    "evidence_type": slot.evidence_type,
                }
                for slot in self.claim_slots
            ],
            "missing_inputs": {
                "evidence_index_slots": [list(slot) for slot in self.missing_index_slots],
                "evidence_index_prerequisite_tokens": list(self.missing_prerequisite_tokens),
                "claim_command_manifest_claims": list(self.missing_claim_bindings),
            },
            "issues": list(self.issues),
            "ready": self.ready,
        }


def _claim_prerequisite_universe(claim: Mapping[str, Any]) -> list[str]:
    """`dependencies ∪ ac_refs ∪ {release_gate}`（宣言順・重複なし）。

    `dependencies ∪ ac_refs` の部分は `claim_closure.prerequisite_tokens`（validator source）
    をそのまま使い、closure artifact 側の exact 照合と定義がずれないようにする。
    """
    tokens = claim_closure.prerequisite_tokens(claim)
    release_gate = claim.get("release_gate")
    if isinstance(release_gate, str) and release_gate not in tokens:
        tokens.append(release_gate)
    return tokens


def _binding_gated_by(binding: Mapping[str, Any] | None) -> set[str]:
    residual = binding.get("residual") if isinstance(binding, Mapping) else None
    tokens = residual.get("gated_by") if isinstance(residual, Mapping) else None
    if not isinstance(tokens, list):
        return set()
    return {item for item in tokens if isinstance(item, str)}


def _binding_release_gate_tokens(binding: Mapping[str, Any] | None) -> list[str]:
    tokens = binding.get("release_gate_evidence_tokens") if isinstance(binding, Mapping) else None
    if not isinstance(tokens, list):
        return []
    return [item for item in tokens if isinstance(item, str)]


def required_prerequisite_tokens(
    slot: expectation.ExpectedSlot,
    claim: Mapping[str, Any],
    binding: Mapping[str, Any] | None,
) -> list[str]:
    """claim 1 件が evidence index から解決しなければならない token を返す。

    - `claim_closure`（implemented_verified）: `dependencies ∪ ac_refs`（`prerequisite_results`）
      に、binding が宣言した `release_gate_evidence_tokens`（release gate の展開 leaf）を足す。
      release gate token 自身は artifact へ書かないため索引しない。
    - `gated_residual`: `dependencies ∪ ac_refs ∪ {release_gate}` から binding の `gated_by`
      （known-false blocker）を除いた `non_blocking_prerequisite_results` の集合。binding が
      無い（dry-run）ときは blocker を落とせないため保守的に全件を返す。
    """
    if slot.kind == "gated_residual":
        gated = _binding_gated_by(binding)
        return [token for token in _claim_prerequisite_universe(claim) if token not in gated]
    tokens = claim_closure.prerequisite_tokens(claim)
    for token in _binding_release_gate_tokens(binding):
        if token not in tokens:
            tokens.append(token)
    return tokens


def build_plan(
    manifest: Mapping[str, Any],
    *,
    task_id: str,
    merged_pr: int,
    closeout_sequence: int,
    out_dir_rel: str,
    index: EvidenceIndex | None,
    bindings: Mapping[str, Mapping[str, Any]] | None,
) -> BuildPlan:
    """期待 slot を導出し、不足している入力を列挙する（副作用なし）。"""
    slots, issues = expectation.derive_expected_slots(manifest, task_id)
    plan = BuildPlan(
        task_id=task_id,
        merged_pr=merged_pr,
        closeout_sequence=closeout_sequence,
        slots=list(slots),
        claim_slots=[slot for slot in slots if slot.claim_id is not None],
        out_dir_rel=out_dir_rel,
        issues=list(issues),
    )
    if not slots:
        return plan
    in_batch_claims = {slot.claim_id for slot in plan.claim_slots if slot.claim_id}
    for slot in slots:
        if slot.claim_id is not None:
            continue
        if index is None or slot.slot not in index.by_slot:
            plan.missing_index_slots.append(slot.slot)
    prerequisite_tokens: list[str] = []
    for slot in plan.claim_slots:
        claim = claim_closure.claim_definition(manifest, str(slot.claim_id))
        if claim is None:
            plan.issues.append(f"manifest に claim {slot.claim_id} がない")
            continue
        binding = bindings.get(str(slot.claim_id)) if bindings is not None else None
        if binding is None:
            plan.missing_claim_bindings.append(str(slot.claim_id))
        for token in required_prerequisite_tokens(slot, claim, binding):
            if token.startswith("claim:") and token[len("claim:") :] in in_batch_claims:
                continue
            if token not in prerequisite_tokens:
                prerequisite_tokens.append(token)
    for token in prerequisite_tokens:
        if index is None or len(index.by_subject.get(token, ())) != 1:
            plan.missing_prerequisite_tokens.append(token)
    plan.missing_index_slots.sort(key=repr)
    plan.missing_prerequisite_tokens[:] = _utf8_sorted(plan.missing_prerequisite_tokens)
    plan.missing_claim_bindings[:] = _utf8_sorted(plan.missing_claim_bindings)
    return plan


# ---------------------------------------------------------------------------
# artifact 組み立て
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeadBinding:
    """P（実装 head）／E（evidence anchor）／M（actual merge terminal）。"""

    implementation_head: str
    evidence_anchor_head: str | None
    actual_merge_terminal_sha: str | None

    def to_object(self) -> dict[str, Any]:
        return {
            "implementation_head": self.implementation_head,
            "evidence_anchor_head": self.evidence_anchor_head,
            "actual_merge_terminal_sha": self.actual_merge_terminal_sha,
        }


@dataclass
class ArtifactBuilder:
    """1 batch 分の claim closure artifact を決定的に組み立てる。"""

    manifest: Mapping[str, Any]
    repo_root: Path
    plan: BuildPlan
    index: EvidenceIndex
    bindings: Mapping[str, Mapping[str, Any]]
    heads: HeadBinding
    evidence_ids: Mapping[expectation.SlotKey, str]
    _built: dict[str, dict[str, Any]] = field(default_factory=dict)
    _raw: dict[str, bytes] = field(default_factory=dict)

    # -- 前提 -------------------------------------------------------------

    def _internal_claim_slot(self, claim_id: str) -> expectation.ExpectedSlot | None:
        for slot in self.plan.claim_slots:
            if slot.claim_id == claim_id:
                return slot
        return None

    def _prerequisite_item(self, token: str) -> dict[str, Any]:
        """前提 token 1 件を `PREREQUISITE_FIELDS` の closed item へ解決する。"""
        if token.startswith("claim:"):
            claim_id = token[len("claim:") :]
            slot = self._internal_claim_slot(claim_id)
            if slot is not None:
                raw = self._raw.get(claim_id)
                if raw is None:
                    raise BuildError(
                        f"claim:{claim_id} は同 batch の前提だが依存順が解決していない"
                        "（claim dependency に循環がある）"
                    )
                return {
                    "subject": token,
                    "subject_tested_head": self.heads.implementation_head,
                    "evidence_id": self.evidence_ids[slot.slot],
                    "binding_digest": sha256_bytes(raw),
                    "evaluation_head": self.heads.implementation_head,
                    "result": "pass",
                }
        binding = self.index.subject(token)
        return {
            "subject": token,
            "subject_tested_head": binding.tested_head,
            "evidence_id": binding.evidence_id,
            "binding_digest": binding.binding_digest,
            "evaluation_head": binding.evaluation_head,
            "result": "pass",
        }

    # -- test_results -----------------------------------------------------

    def _typed_expected(self, value: object, *, label: str) -> tuple[str, Any]:
        obj = _closed_mapping(value, ("type", "value"), label=label)
        kind = obj["type"]
        if not isinstance(kind, str) or kind not in claim_closure.TYPED_VALUE_TYPES:
            raise BuildError(f"{label}.type が closed enum ではない: {kind!r}")
        return kind, obj["value"]

    def _observed_value(self, kind: str, source: object, *, label: str) -> tuple[Any, str, str]:
        """実行済み artifact から observed 値・path・raw SHA を機械抽出する。"""
        obj = _closed_mapping(source, OBSERVED_SOURCE_FIELDS, label=label)
        artifact_path = obj["artifact_path"]
        pointer = obj["pointer"]
        if not isinstance(pointer, str):
            raise BuildError(f"{label}.pointer が文字列ではない")
        raw = _repo_file_bytes(self.repo_root, artifact_path, label=label)
        fmt = claim_closure.format_for_path(str(artifact_path))
        if fmt is None:
            raise BuildError(f"{label}: artifact format を拡張子から決められない: {artifact_path}")
        document, issues = claim_closure.load_closure_document(raw, fmt=fmt)
        if issues:
            raise BuildError(f"{label}: 実行結果 artifact を parse できない: {'; '.join(issues)}")
        value = resolve_json_pointer(document, pointer)
        expected_type = claim_closure.TYPED_VALUE_TYPES[kind]
        if isinstance(value, bool) != (expected_type is bool) or not isinstance(
            value, expected_type
        ):
            raise BuildError(
                f"{label}: 実行結果の値 {value!r} が expected.type={kind} と型一致しない"
            )
        return value, str(artifact_path), sha256_bytes(raw)

    def _test_results(
        self,
        claim_id: str,
        binding: Mapping[str, Any],
        *,
        positive: Sequence[str],
        negative: Sequence[str],
    ) -> list[dict[str, Any]]:
        entries = binding.get("tests")
        if not isinstance(entries, list) or not entries:
            raise BuildError(f"{claim_id}: claim binding の tests が非空配列ではない")
        results: list[dict[str, Any]] = []
        seen: list[tuple[str, str]] = []
        for index, item in enumerate(entries):
            label = f"{claim_id}.tests[{index}]"
            obj = _closed_mapping(item, TEST_BINDING_FIELDS, label=label)
            polarity = obj["polarity"]
            test = obj["test"]
            _require(
                polarity in {"positive", "negative"}, f"{label}.polarity が closed enum ではない"
            )
            _require(isinstance(test, str) and bool(test), f"{label}.test が非空文字列ではない")
            seen.append((str(polarity), str(test)))
            kind, expected_value = self._typed_expected(obj["expected"], label=f"{label}.expected")
            observed_value, raw_path, raw_sha = self._observed_value(
                kind, obj["observed_source"], label=f"{label}.observed_source"
            )
            if observed_value != expected_value:
                raise BuildError(
                    f"{label}: 実行結果 observed={observed_value!r} が事前登録 expected="
                    f"{expected_value!r} と一致しない（result=pass にしない）"
                )
            results.append(
                {
                    "test": str(test),
                    "polarity": str(polarity),
                    "command_id": obj["command_id"],
                    "verification_id": obj["verification_id"],
                    "parser_id": obj["parser_id"],
                    "derivation_id": obj["derivation_id"],
                    "expected": {"type": kind, "value": expected_value},
                    "observed": {"type": kind, "value": observed_value},
                    "raw_artifact_path": raw_path,
                    "raw_artifact_sha256": raw_sha,
                    "result": "pass",
                }
            )
        expected_pairs = [("positive", test) for test in positive] + [
            ("negative", test) for test in negative
        ]
        missing = sorted(set(expected_pairs) - set(seen))
        extra = sorted(set(seen) - set(expected_pairs))
        if missing or extra:
            raise BuildError(
                f"{claim_id}: claim binding の tests が manifest claim の全文 exact 集合と"
                f"一致しない（missing={missing}, extra={extra}）"
            )
        return results

    # -- release gate -----------------------------------------------------

    def _release_gate_result(
        self, claim_id: str, claim: Mapping[str, Any], binding: Mapping[str, Any]
    ) -> dict[str, Any]:
        release_gate = claim.get("release_gate")
        _require(
            isinstance(release_gate, str) and bool(release_gate),
            f"{claim_id}: manifest claim に release_gate がない",
        )
        tokens = binding.get("release_gate_evidence_tokens")
        _require(
            isinstance(tokens, list) and bool(tokens),
            f"{claim_id}: claim binding の release_gate_evidence_tokens が非空配列ではない",
        )
        assert isinstance(tokens, list)
        leaves, issues = expectation.expand_tokens(
            self.manifest, [str(release_gate)], label=f"{claim_id} release_gate"
        )
        if issues:
            raise BuildError(f"{claim_id}: release_gate の展開に失敗: {'; '.join(issues)}")
        allowed = set(leaves)
        evidence_ids: list[str] = []
        for token in tokens:
            _require(
                isinstance(token, str), f"{claim_id}: release_gate_evidence_tokens に非文字列がある"
            )
            _require(
                str(token) in allowed,
                f"{claim_id}: release_gate_evidence_tokens の {token} が"
                f" {release_gate} の展開 leaf に含まれない",
            )
            evidence_ids.append(self._prerequisite_item(str(token))["evidence_id"])
        unique = sorted(set(evidence_ids), key=lambda item: item.encode("utf-8"))
        _require(
            len(unique) == len(evidence_ids),
            f"{claim_id}: release_gate_evidence_tokens が同じ evidence へ重複解決する",
        )
        return {
            "result": "pass",
            "evaluation_head": self.heads.implementation_head,
            "evidence_ids": unique,
        }

    # -- gated residual ---------------------------------------------------

    def _residual_sections(
        self, claim_id: str, claim: Mapping[str, Any], binding: Mapping[str, Any]
    ) -> dict[str, Any]:
        residual = binding.get("residual")
        _require(
            isinstance(residual, Mapping), f"{claim_id}: claim binding に residual object がない"
        )
        assert isinstance(residual, Mapping)
        obj = _closed_mapping(residual, RESIDUAL_BINDING_FIELDS, label=f"{claim_id}.residual")
        gated_by = obj["gated_by"]
        _require(
            isinstance(gated_by, list)
            and bool(gated_by)
            and all(isinstance(item, str) for item in gated_by),
            f"{claim_id}.residual.gated_by が非空の文字列配列ではない",
        )
        assert isinstance(gated_by, list)
        gated_tokens = [str(item) for item in gated_by]
        universe = _claim_prerequisite_universe(claim)
        outside = [token for token in gated_tokens if token not in universe]
        _require(
            not outside,
            f"{claim_id}.residual.gated_by が dependencies∪ac_refs∪{{release_gate}} の"
            f"部分集合ではない: {outside}",
        )
        snapshots = obj["blocker_truth_snapshot"]
        _require(
            isinstance(snapshots, list),
            f"{claim_id}.residual.blocker_truth_snapshot が配列ではない",
        )
        assert isinstance(snapshots, list)
        snapshot_objects: list[dict[str, Any]] = []
        for index, item in enumerate(snapshots):
            label = f"{claim_id}.residual.blocker_truth_snapshot[{index}]"
            entry = _closed_mapping(item, BLOCKER_BINDING_FIELDS, label=label)
            raw = _repo_file_bytes(self.repo_root, entry["raw_artifact_path"], label=label)
            snapshot_objects.append(
                {
                    "token": entry["token"],
                    "truth": False,
                    "evaluator_id": entry["evaluator_id"],
                    "evaluation_head": entry["evaluation_head"],
                    "evaluated_at": entry["evaluated_at"],
                    "raw_artifact_path": entry["raw_artifact_path"],
                    "raw_artifact_sha256": sha256_bytes(raw),
                }
            )
        attempts = obj["attempts"]
        _require(
            isinstance(attempts, list) and bool(attempts),
            f"{claim_id}.residual.attempts が非空配列ではない",
        )
        assert isinstance(attempts, list)
        attempt_objects: list[dict[str, Any]] = []
        for index, item in enumerate(attempts):
            label = f"{claim_id}.residual.attempts[{index}]"
            entry = _closed_mapping(item, ATTEMPT_BINDING_FIELDS, label=label)
            raw = _repo_file_bytes(self.repo_root, entry["raw_artifact_path"], label=label)
            attempt_objects.append(
                {
                    "kind": entry["kind"],
                    "reference": entry["reference"],
                    "exit_status": entry["exit_status"],
                    "attempted_at": entry["attempted_at"],
                    "raw_artifact_sha256": sha256_bytes(raw),
                }
            )
        containment = _closed_mapping(
            obj["minimal_containment"],
            CONTAINMENT_BINDING_FIELDS,
            label=f"{claim_id}.residual.minimal_containment",
        )
        containment_raw = _repo_file_bytes(
            self.repo_root,
            containment["raw_artifact_path"],
            label=f"{claim_id}.residual.minimal_containment",
        )
        non_blocking = [token for token in universe if token not in set(gated_tokens)]
        return {
            "gated_by": gated_tokens,
            "blocker_truth_snapshot": snapshot_objects,
            "non_blocking_prerequisite_results": [
                self._prerequisite_item(token) for token in non_blocking
            ],
            "attempts": attempt_objects,
            "minimal_containment": {
                "containment": containment["containment"],
                "verification_command_id": containment["verification_command_id"],
                "verification_result": containment["verification_result"],
                "raw_artifact_sha256": sha256_bytes(containment_raw),
            },
            "owner": obj["owner"],
            "review_due": obj["review_due"],
        }

    # -- claim 1 件 -------------------------------------------------------

    def build_claim_artifact(self, slot: expectation.ExpectedSlot) -> dict[str, Any]:
        claim_id = str(slot.claim_id)
        claim = claim_closure.claim_definition(self.manifest, claim_id)
        if claim is None:
            raise BuildError(f"manifest に claim {claim_id} がない")
        binding = self.bindings.get(claim_id)
        if binding is None:
            raise BuildError(f"claim command manifest に {claim_id} の binding がない")
        evidence_id = self.evidence_ids[slot.slot]
        common: dict[str, Any] = {
            "evidence_id": evidence_id,
            "claim_id": f"claim:{claim_id}",
            "closure_owner_task": claim.get("closure_owner_task"),
            "adoption": claim.get("adoption"),
            "heads": self.heads.to_object(),
        }
        if slot.kind == "gated_residual":
            allowed_context = claim_closure.GATED_RESIDUAL_ALLOWLIST.get(claim_id)
            _require(
                allowed_context is not None,
                f"{claim_id}: valid_gated_residual allowlist（DEC-20260815-003 決定 5・4 claim）"
                "に含まれない（fail-close）",
            )
            declared = claim.get("closure_finalizer_context")
            _require(
                declared == allowed_context,
                f"{claim_id}: manifest の closure_finalizer_context={declared!r} が"
                f" allowlist の {allowed_context} と一致しない",
            )
            sections = self._residual_sections(claim_id, claim, binding)
            return {
                "schema": claim_closure.GATED_RESIDUAL_SCHEMA,
                **common,
                "finalizer_context": allowed_context,
                "residual_risk": claim.get("residual_risk"),
                "status": claim_closure.GATED_RESIDUAL_STATUS,
                **sections,
            }
        positive = [item for item in claim.get("positive_tests", []) if isinstance(item, str)]
        negative = [item for item in claim.get("negative_tests", []) if isinstance(item, str)]
        return {
            "schema": claim_closure.CLAIM_CLOSURE_SCHEMA,
            **common,
            "release_gate": claim.get("release_gate"),
            "release_gate_result": self._release_gate_result(claim_id, claim, binding),
            "prerequisite_results": [
                self._prerequisite_item(token) for token in claim_closure.prerequisite_tokens(claim)
            ],
            "positive_tests": positive,
            "negative_tests": negative,
            "test_results": self._test_results(
                claim_id, binding, positive=positive, negative=negative
            ),
            "status": claim_closure.CLAIM_CLOSURE_STATUS,
        }

    # -- batch ------------------------------------------------------------

    def _build_order(self) -> list[expectation.ExpectedSlot]:
        """同 batch の claim dependency を先に組み立てる決定的な topological 順を返す。"""
        by_id = {str(slot.claim_id): slot for slot in self.plan.claim_slots}
        pending = set(by_id)
        ordered: list[expectation.ExpectedSlot] = []
        while pending:
            ready = []
            for claim_id in _utf8_sorted(list(pending)):
                claim = claim_closure.claim_definition(self.manifest, claim_id)
                deps = {
                    token[len("claim:") :]
                    for token in _claim_prerequisite_universe(claim or {})
                    if token.startswith("claim:")
                }
                if not (deps & pending):
                    ready.append(claim_id)
            if not ready:
                raise BuildError(f"同 batch の claim dependency に循環がある: {sorted(pending)}")
            for claim_id in ready:
                ordered.append(by_id[claim_id])
                pending.discard(claim_id)
        return ordered

    def build_all(self) -> dict[str, dict[str, Any]]:
        """全 owner claim の artifact を組み立て、自己検証まで通してから返す。"""
        for slot in self._build_order():
            claim_id = str(slot.claim_id)
            document = self.build_claim_artifact(slot)
            issues = claim_closure.validate_claim_closure_artifact(
                document, manifest=self.manifest, claim_id=claim_id
            )
            if issues:
                raise BuildError(
                    f"{claim_id}: 組み立てた artifact が closed schema を満たさない: {issues}"
                )
            self._built[claim_id] = document
            self._raw[claim_id] = serialize_artifact(document)
        return dict(self._built)

    def raw_bytes(self) -> dict[str, bytes]:
        return dict(self._raw)


def serialize_artifact(document: Mapping[str, Any]) -> bytes:
    """artifact の決定的な JSON byte 列（key 昇順・UTF-8・末尾改行 1 個）。"""
    text = json.dumps(dict(document), ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# CLI 本体
# ---------------------------------------------------------------------------


def _optional_head(value: str | None, *, label: str) -> str | None:
    if value is None or value == "":
        return None
    if not _HEX40_RE.match(value):
        raise BuildError(f"{label} が 40 桁 lowercase hex でも空でもない: {value!r}")
    return value


def _claim_artifact_rel_path(out_dir_rel: str, claim_id: str) -> str:
    if not _CLAIM_ID_RE.match(claim_id):
        raise BuildError(f"claim ID が artifact 名に使えない: {claim_id!r}")
    return f"{out_dir_rel}/{claim_id}.json"


def _paths_by_slot(
    plan: BuildPlan, index: EvidenceIndex, *, out_dir_rel: str
) -> dict[expectation.SlotKey, str]:
    paths: dict[expectation.SlotKey, str] = {}
    for slot in plan.slots:
        if slot.claim_id is not None:
            paths[slot.slot] = _claim_artifact_rel_path(out_dir_rel, str(slot.claim_id))
            continue
        binding = index.by_slot.get(slot.slot)
        if binding is None:
            raise BuildError(f"evidence index に slot {slot.slot!r} の entry がない")
        paths[slot.slot] = binding.artifact_path
    return paths


def _check_index_evidence_ids(
    plan: BuildPlan, index: EvidenceIndex, evidence_ids: Mapping[expectation.SlotKey, str]
) -> None:
    """batch slot について index の evidence_id と導出 ID が一致することを要求する。"""
    for slot in plan.slots:
        if slot.claim_id is not None:
            continue
        binding = index.by_slot[slot.slot]
        expected = evidence_ids[slot.slot]
        if binding.evidence_id != expected:
            raise BuildError(
                f"evidence index の slot {slot.slot!r} の evidence_id"
                f" {binding.evidence_id} が canonical 導出値 {expected} と一致しない"
            )


def _roots_descriptors(
    plan: BuildPlan,
    *,
    paths_by_slot: Mapping[expectation.SlotKey, str],
    raw_by_claim: Mapping[str, bytes],
    repo_root: Path,
) -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    for slot in plan.slots:
        path = paths_by_slot[slot.slot]
        if slot.claim_id is not None:
            raw_sha = sha256_bytes(raw_by_claim[str(slot.claim_id)])
        else:
            raw_sha = sha256_bytes(_repo_file_bytes(repo_root, path, label=f"root {slot.slot!r}"))
        descriptors.append(
            {
                "subject_id": slot.subject_id,
                "evidence_type": slot.evidence_type,
                "phase_or_context_id": slot.phase_or_context_id,
                "artifact_path": path,
                "artifact_raw_sha256": raw_sha,
            }
        )
    descriptors.sort(key=lambda item: canonical_content_bytes(str(item["artifact_path"])))
    return descriptors


def _resolve_out_dir(repo_root: Path, out_dir_rel: str) -> Path:
    if not _OUT_DIR_RE.match(out_dir_rel) or ".." in out_dir_rel.split("/"):
        raise BuildError(
            "--out-dir は docs/ 配下の repo 相対 path が必要である（traversal 拒否）: "
            f"{out_dir_rel}"
        )
    target = (repo_root / out_dir_rel).resolve()
    if not target.is_relative_to(repo_root.resolve()):
        raise BuildError(f"--out-dir が repo 外へ解決されるため拒否する: {out_dir_rel}")
    return target


def load_manifest(path: Path) -> dict[str, Any]:
    """materialization manifest を duplicate key 拒否で読む。"""
    return _load_yaml_mapping(path, label="materialization manifest")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="S1₂ の claim closure artifact を manifest と binding から決定的に組み立てる"
    )
    parser.add_argument("--task-id", required=True, help="closeout する canonical task ID")
    parser.add_argument("--merged-pr", required=True, type=int, help="T₂ の merged PR 番号")
    parser.add_argument(
        "--closeout-sequence", type=int, default=DEFAULT_CLOSEOUT_SEQUENCE, help="既定 2（S1₂）"
    )
    parser.add_argument("--closeout-role", default=DEFAULT_CLOSEOUT_ROLE)
    parser.add_argument("--implementation-head", help="P（実装 head・40 hex）")
    parser.add_argument("--evidence-anchor-head", default="", help="E（未確定なら空文字列）")
    parser.add_argument("--actual-merge-terminal", default="", help="M（未確定なら空文字列）")
    parser.add_argument(
        "--ac-evidence-index", type=Path, help="AC／global／terminal slot の証跡束縛"
    )
    parser.add_argument("--claim-command-manifest", type=Path, help="claim の事前 binding")
    parser.add_argument("--out-dir", help=f"既定 {DEFAULT_OUT_DIR_TEMPLATE}")
    parser.add_argument("--roots-out", type=Path, help="writer --roots 用 descriptor YAML の出力先")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, help=f"既定 <repo-root>/{MANIFEST_REL}")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="期待 slot と不足 input を JSON で報告して終了する（何も書かない）",
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    manifest_path = args.manifest if args.manifest is not None else repo_root / MANIFEST_REL
    task_id = str(args.task_id)
    if not _TASK_ID_RE.match(task_id):
        raise BuildError(f"--task-id が canonical task ID ではない: {task_id!r}")
    if args.merged_pr < 1:
        raise BuildError(f"--merged-pr が positive integer ではない: {args.merged_pr}")
    if args.closeout_sequence < 1:
        raise BuildError(f"--closeout-sequence が 1 以上ではない: {args.closeout_sequence}")
    manifest = load_manifest(manifest_path)
    out_dir_rel = args.out_dir or DEFAULT_OUT_DIR_TEMPLATE.format(
        task_id=task_id, merged_pr=args.merged_pr
    )
    out_dir_rel = out_dir_rel.rstrip("/")
    index = load_evidence_index(args.ac_evidence_index) if args.ac_evidence_index else None
    bindings = (
        load_claim_bindings(args.claim_command_manifest) if args.claim_command_manifest else None
    )
    plan = build_plan(
        manifest,
        task_id=task_id,
        merged_pr=args.merged_pr,
        closeout_sequence=args.closeout_sequence,
        out_dir_rel=out_dir_rel,
        index=index,
        bindings=bindings,
    )
    if args.dry_run:
        print(json.dumps(plan.to_descriptor(), ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK if plan.ready else EXIT_INCOMPLETE
    if plan.issues:
        raise BuildError(f"期待 slot を導出できない: {plan.issues}")
    if index is None or bindings is None:
        raise BuildError(
            "--dry-run 以外では --ac-evidence-index と --claim-command-manifest が必要である"
        )
    if not plan.ready:
        raise BuildError(
            "入力が不足している（--dry-run で不足一覧を確認する）: "
            f"index_slots={len(plan.missing_index_slots)},"
            f" prerequisite_tokens={len(plan.missing_prerequisite_tokens)},"
            f" claim_bindings={len(plan.missing_claim_bindings)}"
        )
    if args.implementation_head is None or not _HEX40_RE.match(str(args.implementation_head)):
        raise BuildError("--implementation-head（P）は 40 桁 lowercase hex が必要である")
    heads = HeadBinding(
        implementation_head=str(args.implementation_head),
        evidence_anchor_head=_optional_head(
            args.evidence_anchor_head, label="--evidence-anchor-head"
        ),
        actual_merge_terminal_sha=_optional_head(
            args.actual_merge_terminal, label="--actual-merge-terminal"
        ),
    )
    out_dir = _resolve_out_dir(repo_root, out_dir_rel)
    paths_by_slot = _paths_by_slot(plan, index, out_dir_rel=out_dir_rel)
    evidence_ids = derive_slot_evidence_ids(
        paths_by_slot,
        task_id=task_id,
        merged_pr=args.merged_pr,
        closeout_role=str(args.closeout_role),
        closeout_sequence=args.closeout_sequence,
    )
    _check_index_evidence_ids(plan, index, evidence_ids)
    for slot in plan.claim_slots:
        target = repo_root / paths_by_slot[slot.slot]
        if target.exists():
            raise BuildError(
                f"既存 artifact を上書きしない（append-only）: {paths_by_slot[slot.slot]}"
            )
    builder = ArtifactBuilder(
        manifest=manifest,
        repo_root=repo_root,
        plan=plan,
        index=index,
        bindings=bindings,
        heads=heads,
        evidence_ids=evidence_ids,
    )
    documents = builder.build_all()
    raw_by_claim = builder.raw_bytes()
    descriptors = _roots_descriptors(
        plan, paths_by_slot=paths_by_slot, raw_by_claim=raw_by_claim, repo_root=repo_root
    )
    # ここまで例外が出なければ全 artifact が揃っている（部分生成を作らない）。
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for claim_id in _utf8_sorted(sorted(documents)):
        rel = _claim_artifact_rel_path(out_dir_rel, claim_id)
        (repo_root / rel).write_bytes(raw_by_claim[claim_id])
        written.append(rel)
    if args.roots_out is not None:
        args.roots_out.parent.mkdir(parents=True, exist_ok=True)
        args.roots_out.write_text(
            yaml.safe_dump(descriptors, allow_unicode=True, sort_keys=True), encoding="utf-8"
        )
    summary = {
        "schema": BUILD_PLAN_SCHEMA,
        "task_id": task_id,
        "merged_pr": args.merged_pr,
        "closeout_sequence": args.closeout_sequence,
        "written_artifacts": written,
        "roots_out": None if args.roots_out is None else str(args.roots_out),
        "root_count": len(descriptors),
        "claim_count": len(written),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
