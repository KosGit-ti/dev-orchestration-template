#!/usr/bin/env python3
"""S1 の required_evidence_roots と closure claim 集合を materialization manifest から導出する。

HRAI-REAUDIT-20260815 P0-01／P2-02／P1-01（PR-E2）の中核であり、「期待集合を外部正本
（materialization manifest）から導出する唯一の関数群」である。writer
（`scripts/ai/write_task_closeout_event.py`）は本 module の導出集合と `--roots` の
slot 集合を exact 照合し、R validator（`scripts/ai/validate_task_closeout_events.py`）は
base manifest から同じ集合を再導出して event root と登録 entry を照合し、resolver
（`scripts/ai/resolve_current_state.py`）は task／gate／AC token truth を registry の
current entry から判定する。呼出側は root の artifact path／raw SHA の割当だけを担い、
slot 集合や closure claim 集合を手書きしない。

本 module は純粋関数だけで構成し、git・GitHub・working tree・ファイル読込に依存しない
（manifest は読込済み dict を受け取る）。正本は
`docs/audits/audit-materialization-manifest-2026-08-12.yml` の
`evidence_registry.registration_update_contract.batch_source`／`claim_owner_partition`、
`task_closeout_state_machine.S1_owner_claims`、`semantics.claim_owner_contract.finalizer_rule`、
`task_truth_contract`、`claim_truth`、`evidence_registry.truth_entry_selection` と、
`docs/specs/N-598C-exact-current-state-resolver.md`「Plan Active Block Schema」
（required_evidence_roots の tuple 規則・required_closure_claim_ids）、
`docs/specs/N-602-evidence-truthfulness-generation.md`（owner claim・finalizer）である。

導出規則（`derive_expected_slots`）:

1. `task_catalog.task:<id>` の canonical entry と非空の `required_terminal_evidence_types`
   を要求する。task alias、aggregate（umbrella／control program／program derived）は issue
   （fail-close）。
2. task terminal: 各 required type について `(task:<id>, <type>, null)`。
3. scope AC leaf: `scope_ac_refs ∪ expand(completion_gate)`。展開は release／task alias／
   milestone だけを再帰し、`claim:`／canonical `task:` は stop する。到達した `ac:` は
   `ac_leaf_registry[ac].phase_tasks ∋ task:<id>` を要求し `(ac, <required_evidence_type>,
   task:<id>)`。到達した `gate:` は phase-scoped なら `(gate, <type>, task:<id>)`、scalar なら
   `(gate, <type>, null)`。ただし control unit が `produced_tokens` として生成する gate
   （R-025 の freeze／reservation／single-look 系）は当該 control unit の UR が登録主体で
   あり task の T が生成できないため、期待 slot から黙って除外する。`issues` は「集合を
   確定できない」を表す fail-close 信号であり、意図した除外を理由として積むと全 task の
   導出が判定不能になるため、この除外は issues へ出さない（PR #1387 Round 3 suppressed
   指摘: docstring が「理由を返す」と書いて実装と食い違っていた）。
4. global leaf: `task_truth_contract.normal_task_global_prerequisites` を展開し、到達 gate を
   `(gate, <type>, task:<id>)` として重複除去する（phase-scoped global leaf は呼出 task phase
   に束縛する。`task_truth_contract.global_leaf_phase_binding`）。
5. owner claim: `closure_owner_task == task:<id>` ∧ adoption ∈ `claim_truth.actionable_adoptions`
   ∧ `closure.state == open` の claim を UTF-8 byte 昇順に並べ、`classify_claim_closure` の
   結果で `(claim:<id>, evidence-type:claim_closure_result | evidence-type:gated_residual_result,
   null)` を作る。分類不能（self-deadlock で finalizer 未宣言）は暫定 slot
   （gated_residual_result）を返しつつ issue `deadlock_without_finalizer` を積む（fail-close）。

self-deadlock（`classify_claim_closure`）: claim の `dependencies ∪ ac_refs ∪ {release_gate}` を
展開し、到達 `gate:` の producer control unit（`control_unit_catalog[*].produced_tokens`）の
preconditions が `task:<owner>` を（再帰的に）要求する、または到達 task／claim owner が
dependency DAG 上で owner より後（owner に依存する）なら、owner task の T では
implemented_verified にできない。`closure_finalizer_context` が当該 producer の context に
一致して宣言済みなら `valid_gated_residual`、未宣言なら None（issue）。adoption=gated は
常に `valid_gated_residual`。

「canonical order で owner より後」は本 module では manifest の `task_catalog.dependencies` の
推移閉包で定義する（到達 task が owner に推移的に依存すれば後）。固定順序表は manifest に
無く、`task_truth_contract.applies_to` の並びは実行順ではない（N-602A→N-598C→N-594B の
既 merge 順と一致しない）ため使わない。

registry current entry（`registry_current_entries`）: `evidence_registry.entries` を slot
`(subject_id, evidence_type, phase_or_context_id)` ごとに `correction_of` chain へ組み、
original exact 1 件から child を辿った末端（unsuperseded）が `status=pass` かつ
`disposition != invalidates` の場合だけ current とする（`truth_entry_selection` 相当）。
複数 original、branch、cycle、別 slot への edge、dangling `correction_of` は issue とし
当該 slot を current なしとする。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    "AC_LEAF_EVIDENCE_TYPE",
    "CLAIM_CLOSURE_EVIDENCE_TYPE",
    "CONTROL_LEAF_EVIDENCE_TYPE",
    "GATED_RESIDUAL_EVIDENCE_TYPE",
    "DEFAULT_ACTIONABLE_ADOPTIONS",
    "NON_TASK_KINDS",
    "SLOT_KINDS",
    "ExpectedSlot",
    "SlotKey",
    "classify_claim_closure",
    "compare_slot_sets",
    "derive_expected_roots",
    "derive_expected_slots",
    "derive_finalizer_claims",
    "derive_owner_open_claims",
    "derive_task_truth_slots",
    "entry_merged_pr",
    "expand_tokens",
    "gate_is_phase_scoped",
    "registry_current_entries",
    "registry_current_entries_and_issues",
    "slot_key_of",
]

SlotKey = tuple[str, str, str | None]

CLAIM_CLOSURE_EVIDENCE_TYPE: Final = "evidence-type:claim_closure_result"
GATED_RESIDUAL_EVIDENCE_TYPE: Final = "evidence-type:gated_residual_result"
AC_LEAF_EVIDENCE_TYPE: Final = "evidence-type:ac_leaf_result"
CONTROL_LEAF_EVIDENCE_TYPE: Final = "evidence-type:control_leaf_result"

SLOT_KINDS: Final = (
    "task_terminal",
    "scope_ac_leaf",
    "global_leaf",
    "claim_closure",
    "gated_residual",
)
CLOSURE_KIND_BY_STATE: Final = {
    "implemented_verified": ("claim_closure", CLAIM_CLOSURE_EVIDENCE_TYPE),
    "valid_gated_residual": ("gated_residual", GATED_RESIDUAL_EVIDENCE_TYPE),
}

DEFAULT_ACTIONABLE_ADOPTIONS: Final = ("adopt", "adopt_with_refinement", "gated")

# task_truth_contract.aggregate_kinds（通常 task ではなく S1／R／S2 を持たない kind）。
NON_TASK_KINDS: Final = frozenset(
    {"umbrella_aggregate", "control_program_aggregate", "program_derived_aggregate"}
)

# acceptance_gates の kind のうち、呼出 task phase／control context ごとに別 slot を持つもの
# （`leaf_evaluator_contract.invocation_binding`）。
PHASE_SCOPED_GATE_KINDS: Final = frozenset({"phase_scoped_global_acceptance_leaf"})

# 展開対象の namespace（`registration_update_contract.owner_self_dependency_projection.
# recursive_namespaces` から claim／derived_ac を除いたもの。claim は同 owner DAG／cross-owner
# 契約で別評価し、canonical task は公開 S2 truth で別評価するため stop する）。
_EXPAND_NAMESPACES: Final = frozenset({"release", "milestone"})
_LEAF_NAMESPACES: Final = frozenset({"gate", "ac", "external", "claim", "task", "control-context"})


@dataclass(frozen=True)
class ExpectedSlot:
    """S1 が要求する root 1 件の slot（artifact path／raw SHA を持たない）。"""

    subject_id: str
    evidence_type: str
    phase_or_context_id: str | None
    kind: str
    claim_id: str | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def slot(self) -> SlotKey:
        return (self.subject_id, self.evidence_type, self.phase_or_context_id)

    def to_descriptor(self) -> dict[str, Any]:
        """人間可読／JSON 出力用の closed projection。"""
        return {
            "subject_id": self.subject_id,
            "evidence_type": self.evidence_type,
            "phase_or_context_id": self.phase_or_context_id,
            "kind": self.kind,
            "claim_id": self.claim_id,
            "reasons": list(self.reasons),
        }


def slot_key_of(obj: Mapping[str, Any]) -> SlotKey:
    """root tuple／registry entry の slot key を返す（型は呼出側で検査済みとする）。"""
    phase = obj.get("phase_or_context_id")
    return (
        str(obj.get("subject_id")),
        str(obj.get("evidence_type")),
        None if phase is None else str(phase),
    )


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _utf8_sorted(items: Iterable[str]) -> list[str]:
    return sorted(set(items), key=lambda item: item.encode("utf-8"))


# ---------------------------------------------------------------------------
# token 展開（release／task alias／milestone）
# ---------------------------------------------------------------------------


def expand_tokens(
    manifest: Mapping[str, Any], tokens: Sequence[str], *, label: str
) -> tuple[list[str], list[str]]:
    """release／task alias／milestone を再帰展開し、到達 leaf token を順序保持・重複除去で返す。

    leaf は `gate:`／`ac:`／`external:`／`claim:`／canonical `task:`／`control-context:` で
    ある（`claim:` と canonical `task:` は stop）。未定義 release／milestone、未知 namespace、
    prefix なし token、循環は issue にする（fail-close）。
    """
    release_gates = _mapping(manifest.get("release_gates"))
    task_aliases = _mapping(manifest.get("task_aliases"))
    task_catalog = _mapping(manifest.get("task_catalog"))
    terminal_conditions = _mapping(manifest.get("terminal_conditions"))
    leaves: list[str] = []
    seen_leaves: set[str] = set()
    issues: list[str] = []

    def visit(token: object, path: tuple[str, ...]) -> None:
        if not isinstance(token, str) or ":" not in token:
            issues.append(f"{label}: prefix 付き token ではない: {token!r}")
            return
        if token in path:
            issues.append(f"{label}: 展開に循環がある: {' -> '.join((*path, token))}")
            return
        prefix, _, ident = token.partition(":")
        if prefix == "release":
            members = release_gates.get(token)
            if not isinstance(members, list) or not members:
                issues.append(f"{label}: {token} が release_gates に見つからない（または空）")
                return
            for member in members:
                visit(member, (*path, token))
            return
        if prefix == "milestone":
            entry = terminal_conditions.get(token)
            if not isinstance(entry, Mapping):
                issues.append(f"{label}: {token} が terminal_conditions に見つからない")
                return
            milestone_members: list[Any] = []
            if entry.get("operator") == "any":
                for alternative in entry.get("alternatives") or []:
                    alt_members = (
                        alternative.get("all") if isinstance(alternative, Mapping) else None
                    )
                    if isinstance(alt_members, list):
                        milestone_members.extend(alt_members)
            else:
                for key in ("evidence", "owner_tasks"):
                    values = entry.get(key)
                    if isinstance(values, list):
                        milestone_members.extend(values)
            if not milestone_members:
                issues.append(f"{label}: {token} に展開対象 member がない")
                return
            for member in milestone_members:
                visit(member, (*path, token))
            return
        if prefix == "task":
            alias = task_aliases.get(token)
            if isinstance(alias, Mapping) and token not in task_catalog:
                alias_ids = alias.get("task_ids")
                if not isinstance(alias_ids, list) or not alias_ids:
                    issues.append(f"{label}: task alias {token} に task_ids がない")
                    return
                for member in alias_ids:
                    visit(member, (*path, token))
                return
            # canonical task（または未登録 task）は stop leaf。
        elif prefix not in _LEAF_NAMESPACES:
            issues.append(f"{label}: 未知の namespace prefix: {token}")
            return
        if token not in seen_leaves:
            seen_leaves.add(token)
            leaves.append(token)

    for token in tokens:
        visit(token, ())
    return leaves, issues


# ---------------------------------------------------------------------------
# task catalog／leaf slot
# ---------------------------------------------------------------------------


def _canonical_task_entry(
    manifest: Mapping[str, Any], task_id: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """`task_catalog.task:<id>` の canonical 通常 task entry を返す。

    alias／aggregate／required_terminal_evidence_types 欠落は issue（導出不能）。
    """
    token = f"task:{task_id}"
    task_catalog = _mapping(manifest.get("task_catalog"))
    entry = task_catalog.get(token)
    if not isinstance(entry, Mapping):
        aliases = _mapping(manifest.get("task_aliases"))
        if token in aliases:
            return None, [
                f"{token} は task alias であり canonical task entry を持たない（導出不能）"
            ]
        return None, [f"{token} が manifest task_catalog に見つからない（導出不能）"]
    kind = entry.get("kind")
    if isinstance(kind, str) and kind in NON_TASK_KINDS:
        return None, [f"{token} は kind={kind} の aggregate であり S1 root を持たない（導出不能）"]
    required_types = entry.get("required_terminal_evidence_types")
    if not isinstance(required_types, list) or not required_types:
        return None, [f"{token} は required_terminal_evidence_types を持たない（導出不能）"]
    if any(not isinstance(item, str) or not item for item in required_types):
        return None, [f"{token}.required_terminal_evidence_types に不正な値がある"]
    return dict(entry), []


def _control_produced_gates(manifest: Mapping[str, Any]) -> dict[str, list[str]]:
    """`gate:` token → それを produced_tokens に持つ control unit ID の一覧。"""
    produced: dict[str, list[str]] = {}
    for control_id, entry in _mapping(manifest.get("control_unit_catalog")).items():
        if not isinstance(entry, Mapping):
            continue
        for token in _string_list(entry.get("produced_tokens")):
            produced.setdefault(token, []).append(str(control_id))
    return produced


def gate_is_phase_scoped(gate_entry: Mapping[str, Any]) -> bool:
    """acceptance gate が phase／context ごとに別 slot を持つ（phase-scoped）か。"""
    kind = gate_entry.get("kind")
    if isinstance(kind, str) and kind in PHASE_SCOPED_GATE_KINDS:
        return True
    return isinstance(gate_entry.get("verified_evidence_by_phase"), Mapping)


def _gate_slot(
    manifest: Mapping[str, Any],
    gate_token: str,
    *,
    task_id: str,
    kind: str,
    reasons: Sequence[str],
) -> tuple[ExpectedSlot | None, list[str]]:
    gates = _mapping(manifest.get("acceptance_gates"))
    entry = gates.get(gate_token)
    if not isinstance(entry, Mapping):
        return None, [f"{gate_token} が manifest acceptance_gates に見つからない"]
    evidence_type = entry.get("required_evidence_type")
    if not isinstance(evidence_type, str) or not evidence_type:
        return None, [f"{gate_token} に required_evidence_type がない"]
    phase: str | None = None
    if gate_is_phase_scoped(entry):
        phase = f"task:{task_id}"
        by_phase = entry.get("verified_evidence_by_phase")
        if isinstance(by_phase, Mapping) and phase not in by_phase:
            return None, [
                f"{gate_token} の verified_evidence_by_phase に呼出 phase {phase} が"
                "登録されていない"
            ]
    return (
        ExpectedSlot(
            subject_id=gate_token,
            evidence_type=evidence_type,
            phase_or_context_id=phase,
            kind=kind,
            reasons=tuple(reasons),
        ),
        [],
    )


def _ac_slot(
    manifest: Mapping[str, Any], ac_token: str, *, task_id: str, reasons: Sequence[str]
) -> tuple[ExpectedSlot | None, list[str]]:
    registry = _mapping(manifest.get("ac_leaf_registry"))
    entry = registry.get(ac_token)
    if not isinstance(entry, Mapping):
        return None, [f"{ac_token} が manifest ac_leaf_registry に見つからない"]
    phase = f"task:{task_id}"
    phase_tasks = _string_list(entry.get("phase_tasks"))
    if phase not in phase_tasks:
        return None, [
            f"{ac_token} の phase_tasks に {phase} が含まれない"
            "（別 phase の AC を task slot にしない）"
        ]
    evidence_type = entry.get("required_evidence_type")
    if not isinstance(evidence_type, str) or not evidence_type:
        return None, [f"{ac_token} に required_evidence_type がない"]
    return (
        ExpectedSlot(
            subject_id=ac_token,
            evidence_type=evidence_type,
            phase_or_context_id=phase,
            kind="scope_ac_leaf",
            reasons=tuple(reasons),
        ),
        [],
    )


def _append_unique(
    slots: list[ExpectedSlot], index: dict[SlotKey, ExpectedSlot], slot: ExpectedSlot
) -> None:
    if slot.slot in index:
        return
    index[slot.slot] = slot
    slots.append(slot)


def derive_task_truth_slots(
    manifest: Mapping[str, Any], task_id: str
) -> tuple[list[ExpectedSlot], list[str]]:
    """task terminal・scope AC leaf・global leaf の slot（claim を含まない）を導出する。

    `resolve_current_state.resolve_task_token_truth` の判定対象と、`derive_expected_slots`
    の非 claim 部分の共通実装である。
    """
    entry, issues = _canonical_task_entry(manifest, task_id)
    if entry is None:
        return [], issues
    slots: list[ExpectedSlot] = []
    index: dict[SlotKey, ExpectedSlot] = {}
    task_token = f"task:{task_id}"

    for evidence_type in _string_list(entry.get("required_terminal_evidence_types")):
        _append_unique(
            slots,
            index,
            ExpectedSlot(
                subject_id=task_token,
                evidence_type=evidence_type,
                phase_or_context_id=None,
                kind="task_terminal",
                reasons=(f"{task_token}.required_terminal_evidence_types",),
            ),
        )

    contract = _mapping(manifest.get("task_truth_contract"))
    global_tokens = _string_list(contract.get("normal_task_global_prerequisites"))
    if global_tokens:
        global_leaves, global_issues = expand_tokens(
            manifest, global_tokens, label=f"{task_token} global prerequisites"
        )
        issues.extend(global_issues)
        for leaf in global_leaves:
            if leaf.partition(":")[0] != "gate":
                issues.append(
                    f"{task_token}: normal_task_global_prerequisites の展開先に"
                    f" gate 以外の leaf がある: {leaf}"
                )
                continue
            slot, slot_issues = _gate_slot(
                manifest,
                leaf,
                task_id=task_id,
                kind="global_leaf",
                reasons=("task_truth_contract.normal_task_global_prerequisites",),
            )
            issues.extend(slot_issues)
            if slot is not None:
                _append_unique(slots, index, slot)

    produced = _control_produced_gates(manifest)
    scope_tokens: list[str] = []
    scope_refs = entry.get("scope_ac_refs")
    if scope_refs is not None:
        if not isinstance(scope_refs, list):
            issues.append(f"{task_token}.scope_ac_refs が配列ではない")
        else:
            scope_tokens.extend(str(item) for item in scope_refs)
    completion_gate = entry.get("completion_gate")
    if completion_gate is not None:
        if not isinstance(completion_gate, str):
            issues.append(f"{task_token}.completion_gate が文字列ではない")
        else:
            scope_tokens.append(completion_gate)
    leaves, expand_issues = expand_tokens(manifest, scope_tokens, label=f"{task_token} scope")
    issues.extend(expand_issues)
    for leaf in leaves:
        prefix = leaf.partition(":")[0]
        if prefix == "ac":
            slot, slot_issues = _ac_slot(
                manifest,
                leaf,
                task_id=task_id,
                reasons=(f"{task_token} scope_ac_refs/completion_gate",),
            )
        elif prefix == "gate":
            producers = produced.get(leaf)
            if producers:
                # control unit の UR が登録主体。task T は生成できないため task slot にしない。
                continue
            slot, slot_issues = _gate_slot(
                manifest,
                leaf,
                task_id=task_id,
                kind="scope_ac_leaf",
                reasons=(f"{task_token} scope_ac_refs/completion_gate",),
            )
        else:
            # claim／task／external／control-context は scope leaf ではない（stop）。
            continue
        issues.extend(slot_issues)
        if slot is not None:
            _append_unique(slots, index, slot)

    return slots, issues


# ---------------------------------------------------------------------------
# owner claim と closure 分類
# ---------------------------------------------------------------------------


def _actionable_adoptions(manifest: Mapping[str, Any]) -> frozenset[str]:
    truth = _mapping(manifest.get("claim_truth"))
    values = _string_list(truth.get("actionable_adoptions"))
    return frozenset(values) if values else frozenset(DEFAULT_ACTIONABLE_ADOPTIONS)


def _claims_by_id(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    claims = manifest.get("claims")
    result: dict[str, dict[str, Any]] = {}
    if isinstance(claims, list):
        for claim in claims:
            if isinstance(claim, Mapping) and isinstance(claim.get("claim_id"), str):
                result[str(claim["claim_id"])] = dict(claim)
    return result


def _closure_state(claim: Mapping[str, Any], manifest: Mapping[str, Any]) -> str:
    closure = claim.get("closure")
    default_state = _mapping(manifest.get("claim_truth")).get("default_state")
    default = default_state if isinstance(default_state, str) else "open"
    if not isinstance(closure, Mapping):
        return default
    state = closure.get("state")
    return state if isinstance(state, str) else default


def derive_owner_open_claims(manifest: Mapping[str, Any], task_id: str) -> list[str]:
    """owner 一致 ∧ actionable ∧ closure open の claim ID（UTF-8 byte 昇順）。

    `closure_owner_task == task:<id>`、adoption ∈ `claim_truth.actionable_adoptions`、
    `closure.state == open`（closure 欠落は `claim_truth.default_state`）を全て満たす claim。
    """
    owner = f"task:{task_id}"
    actionable = _actionable_adoptions(manifest)
    ids = [
        claim_id
        for claim_id, claim in _claims_by_id(manifest).items()
        if claim.get("closure_owner_task") == owner
        and claim.get("adoption") in actionable
        and _closure_state(claim, manifest) == "open"
    ]
    return _utf8_sorted(ids)


def derive_finalizer_claims(manifest: Mapping[str, Any], context_id: str) -> list[str]:
    """`closure_finalizer_context == <context_id>` の actionable claim ID（UTF-8 byte 昇順）。

    CUE（control unit event）が「宣言 context の UR だけが owner 不変の新 closure history で
    implemented_verified へ進める」claim 集合を導出するための関数（本 PR では提供のみ）。
    """
    actionable = _actionable_adoptions(manifest)
    ids = [
        claim_id
        for claim_id, claim in _claims_by_id(manifest).items()
        if claim.get("closure_finalizer_context") == context_id
        and claim.get("adoption") in actionable
    ]
    return _utf8_sorted(ids)


class _DeadlockProbe:
    """token が owner task の truth を（推移的に）要求するかを判定する（memo 付き）。"""

    def __init__(self, manifest: Mapping[str, Any], owner_task: str) -> None:
        self.manifest = manifest
        self.owner_task = owner_task
        self.task_catalog = _mapping(manifest.get("task_catalog"))
        self.task_aliases = _mapping(manifest.get("task_aliases"))
        self.claims = _claims_by_id(manifest)
        self.control_units = {
            str(control_id): dict(entry)
            for control_id, entry in _mapping(manifest.get("control_unit_catalog")).items()
            if isinstance(entry, Mapping)
        }
        self.produced = _control_produced_gates(manifest)
        self.contexts: set[str] = set()
        self.reasons: list[str] = []
        self._memo: dict[str, bool] = {}
        self._active: set[str] = set()

    def requires_owner(self, token: object) -> bool:
        if not isinstance(token, str) or ":" not in token:
            return False
        if token in self._memo:
            return self._memo[token]
        if token in self._active:
            return False  # 循環は別 validator が拒否する。ここでは deadlock と数えない。
        self._active.add(token)
        try:
            result = self._evaluate(token)
        finally:
            self._active.discard(token)
        self._memo[token] = result
        return result

    def _any(self, tokens: Iterable[object]) -> bool:
        found = False
        for token in tokens:
            if self.requires_owner(token):
                found = True
        return found

    def _evaluate(self, token: str) -> bool:
        prefix, _, ident = token.partition(":")
        if prefix == "task":
            if token == self.owner_task:
                return True
            alias = self.task_aliases.get(token)
            if isinstance(alias, Mapping) and token not in self.task_catalog:
                return self._any(_string_list(alias.get("task_ids")))
            entry = self.task_catalog.get(token)
            if not isinstance(entry, Mapping):
                return False
            if self._any(_string_list(entry.get("dependencies"))):
                self.reasons.append(f"{token} は dependency DAG 上で {self.owner_task} より後")
                return True
            return False
        if prefix == "claim":
            claim = self.claims.get(ident)
            if claim is None:
                return False
            claim_owner = claim.get("closure_owner_task")
            if not isinstance(claim_owner, str) or claim_owner == self.owner_task:
                return False
            if self.requires_owner(claim_owner):
                self.reasons.append(
                    f"{token} の owner {claim_owner} は dependency DAG 上で"
                    f" {self.owner_task} より後"
                )
                return True
            return False
        if prefix == "gate":
            found = False
            for control_id in self.produced.get(token, []):
                unit = self.control_units.get(control_id, {})
                if self._any(_string_list(unit.get("preconditions"))):
                    context = unit.get("context_id")
                    if isinstance(context, str):
                        self.contexts.add(context)
                    self.reasons.append(
                        f"{token} の producer {control_id} の preconditions が"
                        f" {self.owner_task} を要求"
                    )
                    found = True
            return found
        if prefix in _EXPAND_NAMESPACES:
            leaves, issues = expand_tokens(self.manifest, [token], label=token)
            if issues:
                # PR #1391 Round 1 指摘: 展開不能（未定義 release／milestone・未知 token・
                # 循環）を「deadlock なし」に丸めると、期待集合の唯一導出を fail-close へ
                # 寄せる目的に反する。理由を積んだうえで deadlock 側（True）へ倒す。
                self.reasons.extend(
                    f"{token} の展開に失敗（判定不能）: {issue}" for issue in issues
                )
                return True
            return self._any(leaves)
        return False


def classify_claim_closure(
    manifest: Mapping[str, Any], claim: Mapping[str, Any]
) -> tuple[str | None, list[str]]:
    """owner task の T で claim を閉じる形（`implemented_verified`／`valid_gated_residual`／None）。

    None は「owner T では閉じられず finalizer も未宣言」（fail-close）を表し、reasons に
    `deadlock_without_finalizer` を含む。
    """
    reasons: list[str] = []
    owner = claim.get("closure_owner_task")
    if not isinstance(owner, str) or not owner.startswith("task:"):
        return None, ["closure_owner_task が canonical task ではない"]
    if claim.get("adoption") == "gated":
        return "valid_gated_residual", ["adoption=gated"]

    tokens: list[str] = []
    tokens.extend(_string_list(claim.get("dependencies")))
    tokens.extend(_string_list(claim.get("ac_refs")))
    release_gate = claim.get("release_gate")
    if isinstance(release_gate, str):
        tokens.append(release_gate)
    probe = _DeadlockProbe(manifest, owner)
    deadlocked = probe._any(tokens)
    if not deadlocked:
        return "implemented_verified", reasons
    reasons.extend(probe.reasons)
    finalizer = claim.get("closure_finalizer_context")
    if finalizer is None:
        return None, ["deadlock_without_finalizer", *reasons]
    if not isinstance(finalizer, str):
        return None, ["closure_finalizer_context が文字列ではない", *reasons]
    if finalizer in probe.contexts:
        notes = [f"deadlock_with_declared_finalizer:{finalizer}"]
        if claim.get("adoption") != "gated":
            notes.append(
                "adoption!=gated（DEC-20260815-003 決定 5 で exact allowlist 4 claim へ"
                "限定解禁済み。allowlist 判定は scripts/ai/claim_closure.py が正本）"
            )
        return "valid_gated_residual", [*notes, *reasons]
    return None, [f"finalizer_context_mismatch:{finalizer}", *reasons]


def _derive_claim_slots(
    manifest: Mapping[str, Any], task_id: str
) -> tuple[list[ExpectedSlot], list[str]]:
    claims = _claims_by_id(manifest)
    slots: list[ExpectedSlot] = []
    issues: list[str] = []
    for claim_id in derive_owner_open_claims(manifest, task_id):
        claim = claims[claim_id]
        state, reasons = classify_claim_closure(manifest, claim)
        if state is None:
            # 分類不能: 暫定 slot（gated_residual）を返し issue で fail-close する。
            issues.append(
                f"{reasons[0] if reasons else 'unclassifiable'}: claim:{claim_id}"
                + (f"（{'; '.join(reasons[1:])}）" if len(reasons) > 1 else "")
            )
            kind, evidence_type = CLOSURE_KIND_BY_STATE["valid_gated_residual"]
        else:
            kind, evidence_type = CLOSURE_KIND_BY_STATE[state]
        slots.append(
            ExpectedSlot(
                subject_id=f"claim:{claim_id}",
                evidence_type=evidence_type,
                phase_or_context_id=None,
                kind=kind,
                claim_id=claim_id,
                reasons=tuple(reasons),
            )
        )
    return slots, issues


def derive_expected_slots(
    manifest: Mapping[str, Any], task_id: str
) -> tuple[list[ExpectedSlot], list[str]]:
    """task の S1 が要求する slot 全集合（terminal・scope AC・global leaf・owner claim）を導出する。

    issues が非空なら集合は確定できない（呼出側は fail-close する）。順序は
    terminal → global leaf → scope AC → claim（claim は UTF-8 byte 昇順）。global leaf を scope
    より先に置くのは、completion_gate が `release:safety_floor` 等を含む task（R-025）で同じ
    slot に `global_leaf` kind を安定して付けるためである（slot 集合は順序に依存しない）。
    """
    slots, issues = derive_task_truth_slots(manifest, task_id)
    if not slots and issues:
        return [], issues
    claim_slots, claim_issues = _derive_claim_slots(manifest, task_id)
    index = {slot.slot for slot in slots}
    for slot in claim_slots:
        if slot.slot in index:
            issues.append(f"slot が重複した: {slot.slot!r}")
            continue
        index.add(slot.slot)
        slots.append(slot)
    issues.extend(claim_issues)
    return slots, issues


# ---------------------------------------------------------------------------
# registry current entry（truth_entry_selection 相当）
# ---------------------------------------------------------------------------


def registry_current_entries_and_issues(
    manifest: Mapping[str, Any],
) -> tuple[dict[SlotKey, dict[str, Any]], list[str]]:
    """slot ごとの current entry（unsuperseded terminal・status=pass・disposition≠invalidates）。

    subject_id／evidence_type／status を持たない entry は current になり得ないため無視する。
    chain 不正（複数 original、branch、cycle、別 slot への edge、dangling correction_of、
    original に disposition）は issue とし、当該 slot を current なしにする。
    """
    registry = _mapping(manifest.get("evidence_registry"))
    entries_raw = registry.get("entries")
    entries: dict[str, dict[str, Any]] = {}
    if isinstance(entries_raw, Mapping):
        for entry_id, entry in entries_raw.items():
            if not isinstance(entry, Mapping):
                continue
            if not isinstance(entry.get("subject_id"), str) or not isinstance(
                entry.get("evidence_type"), str
            ):
                continue
            entries[str(entry_id)] = dict(entry)

    by_slot: dict[SlotKey, dict[str, dict[str, Any]]] = {}
    for entry_id, entry in entries.items():
        by_slot.setdefault(slot_key_of(entry), {})[entry_id] = entry

    current: dict[SlotKey, dict[str, Any]] = {}
    issues: list[str] = []
    for slot in sorted(by_slot, key=lambda item: repr(item)):
        members = by_slot[slot]
        originals = [
            entry_id for entry_id, entry in members.items() if entry.get("correction_of") is None
        ]
        slot_issues: list[str] = []
        for entry_id in originals:
            if members[entry_id].get("disposition") is not None:
                slot_issues.append(f"{entry_id}: original に disposition がある")
        if len(originals) != 1:
            slot_issues.append(
                f"slot {slot!r}: original entry が {len(originals)} 件（1 件が必要）"
            )
        children: dict[str, list[str]] = {}
        for entry_id, entry in members.items():
            parent = entry.get("correction_of")
            if parent is None:
                continue
            if not isinstance(parent, str) or parent not in entries:
                slot_issues.append(f"{entry_id}: correction_of={parent!r} の entry が存在しない")
                continue
            if parent not in members:
                slot_issues.append(f"{entry_id}: correction_of={parent} が別 slot の entry を指す")
                continue
            children.setdefault(parent, []).append(entry_id)
        for parent, child_ids in children.items():
            if len(child_ids) > 1:
                slot_issues.append(
                    f"{parent}: 訂正 chain が branch している: {', '.join(sorted(child_ids))}"
                )
        if slot_issues:
            issues.extend(slot_issues)
            continue
        node = originals[0]
        seen = {node}
        while children.get(node):
            node = children[node][0]
            if node in seen:
                slot_issues.append(f"slot {slot!r}: 訂正 chain に cycle がある")
                break
            seen.add(node)
        if slot_issues:
            issues.extend(slot_issues)
            continue
        if len(seen) != len(members):
            issues.append(f"slot {slot!r}: original から到達できない entry がある")
            continue
        terminal = members[node]
        if terminal.get("disposition") == "invalidates":
            continue
        if terminal.get("status") != "pass":
            continue
        current[slot] = terminal
    return current, issues


def registry_current_entries(manifest: Mapping[str, Any]) -> dict[SlotKey, dict[str, Any]]:
    """`registry_current_entries_and_issues` の dict 部分だけを返す。"""
    return registry_current_entries_and_issues(manifest)[0]


def entry_merged_pr(entry: Mapping[str, Any]) -> int | None:
    """entry の `postmerge_mapping.merged_pr`（正の整数以外は None）。"""
    mapping = entry.get("postmerge_mapping")
    value = mapping.get("merged_pr") if isinstance(mapping, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def derive_expected_roots(
    manifest: Mapping[str, Any], task_id: str, *, merged_pr: int
) -> tuple[list[ExpectedSlot], list[str]]:
    """S1 の required_evidence_roots が持つべき slot 集合。

    `derive_expected_slots` から、同一 `postmerge_mapping.merged_pr` の current entry が既に
    ある slot だけを除く（同じ T の再 S1／訂正で二重登録しない）。別 T（別 merged_pr）の
    current entry は除外しない（前提 slot の流用を認めない）。
    """
    slots, issues = derive_expected_slots(manifest, task_id)
    current, registry_issues = registry_current_entries_and_issues(manifest)
    issues = issues + registry_issues
    remaining = [
        slot
        for slot in slots
        if not (slot.slot in current and entry_merged_pr(current[slot.slot]) == merged_pr)
    ]
    return remaining, issues


# ---------------------------------------------------------------------------
# 集合照合
# ---------------------------------------------------------------------------


def compare_slot_sets(
    expected: Sequence[ExpectedSlot | SlotKey], actual: Sequence[SlotKey]
) -> tuple[list[SlotKey], list[SlotKey], list[SlotKey]]:
    """`(missing, extra, duplicate)` を返す（各 list は repr 順）。"""
    expected_keys = [item.slot if isinstance(item, ExpectedSlot) else item for item in expected]
    expected_set = set(expected_keys)
    actual_list = list(actual)
    seen: set[SlotKey] = set()
    duplicate: list[SlotKey] = []
    for key in actual_list:
        if key in seen and key not in duplicate:
            duplicate.append(key)
        seen.add(key)
    missing = sorted(expected_set - seen, key=repr)
    extra = sorted(seen - expected_set, key=repr)
    return missing, extra, sorted(duplicate, key=repr)
