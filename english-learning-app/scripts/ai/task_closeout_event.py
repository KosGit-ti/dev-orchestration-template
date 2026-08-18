#!/usr/bin/env python3
"""task closeout event（`task-closeout-event/v1`）の共通 schema／hash core。

N-602A Phase A（`docs/specs/N-602-evidence-truthfulness-generation.md`
「Task Closeout Event Schema v1」・AC-14、`docs/design.md`「共通 content hash 契約」）
の実装である。writer（`scripts/ai/write_task_closeout_event.py`）と専用 validator
（`scripts/ai/validate_task_closeout_events.py`）は本 module だけを規則の正本として
使い、schema／hash 規則をコピー実装しない。

canonical-content-v1 の実装は本 module へ自前で持つ。同一仕様の実装が N-602A の
別 PR（`scripts/ai/evidence_contract.py`）にもあるため、両者が main で合流した後に
`evidence_contract.py` 側へ共通化し、本 module の該当節を import へ置き換える
（重複実装を恒久化しない）。仕様は次の 5 点である。

- RFC 8785（JSON Canonicalization Scheme）相当の serialization・UTF-8・末尾改行なし
- float（非有限数・負の 0 を含む）を拒否し、精密値は plain decimal string で表す
- safe integer 範囲（±(2**53 - 1)）外の JSON number を拒否する
- Unicode NFC でない文字列を暗黙変換せず拒否する
- object key は UTF-16 code unit 順で sort する

仕様が曖昧だった点と本実装の解釈（正本更新時はここを先に直す）:

1. `evidence_roots` の並び順は「canonical tuple の UTF-8 byte 順」（N-602）と
   「root_sequence 1 始まり連続」「配列を同順で canonicalize」（materialization
   manifest `batch_identity`）の両方を満たす必要がある。canonical tuple は JCS で
   key を sort するため先頭 field が `artifact_path` になり、path は重複禁止なので
   byte 順は path 順と一致する。したがって「canonical byte 昇順に並べ、その順で
   root_sequence を 1..N へ割り当てる」ことで両契約を同時に満たせる。本実装は
   writer でこの順に固定し、validator で昇順・連続・重複なしを再検査する。
2. 訂正 root の evidence ID が参照する「訂正 event ID」は、循環を避けるため
   `corrects_event_id`（直前 event の ID・確定済み）と解釈する。訂正 event 自身の
   ID は root を含む全 object から導出されるため、自身を入力にはできない。
3. `closure_claim_ids_sha256` は N-602 の「N-598C と同じ JCS／LF 連結規則」に従い、
   各 member を JCS で canonicalize（string は引用符付き）して LF 終端で連結した
   byte 列の SHA-256 とする。materialization manifest の control unit event 側
   （`closure_claim_ids_sha256`）は raw token + LF と書かれており、両 schema の
   規則が揃っていない。TCE は自 schema の記述（N-602 §Task Closeout Event Schema）
   を優先し、統一は PR 合流時に正本側で行う。
4. `closeout_role` と `status` は spec が `task_terminal` / `verified` の 1 値ずつしか
   固定していないため、closed set として扱い未知値を fail-close する。
5. 訂正 event（`correction_ordinal >= 1`・Rcorr'）と新 closeout sequence（`closeout_sequence
   >= 2`・R₂）は別物である（DEC-20260815-003 決定 6・PR-E3c）。Rcorr' は既存 slot の訂正専用で
   親 event に無い slot を追加できない（`build_evidence_roots` は親 event の同 slot root を
   要求する。欠落 slot を Rcorr' へ足すことは表現不能＝仕様どおり）。欠落 slot は新 sequence の
   S1₂／R₂（`correction_ordinal=0`・`corrects_event_id=null`・stable key が別）で作る。
   `closeout_sequence=n>=2` は同 task／role の `n-1` が存在するときだけ受理し
   （`validate_event_chain`）、plan の repair-hold（`correction_repair.repair_target_task_id`）
   と一致する task に限る（plan 依存の検査は writer `--repair-hold-check` と validator の
   S1 unit 検査が担う）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Final

EVENT_DIR_REL: Final = "docs/ai/task-closeout-events"
EVENT_PATH_GLOB: Final = "docs/ai/task-closeout-events/*.yml"
EVENT_ID_PREFIX: Final = "TCE-"
EVIDENCE_ID_PREFIX: Final = "EV-"
SCHEMA_VERSION: Final = 1

CLOSEOUT_ROLES: Final = frozenset({"task_terminal"})
MERGE_METHODS: Final = frozenset({"rebase", "squash", "merge"})
EVENT_STATUSES: Final = frozenset({"verified"})

# N-598C `closeout_action.kind` のうち、N-602A bootstrap が扱う 4 値
# （`program_closeout` は N-604／N-597 の担当であり bootstrap では扱わない）。
CLOSEOUT_ACTIONS: Final = (
    "evidence_registration",
    "correction_staging",
    "correction_registration",
    "advance_state",
)

EVENT_TOP_LEVEL_FIELDS: Final = (
    "schema_version",
    "event_id",
    "task_id",
    "closeout_role",
    "closeout_sequence",
    "premerge_tested_head",
    "actual_merge_terminal_sha",
    "merged_pr",
    "merge_method",
    "terminal_verification",
    "corrects_event_id",
    "correction_ordinal",
    "status",
)
TERMINAL_VERIFICATION_FIELDS: Final = (
    "n598b_evidence_ref",
    "n598b_evidence_sha256",
    "evidence_roots",
    "evidence_roots_sha256",
    "closure_claim_ids",
    "closure_claim_ids_sha256",
)
ROOT_TUPLE_FIELDS: Final = (
    "root_sequence",
    "evidence_id",
    "subject_id",
    "evidence_type",
    "phase_or_context_id",
    "artifact_path",
    "artifact_raw_sha256",
)
ROOT_DESCRIPTOR_FIELDS: Final = (
    "subject_id",
    "evidence_type",
    "phase_or_context_id",
    "artifact_path",
    "artifact_raw_sha256",
)

HEX40_RE: Final = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
EVENT_ID_RE: Final = re.compile(r"^TCE-[0-9a-f]{64}$")
EVIDENCE_ID_RE: Final = re.compile(r"^EV-[0-9a-f]{64}$")
EVENT_FILE_RE: Final = re.compile(r"^TCE-[0-9a-f]{64}\.yml$")
TASK_ID_RE: Final = re.compile(r"^(?:[A-Z]+-[0-9]+[A-Z]?|R-[0-9]{3})$")
CLAIM_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUBJECT_ID_RE: Final = re.compile(
    r"^(?:ac|task|claim|gate|release|external|milestone):[A-Za-z0-9][A-Za-z0-9._:\-]*$"
)
EVIDENCE_TYPE_RE: Final = re.compile(r"^evidence-type:[a-z][a-z0-9_]*$")
PHASE_OR_CONTEXT_RE: Final = re.compile(r"^(?:task|control-context):[A-Za-z0-9][A-Za-z0-9._:/\-]*$")
TRACKED_PATH_RE: Final = re.compile(r"^docs/[A-Za-z0-9][A-Za-z0-9._/\-]*$")

_MAX_SAFE_INTEGER: Final = 2**53 - 1


class CanonicalContentError(ValueError):
    """canonical-content-v1 の入力契約違反。"""


class TaskCloseoutEventError(ValueError):
    """task closeout event の生成契約違反（writer 側の fail-close）。"""


# ---------------------------------------------------------------------------
# canonical-content-v1（docs/design.md「共通 content hash 契約」）
# ---------------------------------------------------------------------------


def _canonical_string(value: str) -> str:
    """RFC 8785 の string serialization を返す（NFC でない文字列は拒否する）。"""
    if not unicodedata.is_normalized("NFC", value):
        raise CanonicalContentError("NFC 正規化されていない文字列は暗黙変換せず拒否する")
    out: list[str] = ['"']
    for char in value:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif code == 0x08:
            out.append("\\b")
        elif code == 0x09:
            out.append("\\t")
        elif code == 0x0A:
            out.append("\\n")
        elif code == 0x0C:
            out.append("\\f")
        elif code == 0x0D:
            out.append("\\r")
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _canonical_value(value: object) -> str:
    """canonical-content-v1 の serialization を返す。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise CanonicalContentError("safe integer 範囲外の JSON number は拒否する")
        return str(value)
    if isinstance(value, float):
        # 精密な時刻・秒・予算は plain decimal string で表す規約のため float を受けない。
        raise CanonicalContentError(
            "float は canonical-content-v1 の入力にできない（plain decimal string を使う）"
        )
    if isinstance(value, str):
        return _canonical_string(value)
    if isinstance(value, Mapping):
        items: list[tuple[bytes, str, object]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalContentError("object key は string でなければならない")
            items.append((key.encode("utf-16-be"), key, item))
        items.sort(key=lambda entry: entry[0])
        body = ",".join(
            f"{_canonical_string(key)}:{_canonical_value(item)}" for _, key, item in items
        )
        return "{" + body + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_value(item) for item in value) + "]"
    raise CanonicalContentError(f"canonical-content-v1 が扱えない型: {type(value).__name__}")


