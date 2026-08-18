#!/usr/bin/env python3
"""claim closure artifact（`schema:claim-closure/v2`）と gated residual artifact
（`schema:gated-residual/v2`）の closed schema・検証・truth 評価。

DEC-20260815-003（B+・決定 2／5／8）と HRAI-REAUDIT-20260815 P0-01（PR-E3b）の実装であり、
P-070 正本順位（validator source＝rank 4、Materialization Manifest＝rank 6）に従って
**closure artifact schema の正本を本 module に置く**。manifest の `schema:claim-closure/v1`／
`schema:gated-residual/v1` は manifest 内 schema として残るが、v1 名を縮めて流用せず、
新版 v2 は本 module だけが定義する（manifest は v2 を持たない）。

## schema:claim-closure/v2（`status=implemented_verified`）

required（closed・追加 field 禁止）:

- `schema`: exact `schema:claim-closure/v2`
- `evidence_id`: registry key `EV-<64 hex>` と exact 一致
- `claim_id`: `claim:<id>`（manifest claim と exact 一致）
- `closure_owner_task`／`adoption`: manifest claim の同名 field と exact 一致
- `release_gate`: manifest claim の `release_gate` token と exact 一致
- `release_gate_result`: typed closed object `{result: pass, evaluation_head: <40 hex>,
  evidence_ids: [EV-…]（非空・重複なし・UTF-8 昇順）}`
- `prerequisite_results`: manifest claim の `dependencies ∪ ac_refs` の exact 集合（missing／extra／
  duplicate 拒否）。各 item は closed `{subject, subject_tested_head, evidence_id, binding_digest,
  evaluation_head, result}`（result は `pass` のみ）
- `positive_tests`／`negative_tests`: manifest claim の全文 multiset と exact 一致（重複拒否）
- `test_results`: positive／negative の各 test 文字列へ exact 1 item。closed `{test, polarity,
  command_id, verification_id, parser_id, derivation_id, expected, observed, raw_artifact_path,
  raw_artifact_sha256, result}`。expected／observed は typed value `{type, value}` で
  型一致し、`result=pass` は `expected == observed` を要求する（負例は「期待どおり reject」を
  typed expected へ書き、observed が一致することで pass）
- `heads`: closed `{implementation_head（P・40 hex 必須）, evidence_anchor_head（E・40 hex または
  null）, actual_merge_terminal_sha（M・40 hex または null）}`。P は tested head、E は本 artifact
  が入力として読んだ evidence の anchor（本 artifact 自身を含む commit ではない。生成時点で
  未確定なら null）、M は実装 PR の actual merge terminal（merge 前生成なら null）。registry
  entry の `premerge_pair.tested_head` は P と exact 一致し、E／M が非 null なら registry entry
  の同名 field と exact 一致する（`validate_evidence_registry.py`）。P と E／M を代用しない
- `status`: exact `implemented_verified`

prohibited: top-level `evidence_anchor_head`（自己 anchor の埋め込み）、`artifact_sha256`、
`closure_history`、`previous_closure_snapshot_sha256`（history は registry 側 append-only event
chain＝`claim_closure_events.py` が持つ）。

## schema:gated-residual/v2（`status=valid_gated_residual`）

required（closed）: `schema`・`evidence_id`・`claim_id`・`closure_owner_task`・`adoption`・
`gated_by`（`dependencies ∪ ac_refs ∪ {release_gate}` の非空部分集合・重複なし）・
`finalizer_context`（`control-context:` token。**exact allowlist 4 claim** のみ:
COMPUTE-REQ-003／COMPUTE-REQ-005／R025-FREEZE-REQ-07／R025-FREEZE-REQ-10 →
`control-context:R-025/R025-freeze-audit/1`。allowlist 外の claim は fail-close）・
`blocker_truth_snapshot`（gated_by 各 token へ exact 1 item・`truth=false` の known-false 記録・
unknown 拒否）・`non_blocking_prerequisite_results`（`(dependencies ∪ ac_refs ∪ {release_gate}) −
gated_by` の exact 集合・全 `result=pass`）・`attempts`（1 件以上）・`minimal_containment`・
`owner`（非空）・`review_due`（RFC 3339・validate 時刻より後。期限切れは false）・
`residual_risk`（manifest claim と exact 一致）・`heads`・`status`。prohibited は v2 closure と
同じ。

## truth の分離

`evaluate_claim_closure()` は `closure_obligation_satisfied`（implemented_verified または有効な
valid_gated_residual）と `implementation_verified`（implemented_verified のみ）を別 truth として
返す。residual を実装完了と扱わない（DEC-20260815-003 決定 2）。

## parser

JSON は `json.loads(object_pairs_hook=…)`、YAML は compose した node 木で mapping key の重複を
検出してから `safe_load` する。duplicate key を後勝ちで潰さない（fail-close）。

本 module は git・GitHub・working tree に依存しない純粋関数群である（manifest は読込済み
dict を受け取る）。R-025 の outcome／verdict は読まない。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

import yaml

__all__ = [
    "ATTEMPT_FIELDS",
    "BLOCKER_SNAPSHOT_FIELDS",
    "CLAIM_CLOSURE_EVIDENCE_TYPE",
    "CLAIM_CLOSURE_SCHEMA",
    "CLAIM_CLOSURE_STATUS",
    "CLAIM_CLOSURE_V2_PROHIBITED_FIELDS",
    "CLAIM_CLOSURE_V2_REQUIRED_FIELDS",
    "CONTAINMENT_FIELDS",
    "FREEZE_AUDIT_CONTEXT",
    "GATED_RESIDUAL_ALLOWLIST",
    "GATED_RESIDUAL_EVIDENCE_TYPE",
    "GATED_RESIDUAL_SCHEMA",
    "GATED_RESIDUAL_STATUS",
    "GATED_RESIDUAL_V2_PROHIBITED_FIELDS",
    "GATED_RESIDUAL_V2_REQUIRED_FIELDS",
    "HEADS_FIELDS",
    "PREREQUISITE_FIELDS",
    "RELEASE_GATE_RESULT_FIELDS",
    "SCHEMA_DOCUMENT_REL",
    "TEST_RESULT_FIELDS",
    "TYPED_VALUE_TYPES",
    "VALIDATOR_SOURCE_SCHEMAS",
    "ArtifactSchema",
    "ClosureEvaluation",
    "DuplicateKeyError",
    "artifact_schema_for",
    "claim_definition",
    "evaluate_claim_closure",
    "format_for_path",
    "load_closure_document",
    "normalize_claim_id",
    "parse_json_strict",
    "parse_yaml_strict",
    "prerequisite_tokens",
    "validate_claim_closure_artifact",
]

SCHEMA_DOCUMENT_REL: Final = "scripts/ai/claim_closure.py"

CLAIM_CLOSURE_SCHEMA: Final = "schema:claim-closure/v2"
GATED_RESIDUAL_SCHEMA: Final = "schema:gated-residual/v2"
CLAIM_CLOSURE_STATUS: Final = "implemented_verified"
GATED_RESIDUAL_STATUS: Final = "valid_gated_residual"
CLAIM_CLOSURE_EVIDENCE_TYPE: Final = "evidence-type:claim_closure_result"
GATED_RESIDUAL_EVIDENCE_TYPE: Final = "evidence-type:gated_residual_result"

FREEZE_AUDIT_CONTEXT: Final = "control-context:R-025/R025-freeze-audit/1"

# DEC-20260815-003 決定 5: valid_gated_residual を許す exact allowlist（claim ID → finalizer
# context）。adopt 全般へは解禁しない。追加・変更は DEC を要する。
GATED_RESIDUAL_ALLOWLIST: Final[Mapping[str, str]] = MappingProxyType(
    {
        "COMPUTE-REQ-003": FREEZE_AUDIT_CONTEXT,
        "COMPUTE-REQ-005": FREEZE_AUDIT_CONTEXT,
        "R025-FREEZE-REQ-07": FREEZE_AUDIT_CONTEXT,
        "R025-FREEZE-REQ-10": FREEZE_AUDIT_CONTEXT,
    }
)

CLAIM_CLOSURE_V2_REQUIRED_FIELDS: Final = (
    "schema",
    "evidence_id",
    "claim_id",
    "closure_owner_task",
    "adoption",
    "release_gate",
    "release_gate_result",
    "prerequisite_results",
    "positive_tests",
    "negative_tests",
    "test_results",
    "heads",
    "status",
)
CLAIM_CLOSURE_V2_PROHIBITED_FIELDS: Final = (
    "evidence_anchor_head",
    "artifact_sha256",
    "closure_history",
    "previous_closure_snapshot_sha256",
)
GATED_RESIDUAL_V2_REQUIRED_FIELDS: Final = (
    "schema",
    "evidence_id",
    "claim_id",
    "closure_owner_task",
    "adoption",
    "gated_by",
    "finalizer_context",
    "blocker_truth_snapshot",
    "non_blocking_prerequisite_results",
    "attempts",
    "minimal_containment",
    "owner",
    "review_due",
    "residual_risk",
    "heads",
    "status",
)
GATED_RESIDUAL_V2_PROHIBITED_FIELDS: Final = CLAIM_CLOSURE_V2_PROHIBITED_FIELDS

HEADS_FIELDS: Final = ("implementation_head", "evidence_anchor_head", "actual_merge_terminal_sha")
PREREQUISITE_FIELDS: Final = (
    "subject",
    "subject_tested_head",
    "evidence_id",
    "binding_digest",
    "evaluation_head",
    "result",
)
RELEASE_GATE_RESULT_FIELDS: Final = ("result", "evaluation_head", "evidence_ids")
TEST_RESULT_FIELDS: Final = (
    "test",
    "polarity",
    "command_id",
    "verification_id",
    "parser_id",
    "derivation_id",
    "expected",
    "observed",
    "raw_artifact_path",
    "raw_artifact_sha256",
    "result",
)
BLOCKER_SNAPSHOT_FIELDS: Final = (
    "token",
    "truth",
    "evaluator_id",
    "evaluation_head",
    "evaluated_at",
    "raw_artifact_path",
    "raw_artifact_sha256",
)
ATTEMPT_FIELDS: Final = ("kind", "reference", "exit_status", "attempted_at", "raw_artifact_sha256")
CONTAINMENT_FIELDS: Final = (
    "containment",
    "verification_command_id",
    "verification_result",
    "raw_artifact_sha256",
)
_TYPED_VALUE_FIELDS: Final = ("type", "value")
TYPED_VALUE_TYPES: Final[Mapping[str, type]] = MappingProxyType(
    {"boolean": bool, "integer": int, "string": str, "decimal_string": str}
)
_ATTEMPT_KINDS: Final = frozenset({"command", "external_query"})
_POLARITIES: Final = frozenset({"positive", "negative"})
_ACTIONABLE_DEFAULT: Final = ("adopt", "adopt_with_refinement", "gated")

_HEX40_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_ID_RE: Final = re.compile(r"^EV-[0-9a-f]{64}$")
_CLAIM_TOKEN_RE: Final = re.compile(r"^claim:[A-Za-z0-9][A-Za-z0-9._-]*$")
_TASK_TOKEN_RE: Final = re.compile(r"^task:[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONTROL_CONTEXT_RE: Final = re.compile(r"^control-context:[A-Za-z0-9][A-Za-z0-9._/:\-]*$")
_TOKEN_RE: Final = re.compile(
    r"^(?:ac|task|claim|gate|release|external|milestone|control-context):"
    r"[A-Za-z0-9][A-Za-z0-9._:/\-]*$"
)
_TRACKED_PATH_RE: Final = re.compile(r"^(?:docs|tests|scripts)/[A-Za-z0-9][A-Za-z0-9._/\-]*$")
_RFC3339_RE: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]*$")
_DECIMAL_RE: Final = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


class DuplicateKeyError(ValueError):
    """JSON／YAML の mapping key 重複（後勝ちで潰さず拒否する）。"""


@dataclass(frozen=True)
class ArtifactSchema:
    """validator source が正本として持つ closure artifact schema の closed 定義。"""

    schema: str
    status: str
    evidence_type: str
    required_fields: tuple[str, ...]
    prohibited_fields: tuple[str, ...]
    head_binding_field: str = "heads.implementation_head"
    format: str = "yaml_or_json"
    schema_document: str = SCHEMA_DOCUMENT_REL

    def to_descriptor(self) -> dict[str, Any]:
        """人間可読／JSON 出力用 projection。"""
        return {
            "schema": self.schema,
            "schema_document": self.schema_document,
            "format": self.format,
            "additional_properties": False,
            "required_fields": list(self.required_fields),
            "prohibited_fields": list(self.prohibited_fields),
            "status": self.status,
            "evidence_type": self.evidence_type,
            "head_binding_field": self.head_binding_field,
        }


VALIDATOR_SOURCE_SCHEMAS: Final[Mapping[str, ArtifactSchema]] = MappingProxyType(
    {
        CLAIM_CLOSURE_SCHEMA: ArtifactSchema(
            schema=CLAIM_CLOSURE_SCHEMA,
            status=CLAIM_CLOSURE_STATUS,
            evidence_type=CLAIM_CLOSURE_EVIDENCE_TYPE,
            required_fields=CLAIM_CLOSURE_V2_REQUIRED_FIELDS,
            prohibited_fields=CLAIM_CLOSURE_V2_PROHIBITED_FIELDS,
        ),
        GATED_RESIDUAL_SCHEMA: ArtifactSchema(
            schema=GATED_RESIDUAL_SCHEMA,
            status=GATED_RESIDUAL_STATUS,
            evidence_type=GATED_RESIDUAL_EVIDENCE_TYPE,
            required_fields=GATED_RESIDUAL_V2_REQUIRED_FIELDS,
            prohibited_fields=GATED_RESIDUAL_V2_PROHIBITED_FIELDS,
        ),
    }
)


def artifact_schema_for(token: object) -> ArtifactSchema | None:
    """`schema:` token を validator source schema へ解決する（未知は None）。"""
    if not isinstance(token, str):
        return None
    return VALIDATOR_SOURCE_SCHEMAS.get(token)


# ---------------------------------------------------------------------------
# duplicate key を拒否する parser
# ---------------------------------------------------------------------------


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"JSON object key が重複している: {key!r}")
        result[key] = value
    return result


def parse_json_strict(raw: bytes) -> Any:
    """duplicate key を拒否する JSON parser（UTF-8 のみ）。"""
    text = raw.decode("utf-8")
    return json.loads(text, object_pairs_hook=_reject_duplicate_pairs)


def _yaml_key_identity(node: Any) -> tuple[Any, ...]:
    """YAML key node の同一性（scalar は tag＋value、複合 key は canonical repr）。"""
    if isinstance(node, yaml.ScalarNode):
        return ("scalar", node.tag, node.value)
    if isinstance(node, yaml.SequenceNode):
        return ("seq", tuple(_yaml_key_identity(item) for item in node.value))
    if isinstance(node, yaml.MappingNode):
        return (
            "map",
            tuple(
                sorted(
                    (_yaml_key_identity(key), _yaml_key_identity(value))
                    for key, value in node.value
                )
            ),
        )
    return ("other", repr(node))


def _reject_duplicate_yaml_keys(node: Any, path: str) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[tuple[Any, ...]] = set()
        for key_node, value_node in node.value:
            identity = _yaml_key_identity(key_node)
            if identity in seen:
                label = key_node.value if isinstance(key_node, yaml.ScalarNode) else "<複合 key>"
                raise DuplicateKeyError(f"YAML mapping key が重複している: {path}/{label}")
            seen.add(identity)
            child = key_node.value if isinstance(key_node, yaml.ScalarNode) else "<複合 key>"
            _reject_duplicate_yaml_keys(value_node, f"{path}/{child}")
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            _reject_duplicate_yaml_keys(item, f"{path}[{index}]")


def parse_yaml_strict(raw: bytes) -> Any:
    """duplicate mapping key を拒否する YAML parser（safe・単一 document・UTF-8 のみ）。"""
    text = raw.decode("utf-8")
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    _reject_duplicate_yaml_keys(node, "")
    return yaml.safe_load(text)


def format_for_path(path: str) -> str | None:
    """artifact path の拡張子から parser format（`json`／`yaml`）を決める。"""
    lower = path.lower()
    if lower.endswith(".json"):
        return "json"
    if lower.endswith((".yml", ".yaml")):
        return "yaml"
    return None


def load_closure_document(raw: bytes, *, fmt: str) -> tuple[Any, list[str]]:
    """raw bytes を format で parse し `(document, issues)` を返す（失敗は document=None）。"""
    try:
        if fmt == "json":
            return parse_json_strict(raw), []
        if fmt == "yaml":
            return parse_yaml_strict(raw), []
    except DuplicateKeyError as exc:
        return None, [str(exc)]
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        return None, [f"{fmt} として parse できない: {exc}"]
    return None, [f"未知の artifact format: {fmt!r}"]


# ---------------------------------------------------------------------------
# manifest claim 参照
# ---------------------------------------------------------------------------


def normalize_claim_id(value: str) -> str:
    """`claim:<id>`／`<id>` のどちらを受けても `<id>` を返す。"""
    return value[len("claim:") :] if value.startswith("claim:") else value


def claim_definition(manifest: Mapping[str, Any], claim_id: str) -> dict[str, Any] | None:
    """manifest `claims[]` から claim ID の定義を返す（不在は None）。"""
    wanted = normalize_claim_id(claim_id)
    claims = manifest.get("claims")
    if not isinstance(claims, list):
        return None
    for claim in claims:
        if isinstance(claim, Mapping) and claim.get("claim_id") == wanted:
            return dict(claim)
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _utf8_sorted(items: Sequence[str]) -> list[str]:
    return sorted(items, key=lambda item: item.encode("utf-8"))


# ---------------------------------------------------------------------------
# field 検査 primitive
# ---------------------------------------------------------------------------


def _is_hex40(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX40_RE.match(value))


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX64_RE.match(value))


def _is_evidence_id(value: object) -> bool:
    return isinstance(value, str) and bool(_EVIDENCE_ID_RE.match(value))


def _is_rfc3339(value: object) -> bool:
    return isinstance(value, str) and bool(_RFC3339_RE.match(value))


def _parse_rfc3339(value: str) -> _dt.datetime | None:
    if not _RFC3339_RE.match(value):
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _closed_object(
    value: object, fields: Sequence[str], *, label: str, errors: list[str]
) -> dict[str, Any] | None:
    """closed object（exact field 集合）を検査して dict を返す（不正は None）。"""
    if not isinstance(value, Mapping):
        errors.append(f"{label} が object ではない")
        return None
    keys = set(value)
    expected = set(fields)
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    if missing:
        errors.append(f"{label}: 必須 field 欠落: {missing}")
    if extra:
        errors.append(f"{label}: 未知 field（closed schema）: {extra}")
    if missing or extra:
        return None
    return dict(value)


def _typed_value(value: object, *, label: str, errors: list[str]) -> tuple[str, Any] | None:
    obj = _closed_object(value, _TYPED_VALUE_FIELDS, label=label, errors=errors)
    if obj is None:
        return None
    kind = obj.get("type")
    if not isinstance(kind, str) or kind not in TYPED_VALUE_TYPES:
        errors.append(f"{label}.type が closed enum ではない: {kind!r}")
        return None
    inner = obj.get("value")
    expected_type = TYPED_VALUE_TYPES[kind]
    if isinstance(inner, bool) and expected_type is not bool:
        errors.append(f"{label}.value の型が {kind} と一致しない")
        return None
    if not isinstance(inner, expected_type):
        errors.append(f"{label}.value の型が {kind} と一致しない")
        return None
    if kind == "decimal_string" and not _DECIMAL_RE.match(str(inner)):
        errors.append(f"{label}.value が plain decimal string ではない")
        return None
    return kind, inner


def _check_heads(value: object, *, errors: list[str]) -> dict[str, Any] | None:
    heads = _closed_object(value, HEADS_FIELDS, label="heads", errors=errors)
    if heads is None:
        return None
    if not _is_hex40(heads.get("implementation_head")):
        errors.append("heads.implementation_head（P）が 40 桁 lowercase hex ではない")
    for name in ("evidence_anchor_head", "actual_merge_terminal_sha"):
        item = heads.get(name)
        if item is not None and not _is_hex40(item):
            errors.append(f"heads.{name} が 40 桁 lowercase hex でも null でもない")
    implementation = heads.get("implementation_head")
    for name in ("evidence_anchor_head", "actual_merge_terminal_sha"):
        item = heads.get(name)
        if _is_hex40(item) and item == implementation:
            errors.append(f"heads.{name} が implementation_head と同一（P と E／M を代用しない）")
    return heads


def _check_prerequisites(
    value: object,
    *,
    expected_tokens: Sequence[str],
    label: str,
    errors: list[str],
) -> None:
    """prerequisite item 配列を expected token 集合と exact 照合する（全 result=pass）。"""
    if not isinstance(value, list):
        errors.append(f"{label} が配列ではない")
        return
    seen: list[str] = []
    for index, item in enumerate(value):
        obj = _closed_object(item, PREREQUISITE_FIELDS, label=f"{label}[{index}]", errors=errors)
        if obj is None:
            continue
        subject = obj.get("subject")
        if not isinstance(subject, str) or not _TOKEN_RE.match(subject):
            errors.append(f"{label}[{index}].subject が typed token ではない: {subject!r}")
        else:
            seen.append(subject)
        if not _is_hex40(obj.get("subject_tested_head")):
            errors.append(f"{label}[{index}].subject_tested_head が 40 桁 hex ではない")
        if not _is_evidence_id(obj.get("evidence_id")):
            errors.append(f"{label}[{index}].evidence_id が EV-<64 hex> ではない")
        if not _is_hex64(obj.get("binding_digest")):
            errors.append(f"{label}[{index}].binding_digest が 64 桁 hex ではない")
        if not _is_hex40(obj.get("evaluation_head")):
            errors.append(f"{label}[{index}].evaluation_head が 40 桁 hex ではない")
        if obj.get("result") != "pass":
            errors.append(f"{label}[{index}].result が pass ではない: {obj.get('result')!r}")
    duplicates = sorted({token for token in seen if seen.count(token) > 1})
    if duplicates:
        errors.append(f"{label}: subject が重複している: {duplicates}")
    expected = set(expected_tokens)
    actual = set(seen)
    missing = _utf8_sorted(list(expected - actual))
    extra = _utf8_sorted(list(actual - expected))
    if missing:
        errors.append(f"{label}: manifest claim の前提が欠けている: {missing}")
    if extra:
        errors.append(f"{label}: manifest claim に無い前提がある: {extra}")


def _check_test_list(
    value: object, *, expected: Sequence[str], label: str, errors: list[str]
) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{label} が文字列配列ではない")
        return []
    items = [str(item) for item in value]
    if len(items) != len(set(items)):
        errors.append(f"{label} に重複がある")
    if sorted(items) != sorted(expected):
        errors.append(f"{label} が manifest claim の全文 exact 集合と一致しない")
    return items


def _check_test_results(
    value: object,
    *,
    positive: Sequence[str],
    negative: Sequence[str],
    errors: list[str],
) -> None:
    label = "test_results"
    if not isinstance(value, list):
        errors.append(f"{label} が配列ではない")
        return
    seen: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        obj = _closed_object(item, TEST_RESULT_FIELDS, label=f"{label}[{index}]", errors=errors)
        if obj is None:
            continue
        test = obj.get("test")
        polarity = obj.get("polarity")
        if not isinstance(test, str) or not test:
            errors.append(f"{label}[{index}].test が空")
        if not isinstance(polarity, str) or polarity not in _POLARITIES:
            errors.append(f"{label}[{index}].polarity が positive|negative ではない")
        elif isinstance(test, str):
            seen.append((polarity, test))
        for name in ("command_id", "verification_id", "parser_id", "derivation_id"):
            ident = obj.get(name)
            if not isinstance(ident, str) or not _IDENTIFIER_RE.match(ident):
                errors.append(f"{label}[{index}].{name} が識別子ではない: {ident!r}")
        expected = _typed_value(
            obj.get("expected"), label=f"{label}[{index}].expected", errors=errors
        )
        observed = _typed_value(
            obj.get("observed"), label=f"{label}[{index}].observed", errors=errors
        )
        path = obj.get("raw_artifact_path")
        if not isinstance(path, str) or not _TRACKED_PATH_RE.match(path):
            errors.append(f"{label}[{index}].raw_artifact_path が追跡 path ではない: {path!r}")
        if not _is_hex64(obj.get("raw_artifact_sha256")):
            errors.append(f"{label}[{index}].raw_artifact_sha256 が 64 桁 hex ではない")
        if obj.get("result") != "pass":
            errors.append(f"{label}[{index}].result が pass ではない")
        elif expected is not None and observed is not None and expected != observed:
            errors.append(f"{label}[{index}]: result=pass だが expected と observed が一致しない")
    expected_pairs = [("positive", test) for test in positive] + [
        ("negative", test) for test in negative
    ]
    duplicates = sorted({pair for pair in seen if seen.count(pair) > 1})
    if duplicates:
        errors.append(f"{label}: 同じ test へ複数 item がある: {duplicates}")
    missing = sorted(set(expected_pairs) - set(seen))
    extra = sorted(set(seen) - set(expected_pairs))
    if missing:
        errors.append(f"{label}: test_result が無い test がある: {missing}")
    if extra:
        errors.append(f"{label}: positive_tests／negative_tests に無い test がある: {extra}")


def _check_release_gate_result(value: object, *, errors: list[str]) -> None:
    obj = _closed_object(
        value, RELEASE_GATE_RESULT_FIELDS, label="release_gate_result", errors=errors
    )
    if obj is None:
        return
    if obj.get("result") != "pass":
        errors.append(f"release_gate_result.result が pass ではない: {obj.get('result')!r}")
    if not _is_hex40(obj.get("evaluation_head")):
        errors.append("release_gate_result.evaluation_head が 40 桁 hex ではない")
    ids = obj.get("evidence_ids")
    if not isinstance(ids, list) or not ids:
        errors.append("release_gate_result.evidence_ids が非空配列ではない")
        return
    if not all(_is_evidence_id(item) for item in ids):
        errors.append("release_gate_result.evidence_ids に EV-<64 hex> でない項目がある")
        return
    if len(ids) != len(set(ids)):
        errors.append("release_gate_result.evidence_ids に重複がある")
    if ids != _utf8_sorted([str(item) for item in ids]):
        errors.append("release_gate_result.evidence_ids が UTF-8 昇順ではない")


def _check_common_identity(
    doc: Mapping[str, Any], *, claim: Mapping[str, Any], claim_token: str, errors: list[str]
) -> None:
    if not _is_evidence_id(doc.get("evidence_id")):
        errors.append("evidence_id が registry key（EV-<64 hex>）ではない")
    if doc.get("claim_id") != claim_token:
        errors.append(f"claim_id が {claim_token} と一致しない: {doc.get('claim_id')!r}")
    owner = claim.get("closure_owner_task")
    if doc.get("closure_owner_task") != owner or not isinstance(owner, str):
        errors.append(
            f"closure_owner_task が manifest claim と一致しない: {doc.get('closure_owner_task')!r}"
        )
    elif not _TASK_TOKEN_RE.match(owner):
        errors.append(f"closure_owner_task が canonical task token ではない: {owner!r}")
    if doc.get("adoption") != claim.get("adoption"):
        errors.append(f"adoption が manifest claim と一致しない: {doc.get('adoption')!r}")


def prerequisite_tokens(claim: Mapping[str, Any]) -> list[str]:
    """manifest claim の `dependencies ∪ ac_refs`（宣言順・重複なし）を返す。"""
    tokens: list[str] = []
    for item in [*_string_list(claim.get("dependencies")), *_string_list(claim.get("ac_refs"))]:
        if item not in tokens:
            tokens.append(item)
    return tokens


def _validate_implemented_verified(
    doc: Mapping[str, Any], *, claim: Mapping[str, Any], claim_token: str
) -> list[str]:
    errors: list[str] = []
    _check_common_identity(doc, claim=claim, claim_token=claim_token, errors=errors)
    release_gate = claim.get("release_gate")
    if not isinstance(release_gate, str) or doc.get("release_gate") != release_gate:
        errors.append(
            f"release_gate が manifest claim と一致しない: {doc.get('release_gate')!r}"
            f"（manifest: {release_gate!r}）"
        )
    _check_release_gate_result(doc.get("release_gate_result"), errors=errors)
    _check_prerequisites(
        doc.get("prerequisite_results"),
        expected_tokens=prerequisite_tokens(claim),
        label="prerequisite_results",
        errors=errors,
    )
    positive = _check_test_list(
        doc.get("positive_tests"),
        expected=_string_list(claim.get("positive_tests")),
        label="positive_tests",
        errors=errors,
    )
    negative = _check_test_list(
        doc.get("negative_tests"),
        expected=_string_list(claim.get("negative_tests")),
        label="negative_tests",
        errors=errors,
    )
    _check_test_results(
        doc.get("test_results"), positive=positive, negative=negative, errors=errors
    )
    _check_heads(doc.get("heads"), errors=errors)
    if doc.get("status") != CLAIM_CLOSURE_STATUS:
        errors.append(f"status が {CLAIM_CLOSURE_STATUS} ではない: {doc.get('status')!r}")
    return errors


def _check_blocker_snapshot(value: object, *, gated_by: Sequence[str], errors: list[str]) -> None:
    label = "blocker_truth_snapshot"
    if not isinstance(value, list):
        errors.append(f"{label} が配列ではない")
        return
    seen: list[str] = []
    for index, item in enumerate(value):
        obj = _closed_object(
            item, BLOCKER_SNAPSHOT_FIELDS, label=f"{label}[{index}]", errors=errors
        )
        if obj is None:
            continue
        token = obj.get("token")
        if isinstance(token, str):
            seen.append(token)
        else:
            errors.append(f"{label}[{index}].token が文字列ではない")
        truth = obj.get("truth")
        if truth is not False:
            # known-false だけを許す。unknown／null／true は fail-close。
            errors.append(
                f"{label}[{index}].truth が typed false（known-false）ではない: {truth!r}"
            )
        if not _nonempty_string(obj.get("evaluator_id")):
            errors.append(f"{label}[{index}].evaluator_id が空")
        if not _is_hex40(obj.get("evaluation_head")):
            errors.append(f"{label}[{index}].evaluation_head が 40 桁 hex ではない")
        if not _is_rfc3339(obj.get("evaluated_at")):
            errors.append(f"{label}[{index}].evaluated_at が RFC 3339 ではない")
        path = obj.get("raw_artifact_path")
        if not isinstance(path, str) or not _TRACKED_PATH_RE.match(path):
            errors.append(f"{label}[{index}].raw_artifact_path が追跡 path ではない: {path!r}")
        if not _is_hex64(obj.get("raw_artifact_sha256")):
            errors.append(f"{label}[{index}].raw_artifact_sha256 が 64 桁 hex ではない")
    if sorted(seen) != sorted(set(gated_by)) or len(seen) != len(set(seen)):
        errors.append(
            f"{label}: gated_by 各 token へ exact 1 item ではない（{seen} vs {list(gated_by)}）"
        )


def _check_attempts(value: object, *, errors: list[str]) -> None:
    label = "attempts"
    if not isinstance(value, list) or not value:
        errors.append(f"{label} が非空配列ではない")
        return
    for index, item in enumerate(value):
        obj = _closed_object(item, ATTEMPT_FIELDS, label=f"{label}[{index}]", errors=errors)
        if obj is None:
            continue
        if obj.get("kind") not in _ATTEMPT_KINDS:
            errors.append(f"{label}[{index}].kind が command|external_query ではない")
        if not _nonempty_string(obj.get("reference")):
            errors.append(f"{label}[{index}].reference が空")
        status = obj.get("exit_status")
        if isinstance(status, bool) or not (isinstance(status, int) or _nonempty_string(status)):
            errors.append(f"{label}[{index}].exit_status が整数または非空文字列ではない")
        if not _is_rfc3339(obj.get("attempted_at")):
            errors.append(f"{label}[{index}].attempted_at が RFC 3339 ではない")
        if not _is_hex64(obj.get("raw_artifact_sha256")):
            errors.append(f"{label}[{index}].raw_artifact_sha256 が 64 桁 hex ではない")


def _check_containment(value: object, *, errors: list[str]) -> None:
    obj = _closed_object(value, CONTAINMENT_FIELDS, label="minimal_containment", errors=errors)
    if obj is None:
        return
    if not _nonempty_string(obj.get("containment")):
        errors.append("minimal_containment.containment が空")
    if not _nonempty_string(obj.get("verification_command_id")):
        errors.append("minimal_containment.verification_command_id が空")
    if obj.get("verification_result") != "pass":
        errors.append("minimal_containment.verification_result が pass ではない")
    if not _is_hex64(obj.get("raw_artifact_sha256")):
        errors.append("minimal_containment.raw_artifact_sha256 が 64 桁 hex ではない")


def _validate_gated_residual(
    doc: Mapping[str, Any],
    *,
    claim: Mapping[str, Any],
    claim_token: str,
    now: _dt.datetime,
) -> list[str]:
    errors: list[str] = []
    _check_common_identity(doc, claim=claim, claim_token=claim_token, errors=errors)
    plain_id = normalize_claim_id(claim_token)
    allowed_context = GATED_RESIDUAL_ALLOWLIST.get(plain_id)
    if allowed_context is None:
        errors.append(
            f"{claim_token} は valid_gated_residual allowlist（DEC-20260815-003 決定 5・4 claim）"
            "に含まれない（fail-close）"
        )
    context = doc.get("finalizer_context")
    if not isinstance(context, str) or not _CONTROL_CONTEXT_RE.match(context):
        errors.append(f"finalizer_context が control-context: token ではない: {context!r}")
    elif allowed_context is not None and context != allowed_context:
        errors.append(
            f"finalizer_context が allowlist の context と一致しない: {context!r}"
            f"（要求: {allowed_context}）"
        )
    declared = claim.get("closure_finalizer_context")
    if declared is not None and declared != context:
        errors.append(
            f"manifest claim の closure_finalizer_context（{declared!r}）と artifact の"
            f" finalizer_context（{context!r}）が一致しない"
        )
    release_gate = claim.get("release_gate")
    universe = prerequisite_tokens(claim)
    if isinstance(release_gate, str) and release_gate not in universe:
        universe.append(release_gate)
    gated_by_raw = doc.get("gated_by")
    gated_by: list[str] = []
    if not isinstance(gated_by_raw, list) or not gated_by_raw:
        errors.append("gated_by が非空配列ではない")
    elif not all(isinstance(item, str) and _TOKEN_RE.match(item) for item in gated_by_raw):
        errors.append("gated_by に typed token でない項目がある")
    else:
        gated_by = [str(item) for item in gated_by_raw]
        if len(gated_by) != len(set(gated_by)):
            errors.append("gated_by に重複がある")
        outside = _utf8_sorted([token for token in gated_by if token not in universe])
        if outside:
            errors.append(
                f"gated_by が dependencies∪ac_refs∪{{release_gate}} の部分集合ではない: {outside}"
            )
    _check_blocker_snapshot(doc.get("blocker_truth_snapshot"), gated_by=gated_by, errors=errors)
    non_blocking = [token for token in universe if token not in set(gated_by)]
    _check_prerequisites(
        doc.get("non_blocking_prerequisite_results"),
        expected_tokens=non_blocking,
        label="non_blocking_prerequisite_results",
        errors=errors,
    )
    _check_attempts(doc.get("attempts"), errors=errors)
    _check_containment(doc.get("minimal_containment"), errors=errors)
    if not _nonempty_string(doc.get("owner")):
        errors.append("owner が空")
    review_due = doc.get("review_due")
    parsed_due = _parse_rfc3339(review_due) if isinstance(review_due, str) else None
    if parsed_due is None:
        errors.append(f"review_due が RFC 3339（timezone 付き）ではない: {review_due!r}")
    elif parsed_due <= now:
        errors.append(
            f"review_due が期限切れ（{review_due} <= 検証時刻 {now.isoformat()}）:"
            " residual は false"
        )
    residual_risk = claim.get("residual_risk")
    if not _nonempty_string(residual_risk) or doc.get("residual_risk") != residual_risk:
        errors.append("residual_risk が manifest claim の非空 residual_risk と一致しない")
    _check_heads(doc.get("heads"), errors=errors)
    if doc.get("status") != GATED_RESIDUAL_STATUS:
        errors.append(f"status が {GATED_RESIDUAL_STATUS} ではない: {doc.get('status')!r}")
    return errors


def _closed_top_level(
    doc: Mapping[str, Any], definition: ArtifactSchema, *, errors: list[str]
) -> None:
    keys = set(doc)
    required = set(definition.required_fields)
    prohibited = [name for name in definition.prohibited_fields if name in keys]
    if prohibited:
        errors.append(f"prohibited field が混入している: {sorted(prohibited)}")
    missing = sorted(required - keys)
    if missing:
        errors.append(f"必須 field 欠落: {missing}")
    extra = sorted(keys - required - set(prohibited))
    if extra:
        errors.append(f"closed schema に無い field: {extra}")


def _now_utc(now: _dt.datetime | None) -> _dt.datetime:
    if now is None:
        return _dt.datetime.now(_dt.UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=_dt.UTC)
    return now


def validate_claim_closure_artifact(
    doc: object,
    *,
    manifest: Mapping[str, Any],
    claim_id: str,
    now: _dt.datetime | None = None,
) -> list[str]:
    """closure artifact を closed schema と manifest claim 定義へ exact 照合し issue 一覧を返す。

    空 list が pass。`claim_id` は `claim:<id>`／`<id>` のどちらでもよい。gated residual の
    `review_due` は `now`（既定は現在時刻 UTC）と比較し、期限切れは issue（false）。
    """
    if not isinstance(doc, Mapping):
        return ["artifact が object ではない"]
    schema = doc.get("schema")
    definition = artifact_schema_for(schema)
    if definition is None:
        return [
            f"schema が validator source の closed schema ではない: {schema!r}"
            f"（許可: {sorted(VALIDATOR_SOURCE_SCHEMAS)}）"
        ]
    claim = claim_definition(manifest, claim_id)
    if claim is None:
        return [f"manifest に claim {normalize_claim_id(claim_id)} が無い"]
    claim_token = f"claim:{normalize_claim_id(claim_id)}"
    errors: list[str] = []
    _closed_top_level(doc, definition, errors=errors)
    if errors:
        return errors
    if definition.schema == CLAIM_CLOSURE_SCHEMA:
        return _validate_implemented_verified(doc, claim=claim, claim_token=claim_token)
    return _validate_gated_residual(doc, claim=claim, claim_token=claim_token, now=_now_utc(now))


@dataclass(frozen=True)
class ClosureEvaluation:
    """closure artifact の truth 評価結果（obligation と implementation を分離）。"""

    claim_id: str
    schema: str | None
    state: str | None
    errors: tuple[str, ...]

    @property
    def closure_obligation_satisfied(self) -> bool:
        """implemented_verified または有効な valid_gated_residual なら true。"""
        return not self.errors and self.state in {CLAIM_CLOSURE_STATUS, GATED_RESIDUAL_STATUS}

    @property
    def implementation_verified(self) -> bool:
        """implemented_verified のみ true（residual は実装完了と扱わない）。"""
        return not self.errors and self.state == CLAIM_CLOSURE_STATUS

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "schema": self.schema,
            "state": self.state,
            "closure_obligation_satisfied": self.closure_obligation_satisfied,
            "implementation_verified": self.implementation_verified,
            "errors": list(self.errors),
        }


def evaluate_claim_closure(
    doc: object,
    *,
    manifest: Mapping[str, Any],
    claim_id: str,
    now: _dt.datetime | None = None,
) -> ClosureEvaluation:
    """`validate_claim_closure_artifact` の結果を 2 つの truth へ写す。"""
    plain = normalize_claim_id(claim_id)
    errors = validate_claim_closure_artifact(doc, manifest=manifest, claim_id=plain, now=now)
    schema = doc.get("schema") if isinstance(doc, Mapping) else None
    definition = artifact_schema_for(schema)
    state = definition.status if definition is not None and not errors else None
    return ClosureEvaluation(
        claim_id=f"claim:{plain}",
        schema=schema if isinstance(schema, str) else None,
        state=state,
        errors=tuple(errors),
    )
