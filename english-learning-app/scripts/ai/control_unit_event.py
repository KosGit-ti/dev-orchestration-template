#!/usr/bin/env python3
"""control unit event（`control-unit-event/v1`）の共通 schema／hash core。

`docs/specs/N-602-evidence-truthfulness-generation.md`「Control Unit Event Schema v1」
（240-419 行）と `docs/specs/N-598C-exact-current-state-resolver.md` AC-10 の実装である。
R-025 のfreeze前監査、freeze遷移、post-freeze予約preflight、reserved遷移、実装適合、
single lookは通常taskのT→S1→R→S2ではなく、`docs/ai/control-unit-events/<event-id>.yml`の
append-only eventとU0→U1→UR→U2で搬送する（通常task closeoutとは別の schema／validator
であり、`task_closeout_event.py` の task 版とは stable key・top-level field が異なる）。

canonical hash primitive（canonical-content-v1・JCS 相当の serialization）は
`task_closeout_event.py` から import して再利用し、同じ規則を複製しない
（root tuple の shape は task closeout event と同一のため `validate_root_tuple` 等の
root 検証関数も同様に再利用する）。stable key・top-level schema・catalog 適合検査は
CUE 固有であり、`task_closeout_event.py` の `validate_event_chain` 相当を並行実装する
（field 名が異なるため関数シグネチャを共有できない。両者の共通化は
`scripts/ai/evidence_contract.py` への合流時に行う・`task_closeout_event.py` module
docstring と同じ「重複実装を恒久化しない」方針を踏襲する）。

N-598C AC-10 のスコープは機構（schema・catalog 適合・U0/U1/UR/U2 の状態遷移・
partial batch／unknown unit／wrong order の拒否）の実証に限る。実 R-025 の
freeze／reservation／single-look の評価結果（安全床・研究保全の実体評価、
transition anchor の git blob 導出、L1/L2 独立受入 identity）は本 module のスコープ外
とし、後続 control gate が各 action head で評価する（synthetic fixture のみで検証する
安全床・N-598C spec Migration Phase 7）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from scripts.ai.task_closeout_event import (
    CanonicalContentError,
    canonical_content_bytes,
    canonical_content_sha256,
    canonical_hash_with_null_fields,
    root_slot,
    root_tuple_projection,
    set_content_sha256,
    sha256_bytes,
    validate_root_tuple,
)

__all__ = [
    "CanonicalContentError",
    "canonical_content_bytes",
    "canonical_content_sha256",
    "sha256_bytes",
    "set_content_sha256",
    "CONTROL_EVENT_DIR_REL",
    "CONTROL_EVENT_PATH_GLOB",
    "CONTROL_EVENT_ID_PREFIX",
    "CONTROL_UNIT_TASK_ID",
    "CONTROL_CLOSING_SUBSTEP",
    "CONTROL_KINDS",
    "CONTROL_STATUSES",
    "TARGET_PROFILES",
    "CONTROL_UNIT_TABLE",
    "CONTROL_UNIT_ORDER",
    "GLOBAL_SAFETY_PRECONDITIONS",
    "build_context_id",
    "derive_event_id",
    "event_stable_key",
    "event_filename",
    "validate_event_object",
    "validate_catalog_conformance",
    "validate_event_chain",
    "current_event_for_key",
    "units_with_lower_execution_order",
]

CONTROL_EVENT_DIR_REL: Final = "docs/ai/control-unit-events"
CONTROL_EVENT_PATH_GLOB: Final = "docs/ai/control-unit-events/*.yml"
CONTROL_EVENT_ID_PREFIX: Final = "CUE-"
SCHEMA_VERSION: Final = 1

# spec「closing」用語（N-598C spec 62-63 行）: control unit の U1 は task closeout の
# S1 と同じ `closing` state を使うが、task_id は常に R-025、substep は
# `control-evidence-registration` に固定する（design.md「U1はcheckpoint-nextと
# full-plan flagも同じ3ファイルをtask_id=R-025/substep=control-evidence-registration
# にexact同期し」）。
CONTROL_UNIT_TASK_ID: Final = "R-025"
CONTROL_CLOSING_SUBSTEP: Final = "control-evidence-registration"

CONTROL_KINDS: Final = frozenset({"evaluation", "state_transition", "single_look"})
CONTROL_STATUSES: Final = frozenset({"verified"})
TARGET_PROFILES: Final = frozenset({"prereg_freeze", "registry_reservation"})

EVENT_TOP_LEVEL_FIELDS: Final = (
    "schema_version",
    "event_id",
    "control_id",
    "control_kind",
    "control_sequence",
    "context_id",
    "pre_action_tested_head",
    "preconditions",
    "produced_tokens",
    "action_terminal_sha",
    "evidence_roots",
    "evidence_roots_sha256",
    "supporting_artifacts",
    "target_transition",
    "closure_claim_ids",
    "closure_claim_ids_sha256",
    "terminal_verification",
    "corrects_event_id",
    "correction_ordinal",
    "status",
)
TARGET_TRANSITION_FIELDS: Final = (
    "target_profile",
    "artifact_path",
    "from_blob_sha256",
    "to_blob_sha256",
    "derived_transition_anchor",
)
SUPPORTING_ARTIFACT_FIELDS: Final = ("path", "raw_sha256")
TERMINAL_VERIFICATION_FIELDS: Final = (
    "n598b_evidence_ref",
    "n598b_evidence_sha256",
    "evaluator_id",
    "evaluator_source_sha256",
)

HEX40_RE: Final = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
EVENT_ID_RE: Final = re.compile(r"^CUE-[0-9a-f]{64}$")
EVENT_FILE_RE: Final = re.compile(r"^CUE-[0-9a-f]{64}\.yml$")
CONTROL_TOKEN_RE: Final = re.compile(r"^(?:task|ac|gate|release|external):[A-Za-z0-9][\w.:\-]*$")
CONTEXT_ID_RE: Final = re.compile(
    r"^control-context:R-025/(?P<control_id>[A-Za-z0-9\-]+)/(?P<sequence>[1-9][0-9]*)$"
)
TRACKED_PATH_RE: Final = re.compile(r"^docs/[A-Za-z0-9][A-Za-z0-9._/\-]*$")

# 通常 task の global prerequisite（`docs/specs/N-598C-...` 及び manifest
# `task_truth_contract.normal_task_global_prerequisites`）を control unit の
# preconditions にも要求する（`docs/design.md` N-602 節「安全床と研究保全は…各unitに
# 事前割当し、U1 batchへ必ず含める」）。
GLOBAL_SAFETY_PRECONDITIONS: Final = ("release:safety_floor", "release:preserve_existing")


# ---------------------------------------------------------------------------
# closed control unit catalog（N-602 spec 292-305 行の表を正本とする）
#
# manifest（`docs/audits/audit-materialization-manifest-2026-08-12.yml`
# `control_unit_catalog`）は運用時の root descriptor／current_state を持つ生成物であり、
# 本 module はそれを都度読み込む依存を持たない（fixture／pure-function validator を
# manifest 再生成から独立させる）。spec の closed 表と値がずれた場合は
# `docs/audits/audit-materialization-manifest-2026-08-12.yml` 側の
# `validate_audit_materialization.py` が別途検査する。
# ---------------------------------------------------------------------------


class ControlUnitRow:
    """closed control unit catalog の 1 行（immutable）。"""

    __slots__ = ("control_id", "kind", "execution_order", "preconditions", "produced_tokens")

    def __init__(
        self,
        *,
        control_id: str,
        kind: str,
        execution_order: int,
        preconditions: Sequence[str],
        produced_tokens: Sequence[str],
    ) -> None:
        self.control_id = control_id
        self.kind = kind
        self.execution_order = execution_order
        self.preconditions = tuple(preconditions)
        self.produced_tokens = tuple(produced_tokens)


CONTROL_UNIT_TABLE: Final[tuple[ControlUnitRow, ...]] = (
    ControlUnitRow(
        control_id="R025-freeze-audit",
        kind="evaluation",
        execution_order=1,
        preconditions=("task:N-602A", "task:N-598C", "task:N-594B", *GLOBAL_SAFETY_PRECONDITIONS),
        produced_tokens=(
            "gate:R025-FREEZE-01",
            "gate:R025-FREEZE-02",
            "gate:R025-FREEZE-03",
            "gate:R025-FREEZE-04",
            "gate:R025-FREEZE-05",
            "gate:R025-FREEZE-06",
            "gate:R025-FREEZE-07",
            "gate:R025-FREEZE-08",
            "gate:R025-FREEZE-09",
            "gate:R025-FREEZE-10",
            "gate:R025-FREEZE-INDEPENDENT-ACCEPTANCE",
        ),
    ),
    ControlUnitRow(
        control_id="R025-freeze-transition",
        kind="state_transition",
        execution_order=2,
        preconditions=("release:R025-freeze-audit", *GLOBAL_SAFETY_PRECONDITIONS),
        produced_tokens=("gate:R025-FROZEN-MILESTONE",),
    ),
    ControlUnitRow(
        control_id="R025-reservation-preflight",
        kind="evaluation",
        execution_order=3,
        preconditions=("gate:R025-FROZEN-MILESTONE", "task:N-594B", *GLOBAL_SAFETY_PRECONDITIONS),
        produced_tokens=("ac:N-594B:AC-08", "gate:R025-RESERVATION-POSTFREEZE-PREFLIGHT"),
    ),
    ControlUnitRow(
        control_id="R025-reserved-transition",
        kind="state_transition",
        execution_order=4,
        preconditions=(
            "gate:R025-FROZEN-MILESTONE",
            "gate:R025-RESERVATION-POSTFREEZE-PREFLIGHT",
            *GLOBAL_SAFETY_PRECONDITIONS,
        ),
        produced_tokens=("gate:R025-RESERVED-MILESTONE",),
    ),
    ControlUnitRow(
        control_id="R025-implementation-conformance",
        kind="evaluation",
        execution_order=5,
        preconditions=(
            "gate:R025-RESERVED-MILESTONE",
            "task:R-025",
            "release:R025-measurement-implementation",
            *GLOBAL_SAFETY_PRECONDITIONS,
        ),
        produced_tokens=("gate:R025-NO-EARLY-LOOK", "gate:R025-IMPLEMENTATION-CONFORMANCE"),
    ),
    ControlUnitRow(
        control_id="R025-single-look",
        kind="single_look",
        execution_order=6,
        preconditions=(
            "gate:R025-NO-EARLY-LOOK",
            "gate:R025-RESERVED-MILESTONE",
            "gate:R025-IMPLEMENTATION-CONFORMANCE",
            *GLOBAL_SAFETY_PRECONDITIONS,
        ),
        produced_tokens=("gate:R025-SINGLE-LOOK-MILESTONE",),
    ),
)

CONTROL_UNIT_ORDER: Final[dict[str, ControlUnitRow]] = {
    row.control_id: row for row in CONTROL_UNIT_TABLE
}


def build_context_id(control_id: str, control_sequence: int) -> str:
    """`control-context:R-025/<control-id>/<control-sequence>` を組み立てる。"""
    return f"control-context:R-025/{control_id}/{control_sequence}"


def units_with_lower_execution_order(control_id: str) -> list[str]:
    """catalog 上で対象 control unit より先に完了しているべき unit 群を返す。"""
    target = CONTROL_UNIT_ORDER.get(control_id)
    if target is None:
        return []
    return [
        row.control_id for row in CONTROL_UNIT_TABLE if row.execution_order < target.execution_order
    ]


# ---------------------------------------------------------------------------
# ID 導出
# ---------------------------------------------------------------------------


def derive_event_id(event: Mapping[str, Any]) -> str:
    """`event_id=null` にした全 object の canonical hash から event ID を導出する。"""
    return CONTROL_EVENT_ID_PREFIX + canonical_hash_with_null_fields(event, "event_id")


def event_stable_key(event: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
    """stable original key `(control_id, control_kind, control_sequence, context_id)`。"""
    return (
        event.get("control_id"),
        event.get("control_kind"),
        event.get("control_sequence"),
        event.get("context_id"),
    )


def event_filename(event: Mapping[str, Any]) -> str:
    return f"{event['event_id']}.yml"


# ---------------------------------------------------------------------------
# schema 検査（純粋関数）
# ---------------------------------------------------------------------------


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_closed_fields(obj: Mapping[str, Any], fields: Sequence[str], *, label: str) -> list[str]:
    issues: list[str] = []
    missing = [name for name in fields if name not in obj]
    extra = [name for name in obj if name not in fields]
    if missing:
        issues.append(f"{label}: 必須 field が欠落している: {', '.join(sorted(missing))}")
    if extra:
        issues.append(f"{label}: 未知 field を拒否する: {', '.join(sorted(extra))}")
    return issues


def _validate_token_list(tokens: object, *, label: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(tokens, list) or (not allow_empty and not tokens):
        return [f"{label}: 非空の typed token 配列が必要である"]
    issues: list[str] = []
    for index, token in enumerate(tokens):
        if not isinstance(token, str) or not CONTROL_TOKEN_RE.match(token):
            issues.append(
                f"{label}[{index}]: typed token（<namespace>:<id>）が必要である: {token!r}"
            )
    if len(set(tokens)) != len(tokens):
        issues.append(f"{label}: 重複 token がある")
    return issues


def _validate_terminal_verification(event: Mapping[str, Any], *, label: str) -> list[str]:
    verification = event.get("terminal_verification")
    if not isinstance(verification, Mapping):
        return [f"{label}: terminal_verification は object でなければならない"]
    issues = _check_closed_fields(
        verification, TERMINAL_VERIFICATION_FIELDS, label=f"{label}.terminal_verification"
    )
    ref = verification.get("n598b_evidence_ref")
    if not isinstance(ref, str) or not TRACKED_PATH_RE.match(ref) or ".." in ref.split("/"):
        issues.append(f"{label}: n598b_evidence_ref は追跡済み `docs/` 配下の path が必要である")
    for field in ("n598b_evidence_sha256", "evaluator_source_sha256"):
        value = verification.get(field)
        if not isinstance(value, str) or not HEX64_RE.match(value):
            issues.append(f"{label}: terminal_verification.{field} は 64 桁 lower hex が必要である")
    evaluator_id = verification.get("evaluator_id")
    if not isinstance(evaluator_id, str) or not evaluator_id.strip():
        issues.append(f"{label}: terminal_verification.evaluator_id は非空文字列が必要である")
    return issues


def _validate_evidence_roots(event: Mapping[str, Any], *, label: str) -> list[str]:
    roots = event.get("evidence_roots")
    if not isinstance(roots, list) or not roots:
        return [f"{label}: evidence_roots は 1 件以上の配列が必要である"]
    issues: list[str] = []
    for index, root in enumerate(roots, start=1):
        issues.extend(validate_root_tuple(root, label=f"{label}.evidence_roots[{index}]"))
    if issues:
        return issues

    typed_roots = [root for root in roots if isinstance(root, Mapping)]
    sequences = [root["root_sequence"] for root in typed_roots]
    if sequences != list(range(1, len(typed_roots) + 1)):
        issues.append(f"{label}: root_sequence は 1 から連続でなければならない（gap／重複を拒否）")
    slots = [root_slot(root) for root in typed_roots]
    if len(set(slots)) != len(slots):
        issues.append(f"{label}: 同じ slot の root tuple が重複している")
    ids = [root["evidence_id"] for root in typed_roots]
    if len(set(ids)) != len(ids):
        issues.append(f"{label}: 同じ evidence_id の root tuple が重複している")

    projections = [root_tuple_projection(root) for root in typed_roots]
    canonical = [canonical_content_bytes(projection) for projection in projections]
    if canonical != sorted(canonical):
        issues.append(f"{label}: evidence_roots は canonical tuple の UTF-8 byte 昇順が必要である")

    expected_roots_sha = set_content_sha256(projections)
    if event.get("evidence_roots_sha256") != expected_roots_sha:
        issues.append(
            f"{label}: evidence_roots_sha256 が再計算値と一致しない"
            f"（expected={expected_roots_sha}）"
        )
    return issues


def _validate_target_transition(event: Mapping[str, Any], *, label: str) -> list[str]:
    kind = event.get("control_kind")
    target = event.get("target_transition")
    if kind != "state_transition":
        if target is not None:
            issues = [f"{label}: control_kind={kind!r} では target_transition は null が必要である"]
            return issues
        return []
    if not isinstance(target, Mapping):
        return [f"{label}: control_kind=state_transition では target_transition が必須である"]
    issues = _check_closed_fields(
        target, TARGET_TRANSITION_FIELDS, label=f"{label}.target_transition"
    )
    if target.get("target_profile") not in TARGET_PROFILES:
        issues.append(
            f"{label}.target_transition.target_profile は {sorted(TARGET_PROFILES)} に限る"
        )
    artifact_path = target.get("artifact_path")
    if (
        not isinstance(artifact_path, str)
        or not TRACKED_PATH_RE.match(artifact_path)
        or ".." in artifact_path.split("/")
    ):
        issues.append(f"{label}.target_transition.artifact_path は追跡済み path が必要である")
    for field in ("from_blob_sha256", "to_blob_sha256"):
        value = target.get(field)
        if not isinstance(value, str) or not HEX64_RE.match(value):
            issues.append(f"{label}.target_transition.{field} は 64 桁 lower hex が必要である")
    if target.get("from_blob_sha256") == target.get("to_blob_sha256"):
        issues.append(f"{label}.target_transition: from_blob_sha256 と to_blob_sha256 が同一である")
    anchor = target.get("derived_transition_anchor")
    if not isinstance(anchor, str) or not HEX40_RE.match(anchor):
        issues.append(f"{label}.target_transition.derived_transition_anchor は40桁lower hexが必要")
    return issues


def _validate_supporting_artifacts(event: Mapping[str, Any], *, label: str) -> list[str]:
    artifacts = event.get("supporting_artifacts")
    if not isinstance(artifacts, list):
        return [f"{label}: supporting_artifacts は配列である必要がある"]
    issues: list[str] = []
    paths: list[str] = []
    for index, artifact in enumerate(artifacts):
        item_label = f"{label}.supporting_artifacts[{index}]"
        if not isinstance(artifact, Mapping):
            issues.append(f"{item_label}: object である必要がある")
            continue
        issues.extend(_check_closed_fields(artifact, SUPPORTING_ARTIFACT_FIELDS, label=item_label))
        path = artifact.get("path")
        if not isinstance(path, str) or not TRACKED_PATH_RE.match(path) or ".." in path.split("/"):
            issues.append(f"{item_label}.path は追跡済み `docs/` 配下の path が必要である")
        else:
            paths.append(path)
        raw_sha = artifact.get("raw_sha256")
        if not isinstance(raw_sha, str) or not HEX64_RE.match(raw_sha):
            issues.append(f"{item_label}.raw_sha256 は 64 桁 lower hex が必要である")
    if len(set(paths)) != len(paths):
        issues.append(f"{label}: supporting_artifacts に重複 path がある")
    return issues


def validate_catalog_conformance(event: Mapping[str, Any], *, label: str = "event") -> list[str]:
    """event の control_id／kind／context_id／preconditions／produced_tokens が
    closed catalog（`CONTROL_UNIT_TABLE`）と exact 一致するかを検査する。
    """
    control_id = event.get("control_id")
    if not isinstance(control_id, str) or control_id not in CONTROL_UNIT_ORDER:
        return [f"{label}: control_id が closed catalog に存在しない: {control_id!r}"]
    row = CONTROL_UNIT_ORDER[control_id]
    issues: list[str] = []
    if event.get("control_kind") != row.kind:
        issues.append(f"{label}: control_kind が catalog と一致しない（catalog={row.kind!r}）")
    sequence = event.get("control_sequence")
    if not _is_positive_int(sequence):
        issues.append(f"{label}: control_sequence は 1 以上の整数が必要である")
    else:
        assert isinstance(sequence, int)  # _is_positive_int が保証済み（mypy narrowing 用）
        expected_context = build_context_id(control_id, sequence)
        if event.get("context_id") != expected_context:
            issues.append(
                f"{label}: context_id が catalog 導出値と一致しない: {expected_context!r}"
            )
    preconditions = event.get("preconditions")
    if isinstance(preconditions, list) and list(preconditions) != list(row.preconditions):
        issues.append(f"{label}: preconditions が catalog と exact 一致しない（順序含む）")
    produced = event.get("produced_tokens")
    if isinstance(produced, list) and list(produced) != list(row.produced_tokens):
        issues.append(f"{label}: produced_tokens が catalog と exact 一致しない（順序含む）")
    return issues


def validate_event_object(event: object, *, label: str = "event") -> list[str]:
    """control unit event 1 件の closed schema・ID 導出・catalog 適合を検査する。"""
    if not isinstance(event, Mapping):
        return [f"{label}: event は object でなければならない"]
    issues = _check_closed_fields(event, EVENT_TOP_LEVEL_FIELDS, label=label)
    if event.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"{label}: schema_version は {SCHEMA_VERSION} でなければならない")
    if event.get("control_kind") not in CONTROL_KINDS:
        issues.append(f"{label}: control_kind は {sorted(CONTROL_KINDS)} に限る")
    context_id = event.get("context_id")
    if not isinstance(context_id, str) or not CONTEXT_ID_RE.match(context_id):
        issues.append(f"{label}: context_id は `control-context:R-025/<id>/<seq>` 形式が必要である")
    for field in ("pre_action_tested_head", "action_terminal_sha"):
        value = event.get(field)
        if not isinstance(value, str) or not HEX40_RE.match(value):
            issues.append(f"{label}: {field} は 40 桁 lower hex が必要である")
    issues.extend(_validate_token_list(event.get("preconditions"), label=f"{label}.preconditions"))
    issues.extend(
        _validate_token_list(event.get("produced_tokens"), label=f"{label}.produced_tokens")
    )
    issues.extend(validate_catalog_conformance(event, label=label))
    issues.extend(_validate_evidence_roots(event, label=label))
    issues.extend(_validate_supporting_artifacts(event, label=label))
    issues.extend(_validate_target_transition(event, label=label))
    issues.extend(_validate_terminal_verification(event, label=label))

    claim_ids = event.get("closure_claim_ids")
    if not isinstance(claim_ids, list) or any(not isinstance(item, str) for item in claim_ids):
        issues.append(f"{label}: closure_claim_ids は文字列配列でなければならない")
    else:
        if claim_ids != sorted(claim_ids, key=lambda item: item.encode("utf-8")):
            issues.append(f"{label}: closure_claim_ids は UTF-8 byte 昇順が必要である")
        if len(set(claim_ids)) != len(claim_ids):
            issues.append(f"{label}: closure_claim_ids が重複している")
        expected_claims_sha = set_content_sha256(claim_ids)
        if event.get("closure_claim_ids_sha256") != expected_claims_sha:
            issues.append(
                f"{label}: closure_claim_ids_sha256 が再計算値と一致しない"
                f"（expected={expected_claims_sha}）"
            )

    if event.get("status") not in CONTROL_STATUSES:
        issues.append(f"{label}: status は {sorted(CONTROL_STATUSES)} に限る")

    ordinal = event.get("correction_ordinal")
    corrects = event.get("corrects_event_id")
    if not _is_nonnegative_int(ordinal):
        issues.append(f"{label}: correction_ordinal は 0 以上の整数でなければならない")
    elif ordinal == 0:
        if corrects is not None:
            issues.append(f"{label}: original は corrects_event_id=null でなければならない")
    else:
        if not isinstance(corrects, str) or not EVENT_ID_RE.match(corrects):
            issues.append(f"{label}: 訂正 event は corrects_event_id に直前 event ID が必要である")

    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not EVENT_ID_RE.match(event_id):
        issues.append(f"{label}: event_id は `CUE-<64 lower hex>` でなければならない")
    elif not issues:
        expected = derive_event_id(event)
        if event_id != expected:
            issues.append(f"{label}: event_id が再計算値と一致しない（expected={expected}）")
    return issues


def validate_event_chain(events: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """event 集合の stable key 一意性と訂正 chain（cycle／branch／gap）を検査する。

    `task_closeout_event.validate_event_chain` と同じ規則を CUE の stable key
    （`event_stable_key`）で並行実装する（module docstring 参照）。
    """
    issues: list[str] = []
    originals: dict[tuple[Any, ...], list[str]] = {}
    children: dict[str, list[str]] = {}
    keys: dict[tuple[Any, ...], list[str]] = {}

    for event_id, event in sorted(events.items()):
        keys.setdefault(event_stable_key(event), []).append(event_id)
        if event.get("correction_ordinal") == 0:
            originals.setdefault(event_stable_key(event), []).append(event_id)
            continue
        parent_id = event.get("corrects_event_id")
        if not isinstance(parent_id, str):
            issues.append(f"{event_id}: 訂正 event に corrects_event_id がない")
            continue
        if parent_id not in events:
            issues.append(f"{event_id}: corrects_event_id={parent_id} の event が存在しない")
            continue
        children.setdefault(parent_id, []).append(event_id)

    for key, ids in sorted(keys.items(), key=lambda item: str(item[0])):
        original_ids = originals.get(key, [])
        if len(original_ids) != 1:
            issues.append(
                f"original key {key!r} の original event が {len(original_ids)} 件ある"
                f"（1 件が必要・ids={', '.join(sorted(ids))}）"
            )

    for parent_id, child_ids in sorted(children.items()):
        if len(child_ids) > 1:
            issues.append(
                f"{parent_id}: 訂正 chain が branch している: {', '.join(sorted(child_ids))}"
            )

    for event_id, event in sorted(events.items()):
        ordinal = event.get("correction_ordinal")
        if ordinal == 0:
            continue
        parent_id = event.get("corrects_event_id")
        if not isinstance(parent_id, str) or parent_id not in events:
            continue
        parent = events[parent_id]
        parent_ordinal = parent.get("correction_ordinal")
        if (
            isinstance(ordinal, int)
            and isinstance(parent_ordinal, int)
            and ordinal != parent_ordinal + 1
        ):
            issues.append(
                f"{event_id}: correction_ordinal が直前 event+1 でない"
                f"（{parent_ordinal} -> {ordinal}）"
            )
        if event_stable_key(parent) != event_stable_key(event):
            issues.append(f"{event_id}: 訂正 event が別 original key へ付け替えられている")

    issues.extend(_detect_chain_cycles(events))

    for key, ids in sorted(keys.items(), key=lambda item: str(item[0])):
        terminals = [event_id for event_id in ids if not children.get(event_id)]
        if len(terminals) != 1:
            issues.append(
                f"original key {key!r} の unsuperseded terminal event が {len(terminals)} 件ある"
            )
    return issues


def _detect_chain_cycles(events: Mapping[str, Mapping[str, Any]]) -> list[str]:
    issues: list[str] = []
    reported: set[frozenset[str]] = set()
    for event_id in sorted(events):
        seen: list[str] = []
        current_id = event_id
        while True:
            if current_id in seen:
                cycle = frozenset(seen[seen.index(current_id) :])
                if cycle not in reported:
                    reported.add(cycle)
                    issues.append(f"訂正 chain に cycle がある: {', '.join(sorted(cycle))}")
                break
            seen.append(current_id)
            parent_id = events[current_id].get("corrects_event_id")
            if not isinstance(parent_id, str) or parent_id not in events:
                break
            current_id = parent_id
    return issues


def current_event_for_key(
    events: Mapping[str, Mapping[str, Any]], key: tuple[Any, ...]
) -> Mapping[str, Any] | None:
    """stable key に対する唯一の unsuperseded terminal event を返す。"""
    superseded = {
        str(event.get("corrects_event_id"))
        for event in events.values()
        if event.get("corrects_event_id") is not None
    }
    candidates = [
        event
        for event_id, event in events.items()
        if event_stable_key(event) == key and event_id not in superseded
    ]
    if len(candidates) != 1:
        return None
    return candidates[0]