def canonical_content_bytes(value: object) -> bytes:
    """canonical-content-v1（RFC 8785 / UTF-8 / 末尾改行なし）の byte 列を返す。"""
    return _canonical_value(value).encode("utf-8")


def canonical_content_sha256(value: object) -> str:
    """canonical-content-v1 byte 列の SHA-256（lowercase hex）を返す。"""
    return hashlib.sha256(canonical_content_bytes(value)).hexdigest()


def canonical_hash_with_null_fields(obj: Mapping[str, Any], *fields: str) -> str:
    """指定 field を JSON null へ置いた projection の canonical hash を返す。"""
    projection: dict[str, Any] = dict(obj)
    for name in fields:
        projection[name] = None
    return canonical_content_sha256(projection)


def sha256_bytes(data: bytes) -> str:
    """raw byte 列の SHA-256 を返す（canonical hash とは別規則）。"""
    return hashlib.sha256(data).hexdigest()


def set_content_sha256(members: Sequence[Any]) -> str:
    """各 member を JCS canonicalize し LF 終端で連結した byte 列の SHA-256 を返す。

    member の並びは呼び出し側が確定した配列順をそのまま使う（配列自体が canonical
    byte 昇順であることは schema 検査側で保証する）。空集合は SHA-256("") となる。
    """
    joined = b"".join(canonical_content_bytes(member) + b"\n" for member in members)
    return hashlib.sha256(joined).hexdigest()


