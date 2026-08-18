#!/usr/bin/env python3
"""independent acceptance artifact（`schema:independent-acceptance-extension/v1`）の検証器。

DEC-20260815-003 の post-S2 回復で、N-602A／N-598C／N-594B の
`required_terminal_evidence_types` が要求する `evidence-type:independent_acceptance` を
機械検証するために追加した（AC-15 の実装本体）。schema の正本は
`docs/audits/audit-materialization-manifest-2026-08-12.yml` の
`artifact_schemas['schema:independent-acceptance-extension/v1']` であり、本 module は
required field 集合をそこから読む（第二 SSOT を作らない）。

**not-exposed sentinel（DEC-20260817-001・人間裁定 2026-08-17）**

`reviewer_identity` / `implementer_identities` の 5 field は provider-native immutable record
から導出するのが原則だが、GitHub は Copilot レビューの `session_id` と `context_root_id` に
相当する値を公開しない（PR #1401 の 2 レビューで実測）。自由文や偽値で埋めると P0-01 と同種の
「偽の証跡が pass する」欠陥になるため、次の閉じた規則を置く。

- 非公開 field には typed sentinel `not_exposed:<理由>` だけを書ける（`SENTINELS` の閉じた集合）
- sentinel を書けるのは `session_id` と `context_root_id` だけ。`provider` / `account_id` /
  `run_id` に sentinel を書いたら reject（この 3 つは実測できるため）
- **sentinel を 1 つでも含む identity では L2／L3 を主張できない**。L2 は「reviewer session が
  全 implementer session と異なり lineage が非交差」を要求するので、session が不明なら判定不能
  ＝fail-close。L1 は provider 相違だけを要求するので sentinel があっても成立する

この規則により、独立性の主張が実体より強くなることはない。
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

SCHEMA_KEY: Final = "schema:independent-acceptance-extension/v1"
SCHEMA_VALUE: Final = "independent-acceptance-v1"
INDEPENDENCE_CONTRACT_AC_REF: Final = "ac:N-602:AC-15"
INDEPENDENT_ACCEPTANCE_EVIDENCE_TYPE: Final = "evidence-type:independent_acceptance"

IDENTITY_FIELDS: Final = ("provider", "account_id", "session_id", "context_root_id", "run_id")
SENTINEL_ALLOWED_FIELDS: Final = frozenset({"session_id", "context_root_id"})
SENTINELS: Final = frozenset({"not_exposed:stateless_run_scoped"})
SENTINEL_PREFIX: Final = "not_exposed:"

TARGET_KINDS: Final = frozenset({"task_phase", "control_unit"})
CONTROL_UNIT_PREFIX: Final = "control:"
CONTROL_CONTEXT_PREFIX: Final = "control-context:"
R025_TASK: Final = "task:R-025"
R025_INDEPENDENCE_GATE: Final = "gate:R025-IMPLEMENTATION-INDEPENDENT-ACCEPTANCE"
INDEPENDENCE_LEVELS: Final = ("L1", "L2", "L3")
VERDICTS: Final = frozenset({"accepted", "rejected", "recorded_unverified"})
ACCEPTED_LEVELS: Final = frozenset({"L1", "L2"})

PROVENANCE_FIELDS: Final = (
    "provider_record_ref",
    "provider_record_sha256",
    "parser_id",
    "parser_source_sha256",
)
BINDING_FIELDS: Final = ("commit_sha", "pr", "identity_index")
PROJECTION_FIELDS: Final = (
    "source_release_gate",
    "excluded_ac_refs",
    "excluded_evidence_types",
    "excluded_control_tokens",
    "required_atomic_tokens",
    "required_atomic_tokens_sha256",
)
REVIEWED_EVIDENCE_FIELDS: Final = ("slot_token", "evidence_id", "raw_sha256", "result")
FINDING_FIELDS: Final = ("finding_id", "severity", "location", "status", "evidence_ids")

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_SHA1_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_RFC3339_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_OPEN_STATUSES: Final = frozenset({"open", "unknown"})
# 判定は case-insensitive（`severity.strip().lower()`）で行うため小文字だけを持つ。
_MUST_SEVERITIES: Final = frozenset({"must"})


def is_sentinel(value: object) -> bool:
    """typed not-exposed sentinel かどうか。前方一致だけでは受理しない（閉じた集合）。"""
    return isinstance(value, str) and value in SENTINELS


def _looks_like_sentinel(value: object) -> bool:
    """sentinel を騙る自由文（`not_exposed:` 前置きだが未登録）を検出する。"""
    return isinstance(value, str) and value.startswith(SENTINEL_PREFIX)


def schema_required_fields(manifest: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """manifest の schema entry から required field 集合を読む（第二 SSOT を作らない）。"""
    issues: list[str] = []
    schemas = manifest.get("artifact_schemas")
    if not isinstance(schemas, Mapping):
        return [], ["manifest に artifact_schemas がありません"]
    entry = schemas.get(SCHEMA_KEY)
    if not isinstance(entry, Mapping):
        return [], [f"manifest に {SCHEMA_KEY} の schema entry がありません"]
    required = entry.get("required_fields")
    if not isinstance(required, list) or not required:
        return [], [f"{SCHEMA_KEY} の required_fields が非空配列ではありません"]
    # schema 定義そのものなので str() で握り潰さず fail-close する
    # （manifest 側の型崩れを検証器が黙って吸収しない）。
    bad = [item for item in required if not isinstance(item, str)]
    if bad:
        return [], [f"{SCHEMA_KEY} の required_fields に文字列でない要素があります: {bad!r}"]
    return list(required), issues


def r025_implementation_gates(
    manifest: Mapping[str, Any],
) -> tuple[list[str] | None, list[str]]:
    """task:R-025 の実装 gate（scope から独立受入 gate 自身を除いた exact 差集合）を返す。

    manifest の `task_catalog['task:R-025'].scope_ac_refs` が正本で、本 module は
    第二 SSOT を作らない。catalog を読めない場合は fail-close（None + issue）。
    """
    catalog = manifest.get("task_catalog")
    entry = catalog.get(R025_TASK) if isinstance(catalog, Mapping) else None
    if not isinstance(entry, Mapping):
        return None, [f"task_catalog に {R025_TASK} がありません"]
    scope = entry.get("scope_ac_refs")
    if not isinstance(scope, list) or not scope:
        return None, [f"task_catalog[{R025_TASK}].scope_ac_refs が非空配列ではありません"]
    # str() で強制変換すると manifest 側の型崩れを検証器が黙って吸収する。fail-close する。
    bad = [token for token in scope if not isinstance(token, str)]
    if bad:
        return None, [
            f"task_catalog[{R025_TASK}].scope_ac_refs に文字列でない要素があります: {bad!r}"
        ]
    gates = sorted(set(scope) - {R025_INDEPENDENCE_GATE})
    if not gates:
        return None, [f"task_catalog[{R025_TASK}] の実装 gate が空になりました"]
    return gates, []


def _string_tokens(value: object, *, label: str, issues: list[str]) -> list[str] | None:
    """token 配列を型安全に取り出す。非文字列が混ざっていたら fail-close で issue にする。

    `sorted()` を直接呼ぶと混在型で `TypeError` になり検証器が例外終了してしまう
    （fail-close の意図に反する）。比較の前に必ずこの helper を通す。
    """
    if not isinstance(value, list):
        issues.append(f"{label}: 配列ではありません")
        return None
    bad = [item for item in value if not isinstance(item, str)]
    if bad:
        issues.append(f"{label}: 文字列でない要素があります: {bad!r}")
        return None
    return sorted(value)


def _closed_object(
    value: object, fields: Sequence[str], *, label: str, issues: list[str]
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        issues.append(f"{label}: object ではありません")
        return None
    unknown = sorted(set(value) - set(fields))
    missing = sorted(set(fields) - set(value))
    if unknown:
        issues.append(f"{label}: 未知 field があります: {unknown}")
    if missing:
        issues.append(f"{label}: 必須 field がありません: {missing}")
    return dict(value)


def validate_identity(value: object, *, label: str) -> tuple[dict[str, str] | None, list[str]]:
    """identity closed object を検証し、sentinel の置ける field を制限する。"""
    issues: list[str] = []
    obj = _closed_object(value, IDENTITY_FIELDS, label=label, issues=issues)
    if obj is None or issues:
        return None, issues
    identity: dict[str, str] = {}
    for field in IDENTITY_FIELDS:
        raw = obj.get(field)
        if not isinstance(raw, str) or not raw.strip():
            issues.append(f"{label}.{field}: 非空文字列ではありません")
            continue
        # 前後空白は fail-close で拒否する。silent trim にすると provenance の実値と
        # ずれたまま通り、空白違いで provider／account の一致判定（L1／L2・同一 account）を
        # 回避できてしまう。
        if raw != raw.strip():
            issues.append(
                f"{label}.{field}: 前後に空白があります（provider-native 実値と一致しません）:"
                f" {raw!r}"
            )
            continue
        if _looks_like_sentinel(raw) and not is_sentinel(raw):
            issues.append(f"{label}.{field}: 未登録の not_exposed sentinel です: {raw!r}")
            continue
        if is_sentinel(raw) and field not in SENTINEL_ALLOWED_FIELDS:
            issues.append(
                f"{label}.{field}: この field に sentinel は置けません"
                f"（provider-native 実測値が取得できるため）"
            )
            continue
        identity[field] = raw
    if issues:
        return None, issues
    return identity, issues


def identity_has_sentinel(identity: Mapping[str, str]) -> bool:
    return any(is_sentinel(identity.get(field)) for field in IDENTITY_FIELDS)


def evaluate_independence(
    reviewer: Mapping[str, str],
    implementers: Sequence[Mapping[str, str]],
    level: str,
) -> list[str]:
    """independence level の成立条件を検査する。成立しない理由を返す。"""
    issues: list[str] = []
    if level not in INDEPENDENCE_LEVELS:
        return [f"independence_level が閉じた enum ではありません: {level!r}"]

    reviewer_provider = reviewer.get("provider")
    implementer_providers = {impl.get("provider") for impl in implementers}
    # 同一 account の別表記は provider が違っても独立ではない。
    if reviewer.get("account_id") in {impl.get("account_id") for impl in implementers}:
        issues.append(
            "reviewer_identity.account_id が implementer と同一です"
            "（同一 account の別表記を独立として扱えません）"
        )
    # run ID と commit SHA の異種比較を禁止する。
    if isinstance(reviewer.get("run_id"), str) and _SHA1_RE.match(reviewer["run_id"]):
        issues.append("reviewer_identity.run_id が commit SHA 形式です（異種比較は許しません）")

    sentinel_present = identity_has_sentinel(reviewer) or any(
        identity_has_sentinel(impl) for impl in implementers
    )

    if level == "L1":
        if reviewer_provider in implementer_providers:
            issues.append(
                f"L1 は reviewer provider が全 implementer provider と異なることを要求します"
                f"（reviewer={reviewer_provider!r}）"
            )
        return issues

    if sentinel_present:
        issues.append(
            f"{level} は not-exposed sentinel を含む identity では主張できません"
            "（session／context lineage が不明で判定不能のため fail-close）"
        )
        return issues

    if level == "L2":
        if reviewer.get("session_id") in {impl.get("session_id") for impl in implementers}:
            issues.append(
                "L2 は reviewer session が全 implementer session と異なることを要求します"
            )
        if reviewer.get("context_root_id") in {
            impl.get("context_root_id") for impl in implementers
        }:
            issues.append("L2 は context lineage が全件非交差であることを要求します")
    return issues


def _validate_projection(value: object, *, issues: list[str]) -> None:
    obj = _closed_object(value, PROJECTION_FIELDS, label="completion_projection", issues=issues)
    if obj is None:
        return
    for field in (
        "excluded_ac_refs",
        "excluded_evidence_types",
        "excluded_control_tokens",
        "required_atomic_tokens",
    ):
        _string_tokens(obj.get(field), label=f"completion_projection.{field}", issues=issues)
    excluded_ac = obj.get("excluded_ac_refs")
    if not isinstance(excluded_ac, list) or INDEPENDENCE_CONTRACT_AC_REF not in excluded_ac:
        issues.append(
            "completion_projection.excluded_ac_refs が独立性の自己 leaf"
            f"（{INDEPENDENCE_CONTRACT_AC_REF}）を除外していません"
        )
    excluded_types = obj.get("excluded_evidence_types")
    if (
        not isinstance(excluded_types, list)
        or INDEPENDENT_ACCEPTANCE_EVIDENCE_TYPE not in excluded_types
    ):
        issues.append(
            "completion_projection.excluded_evidence_types が"
            f" {INDEPENDENT_ACCEPTANCE_EVIDENCE_TYPE} を除外していません"
        )
    digest = obj.get("required_atomic_tokens_sha256")
    if not isinstance(digest, str) or not _SHA256_RE.match(digest):
        issues.append(
            "completion_projection.required_atomic_tokens_sha256 が 64 桁小文字 hex ではありません"
        )


def _validate_findings(value: object, *, issues: list[str]) -> bool:
    """findings を検査し、Must の open/unknown が無いかを返す。"""
    if not isinstance(value, list):
        issues.append("findings が配列ではありません")
        return False
    must_open = False
    for index, item in enumerate(value):
        label = f"findings[{index}]"
        obj = _closed_object(item, FINDING_FIELDS, label=label, issues=issues)
        if obj is None:
            continue
        severity = obj.get("severity")
        status = obj.get("status")
        evidence_ids = obj.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            issues.append(f"{label}: 根拠 evidence のない finding は許しません")
        # severity／status の表記ゆれ（大文字小文字・前後空白）で Must open 判定を
        # 回避させない。前後空白は fail-close で拒否し、判定は case-insensitive で行う。
        for field, raw in (("severity", severity), ("status", status)):
            if not isinstance(raw, str) or not raw.strip():
                issues.append(f"{label}.{field}: 非空文字列ではありません")
            elif raw != raw.strip():
                issues.append(f"{label}.{field}: 前後に空白があります: {raw!r}")
        if (
            isinstance(severity, str)
            and isinstance(status, str)
            and severity.strip().lower() in _MUST_SEVERITIES
            and status.strip().lower() in _OPEN_STATUSES
        ):
            must_open = True
    return must_open


def validate_artifact(
    artifact: object,
    *,
    manifest: Mapping[str, Any],
    scope_ac_refs: Sequence[str] | None = None,
    expected_tested_head: str | None = None,
) -> list[str]:
    """independent acceptance artifact を closed schema と独立性規則で検証する。"""
    issues: list[str] = []
    required, schema_issues = schema_required_fields(manifest)
    issues.extend(schema_issues)
    if not required:
        return issues

    obj = _closed_object(artifact, required, label="independent_acceptance", issues=issues)
    if obj is None or issues:
        return issues

    if obj.get("schema") != SCHEMA_VALUE:
        issues.append(f"schema が {SCHEMA_VALUE} ではありません: {obj.get('schema')!r}")

    target_kind = obj.get("target_kind")
    if target_kind not in TARGET_KINDS:
        issues.append(f"target_kind が閉じた enum ではありません: {target_kind!r}")

    subject = obj.get("target_subject_id")
    if target_kind == "task_phase" and not (
        isinstance(subject, str) and subject.startswith("task:")
    ):
        issues.append(
            "target_kind=task_phase では target_subject_id が task: token である必要があります"
        )
    # `control:` unit token だけを許す。`control-context:` は context ID であり
    # manifest が「context ID は別 field へ保持する」と定めているので受理しない。
    if target_kind == "control_unit" and not (
        isinstance(subject, str) and subject.startswith(CONTROL_UNIT_PREFIX)
    ):
        issues.append(
            "target_kind=control_unit では target_subject_id が"
            f" {CONTROL_UNIT_PREFIX} token である必要があります（control-context: は別 field）"
        )

    tested_head = obj.get("tested_head")
    if not isinstance(tested_head, str) or not _SHA1_RE.match(tested_head):
        issues.append("tested_head が 40 桁小文字 hex ではありません")
    elif expected_tested_head is not None and tested_head != expected_tested_head:
        issues.append(
            f"tested_head が判定対象 head と一致しません: {tested_head} != {expected_tested_head}"
        )

    if obj.get("independence_contract_ac_ref") != INDEPENDENCE_CONTRACT_AC_REF:
        issues.append(
            f"independence_contract_ac_ref が {INDEPENDENCE_CONTRACT_AC_REF} ではありません"
        )

    reviewer, reviewer_issues = validate_identity(
        obj.get("reviewer_identity"), label="reviewer_identity"
    )
    issues.extend(reviewer_issues)

    implementers: list[dict[str, str]] = []
    raw_impls = obj.get("implementer_identities")
    if not isinstance(raw_impls, list) or not raw_impls:
        issues.append("implementer_identities が非空配列ではありません")
    else:
        seen: set[tuple[str, ...]] = set()
        for index, item in enumerate(raw_impls):
            identity, identity_issues = validate_identity(
                item, label=f"implementer_identities[{index}]"
            )
            issues.extend(identity_issues)
            if identity is None:
                continue
            key = tuple(identity[field] for field in IDENTITY_FIELDS)
            if key in seen:
                issues.append(f"implementer_identities[{index}]: 重複しています")
            seen.add(key)
            implementers.append(identity)

    level = obj.get("independence_level")
    # str() で強制変換すると非文字列 level が enum 判定へ流れ込むので、型を先に確定する。
    if not isinstance(level, str):
        issues.append(f"independence_level が文字列ではありません: {level!r}")
    elif reviewer is not None and implementers:
        issues.extend(evaluate_independence(reviewer, implementers, level))
    elif level not in INDEPENDENCE_LEVELS:
        issues.append(f"independence_level が閉じた enum ではありません: {level!r}")

    bindings = obj.get("implementation_bindings")
    if not isinstance(bindings, list) or not bindings:
        issues.append("implementation_bindings が非空配列ではありません")
    else:
        seen_commits: set[str] = set()
        for index, item in enumerate(bindings):
            label = f"implementation_bindings[{index}]"
            binding = _closed_object(item, BINDING_FIELDS, label=label, issues=issues)
            if binding is None:
                continue
            commit = binding.get("commit_sha")
            if not isinstance(commit, str) or not _SHA1_RE.match(commit):
                issues.append(f"{label}.commit_sha が 40 桁小文字 hex ではありません")
            elif commit in seen_commits:
                issues.append(f"{label}.commit_sha が重複しています")
            else:
                seen_commits.add(commit)
            pr = binding.get("pr")
            if not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0:
                issues.append(f"{label}.pr が正整数ではありません")
            idx = binding.get("identity_index")
            if (
                not isinstance(idx, int)
                or isinstance(idx, bool)
                or not 0 <= idx < len(implementers)
            ):
                issues.append(f"{label}.identity_index が implementer_identities へ解決しません")

    provenance = _closed_object(
        obj.get("identity_provenance"),
        PROVENANCE_FIELDS,
        label="identity_provenance",
        issues=issues,
    )
    if provenance is not None:
        for field in ("provider_record_sha256", "parser_source_sha256"):
            digest = provenance.get(field)
            if not isinstance(digest, str) or not _SHA256_RE.match(digest):
                issues.append(f"identity_provenance.{field} が 64 桁小文字 hex ではありません")
        for field in ("provider_record_ref", "parser_id"):
            if not isinstance(provenance.get(field), str) or not str(provenance.get(field)).strip():
                issues.append(f"identity_provenance.{field} が非空文字列ではありません")

    review_scope = obj.get("review_scope_ac_refs")
    # scope_ac_refs 未指定でも要素型を必ず検証する（未指定時に型検査が抜けていた）。
    if _string_tokens(review_scope, label="review_scope_ac_refs", issues=issues) is None:
        pass
    elif isinstance(review_scope, list):
        if INDEPENDENCE_CONTRACT_AC_REF in review_scope:
            issues.append(
                "review_scope_ac_refs に独立性契約 AC 自身を含められません"
                "（AC-15 → independent acceptance → completion gate → AC-15 の循環を作らない）"
            )
        if target_kind == "control_unit" and review_scope:
            issues.append("target_kind=control_unit では review_scope_ac_refs は空です")
        if scope_ac_refs is not None and target_kind == "task_phase":
            expected = sorted(set(scope_ac_refs) - {INDEPENDENCE_CONTRACT_AC_REF})
            sorted_scope = _string_tokens(review_scope, label="review_scope_ac_refs", issues=issues)
            if sorted_scope is not None and sorted_scope != expected:
                issues.append(
                    "review_scope_ac_refs が task scope から独立性契約 AC を除いた"
                    " exact 差集合ではありません"
                )

    control_tokens = obj.get("review_scope_control_tokens")
    if not isinstance(control_tokens, list):
        issues.append("review_scope_control_tokens が配列ではありません")
    elif target_kind == "task_phase" and subject == R025_TASK:
        # task:R-025 だけが実装 gate を review scope に持つ。task_catalog の scope から
        # 独立受入 gate 自身を除いた exact 差集合（＝三つの実装 gate）を要求する
        # （空や部分集合を fail-open で通さない）。
        expected_r025, catalog_issues = r025_implementation_gates(manifest)
        issues.extend(catalog_issues)
        sorted_tokens = _string_tokens(
            control_tokens, label="review_scope_control_tokens", issues=issues
        )
        if (
            expected_r025 is not None
            and sorted_tokens is not None
            and sorted_tokens != expected_r025
        ):
            issues.append(
                "task:R-025 の review_scope_control_tokens が実装 gate の exact 集合と"
                f"一致しません（期待 {expected_r025}・実際 {sorted_tokens}）"
            )
    elif target_kind == "task_phase" and control_tokens:
        # 通常の AC task では空（manifest: task:R-025 だけが三つの実装 gate を持つ）。
        issues.append("通常の task phase では review_scope_control_tokens は空です")
    elif target_kind == "control_unit":
        # control unit では catalog の review_scope_control_tokens と exact 一致を要求する
        # （部分集合や別 unit の集合を持ち込ませない）。
        catalog = manifest.get("control_unit_catalog")
        entry = catalog.get(subject) if isinstance(catalog, Mapping) else None
        if not isinstance(entry, Mapping):
            issues.append(f"control_unit_catalog に {subject!r} がありません")
        else:
            expected_tokens = entry.get("review_scope_control_tokens")
            if not isinstance(expected_tokens, list):
                issues.append(
                    f"control_unit_catalog[{subject}] に review_scope_control_tokens がありません"
                )
            else:
                sorted_actual = _string_tokens(
                    control_tokens, label="review_scope_control_tokens", issues=issues
                )
                sorted_expected = _string_tokens(
                    expected_tokens,
                    label=f"control_unit_catalog[{subject}].review_scope_control_tokens",
                    issues=issues,
                )
                if (
                    sorted_actual is not None
                    and sorted_expected is not None
                    and sorted_actual != sorted_expected
                ):
                    issues.append(
                        "review_scope_control_tokens が control_unit_catalog の exact 集合と"
                        f"一致しません（期待 {len(sorted_expected)} 件・"
                        f"実際 {len(sorted_actual)} 件）"
                    )

    _validate_projection(obj.get("completion_projection"), issues=issues)

    reviewed = obj.get("reviewed_evidence")
    if not isinstance(reviewed, list) or not reviewed:
        issues.append("reviewed_evidence が非空配列ではありません")
    else:
        seen_slots: set[str] = set()
        for index, item in enumerate(reviewed):
            label = f"reviewed_evidence[{index}]"
            entry = _closed_object(item, REVIEWED_EVIDENCE_FIELDS, label=label, issues=issues)
            if entry is None:
                continue
            slot = entry.get("slot_token")
            if not isinstance(slot, str) or not slot.strip():
                # 型検証なしだと非文字列 slot が issue なしで紛れ込み、被覆検査からも漏れる。
                issues.append(f"{label}.slot_token: 非空文字列ではありません: {slot!r}")
            else:
                if slot in (INDEPENDENCE_CONTRACT_AC_REF, INDEPENDENT_ACCEPTANCE_EVIDENCE_TYPE):
                    issues.append(f"{label}: 独立受入自身または AC-15 proof を入力にできません")
                if slot in seen_slots:
                    issues.append(f"{label}: slot が重複しています")
                seen_slots.add(slot)
            digest = entry.get("raw_sha256")
            if not isinstance(digest, str) or not _SHA256_RE.match(digest):
                issues.append(f"{label}.raw_sha256 が 64 桁小文字 hex ではありません")

        # review_scope と required_atomic_tokens の各 slot へ exact 1 件を要求する
        # （部分レビューを accepted にしない）。除外対象は自己 leaf だけなので、
        # 差分は「未レビュー」または「余分」のどちらかで必ず issue になる。
        required_slots: set[str] = set()
        if isinstance(review_scope, list):
            required_slots |= {token for token in review_scope if isinstance(token, str)}
        if isinstance(control_tokens, list):
            required_slots |= {token for token in control_tokens if isinstance(token, str)}
        projection = obj.get("completion_projection")
        if isinstance(projection, Mapping):
            atomic = projection.get("required_atomic_tokens")
            if isinstance(atomic, list):
                required_slots |= {token for token in atomic if isinstance(token, str)}
        required_slots -= {INDEPENDENCE_CONTRACT_AC_REF, INDEPENDENT_ACCEPTANCE_EVIDENCE_TYPE}
        uncovered = sorted(required_slots - seen_slots)
        extra = sorted(seen_slots - required_slots)
        if uncovered:
            issues.append(
                f"reviewed_evidence が review scope／required atomic token の slot を"
                f"網羅していません: {uncovered}"
            )
        if extra:
            issues.append(f"reviewed_evidence に scope 外の余分な slot があります: {extra}")

    must_open = _validate_findings(obj.get("findings"), issues=issues)

    verdict = obj.get("verdict")
    if verdict not in VERDICTS:
        issues.append(f"verdict が閉じた enum ではありません: {verdict!r}")
    elif verdict == "accepted":
        if level not in ACCEPTED_LEVELS:
            issues.append("verdict=accepted は L1 または L2 でのみ許されます（L3 は記録専用）")
        if must_open:
            issues.append("verdict=accepted なのに Must の open/unknown finding が残っています")
        if issues:
            issues.append("verdict=accepted ですが上記の検査に fail しています")
    elif level == "L3" and verdict != "recorded_unverified":
        issues.append("L3 は recorded_unverified だけを許します")

    # manifest は「provider provenance と一致する RFC 3339 日時」を要求する。
    # 自由文や日付だけの表記を受理しない（typed 比較の前提を壊さない）。
    reviewed_at = obj.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip():
        issues.append("reviewed_at が非空文字列ではありません")
    else:
        try:
            datetime.datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError:
            issues.append(f"reviewed_at が RFC 3339 日時ではありません: {reviewed_at!r}")
        else:
            if not _RFC3339_RE.match(reviewed_at):
                issues.append(
                    "reviewed_at が RFC 3339 の日時形式（offset 必須）ではありません:"
                    f" {reviewed_at!r}"
                )

    return issues
