#!/usr/bin/env python3
"""post-S2 回復（repair-hold）の `correction_repair` closed object（recovery schema v2）の共通実装。

`docs/specs/N-598C-exact-current-state-resolver.md`「Plan Active Block Schema」の
`correction_repair`（v1 field）と、DEC-20260815-003 決定 6（versioned recovery schema
`recovery_schema_version: 2` に `discovery_evidence`／`invalid_batch_ids`／`missing_slot_keys` を
追加）、manifest `task_closeout_state_machine.post_publication_repair_path`
（`correction_repair_closed_fields`／`affected_set_derivation`／`failure_state`）を機械化する。

役割分担:

- 本 module: closed schema の構造検査（純粋関数）、affected task／claim・missing slot・
  repair target の**再導出**（manifest と closeout event 集合から決定的に導く）、
  discovery artifact／`held_active`／`discovered_at_head` の repo 側検査。
- `scripts/ai/resolve_current_state.validate_correction_repair_object`: 上記を束ねた
  完全検査（構造＋再導出 exact 一致＋repo 検査）と、affected task の truth override
  （回復完了まで false）。
- `scripts/ai/validate_current_state._check_closing_block`: repair-hold 中の active block
  規則（`state=closing`・`substep ∈ evidence-correction|repair`・`blocked_by=["repair:<16hex>"]`・
  `closeout_registration` null 可・checkpoint／flag mirror）。
- `scripts/ai/write_repair_hold.py`: 上記導出で `correction_repair` object を生成する CLI
  （plan／checkpoint／flag へは書かない）。

導出規則（`derive_recovery_sets`）:

1. seed task = `invalid_batch_ids`（不完全 closeout batch＝TCE event）の owner task ∪
   `invalid_evidence_ids`（registry entry）の owner task（subject が `task:`／phase が
   `task:`／claim subject の `closure_owner_task`）。seed は「公開済み」（S2 済みの registry
   観測可能な代理＝当該 task の unsuperseded TCE event の root 全件が registry に登録済み）で
   なければならない（公開前訂正を repair-hold 経路へ送らない・`post_publication_repair_path.
   trigger`）。
2. affected task = seed ∪ manifest `task_catalog.dependencies` の推移閉包で seed の下流にある
   公開済み canonical task（aggregate は除く）。UTF-8 byte 昇順・重複なし。
3. affected claim = affected task の owner open claim（`closeout_expectation.
   derive_owner_open_claims`）∪ invalid evidence の claim subject。UTF-8 byte 昇順。
4. missing slot key = affected task ごとに `closeout_expectation.derive_expected_slots` と
   registry current entry（`registry_current_entries`）の差 `{task_id, subject_id,
   evidence_type, phase_or_context_id}`。並びは `(task_id, subject_id, evidence_type,
   phase_or_context_id)` の UTF-8 byte 昇順（null phase は空 byte 列として最前）。
   claim 分類の issue（`deadlock_without_finalizer` 等）は暫定 slot（gated_residual）で slot 集合が
   確定するため note として扱い、`derive_task_truth_slots` の issue（AC／gate 未定義・alias・
   aggregate）と slot 重複だけを導出不能（fail-close）とする。
5. repair target = affected のうち未回復（`recovered` 引数で除外）の task を dependency 推移閉包で
   最上流順に並べた先頭（同順位は UTF-8 byte 昇順の先頭）。全件回復済みなら None。

`held_active` は repair-hold 設置前の active block（`held_active.source_plan_revision` の
`docs/plan.md`）と task_id／substep／state が exact 一致しなければならない。terminal 保持
（`state=terminal`）は task_id／substep とも null。
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import closeout_expectation as expectation  # noqa: E402
from scripts.ai import task_closeout_event as tce  # noqa: E402
from scripts.ai import validate_current_state as current_state  # noqa: E402

__all__ = [
    "CORRECTION_REPAIR_FIELDS",
    "DISCOVERY_EVIDENCE_FIELDS",
    "HELD_ACTIVE_FIELDS",
    "HELD_ACTIVE_STATES",
    "HOLD_SUBSTEPS",
    "HOLD_SUBSTEP_EVIDENCE_CORRECTION",
    "HOLD_SUBSTEP_REPAIR",
    "MISSING_SLOT_KEY_FIELDS",
    "RECOVERY_SCHEMA_VERSION",
    "REPAIR_BLOCKED_BY_HEX_LEN",
    "REPAIR_BLOCKED_BY_PREFIX",
    "REPAIR_PHASES",
    "RESUME_PROJECTION_FIELDS",
    "RecoveryDerivation",
    "canonical_first_task",
    "check_discovered_at_head",
    "check_discovery_artifact",
    "check_held_active_against_plan",
    "correction_targets",
    "derive_missing_slot_keys",
    "derive_recovery_sets",
    "discovery_artifact_sha256",
    "downstream_tasks",
    "event_root_ids",
    "expected_blocked_by",
    "load_closeout_events",
    "owner_task_of_registry_entry",
    "published_task_ids",
    "repair_target_task_id",
    "sort_slot_keys",
    "task_dependency_closure",
    "unsuperseded_events",
    "utf8_sorted",
    "validate_correction_repair_structure",
]

RECOVERY_SCHEMA_VERSION: Final = 2

# spec 189-207 行の v1 field に recovery v2 拡張（DEC-20260815-003 決定 6）を加えた closed field。
CORRECTION_REPAIR_FIELDS: Final = (
    "recovery_schema_version",
    "discovery_evidence_id",
    "discovery_evidence",
    "discovered_at_head",
    "held_active",
    "invalid_evidence_ids",
    "invalid_batch_ids",
    "missing_slot_keys",
    "affected_task_ids",
    "affected_claim_ids",
    "repair_target_task_id",
    "resume_projection",
    "phase",
)
DISCOVERY_EVIDENCE_FIELDS: Final = ("artifact_path", "artifact_raw_sha256")
HELD_ACTIVE_FIELDS: Final = ("task_id", "substep", "state", "source_plan_revision")
HELD_ACTIVE_STATES: Final = ("ready", "in_progress", "blocked", "terminal")
RESUME_PROJECTION_FIELDS: Final = ("task_id", "substep", "terminal")
MISSING_SLOT_KEY_FIELDS: Final = ("task_id", "subject_id", "evidence_type", "phase_or_context_id")
REPAIR_PHASES: Final = ("staging", "registering", "revalidating")

# repair-hold 中の active block substep（spec 208-213 行 `evidence-correction`・
# 412-415 行 `repair`）。`repair` は revalidating で false が残った repair target の再 closeout
# 段階に限る。
HOLD_SUBSTEP_EVIDENCE_CORRECTION: Final = "evidence-correction"
HOLD_SUBSTEP_REPAIR: Final = "repair"
HOLD_SUBSTEPS: Final = (HOLD_SUBSTEP_EVIDENCE_CORRECTION, HOLD_SUBSTEP_REPAIR)

# hold 中の `blocked_by` は discovery artifact raw SHA-256 先頭 16 hex を持つ単一 token。
REPAIR_BLOCKED_BY_PREFIX: Final = "repair:"
REPAIR_BLOCKED_BY_HEX_LEN: Final = 16

SUBSTEP_RE: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class RecoveryDerivation:
    """manifest と event 集合から再導出した recovery 集合。

    ``issues`` が非空なら集合は確定できない（呼出側は fail-close する）。``notes`` は
    集合が確定する範囲の注記（claim 分類の暫定 slot 等）。
    """

    seed_task_ids: tuple[str, ...]
    affected_task_ids: tuple[str, ...]
    affected_claim_ids: tuple[str, ...]
    missing_slot_keys: tuple[dict[str, Any], ...]
    published_task_ids: tuple[str, ...]
    issues: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------


def utf8_sorted(items: Iterable[str]) -> list[str]:
    """重複除去のうえ UTF-8 byte 昇順で返す。"""
    return sorted(set(items), key=lambda item: item.encode("utf-8"))


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _slot_key_sort_key(key: Mapping[str, Any]) -> tuple[bytes, bytes, bytes, bytes]:
    phase = key.get("phase_or_context_id")
    return (
        str(key.get("task_id")).encode("utf-8"),
        str(key.get("subject_id")).encode("utf-8"),
        str(key.get("evidence_type")).encode("utf-8"),
        b"" if phase is None else str(phase).encode("utf-8"),
    )


def _slot_key_tuple(key: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        key.get("task_id"),
        key.get("subject_id"),
        key.get("evidence_type"),
        key.get("phase_or_context_id"),
    )


def sort_slot_keys(keys: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """missing slot key を closed field 順の dict に正規化し、重複除去して canonical 順に並べる。"""
    unique: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
    for key in keys:
        normalized = {name: key.get(name) for name in MISSING_SLOT_KEY_FIELDS}
        unique.setdefault(_slot_key_tuple(normalized), normalized)
    return sorted(unique.values(), key=_slot_key_sort_key)


# ---------------------------------------------------------------------------
# 構造検査（純粋関数）
# ---------------------------------------------------------------------------


def _check_closed(obj: Mapping[str, Any], fields: Sequence[str], *, label: str) -> list[str]:
    issues: list[str] = []
    missing = [name for name in fields if name not in obj]
    extra = sorted(name for name in obj if name not in fields)
    if missing:
        issues.append(f"{label}: 必須 field が欠落しています: {missing}")
    if extra:
        issues.append(f"{label}: unknown field を拒否します: {extra}")
    return issues


def _check_sorted_unique_ids(
    values: object, *, label: str, pattern: re.Pattern[str], what: str
) -> list[str]:
    if not _is_str_list(values):
        return [f"{label} は文字列配列である必要があります"]
    assert isinstance(values, list)
    issues: list[str] = []
    for index, value in enumerate(values):
        if not pattern.match(value):
            issues.append(f"{label}[{index}] は {what} ではありません: {value!r}")
    if len(set(values)) != len(values):
        issues.append(f"{label} に重複があります")
    if list(values) != utf8_sorted(values):
        issues.append(f"{label} は UTF-8 byte 昇順である必要があります")
    return issues


def _check_slot_key(key: object, *, label: str) -> list[str]:
    if not isinstance(key, Mapping):
        return [f"{label} は object である必要があります"]
    issues = _check_closed(key, MISSING_SLOT_KEY_FIELDS, label=label)
    task_id = key.get("task_id")
    if not isinstance(task_id, str) or not tce.TASK_ID_RE.match(task_id):
        issues.append(f"{label}.task_id が canonical task ID ではありません: {task_id!r}")
    subject_id = key.get("subject_id")
    if not isinstance(subject_id, str) or not tce.SUBJECT_ID_RE.match(subject_id):
        issues.append(f"{label}.subject_id は typed ID（`<namespace>:<id>`）が必要です")
    evidence_type = key.get("evidence_type")
    if not isinstance(evidence_type, str) or not tce.EVIDENCE_TYPE_RE.match(evidence_type):
        issues.append(f"{label}.evidence_type は `evidence-type:<name>` が必要です")
    phase = key.get("phase_or_context_id")
    if phase is not None and (
        not isinstance(phase, str) or not tce.PHASE_OR_CONTEXT_RE.match(phase)
    ):
        issues.append(
            f"{label}.phase_or_context_id は null／`task:<id>`／`control-context:<id>` に限ります"
        )
    return issues


def _check_held_active(obj: object, *, label: str) -> list[str]:
    if not isinstance(obj, Mapping):
        return [f"{label} は object である必要があります"]
    issues = _check_closed(obj, HELD_ACTIVE_FIELDS, label=label)
    if issues:
        return issues
    state = obj.get("state")
    if state not in HELD_ACTIVE_STATES:
        issues.append(f"{label}.state は {list(HELD_ACTIVE_STATES)} に限ります（現在 {state!r}）")
    task_id = obj.get("task_id")
    substep = obj.get("substep")
    revision = obj.get("source_plan_revision")
    if state == "terminal":
        if task_id is not None or substep is not None:
            issues.append(f"{label}: state=terminal では task_id／substep をともに null にします")
    else:
        if not isinstance(task_id, str) or not tce.TASK_ID_RE.match(task_id):
            issues.append(f"{label}.task_id が canonical task ID ではありません: {task_id!r}")
        if not isinstance(substep, str) or not SUBSTEP_RE.match(substep):
            issues.append(f"{label}.substep が lower-kebab-case ではありません: {substep!r}")
        if revision is None:
            issues.append(
                f"{label}.source_plan_revision は state!=terminal では必須です"
                "（保持した active block の plan revision を固定する）"
            )
    if revision is not None and (not isinstance(revision, str) or not tce.HEX40_RE.match(revision)):
        issues.append(f"{label}.source_plan_revision は 40 桁 lower hex または null に限ります")
    return issues


def _check_resume_projection(obj: object, *, label: str) -> list[str]:
    if not isinstance(obj, Mapping):
        return [f"{label} は object である必要があります"]
    issues = _check_closed(obj, RESUME_PROJECTION_FIELDS, label=label)
    if issues:
        return issues
    terminal = obj.get("terminal")
    task_id = obj.get("task_id")
    substep = obj.get("substep")
    if terminal is True:
        if task_id is not None or substep is not None:
            issues.append(f"{label}: terminal=true では task_id／substep をともに null にします")
    elif terminal is False:
        if not isinstance(task_id, str) or not tce.TASK_ID_RE.match(task_id):
            issues.append(f"{label}.task_id は terminal=false では canonical task ID が必須です")
        if not isinstance(substep, str) or not SUBSTEP_RE.match(substep):
            issues.append(f"{label}.substep は terminal=false では lower-kebab-case が必須です")
    else:
        issues.append(f"{label}.terminal は真偽値が必要です")
    return issues


def validate_correction_repair_structure(obj: object) -> list[str]:
    """`correction_repair`（recovery schema v2）の closed schema を検査する（manifest 非依存）。

    field 過不足・型・pattern・enum・配列の UTF-8 昇順／重複、`discovery_evidence_id` と
    `discovery_evidence` の少なくとも一方、`invalid_evidence_ids` と `invalid_batch_ids`／
    `missing_slot_keys` の少なくとも一方が非空、`held_active`／`resume_projection` の形状を
    検査する。affected set の再導出は `derive_recovery_sets` を使う呼出側が行う。
    """
    label = "correction_repair"
    if not isinstance(obj, Mapping):
        return [f"{label} は object である必要があります"]
    issues = _check_closed(obj, CORRECTION_REPAIR_FIELDS, label=label)
    if issues:
        return issues

    if obj.get("recovery_schema_version") != RECOVERY_SCHEMA_VERSION:
        issues.append(
            f"{label}.recovery_schema_version は {RECOVERY_SCHEMA_VERSION} である必要があります"
            f"（現在 {obj.get('recovery_schema_version')!r}）"
        )

    discovery_id = obj.get("discovery_evidence_id")
    discovery = obj.get("discovery_evidence")
    if discovery_id is None and discovery is None:
        issues.append(
            f"{label}: discovery_evidence_id と discovery_evidence の少なくとも一方が必要です"
        )
    if discovery_id is not None and (
        not isinstance(discovery_id, str) or not tce.EVIDENCE_ID_RE.match(discovery_id)
    ):
        issues.append(
            f"{label}.discovery_evidence_id は `EV-<64 lower hex>` または null に限ります"
        )
    if discovery is not None:
        if not isinstance(discovery, Mapping):
            issues.append(f"{label}.discovery_evidence は object または null に限ります")
        else:
            issues.extend(
                _check_closed(
                    discovery, DISCOVERY_EVIDENCE_FIELDS, label=f"{label}.discovery_evidence"
                )
            )
            path = discovery.get("artifact_path")
            if (
                not isinstance(path, str)
                or not tce.TRACKED_PATH_RE.match(path)
                or ".." in path.split("/")
            ):
                issues.append(
                    f"{label}.discovery_evidence.artifact_path は追跡済み `docs/` 配下の相対 path"
                    " が必要です"
                )
            raw_sha = discovery.get("artifact_raw_sha256")
            if not isinstance(raw_sha, str) or not tce.HEX64_RE.match(raw_sha):
                issues.append(
                    f"{label}.discovery_evidence.artifact_raw_sha256 は 64 桁 lower hex が必要です"
                )

    head = obj.get("discovered_at_head")
    if not isinstance(head, str) or not tce.HEX40_RE.match(head):
        issues.append(f"{label}.discovered_at_head は 40 桁 lower hex が必要です")

    issues.extend(_check_held_active(obj.get("held_active"), label=f"{label}.held_active"))

    issues.extend(
        _check_sorted_unique_ids(
            obj.get("invalid_evidence_ids"),
            label=f"{label}.invalid_evidence_ids",
            pattern=tce.EVIDENCE_ID_RE,
            what="`EV-<64 lower hex>`",
        )
    )
    issues.extend(
        _check_sorted_unique_ids(
            obj.get("invalid_batch_ids"),
            label=f"{label}.invalid_batch_ids",
            pattern=tce.EVENT_ID_RE,
            what="`TCE-<64 lower hex>`",
        )
    )

    slot_keys = obj.get("missing_slot_keys")
    if not isinstance(slot_keys, list):
        issues.append(f"{label}.missing_slot_keys は配列である必要があります")
    else:
        for index, key in enumerate(slot_keys):
            issues.extend(_check_slot_key(key, label=f"{label}.missing_slot_keys[{index}]"))
        typed = [key for key in slot_keys if isinstance(key, Mapping)]
        if len(typed) == len(slot_keys):
            tuples = [_slot_key_tuple(key) for key in typed]
            if len(set(tuples)) != len(tuples):
                issues.append(f"{label}.missing_slot_keys に重複があります")
            if [_slot_key_tuple(key) for key in sort_slot_keys(typed)] != tuples:
                issues.append(
                    f"{label}.missing_slot_keys は (task_id, subject_id, evidence_type,"
                    " phase_or_context_id) の UTF-8 byte 昇順である必要があります"
                )

    invalid_ids = obj.get("invalid_evidence_ids")
    invalid_batches = obj.get("invalid_batch_ids")
    has_invalid_ids = isinstance(invalid_ids, list) and bool(invalid_ids)
    has_batches = isinstance(invalid_batches, list) and bool(invalid_batches)
    has_slots = isinstance(slot_keys, list) and bool(slot_keys)
    if not has_invalid_ids and not has_batches and not has_slots:
        issues.append(
            f"{label}: invalid_evidence_ids と invalid_batch_ids／missing_slot_keys の少なくとも"
            "一方が非空である必要があります（空の repair-hold を拒否）"
        )

    issues.extend(
        _check_sorted_unique_ids(
            obj.get("affected_task_ids"),
            label=f"{label}.affected_task_ids",
            pattern=tce.TASK_ID_RE,
            what="canonical task ID",
        )
    )
    affected_tasks = obj.get("affected_task_ids")
    if isinstance(affected_tasks, list) and not affected_tasks:
        issues.append(f"{label}.affected_task_ids は空にできません")
    issues.extend(
        _check_sorted_unique_ids(
            obj.get("affected_claim_ids"),
            label=f"{label}.affected_claim_ids",
            pattern=tce.CLAIM_ID_RE,
            what="claim ID",
        )
    )

    target = obj.get("repair_target_task_id")
    if not isinstance(target, str) or not tce.TASK_ID_RE.match(target):
        issues.append(
            f"{label}.repair_target_task_id が canonical task ID ではありません: {target!r}"
        )
    elif isinstance(affected_tasks, list) and target not in affected_tasks:
        issues.append(
            f"{label}.repair_target_task_id={target} が affected_task_ids に含まれていません"
        )

    issues.extend(
        _check_resume_projection(obj.get("resume_projection"), label=f"{label}.resume_projection")
    )

    phase = obj.get("phase")
    if phase not in REPAIR_PHASES:
        issues.append(f"{label}.phase は {list(REPAIR_PHASES)} に限ります（現在 {phase!r}）")
    return issues


# ---------------------------------------------------------------------------
# closeout event の読み込み（validate_task_closeout_events を import しない軽量版）
# ---------------------------------------------------------------------------


def load_closeout_events(repo_root: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """`docs/ai/task-closeout-events/*.yml` を `event_id -> event` で返す。

    schema 全体の検査は専用 validator に委ね、ここでは object であること・`event_id` が
    file 名と一致することだけを要求する（読めない file は issue）。
    """
    event_dir = repo_root / tce.EVENT_DIR_REL
    events: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    if not event_dir.is_dir():
        return events, issues
    for path in sorted(event_dir.iterdir()):
        if path.is_dir() or not tce.EVENT_FILE_RE.match(path.name):
            continue
        rel = f"{tce.EVENT_DIR_REL}/{path.name}"
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            issues.append(f"{rel}: event を読めません: {exc}")
            continue
        if not isinstance(loaded, dict):
            issues.append(f"{rel}: event file が object ではありません")
            continue
        event_id = loaded.get("event_id")
        if not isinstance(event_id, str) or path.name != f"{event_id}.yml":
            issues.append(f"{rel}: file 名が event_id と一致しません")
            continue
        events[event_id] = loaded
    return events, issues


def unsuperseded_events(events: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """`corrects_event_id` で置換されていない現行 event だけを返す。"""
    superseded = {
        str(event.get("corrects_event_id"))
        for event in events.values()
        if event.get("corrects_event_id") is not None
    }
    return {
        event_id: dict(event) for event_id, event in events.items() if event_id not in superseded
    }


def event_root_ids(event: Mapping[str, Any]) -> list[str]:
    """event の `terminal_verification.evidence_roots[].evidence_id`。"""
    verification = event.get("terminal_verification")
    roots = verification.get("evidence_roots") if isinstance(verification, Mapping) else None
    if not isinstance(roots, list):
        return []
    return [str(root.get("evidence_id")) for root in roots if isinstance(root, Mapping)]


def _registry_entries(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    registry = _mapping(manifest.get("evidence_registry"))
    entries = registry.get("entries")
    if not isinstance(entries, Mapping):
        return {}
    return {str(key): dict(value) for key, value in entries.items() if isinstance(value, Mapping)}


def published_task_ids(
    manifest: Mapping[str, Any], events: Mapping[str, Mapping[str, Any]]
) -> set[str]:
    """「公開済み」task（S2 済みの registry 観測可能な代理）の集合。

    当該 task の unsuperseded TCE event のうち、root evidence ID 全件が manifest
    `evidence_registry.entries` に存在する（R 済み）event が 1 件以上ある task。S2 state-sync
    自体は plan 履歴にしか残らないため、registry と event だけで閉じる代理条件を使う。
    """
    entries = _registry_entries(manifest)
    published: set[str] = set()
    for event in unsuperseded_events(events).values():
        task_id = event.get("task_id")
        if not isinstance(task_id, str):
            continue
        roots = event_root_ids(event)
        if roots and all(root_id in entries for root_id in roots):
            published.add(task_id)
    return published


# ---------------------------------------------------------------------------
# dependency DAG（task_catalog.dependencies の推移閉包）
# ---------------------------------------------------------------------------


def _canonical_tasks(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """aggregate を除いた canonical task entry（`task:` prefix を外した ID -> entry）。"""
    catalog = _mapping(manifest.get("task_catalog"))
    tasks: dict[str, dict[str, Any]] = {}
    for token, entry in catalog.items():
        if not isinstance(entry, Mapping) or not str(token).startswith("task:"):
            continue
        kind = entry.get("kind")
        if isinstance(kind, str) and kind in expectation.NON_TASK_KINDS:
            continue
        tasks[str(token).partition(":")[2]] = dict(entry)
    return tasks


def task_dependency_closure(manifest: Mapping[str, Any], task_id: str) -> set[str]:
    """task が `task_catalog.dependencies` で（release／milestone／alias 展開を経て）推移的に
    依存する canonical task ID の集合（自身は含めない）。"""
    catalog = _mapping(manifest.get("task_catalog"))
    closure: set[str] = set()
    stack = [task_id]
    while stack:
        current = stack.pop()
        entry = catalog.get(f"task:{current}")
        deps = _string_list(entry.get("dependencies")) if isinstance(entry, Mapping) else []
        if not deps:
            continue
        leaves, _issues = expectation.expand_tokens(manifest, deps, label=f"task:{current}")
        for leaf in leaves:
            if not leaf.startswith("task:"):
                continue
            dep_id = leaf.partition(":")[2]
            if dep_id == task_id or dep_id in closure:
                continue
            closure.add(dep_id)
            stack.append(dep_id)
    return closure


def downstream_tasks(manifest: Mapping[str, Any], seeds: Iterable[str]) -> set[str]:
    """seed のいずれかに推移的に依存する canonical task（seed 自身は含めない）。"""
    seed_set = set(seeds)
    result: set[str] = set()
    for task_id in _canonical_tasks(manifest):
        if task_id in seed_set:
            continue
        if task_dependency_closure(manifest, task_id) & seed_set:
            result.add(task_id)
    return result


def canonical_first_task(manifest: Mapping[str, Any], task_ids: Iterable[str]) -> str | None:
    """dependency 推移閉包で最上流の task（他の member に依存しない）のうち UTF-8 昇順の先頭。"""
    members = utf8_sorted(task_ids)
    if not members:
        return None
    member_set = set(members)
    for task_id in members:
        if not (task_dependency_closure(manifest, task_id) & member_set):
            return task_id
    # 循環（別 validator が拒否する）では最上流が定まらないため UTF-8 先頭へ倒す。
    return members[0]


# ---------------------------------------------------------------------------
# recovery 集合の再導出
# ---------------------------------------------------------------------------


def owner_task_of_registry_entry(
    manifest: Mapping[str, Any], evidence_id: str
) -> tuple[str | None, str | None]:
    """registry entry の owner task（`(task_id, issue)`）。

    subject が `task:` ならその task、phase が `task:` ならその task、subject が `claim:` なら
    claim の `closure_owner_task`。それ以外（control-context 等）は帰属不能。
    """
    entry = _registry_entries(manifest).get(evidence_id)
    if entry is None:
        return None, f"{evidence_id} が manifest evidence_registry.entries に存在しません"
    subject = entry.get("subject_id")
    phase = entry.get("phase_or_context_id")
    if isinstance(subject, str) and subject.startswith("task:"):
        return subject.partition(":")[2], None
    if isinstance(phase, str) and phase.startswith("task:"):
        return phase.partition(":")[2], None
    if isinstance(subject, str) and subject.startswith("claim:"):
        claim_id = subject.partition(":")[2]
        for claim in manifest.get("claims") or []:
            if isinstance(claim, Mapping) and claim.get("claim_id") == claim_id:
                owner = claim.get("closure_owner_task")
                if isinstance(owner, str) and owner.startswith("task:"):
                    return owner.partition(":")[2], None
                break
        return None, f"{evidence_id}: claim {claim_id} の closure_owner_task を解決できません"
    return (
        None,
        f"{evidence_id}: owner task を帰属できません（subject={subject!r}, phase={phase!r}）",
    )


def derive_missing_slot_keys(
    manifest: Mapping[str, Any], task_ids: Iterable[str]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """affected task ごとの missing slot key（`(keys, issues, notes)`）。

    `derive_task_truth_slots` の issue と slot 重複は導出不能（issues）、claim 分類の issue
    （暫定 slot で集合が確定する）は notes。
    """
    keys: list[dict[str, Any]] = []
    issues: list[str] = []
    notes: list[str] = []
    current, registry_issues = expectation.registry_current_entries_and_issues(manifest)
    issues.extend(f"registry chain 不正: {issue}" for issue in registry_issues)
    for task_id in utf8_sorted(task_ids):
        _truth_slots, truth_issues = expectation.derive_task_truth_slots(manifest, task_id)
        if truth_issues:
            issues.extend(f"task:{task_id}: {issue}" for issue in truth_issues)
            continue
        slots, slot_issues = expectation.derive_expected_slots(manifest, task_id)
        for issue in slot_issues:
            if issue.startswith("slot が重複した"):
                issues.append(f"task:{task_id}: {issue}")
            else:
                notes.append(f"task:{task_id}: {issue}")
        for slot in slots:
            if slot.slot in current:
                continue
            keys.append(
                {
                    "task_id": task_id,
                    "subject_id": slot.subject_id,
                    "evidence_type": slot.evidence_type,
                    "phase_or_context_id": slot.phase_or_context_id,
                }
            )
    return sort_slot_keys(keys), issues, notes


def derive_recovery_sets(
    manifest: Mapping[str, Any],
    *,
    events: Mapping[str, Mapping[str, Any]],
    invalid_evidence_ids: Sequence[str],
    invalid_batch_ids: Sequence[str],
) -> RecoveryDerivation:
    """affected task／claim・missing slot key を manifest と event 集合から再導出する。"""
    issues: list[str] = []
    seeds: set[str] = set()
    claim_subjects: set[str] = set()
    current_events = unsuperseded_events(events)
    for batch_id in invalid_batch_ids:
        event = events.get(batch_id)
        if event is None:
            issues.append(f"invalid_batch_ids: {batch_id} の closeout event が存在しません")
            continue
        if batch_id not in current_events:
            issues.append(
                f"invalid_batch_ids: {batch_id} は既に訂正 event で置換済みです"
                "（unsuperseded 限定）"
            )
        task_id = event.get("task_id")
        if not isinstance(task_id, str) or not tce.TASK_ID_RE.match(task_id):
            issues.append(f"invalid_batch_ids: {batch_id} の task_id を読めません")
            continue
        seeds.add(task_id)
    for evidence_id in invalid_evidence_ids:
        owner, issue = owner_task_of_registry_entry(manifest, evidence_id)
        if issue is not None:
            issues.append(f"invalid_evidence_ids: {issue}")
            continue
        assert owner is not None
        seeds.add(owner)
        entry = _registry_entries(manifest).get(evidence_id, {})
        subject = entry.get("subject_id")
        if isinstance(subject, str) and subject.startswith("claim:"):
            claim_subjects.add(subject.partition(":")[2])
    if not seeds and not issues:
        issues.append(
            "affected の seed task が 0 件です（invalid batch／evidence から帰属できない）"
        )

    published = published_task_ids(manifest, events)
    for task_id in utf8_sorted(seeds):
        if task_id not in published:
            issues.append(
                f"task:{task_id} は公開済み（unsuperseded event の root 全件が registry 登録済み）"
                "ではないため repair-hold の対象にできません（公開前訂正は pre-publication 経路）"
            )
    canonical = _canonical_tasks(manifest)
    for task_id in utf8_sorted(seeds):
        if task_id not in canonical:
            issues.append(
                f"task:{task_id} が manifest task_catalog の canonical task ではありません"
            )

    affected = set(seeds) | (downstream_tasks(manifest, seeds) & published)
    affected_sorted = utf8_sorted(affected)
    keys, key_issues, notes = derive_missing_slot_keys(manifest, affected_sorted)
    issues.extend(key_issues)
    claims: set[str] = set(claim_subjects)
    for task_id in affected_sorted:
        claims.update(expectation.derive_owner_open_claims(manifest, task_id))
    return RecoveryDerivation(
        seed_task_ids=tuple(utf8_sorted(seeds)),
        affected_task_ids=tuple(affected_sorted),
        affected_claim_ids=tuple(utf8_sorted(claims)),
        missing_slot_keys=tuple(keys),
        published_task_ids=tuple(utf8_sorted(published)),
        issues=tuple(issues),
        notes=tuple(notes),
    )


def repair_target_task_id(
    manifest: Mapping[str, Any],
    *,
    affected_task_ids: Iterable[str],
    recovered_task_ids: Iterable[str] = (),
) -> str | None:
    """未回復の affected task のうち canonical order（最上流）先頭。全件回復済みなら None。"""
    unrecovered = set(affected_task_ids) - set(recovered_task_ids)
    return canonical_first_task(manifest, unrecovered)


# ---------------------------------------------------------------------------
# blocked_by／correction_targets／repo 側検査
# ---------------------------------------------------------------------------


def discovery_artifact_sha256(
    obj: Mapping[str, Any], manifest: Mapping[str, Any] | None = None
) -> str | None:
    """discovery artifact の raw SHA-256（`discovery_evidence` 優先・無ければ registry entry）。"""
    discovery = obj.get("discovery_evidence")
    if isinstance(discovery, Mapping):
        raw = discovery.get("artifact_raw_sha256")
        if isinstance(raw, str) and tce.HEX64_RE.match(raw):
            return raw
    discovery_id = obj.get("discovery_evidence_id")
    if manifest is not None and isinstance(discovery_id, str):
        entry = _registry_entries(manifest).get(discovery_id)
        raw = entry.get("raw_sha256") if entry is not None else None
        if isinstance(raw, str) and tce.HEX64_RE.match(raw):
            return raw
    return None


def expected_blocked_by(
    obj: Mapping[str, Any], manifest: Mapping[str, Any] | None = None
) -> list[str] | None:
    """repair-hold 中の active block が持つべき `blocked_by`（導出不能なら None）。"""
    raw = discovery_artifact_sha256(obj, manifest)
    if raw is None:
        return None
    return [f"{REPAIR_BLOCKED_BY_PREFIX}{raw[:REPAIR_BLOCKED_BY_HEX_LEN]}"]


def correction_targets(obj: Mapping[str, Any]) -> list[str]:
    """`closeout_action.correction_targets`＝`invalid_evidence_ids ∪ invalid_batch_ids`（昇順）。"""
    return utf8_sorted(
        [
            *_string_list(obj.get("invalid_evidence_ids")),
            *_string_list(obj.get("invalid_batch_ids")),
        ]
    )


def check_discovery_artifact(obj: Mapping[str, Any], *, repo_root: Path) -> list[str]:
    """`discovery_evidence.artifact_path` が repo 内に存在し raw SHA-256 が一致するか。"""
    discovery = obj.get("discovery_evidence")
    if not isinstance(discovery, Mapping):
        return []
    path = discovery.get("artifact_path")
    raw = discovery.get("artifact_raw_sha256")
    if not isinstance(path, str) or not isinstance(raw, str):
        return []
    if not tce.TRACKED_PATH_RE.match(path) or ".." in path.split("/"):
        return []  # 構造検査が既に拒否している
    target = (repo_root / path).resolve()
    if not target.is_relative_to(repo_root.resolve()):
        return [f"discovery_evidence.artifact_path が repo 外へ解決されます: {path}"]
    if not target.is_file():
        return [f"discovery_evidence.artifact_path が存在しません: {path}"]
    try:
        actual = tce.sha256_bytes(target.read_bytes())
    except OSError as exc:
        return [f"discovery_evidence.artifact_path を読めません: {path}: {exc}"]
    if actual != raw:
        return [
            f"discovery_evidence.artifact_raw_sha256 が実測と一致しません"
            f"（実測 {actual}・宣言 {raw}）"
        ]
    return []


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def check_discovered_at_head(obj: Mapping[str, Any], *, repo_root: Path) -> list[str]:
    """`discovered_at_head` がリポジトリに実在する commit か。"""
    head = obj.get("discovered_at_head")
    if not isinstance(head, str) or not tce.HEX40_RE.match(head):
        return []
    result = _run_git(["cat-file", "-e", f"{head}^{{commit}}"], cwd=repo_root)
    if result.returncode != 0:
        return [f"discovered_at_head: commit {head} がリポジトリに実在しません"]
    return []


def check_held_active_against_plan(obj: Mapping[str, Any], *, repo_root: Path) -> list[str]:
    """`held_active` が `source_plan_revision` 時点の plan active block と exact 一致するか。

    state!=terminal は当該 revision の `docs/plan.md` の active block（task_id／substep／state）
    と一致、state=terminal は当該 revision の plan が active block 0 件であることを要求する。
    revision が null（terminal のみ許容）なら plan 照合は行わない。
    """
    held = obj.get("held_active")
    if not isinstance(held, Mapping):
        return []
    revision = held.get("source_plan_revision")
    if not isinstance(revision, str) or not tce.HEX40_RE.match(revision):
        return []
    result = _run_git(["show", f"{revision}:{current_state.PLAN_REL}"], cwd=repo_root)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        return [
            f"held_active.source_plan_revision: {revision}:{current_state.PLAN_REL} を"
            f"読めません: {detail}"
        ]
    plan_text = result.stdout
    open_c, close_c, pair_c = current_state.count_marker_tags(plan_text, "active-queue:v1")
    if held.get("state") == "terminal":
        if not (open_c == 0 and close_c == 0):
            return [
                "held_active.state=terminal ですが source_plan_revision の plan に active block が"
                "あります（terminal 保持と不一致）"
            ]
        return []
    if not (open_c == 1 and close_c == 1 and pair_c == 1):
        return [
            f"held_active.source_plan_revision {revision} の plan から active block を一意に"
            "抽出できません"
        ]
    block = current_state.extract_active_queue_block(plan_text)
    if block is None:
        return [f"held_active.source_plan_revision {revision} の active block を解析できません"]
    issues: list[str] = []
    for name in ("task_id", "substep", "state"):
        if block.get(name) != held.get(name):
            issues.append(
                f"held_active.{name}={held.get(name)!r} が source_plan_revision の active block"
                f"（{block.get(name)!r}）と一致しません（保持した状態を silent overwrite しない）"
            )
    return issues