# ---------------------------------------------------------------------------
# evidence ID 導出（artifact hash 非依存）
# ---------------------------------------------------------------------------


def derive_evidence_id(
    *,
    task_id: str,
    merged_pr: int,
    closeout_role: str,
    closeout_sequence: int,
    subject_id: str,
    evidence_type: str,
    phase_or_context_id: str | None,
    root_sequence: int,
) -> str:
    """original root の evidence ID を stable key tuple の JCS SHA-256 から導出する。"""
    payload: dict[str, Any] = {
        "task_id": task_id,
        "merged_pr": merged_pr,
        "closeout_role": closeout_role,
        "closeout_sequence": closeout_sequence,
        "subject_id": subject_id,
        "evidence_type": evidence_type,
        "phase_or_context_id": phase_or_context_id,
        "root_sequence": root_sequence,
    }
    return EVIDENCE_ID_PREFIX + canonical_content_sha256(payload)


def derive_correction_evidence_id(
    *,
    root_evidence_id: str,
    subject_id: str,
    evidence_type: str,
    phase_or_context_id: str | None,
    correction_ordinal: int,
    correction_event_id: str,
) -> str:
    """訂正 root の evidence ID を closed projection の JCS SHA-256 から導出する。

    original 式を再利用せず、artifact hash も含めない。`correction_event_id` は
    module docstring の解釈 2 に従い `corrects_event_id`（直前 event の ID）とする。
    """
    payload: dict[str, Any] = {
        "root_evidence_id": root_evidence_id,
        "subject_id": subject_id,
        "evidence_type": evidence_type,
        "phase_or_context_id": phase_or_context_id,
        "correction_ordinal": correction_ordinal,
        "correction_event_id": correction_event_id,
    }
    return EVIDENCE_ID_PREFIX + canonical_content_sha256(payload)


def derive_event_id(event: Mapping[str, Any]) -> str:
    """`event_id=null` にした全 object の canonical hash から event ID を導出する。"""
    return EVENT_ID_PREFIX + canonical_hash_with_null_fields(event, "event_id")


def event_stable_key(event: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
    """original の一意 key `(task_id, merged_pr, closeout_role, closeout_sequence)`。"""
    return (
        event.get("task_id"),
        event.get("merged_pr"),
        event.get("closeout_role"),
        event.get("closeout_sequence"),
    )


def root_slot(root: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    """canonical slot `(subject_id, evidence_type, phase_or_context_id)`。"""
    return (
        root.get("subject_id"),
        root.get("evidence_type"),
        root.get("phase_or_context_id"),
    )


def root_tuple_projection(root: Mapping[str, Any]) -> dict[str, Any]:
    """root tuple を closed field だけの projection へ落とす（hash 入力）。"""
    return {field: root.get(field) for field in ROOT_TUPLE_FIELDS}


# ---------------------------------------------------------------------------
# event 構築（writer 用）
# ---------------------------------------------------------------------------


def _descriptor_sort_key(descriptor: Mapping[str, Any]) -> bytes:
    """root_sequence／evidence_id を含めない descriptor の canonical byte 列。"""
    projection = {field: descriptor.get(field) for field in ROOT_DESCRIPTOR_FIELDS}
    return canonical_content_bytes(projection)


def build_evidence_roots(
    descriptors: Sequence[Mapping[str, Any]],
    *,
    task_id: str,
    merged_pr: int,
    closeout_role: str,
    closeout_sequence: int,
    correction_ordinal: int = 0,
    corrects_event_id: str | None = None,
    previous_roots: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """descriptor 列から canonical 順の root tuple 配列を作る。

    `correction_ordinal >= 1` では `previous_roots`（直前 event の root 配列）から
    同一 slot の root ID を引き当て、訂正 ID 式で evidence ID を導出する。
    """
    if not descriptors:
        raise TaskCloseoutEventError("evidence_roots は空にできない")
    ordered = sorted(descriptors, key=_descriptor_sort_key)
    paths = [str(item.get("artifact_path")) for item in ordered]
    if len(set(paths)) != len(paths):
        raise TaskCloseoutEventError("同じ artifact_path を持つ root tuple は重複として拒否する")
    slots = [root_slot(item) for item in ordered]
    if len(set(slots)) != len(slots):
        raise TaskCloseoutEventError("同じ slot を持つ root tuple は重複として拒否する")

    previous_by_slot: dict[tuple[Any, Any, Any], Mapping[str, Any]] = {}
    if previous_roots is not None:
        previous_by_slot = {root_slot(root): root for root in previous_roots}

    roots: list[dict[str, Any]] = []
    for index, descriptor in enumerate(ordered, start=1):
        subject_id = str(descriptor["subject_id"])
        evidence_type = str(descriptor["evidence_type"])
        raw_phase = descriptor.get("phase_or_context_id")
        phase_or_context_id = None if raw_phase is None else str(raw_phase)
        if correction_ordinal == 0:
            evidence_id = derive_evidence_id(
                task_id=task_id,
                merged_pr=merged_pr,
                closeout_role=closeout_role,
                closeout_sequence=closeout_sequence,
                subject_id=subject_id,
                evidence_type=evidence_type,
                phase_or_context_id=phase_or_context_id,
                root_sequence=index,
            )
        else:
            if corrects_event_id is None:
                raise TaskCloseoutEventError("訂正 event は corrects_event_id を必須とする")
            slot = (subject_id, evidence_type, phase_or_context_id)
            previous = previous_by_slot.get(slot)
            if previous is None:
                raise TaskCloseoutEventError(f"訂正対象 event に同じ slot の root がない: {slot!r}")
            evidence_id = derive_correction_evidence_id(
                root_evidence_id=str(previous["evidence_id"]),
                subject_id=subject_id,
                evidence_type=evidence_type,
                phase_or_context_id=phase_or_context_id,
                correction_ordinal=correction_ordinal,
                correction_event_id=corrects_event_id,
            )
        roots.append(
            {
                "root_sequence": index,
                "evidence_id": evidence_id,
                "subject_id": subject_id,
                "evidence_type": evidence_type,
                "phase_or_context_id": phase_or_context_id,
                "artifact_path": str(descriptor["artifact_path"]),
                "artifact_raw_sha256": str(descriptor["artifact_raw_sha256"]),
            }
        )
    return roots


def closure_claim_ids_from_roots(roots: Sequence[Mapping[str, Any]]) -> list[str]:
    """`subject_id=claim:<id>` の root から closure claim ID 集合を機械導出する。"""
    claims = {
        str(root["subject_id"]).split(":", 1)[1]
        for root in roots
        if str(root.get("subject_id", "")).startswith("claim:")
    }
    return sorted(claims, key=lambda item: item.encode("utf-8"))


def build_event(
    *,
    task_id: str,
    merged_pr: int,
    closeout_role: str,
    closeout_sequence: int,
    premerge_tested_head: str,
    actual_merge_terminal_sha: str,
    merge_method: str,
    n598b_evidence_ref: str,
    n598b_evidence_sha256: str,
    evidence_roots: Sequence[Mapping[str, Any]],
    corrects_event_id: str | None = None,
    correction_ordinal: int = 0,
    status: str = "verified",
) -> dict[str, Any]:
    """schema v1 の event object を組み立てて `event_id` を確定する。"""
    roots = [dict(root) for root in evidence_roots]
    claim_ids = closure_claim_ids_from_roots(roots)
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": None,
        "task_id": task_id,
        "closeout_role": closeout_role,
        "closeout_sequence": closeout_sequence,
        "premerge_tested_head": premerge_tested_head,
        "actual_merge_terminal_sha": actual_merge_terminal_sha,
        "merged_pr": merged_pr,
        "merge_method": merge_method,
        "terminal_verification": {
            "n598b_evidence_ref": n598b_evidence_ref,
            "n598b_evidence_sha256": n598b_evidence_sha256,
            "evidence_roots": roots,
            "evidence_roots_sha256": set_content_sha256(
                [root_tuple_projection(root) for root in roots]
            ),
            "closure_claim_ids": claim_ids,
            "closure_claim_ids_sha256": set_content_sha256(claim_ids),
        },
        "corrects_event_id": corrects_event_id,
        "correction_ordinal": correction_ordinal,
        "status": status,
    }
    event["event_id"] = derive_event_id(event)
    return event


def event_filename(event: Mapping[str, Any]) -> str:
    """event file の basename（`<event-id>.yml`）を返す。"""
    return f"{event['event_id']}.yml"


# ---------------------------------------------------------------------------
# schema 検査（validator 用・純粋関数）
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


def validate_root_tuple(root: object, *, label: str) -> list[str]:
    """root tuple 1 件の closed schema を検査する。"""
    if not isinstance(root, Mapping):
        return [f"{label}: root tuple は object でなければならない"]
    issues = _check_closed_fields(root, ROOT_TUPLE_FIELDS, label=label)
    if not _is_positive_int(root.get("root_sequence")):
        issues.append(f"{label}: root_sequence は 1 以上の整数でなければならない")
    evidence_id = root.get("evidence_id")
    if not isinstance(evidence_id, str) or not EVIDENCE_ID_RE.match(evidence_id):
        issues.append(f"{label}: evidence_id は `EV-<64 lower hex>` でなければならない")
    subject_id = root.get("subject_id")
    if not isinstance(subject_id, str) or not SUBJECT_ID_RE.match(subject_id):
        issues.append(f"{label}: subject_id は typed ID（`<namespace>:<id>`）が必要である")
    evidence_type = root.get("evidence_type")
    if not isinstance(evidence_type, str) or not EVIDENCE_TYPE_RE.match(evidence_type):
        issues.append(f"{label}: evidence_type は `evidence-type:<name>` が必要である")
    phase = root.get("phase_or_context_id")
    if phase is not None and (not isinstance(phase, str) or not PHASE_OR_CONTEXT_RE.match(phase)):
        issues.append(
            f"{label}: phase_or_context_id は null／`task:<id>`／`control-context:<id>` に限る"
        )
    artifact_path = root.get("artifact_path")
    if (
        not isinstance(artifact_path, str)
        or not TRACKED_PATH_RE.match(artifact_path)
        or ".." in artifact_path.split("/")
    ):
        issues.append(f"{label}: artifact_path は追跡済み `docs/` 配下の相対 path が必要である")
    raw_sha = root.get("artifact_raw_sha256")
    if not isinstance(raw_sha, str) or not HEX64_RE.match(raw_sha):
        issues.append(f"{label}: artifact_raw_sha256 は 64 桁 lower hex が必要である")
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
    ref_sha = verification.get("n598b_evidence_sha256")
    if not isinstance(ref_sha, str) or not HEX64_RE.match(ref_sha):
        issues.append(f"{label}: n598b_evidence_sha256 は 64 桁 lower hex が必要である")

    roots = verification.get("evidence_roots")
    if not isinstance(roots, list) or not roots:
        issues.append(f"{label}: evidence_roots は 1 件以上の配列が必要である")
        return issues
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
    paths = [root["artifact_path"] for root in typed_roots]
    if len(set(paths)) != len(paths):
        issues.append(f"{label}: 同じ artifact_path の root tuple が重複している")

    projections = [root_tuple_projection(root) for root in typed_roots]
    canonical = [canonical_content_bytes(projection) for projection in projections]
    if canonical != sorted(canonical):
        issues.append(f"{label}: evidence_roots は canonical tuple の UTF-8 byte 昇順が必要である")

    expected_roots_sha = set_content_sha256(projections)
    if verification.get("evidence_roots_sha256") != expected_roots_sha:
        issues.append(
            f"{label}: evidence_roots_sha256 が再計算値と一致しない"
            f"（expected={expected_roots_sha}）"
        )

    claim_ids = verification.get("closure_claim_ids")
    if not isinstance(claim_ids, list) or any(not isinstance(item, str) for item in claim_ids):
        issues.append(f"{label}: closure_claim_ids は文字列配列でなければならない")
        return issues
    typed_claims: list[str] = [str(item) for item in claim_ids]
    if any(not CLAIM_ID_RE.match(item) for item in typed_claims):
        issues.append(f"{label}: closure_claim_ids に不正な claim ID がある")
    if len(set(typed_claims)) != len(typed_claims):
        issues.append(f"{label}: closure_claim_ids が重複している")
    if typed_claims != sorted(typed_claims, key=lambda item: item.encode("utf-8")):
        issues.append(f"{label}: closure_claim_ids は UTF-8 byte 昇順が必要である")
    expected_claims = closure_claim_ids_from_roots(typed_roots)
    if sorted(typed_claims) != sorted(expected_claims):
        issues.append(
            f"{label}: closure_claim_ids は `subject_id=claim:<id>` の root 集合と"
            "exact 一致しなければならない"
        )
    expected_claims_sha = set_content_sha256(typed_claims)
    if verification.get("closure_claim_ids_sha256") != expected_claims_sha:
        issues.append(
            f"{label}: closure_claim_ids_sha256 が再計算値と一致しない"
            f"（expected={expected_claims_sha}）"
        )
    return issues


def validate_event_object(event: object, *, label: str = "event") -> list[str]:
    """event object 1 件の closed schema・ID 導出・集合 hash を検査する。"""
    if not isinstance(event, Mapping):
        return [f"{label}: event は object でなければならない"]
    issues = _check_closed_fields(event, EVENT_TOP_LEVEL_FIELDS, label=label)
    if event.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"{label}: schema_version は {SCHEMA_VERSION} でなければならない")
    task_id = event.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        issues.append(f"{label}: task_id が canonical task ID ではない")
    if event.get("closeout_role") not in CLOSEOUT_ROLES:
        issues.append(
            f"{label}: closeout_role は {sorted(CLOSEOUT_ROLES)} に限る（未知 role は fail-close）"
        )
    if not _is_positive_int(event.get("closeout_sequence")):
        issues.append(f"{label}: closeout_sequence は 1 以上の整数でなければならない")
    premerge = event.get("premerge_tested_head")
    if not isinstance(premerge, str) or not HEX40_RE.match(premerge):
        issues.append(f"{label}: premerge_tested_head は 40 桁 lower hex が必要である")
    actual = event.get("actual_merge_terminal_sha")
    if not isinstance(actual, str) or not HEX40_RE.match(actual):
        issues.append(f"{label}: actual_merge_terminal_sha は 40 桁 lower hex が必要である")
    if isinstance(premerge, str) and premerge == actual:
        issues.append(
            f"{label}: premerge_tested_head を actual_merge_terminal_sha に代用してはならない"
        )
    if not _is_positive_int(event.get("merged_pr")):
        issues.append(f"{label}: merged_pr は 1 以上の整数でなければならない")
    if event.get("merge_method") not in MERGE_METHODS:
        issues.append(f"{label}: merge_method は {sorted(MERGE_METHODS)} に限る")
    if event.get("status") not in EVENT_STATUSES:
        issues.append(f"{label}: status は {sorted(EVENT_STATUSES)} に限る")

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

    issues.extend(_validate_terminal_verification(event, label=label))

    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not EVENT_ID_RE.match(event_id):
        issues.append(f"{label}: event_id は `TCE-<64 lower hex>` でなければならない")
    elif not issues:
        expected = derive_event_id(event)
        if event_id != expected:
            issues.append(f"{label}: event_id が再計算値と一致しない（expected={expected}）")
    return issues


def validate_root_evidence_ids(event: Mapping[str, Any], *, label: str = "event") -> list[str]:
    """original root の evidence ID が stable key tuple から導出済みかを検査する。

    訂正 event は直前 event の root を参照するため、chain 検査側で別途照合する。
    """
    verification = event.get("terminal_verification")
    if not isinstance(verification, Mapping):
        return []
    roots = verification.get("evidence_roots")
    if not isinstance(roots, list):
        return []
    if event.get("correction_ordinal") != 0:
        return []
    issues: list[str] = []
    for root in roots:
        if not isinstance(root, Mapping):
            continue
        expected = derive_evidence_id(
            task_id=str(event.get("task_id")),
            merged_pr=int(event.get("merged_pr", 0)),
            closeout_role=str(event.get("closeout_role")),
            closeout_sequence=int(event.get("closeout_sequence", 0)),
            subject_id=str(root.get("subject_id")),
            evidence_type=str(root.get("evidence_type")),
            phase_or_context_id=(
                None
                if root.get("phase_or_context_id") is None
                else str(root.get("phase_or_context_id"))
            ),
            root_sequence=int(root.get("root_sequence", 0)),
        )
        if root.get("evidence_id") != expected:
            issues.append(
                f"{label}: root_sequence={root.get('root_sequence')} の evidence_id が"
                f"導出式と一致しない（expected={expected}）"
            )
    return issues


def validate_correction_root_ids(
    event: Mapping[str, Any], parent: Mapping[str, Any], *, label: str = "event"
) -> list[str]:
    """訂正 root の evidence ID を closed projection の導出式で照合する。"""
    verification = event.get("terminal_verification")
    parent_verification = parent.get("terminal_verification")
    if not isinstance(verification, Mapping) or not isinstance(parent_verification, Mapping):
        return [f"{label}: terminal_verification を読めないため訂正 root を検証できない"]
    roots = verification.get("evidence_roots")
    parent_roots = parent_verification.get("evidence_roots")
    if not isinstance(roots, list) or not isinstance(parent_roots, list):
        return [f"{label}: evidence_roots を読めないため訂正 root を検証できない"]
    parent_by_slot = {root_slot(root): root for root in parent_roots if isinstance(root, Mapping)}
    issues: list[str] = []
    for root in roots:
        if not isinstance(root, Mapping):
            continue
        slot = root_slot(root)
        parent_root = parent_by_slot.get(slot)
        if parent_root is None:
            issues.append(f"{label}: 訂正 root の slot {slot!r} が直前 event に存在しない")
            continue
        expected = derive_correction_evidence_id(
            root_evidence_id=str(parent_root.get("evidence_id")),
            subject_id=str(root.get("subject_id")),
            evidence_type=str(root.get("evidence_type")),
            phase_or_context_id=(
                None
                if root.get("phase_or_context_id") is None
                else str(root.get("phase_or_context_id"))
            ),
            correction_ordinal=int(event.get("correction_ordinal", 0)),
            correction_event_id=str(event.get("corrects_event_id")),
        )
        if root.get("evidence_id") == parent_root.get("evidence_id"):
            issues.append(f"{label}: 訂正 root が元 ID を再利用している（slot={slot!r}）")
        elif root.get("evidence_id") != expected:
            issues.append(
                f"{label}: 訂正 root の evidence_id が導出式と一致しない（expected={expected}）"
            )
    return issues


def validate_closeout_sequence_continuity(events: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """`closeout_sequence=n>=2` の event に対し、同 task／role の sequence `n-1` を持つ original が
    存在することを検査する（新 sequence は post-S2 回復の repair target にだけ生まれる・
    module docstring 解釈 5）。sequence 1 を飛ばした sequence 2、根拠のない sequence 3 等を
    拒否する。"""
    issues: list[str] = []
    present: set[tuple[Any, Any, Any]] = set()
    for event in events.values():
        if event.get("correction_ordinal") != 0:
            continue
        sequence = event.get("closeout_sequence")
        if isinstance(sequence, int) and not isinstance(sequence, bool):
            present.add((event.get("task_id"), event.get("closeout_role"), sequence))
    for event_id, event in sorted(events.items()):
        if event.get("correction_ordinal") != 0:
            continue
        sequence = event.get("closeout_sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 2:
            continue
        previous = (event.get("task_id"), event.get("closeout_role"), sequence - 1)
        if previous not in present:
            issues.append(
                f"{event_id}: closeout_sequence={sequence} だが同 task／role の sequence"
                f" {sequence - 1} の original event が存在しない（新 sequence は repair-hold の"
                " 再 closeout にだけ生まれる）"
            )
    return issues


def validate_event_chain(events: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """event 集合の stable key 一意性と訂正 chain（cycle／branch／gap）、closeout sequence の
    連続性を検査する。

    `events` は `event_id -> event object` の mapping である。
    """
    issues: list[str] = []
    originals: dict[tuple[Any, Any, Any, Any], list[str]] = {}
    children: dict[str, list[str]] = {}
    keys: dict[tuple[Any, Any, Any, Any], list[str]] = {}

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

    # 直前 event との関係（ordinal 連番・同じ stable key・訂正 root ID 導出）を検査する。
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
        issues.extend(validate_correction_root_ids(event, parent, label=event_id))

    issues.extend(_detect_chain_cycles(events))
    issues.extend(validate_closeout_sequence_continuity(events))

    for key, ids in sorted(keys.items(), key=lambda item: str(item[0])):
        terminals = [event_id for event_id in ids if not children.get(event_id)]
        if len(terminals) != 1:
            issues.append(
                f"original key {key!r} の unsuperseded terminal event が {len(terminals)} 件ある"
            )
    return issues


def _detect_chain_cycles(events: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """訂正 chain の cycle を検出する（同じ cycle は 1 回だけ報告する）。"""
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
    events: Mapping[str, Mapping[str, Any]], key: tuple[Any, Any, Any, Any]
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


def check_registration_batch(
    *,
    required_evidence_ids: Sequence[str],
    required_claim_ids: Sequence[str],
    registered_evidence_ids: Sequence[str],
    registered_claim_ids: Sequence[str],
) -> list[str]:
    """R の atomic batch が S1 固定集合と exact 一致するかを検査する。

    missing（部分登録）・extra・duplicate をいずれも拒否する。
    """
    issues: list[str] = []
    for label, required, registered in (
        ("evidence root", required_evidence_ids, registered_evidence_ids),
        ("closure claim", required_claim_ids, registered_claim_ids),
    ):
        if len(set(registered)) != len(registered):
            issues.append(f"{label}: 登録集合に duplicate がある")
        missing = sorted(set(required) - set(registered))
        extra = sorted(set(registered) - set(required))
        if missing:
            issues.append(f"{label}: 部分登録（missing {len(missing)} 件）: {', '.join(missing)}")
        if extra:
            issues.append(f"{label}: S1 固定集合外の extra 登録: {', '.join(extra)}")
    return issues
