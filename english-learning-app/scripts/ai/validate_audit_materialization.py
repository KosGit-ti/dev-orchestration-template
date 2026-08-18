#!/usr/bin/env python3
"""第4次再監査のmaterialization manifestと報告書をfail-closeで検証する。

役割（DEC-20260815-003 決定 4）: 本 validator は監査 materialization の **baseline integrity**
（監査原本 SHA・claim／AC／token／graph の構造整合・report の埋込 hash）を検査する。claim の
closure 状態は「全 claim が open」を要求せず、closure が non-open の claim については registry 側
append-only closure event chain（`scripts/ai/claim_closure_events.py`・
`docs/ai/claim-closure-events/<claim>/`）の fold 結果と exact 一致することを要求する
（`_validate_claims`）。event chain 無しの non-open closure は手編集として拒否する。
`valid_gated_residual` は validator source の exact allowlist 4 claim
（`scripts/ai/claim_closure.py` `GATED_RESIDUAL_ALLOWLIST`）＋finalizer context 一致のときだけ
許す。manifest 本文の `closure_contract.finalizer_contract`（gated 限定条文）は
DEC-20260815-003 で allowlist 4 claim へ改訂されており、本 validator は validator source
（P-070 rank 4）の allowlist を正とする（manifest 本文の改訂は別 unit）。
operational な closeout 検証（registry entry と artifact／blob／head の照合）は
`scripts/ai/validate_evidence_registry.py` を別 check として使う。
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import claim_closure, claim_closure_events  # noqa: E402

MANIFEST_REL = "docs/audits/audit-materialization-manifest-2026-08-12.yml"
REPORT_REL = "docs/audits/audit-materialization-report-2026-08-12.md"
VALIDATOR_REL = "scripts/ai/validate_audit_materialization.py"
VALIDATOR_TEST_REL = "tests/ai/test_validate_audit_materialization.py"
SOURCE_SHA256 = "25cd3b2e59e3a7e12847f85e3f7ab6dc9ca494be3cc514d51bbb6029efa2d16e"
ACTIONABLE = {"adopt", "adopt_with_refinement", "gated"}
EXPECTED_CLAIMS = 179
EXPECTED_ACTIONABLE = 145
EXPECTED_AC = 68
EXPECTED_ATOMIC = 440
EXPECTED_MEANING_LINES = 567
EXPECTED_NORMAL_TASKS = (
    "task:N-602A",
    "task:N-598C",
    "task:N-594B",
    "task:N-600",
    "task:R-025",
    "task:N-601",
    "task:N-602B",
    "task:N-603",
    "task:N-604",
)
TASK_ORDER = {task: index for index, task in enumerate(EXPECTED_NORMAL_TASKS, 1)}
SPEC_CONTRACTS = {
    "N-594B": ("docs/specs/N-594B-derived-compute-accounting.md", "## Acceptance Criteria", 8),
    "N-597": ("docs/specs/N-597-self-improvement-control-loop.md", "## 10. 受入条件", 4),
    "N-598C": ("docs/specs/N-598C-exact-current-state-resolver.md", "## Acceptance Criteria", 10),
    "N-600": ("docs/specs/N-600-root-cause-analysis.md", "## Acceptance Criteria", 6),
    "N-601": (
        "docs/specs/N-601-recurrence-analyzer-ledger-validator.md",
        "## Acceptance Criteria",
        7,
    ),
    "N-602": ("docs/specs/N-602-evidence-truthfulness-generation.md", "## Acceptance Criteria", 16),
    "N-603": ("docs/specs/N-603-effectiveness-metrics.md", "## Acceptance Criteria", 8),
    "N-604": ("docs/specs/N-604-control-retirement.md", "## Acceptance Criteria", 9),
}
N602_A_ONLY = {1, 2, 3, 6, 7, 8, 12, 14, 16}
N602_B_ONLY = {4, 11, 13}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def set_sha256(items: list[str] | set[str]) -> str:
    return sha256("".join(f"{item}\n" for item in sorted(items)).encode())


def _section_bytes(raw: bytes, heading: str) -> bytes:
    marker = f"{heading}\n".encode()
    try:
        start = raw.index(marker)
    except ValueError as exc:
        raise ValueError(f"見出しがない: {heading}") from exc
    following = raw[start + len(marker) :]
    next_heading = re.search(rb"^## ", following, re.MULTILINE)
    end = start + len(marker) + (next_heading.start() if next_heading else len(following))
    return raw[start:end]


def extract_ac_records(raw: bytes, heading: str) -> list[bytes]:
    """Acceptance Criteria節だけを走査し、DoD等の同番号bulletを混ぜない。"""
    section = _section_bytes(raw, heading)
    starts = list(re.finditer(rb"^- AC-[0-9]{2}", section, re.MULTILINE))
    return [
        section[
            match.start() : starts[index + 1].start() if index + 1 < len(starts) else len(section)
        ]
        for index, match in enumerate(starts)
    ]


def split_atomic(record: bytes) -> list[tuple[int, int, bytes]]:
    text = record.decode()
    byte_offsets: list[int] = []
    position = 0
    for char in text:
        byte_offsets.append(position)
        position += len(char.encode())
    byte_offsets.append(position)
    cuts = [0]
    for index, char in enumerate(text):
        if char in "。、；;\n":
            cuts.append(byte_offsets[index + 1])
    if cuts[-1] != len(record):
        cuts.append(len(record))
    parts: list[tuple[int, int, bytes]] = []
    for start, end in zip(sorted(set(cuts)), sorted(set(cuts))[1:], strict=False):
        piece = record[start:end]
        if not piece:
            continue
        if not piece.strip() and parts:
            old_start, _, old_piece = parts[-1]
            parts[-1] = (old_start, end, old_piece + piece)
        else:
            parts.append((start, end, piece))
    return parts


def _slug(heading: str) -> str:
    value = heading.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return re.sub(r"-+", "-", value.replace(" ", "-")).strip("-")


def _markdown_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    counts: Counter[str] = Counter()
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if not match:
            continue
        base = _slug(match.group(1))
        suffix = counts[base]
        counts[base] += 1
        anchors.add(base if suffix == 0 else f"{base}-{suffix}")
    return anchors


@dataclass
class Result:
    root: Path
    errors: list[str] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)
    hashes: dict[str, str] = field(default_factory=dict)

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)


def _load_yaml(path: Path, result: Result) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        result.errors.append(f"YAMLを読めない: {path}: {exc}")
        return {}
    if not isinstance(data, dict):
        result.errors.append(f"YAML rootがmappingでない: {path}")
        return {}
    return data


def _validate_source(data: dict[str, Any], result: Result) -> None:
    source = data.get("source", {})
    path = result.root / str(source.get("path", ""))
    result.require(path.is_file(), f"監査原本がない: {path}")
    if not path.is_file():
        return
    raw = path.read_bytes()
    result.hashes["source_sha256"] = sha256(raw)
    result.require(result.hashes["source_sha256"] == SOURCE_SHA256, "監査原本SHAが変化した")
    result.require(source.get("sha256") == SOURCE_SHA256, "manifestの監査原本SHAが不一致")
    result.require(source.get("immutable") is True, "監査原本immutable=trueがない")
    result.require(len(raw.splitlines()) == 938, "監査原本行数が938でない")


# PR #1392 Round 2 指摘: registry 側 event chain の `to_state` は `invalidated` を持つ
# （`disposition=invalidates`）。manifest 側 enum が受理しないと、無効化した claim を
# fold と exact 一致させられず必ず fail-close になり、誤登録の invalidate を表現できない。
CLOSURE_STATES = ("open", "implemented_verified", "valid_gated_residual", "invalidated")
INITIAL_OPEN_NULL_FIELDS = ("tested_head", "evidence_anchor_head", "owner", "review_due")


def _closure_fold(
    data: dict[str, Any], result: Result
) -> dict[str, claim_closure_events.ClaimCurrent]:
    """registry 側 closure event chain（`docs/ai/claim-closure-events/`）の fold 結果を返す。

    events root が無ければ全 claim `state=open`（event 0 件）。fold が不正な claim
    （branch／cycle／gap／hash 不一致・manifest に無い slug）は error にする。
    """
    events_root = result.root / claim_closure_events.EVENTS_DIR_REL
    fold = claim_closure_events.current_closure_status(data, events_root)
    for claim_id in sorted(fold):
        current = fold[claim_id]
        for issue in current.issues:
            result.errors.append(f"{claim_id}: closure event chain不正: {issue}")
    result.counts["closure_event_claim_count"] = sum(
        1 for current in fold.values() if current.event_count > 0
    )
    return fold


def _validate_claim_closure(
    claim: dict[str, Any], fold: dict[str, claim_closure_events.ClaimCurrent], result: Result
) -> None:
    """actionable claim の `closure` を registry fold と照合する（DEC-20260815-003 決定 4）。

    - `state=open`: initial open 形状（evidence=[]・tested_head／evidence_anchor_head／owner／
      review_due=null・history なし）を要求し、event chain があれば矛盾として拒否する。
    - non-open: `claim_closure_events` の fold と state・evidence・tested_head／
      evidence_anchor_head が exact 一致することを要求する。event chain が無い non-open は
      手編集として拒否する。`valid_gated_residual` は allowlist 4 claim＋finalizer context
      一致のときだけ許す。
    """
    claim_id = str(claim.get("claim_id", "<missing>"))
    closure = claim.get("closure")
    if not isinstance(closure, dict):
        result.errors.append(f"{claim_id}: closureがobjectでない")
        return
    state = closure.get("state")
    result.require(
        state in CLOSURE_STATES, f"{claim_id}: closure.stateがclosed enumでない: {state!r}"
    )
    result.require(
        "history" not in closure,
        f"{claim_id}: closure.historyはmanifestに持たない（registry側event chainへ移行）",
    )
    current = fold.get(claim_id)
    context = claim.get("closure_finalizer_context")
    allowlisted_context = claim_closure.GATED_RESIDUAL_ALLOWLIST.get(claim_id)
    if context:
        # closure_contract.finalizer_contract の gated 限定条文は DEC-20260815-003 で
        # allowlist 4 claim（adopt／adopt_with_refinement）へ改訂。validator source の
        # allowlist を正とする。
        result.require(
            claim.get("adoption") == "gated" or context == allowlisted_context,
            f"{claim_id}: gated以外がallowlist外のfinalizerを持つ: {context}",
        )
    if state == "open":
        # Backlog #1397: 他 field は「存在＋null」を要求しているのに evidence だけ
        # `.get() == []` で、欠損時のメッセージが「evidence がある」になり誤解を招いていた。
        # PR #1399 Round 1 指摘: 欠損時に両方の検査を評価すると「field がない」と
        # 「evidence がある」の矛盾する 2 件が同時に出るため、存在しない場合は空配列検査を
        # 行わない。
        if "evidence" not in closure:
            result.errors.append(f"{claim_id}: closure=openなのにevidence field がない")
        else:
            result.require(
                closure.get("evidence") == [],
                f"{claim_id}: closure=openなのにclosure evidenceが空配列でない",
            )
        for name in INITIAL_OPEN_NULL_FIELDS:
            # PR #1396 Round 2 指摘: dict.get() だと field 欠損も None 扱いで通ってしまい、
            # schema の正規形（明示 null）が固定されない。存在と null の両方を要求する。
            result.require(name in closure, f"{claim_id}: closure=openなのに{name} field がない")
            result.require(
                closure.get(name) is None, f"{claim_id}: closure=openなのに{name}がnullでない"
            )
        if current is not None and current.event_count > 0:
            result.errors.append(
                f"{claim_id}: closure=openだがregistry側closure event chainが"
                f"{current.event_count}件ある（fold={current.state}）"
            )
        return
    if current is None or current.event_count == 0:
        result.errors.append(
            f"{claim_id}: closure={state}だがregistry側closure event chainがない（手編集は拒否）"
        )
        return
    if current.state is None:
        result.errors.append(f"{claim_id}: closure={state}だがevent chainのfoldが不正")
        return
    result.require(
        current.state == state,
        f"{claim_id}: closure.state={state}がevent chain fold={current.state}と一致しない",
    )
    result.require(
        closure.get("evidence") == [current.evidence_id],
        f"{claim_id}: closure.evidenceがfoldのevidence_id [{current.evidence_id}]と一致しない",
    )
    heads = current.heads or {}
    result.require(
        closure.get("tested_head") == heads.get("implementation_head"),
        f"{claim_id}: closure.tested_headがfoldのheads.implementation_headと一致しない",
    )
    result.require(
        closure.get("evidence_anchor_head") == heads.get("evidence_anchor_head"),
        f"{claim_id}: closure.evidence_anchor_headがfoldのheads.evidence_anchor_headと一致しない",
    )
    if state == "valid_gated_residual":
        result.require(
            allowlisted_context is not None,
            f"{claim_id}: valid_gated_residualはallowlist 4 claim（DEC-20260815-003）に限る",
        )
        result.require(
            allowlisted_context is not None and context == allowlisted_context,
            f"{claim_id}: valid_gated_residualのfinalizer contextがallowlist"
            f"（{allowlisted_context}）と一致しない: {context!r}",
        )
        for name in ("owner", "review_due"):
            result.require(
                closure.get(name) is not None, f"{claim_id}: valid_gated_residualなのに{name}がnull"
            )


def _validate_claims(data: dict[str, Any], result: Result) -> None:
    claims = data.get("claims", [])
    result.require(isinstance(claims, list), "claimsがlistでない")
    if not isinstance(claims, list):
        return
    ids = [claim.get("claim_id") for claim in claims if isinstance(claim, dict)]
    result.require(len(ids) == EXPECTED_CLAIMS, f"claim数が{EXPECTED_CLAIMS}でない: {len(ids)}")
    result.require(len(ids) == len(set(ids)), "claim_idが重複している")
    actionable = [claim for claim in claims if claim.get("adoption") in ACTIONABLE]
    members = {f"claim:{claim['claim_id']}" for claim in actionable}
    member_hash = set_sha256(members)
    result.counts["claim_count"] = len(ids)
    result.counts["actionable_count"] = len(actionable)
    result.hashes["actionable_members_sha256"] = member_hash
    result.require(len(actionable) == EXPECTED_ACTIONABLE, "actionable claim数が145でない")
    selection = data.get("claim_truth", {}).get("actionable_selection", {})
    result.require(
        selection.get("expected_count") == len(actionable), "selection countが実体と不一致"
    )
    result.require(
        selection.get("expected_members_sha256") == member_hash, "selection hashが不一致"
    )
    aggregate = data.get("release_gates", {}).get("release:RA4-ACTIONABLE-CLOSE-ALL", [])
    result.require(len(aggregate) == len(set(aggregate)), "actionable aggregateに重複がある")
    result.require(set(aggregate) == members, "actionable aggregateがadoption抽出集合と不一致")
    coverage = data.get("coverage", {})
    result.require(coverage.get("claim_count") == len(ids), "coverage claim_countが不一致")
    result.require(
        coverage.get("actionable_leaf_count") == len(actionable), "coverage actionableが不一致"
    )
    result.require(
        coverage.get("actionable_member_set_sha256") == member_hash, "coverage hashが不一致"
    )
    required = {
        "claim_id",
        "source_section",
        "source_lines",
        "summary",
        "type",
        "audit_state",
        "current_state",
        "current_evidence",
        "adoption",
        "requirement_refs",
        "design_refs",
        "spec_refs",
        "ac_refs",
        "plan_refs",
        "dependencies",
        "blocking_target",
        "positive_tests",
        "negative_tests",
        "residual_risk",
        "release_gate",
    }
    owner_counts: Counter[str] = Counter()
    covered_lines: set[int] = set()
    fold = _closure_fold(data, result)
    for claim in claims:
        claim_id = claim.get("claim_id", "<missing>")
        missing = required - set(claim)
        result.require(not missing, f"{claim_id}: 必須field欠損: {sorted(missing)}")
        for first, last in re.findall(r"L(\d+)(?:-L?(\d+))?", str(claim.get("source_lines", ""))):
            start, end = int(first), int(last or first)
            result.require(1 <= start <= end <= 938, f"{claim_id}: source_lines範囲不正")
            covered_lines.update(range(start, end + 1))
        if claim.get("adoption") in ACTIONABLE:
            owner = claim.get("closure_owner_task")
            owner_counts[str(owner)] += 1
            result.require(
                owner in EXPECTED_NORMAL_TASKS, f"{claim_id}: ownerが通常taskでない: {owner}"
            )
            _validate_claim_closure(claim, fold, result)
        else:
            result.require(
                not claim.get("closure_owner_task"), f"{claim_id}: non_requirementがownerを持つ"
            )
        result.require(
            len(claim.get("ac_refs", [])) == len(set(claim.get("ac_refs", []))),
            f"{claim_id}: ac_refs重複",
        )
        result.require(
            len(claim.get("dependencies", [])) == len(set(claim.get("dependencies", []))),
            f"{claim_id}: dependencies重複",
        )
    result.counts["owner_count"] = sum(owner_counts.values())
    result.counts["owner_distribution"] = dict(sorted(owner_counts.items()))
    result.require(sum(owner_counts.values()) == EXPECTED_ACTIONABLE, "owner exact 1件契約に違反")
    result.require(
        data.get("coverage", {}).get("actionable_owner_count") == EXPECTED_ACTIONABLE,
        "coverage owner count不一致",
    )
    source_path = result.root / data["source"]["path"]
    if source_path.is_file():
        content_lines = {
            number
            for number, line in enumerate(source_path.read_text(encoding="utf-8").splitlines(), 1)
            if line.strip() and not line.startswith("#") and line.strip() != "---"
        }
        result.require(content_lines <= covered_lines, "非見出しの監査原文行にclaim割当漏れがある")
    result.require(
        data.get("coverage", {}).get("source_meaning_line_count") == EXPECTED_MEANING_LINES,
        "semantic line母数不一致",
    )
    result.require(
        data.get("coverage", {}).get("covered_source_meaning_line_count") == EXPECTED_MEANING_LINES,
        "semantic line被覆不一致",
    )


def _validate_ac(data: dict[str, Any], result: Result) -> None:
    profiles = data.get("ac_evaluator_profiles", {})
    leaves = data.get("ac_leaf_registry", {})
    expected_tokens: set[str] = set()
    predicate_count = 0
    for spec_id, (relative, heading, expected) in SPEC_CONTRACTS.items():
        path = result.root / relative
        result.require(path.is_file(), f"AC specがない: {relative}")
        if not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            records = extract_ac_records(raw, heading)
        except ValueError as exc:
            result.errors.append(f"{relative}: {exc}")
            continue
        result.require(
            len(records) == expected, f"{spec_id}: AC数不一致 {len(records)} != {expected}"
        )
        profile = profiles.get(f"ac-profile:{spec_id}", {})
        result.require(
            profile.get("canonical_spec_raw_sha256") == sha256(raw),
            f"{spec_id}: spec SHA pin不一致",
        )
        tokens = [f"ac:{spec_id}:AC-{number:02d}" for number in range(1, expected + 1)]
        expected_tokens.update(tokens)
        covered = profile.get("covered_ac_set", {})
        result.require(covered.get("members") == tokens, f"{spec_id}: profile covered set不一致")
        result.require(covered.get("count") == expected, f"{spec_id}: profile count不一致")
        result.require(
            covered.get("members_sha256") == set_sha256(tokens),
            f"{spec_id}: profile set hash不一致",
        )
        for token, record in zip(tokens, records, strict=True):
            leaf = leaves.get(token, {})
            result.require(leaf.get("canonical_spec") == relative, f"{token}: canonical spec不一致")
            ac_record = leaf.get("ac_record", {})
            result.require(
                ac_record.get("raw_sha256") == sha256(record), f"{token}: AC raw SHA不一致"
            )
            result.require(
                ac_record.get("byte_length") == len(record), f"{token}: AC raw length不一致"
            )
            predicates = leaf.get("atomic_predicates", [])
            expected_parts = split_atomic(record)
            predicate_count += len(predicates)
            result.require(
                len(predicates) == len(expected_parts), f"{token}: atomic predicate数不一致"
            )
            ids: list[str] = []
            cursor = 0
            for index, (predicate, part) in enumerate(
                zip(predicates, expected_parts, strict=False), 1
            ):
                start, end, piece = part
                span = predicate.get("source_span", {})
                result.require(
                    span == {"start_byte": start, "end_byte": end},
                    f"{token}: predicate {index} span不一致",
                )
                result.require(start == cursor, f"{token}: predicate {index} gap/overlap")
                result.require(
                    predicate.get("source_raw_sha256") == sha256(piece),
                    f"{token}: predicate {index} raw SHA不一致",
                )
                expected_id = f"predicate:{sha256(f'{token}:{index}:{sha256(piece)}'.encode())}"
                result.require(
                    predicate.get("predicate_id") == expected_id,
                    f"{token}: predicate {index} ID不一致",
                )
                result.require(
                    predicate.get("typed_comparator") == "boolean_equals",
                    f"{token}: comparatorがclosedでない",
                )
                result.require(
                    predicate.get("expected") == {"type": "boolean", "value": True},
                    f"{token}: expectedがclosedでない",
                )
                result.require(
                    bool(predicate.get("required_command_ids_by_phase")),
                    f"{token}: command binding欠損",
                )
                result.require(
                    bool(predicate.get("required_verification_ids_by_phase")),
                    f"{token}: verification binding欠損",
                )
                ids.append(str(predicate.get("predicate_id")))
                cursor = end
            result.require(cursor == len(record), f"{token}: atomic predicatesが全文を被覆しない")
            predicate_set = leaf.get("atomic_predicate_set", {})
            result.require(
                predicate_set.get("count") == len(predicates), f"{token}: predicate set count不一致"
            )
            result.require(
                predicate_set.get("members_sha256") == set_sha256(ids),
                f"{token}: predicate set hash不一致",
            )
            result.require(
                predicate_set.get("source_coverage_sha256") == sha256(record),
                f"{token}: predicate source coverage SHA不一致",
            )
    result.require(set(leaves) == expected_tokens, "AC registryが68件exact集合でない")
    result.require(len(profiles) == len(SPEC_CONTRACTS), "AC profile数が8でない")
    result.require(
        predicate_count == EXPECTED_ATOMIC, f"atomic predicate総数不一致: {predicate_count}"
    )
    result.counts["ac_count"] = len(leaves)
    result.counts["atomic_predicate_count"] = predicate_count
    result.counts["profile_count"] = len(profiles)
    coverage = data.get("coverage", {})
    result.require(coverage.get("ac_leaf_count") == EXPECTED_AC, "coverage AC count不一致")
    result.require(
        coverage.get("atomic_ac_predicate_count") == EXPECTED_ATOMIC, "coverage atomic count不一致"
    )
    result.require(
        profiles.get("ac-profile:N-597", {}).get("kind") == "program_derived_assertion_profile",
        "N597 profileがderivedでない",
    )
    for number in range(1, 5):
        leaf = leaves.get(f"ac:N-597:AC-{number:02d}", {})
        result.require(
            leaf.get("kind") == "derived_assertion_leaf", f"N597 AC{number}がderivedでない"
        )
        result.require(
            leaf.get("verified_derived_assertion") == [], f"N597 AC{number} reserved配列が空でない"
        )


def _token_registries(data: dict[str, Any]) -> dict[str, set[str]]:
    return {
        "claim": {f"claim:{claim['claim_id']}" for claim in data.get("claims", [])},
        "gate": set(data.get("acceptance_gates", {})),
        "release": set(data.get("release_gates", {})),
        "task": set(data.get("task_catalog", {})) | set(data.get("task_aliases", {})),
        "ac": set(data.get("ac_leaf_registry", {})),
        "ac-profile": set(data.get("ac_evaluator_profiles", {})),
        "external": set(data.get("external_dependencies", {})),
        "milestone": set(data.get("terminal_conditions", {})),
        "schema": set(data.get("artifact_schemas", {})),
        "evidence-type": set(data.get("terminal_evidence_types", {})),
        "evidence": set(data.get("evidence_registry", {}).get("entries", {})),
        "control": set(data.get("control_unit_catalog", {})),
        "control-context": set(data.get("control_context_catalog", {})),
        "phase": set(data.get("phase_catalog", {})),
        "projection": set(data.get("internal_task_completion_projections", {})),
    }


def _validate_token(
    token: str, registries: dict[str, set[str]], result: Result, context: str
) -> None:
    if not isinstance(token, str) or ":" not in token:
        result.errors.append(f"{context}: prefixなしtoken: {token!r}")
        return
    namespace = token.split(":", 1)[0]
    if namespace not in registries:
        result.errors.append(f"{context}: 未定義namespace: {token}")
    elif token not in registries[namespace]:
        result.errors.append(f"{context}: 未解決token: {token}")


def _validate_graph_and_tokens(data: dict[str, Any], result: Result) -> None:
    registries = _token_registries(data)
    graph: dict[str, set[str]] = defaultdict(set)

    def add(source: str, tokens: list[str] | tuple[str, ...] | None, context: str) -> None:
        for token in tokens or []:
            _validate_token(token, registries, result, context)
            graph[source].add(token)

    for release, members in data.get("release_gates", {}).items():
        result.require(bool(members), f"{release}: empty aggregate")
        result.require(len(members) == len(set(members)), f"{release}: member重複")
        add(release, members, release)
    for gate, leaf in data.get("acceptance_gates", {}).items():
        add(gate, leaf.get("preconditions", []), gate)
        for key in ("required_evidence_type", "evidence_schema"):
            if leaf.get(key):
                _validate_token(leaf[key], registries, result, f"{gate}.{key}")
    for external, leaf in data.get("external_dependencies", {}).items():
        add(external, leaf.get("preconditions", []), external)
    for task, spec in data.get("task_catalog", {}).items():
        add(task, spec.get("dependencies", []), f"{task}.dependencies")
        add(task, spec.get("scope_ac_refs", []), f"{task}.scope_ac_refs")
        add(
            task,
            [spec["completion_gate"]] if spec.get("completion_gate") else [],
            f"{task}.completion_gate",
        )
        add(task, spec.get("member_tasks", []), f"{task}.member_tasks")
        add(task, spec.get("member_controls", []), f"{task}.member_controls")
        add(task, spec.get("assertion_inputs", []), f"{task}.assertion_inputs")
        add(task, spec.get("required_terminal_evidence_types", []), f"{task}.terminal_types")
    for alias, spec in data.get("task_aliases", {}).items():
        add(alias, spec.get("task_ids", []), alias)
    for milestone, spec in data.get("terminal_conditions", {}).items():
        add(milestone, spec.get("owner_tasks", []), f"{milestone}.owner_tasks")
        add(milestone, spec.get("evidence", []), f"{milestone}.evidence")
        for alternative in spec.get("alternatives", []):
            add(milestone, alternative.get("all", []), f"{milestone}.alternatives")
    for control, spec in data.get("control_unit_catalog", {}).items():
        add(control, spec.get("preconditions", []), f"{control}.preconditions")
        add(control, spec.get("produced_tokens", []), f"{control}.produced_tokens")
        add(control, spec.get("review_scope_control_tokens", []), f"{control}.review_scope")
        for root in spec.get("root_descriptors", []):
            for key in ("subject_id", "evidence_type"):
                _validate_token(root[key], registries, result, f"{control}.root.{key}")
    for projection, spec in data.get("internal_task_completion_projections", {}).items():
        add(projection, spec.get("required_dependencies", []), f"{projection}.dependencies")
        add(projection, spec.get("required_scope_ac_refs", []), f"{projection}.scope")
        add(
            projection,
            spec.get("completion_gate_projection", {}).get("expanded_members", []),
            f"{projection}.completion",
        )
        add(projection, spec.get("required_terminal_evidence_types", []), f"{projection}.terminal")
        add(projection, spec.get("global_prerequisites", []), f"{projection}.global")
    claims = {f"claim:{claim['claim_id']}": claim for claim in data.get("claims", [])}
    for token, claim in claims.items():
        add(token, claim.get("dependencies", []), f"{token}.dependencies")
        add(token, claim.get("ac_refs", []), f"{token}.ac_refs")
        add(token, [claim["release_gate"]], f"{token}.release_gate")
    for ac, leaf in data.get("ac_leaf_registry", {}).items():
        if leaf.get("kind") == "derived_assertion_leaf":
            add(ac, leaf.get("assertion_inputs", []), f"{ac}.assertion_inputs")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in visiting:
            result.errors.append(f"control graph cycle: {' -> '.join((*path, node))}")
            return
        if node in visited:
            return
        visiting.add(node)
        for target in graph.get(node, set()):
            if target in graph:
                visit(target, (*path, node))
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, ())
    result.counts["resolved_token_count"] = sum(len(values) for values in graph.values())
    result.counts["control_graph_cycle_count"] = sum(
        error.startswith("control graph cycle") for error in result.errors
    )


def _ac_producer(token: str, phase: str | None) -> str | None:
    match = re.fullmatch(r"ac:([^:]+):AC-(\d+)", token)
    if not match:
        return None
    spec, number = match.group(1), int(match.group(2))
    direct = {
        "N-594B": "task:N-594B",
        "N-598C": "task:N-598C",
        "N-600": "task:N-600",
        "N-601": "task:N-601",
        "N-603": "task:N-603",
        "N-604": "task:N-604",
    }
    if spec in direct:
        return None if spec == "N-594B" and number == 8 else direct[spec]
    if spec == "N-602":
        if number in N602_A_ONLY:
            return "task:N-602A"
        if number in N602_B_ONLY:
            return "task:N-602B"
        return phase
    return None


def _validate_phase_and_owner(data: dict[str, Any], result: Result) -> None:
    leaves = data.get("ac_leaf_registry", {})
    releases = data.get("release_gates", {})
    gates = data.get("acceptance_gates", {})
    milestones = data.get("terminal_conditions", {})
    aliases = data.get("task_aliases", {})
    claims = {f"claim:{claim['claim_id']}": claim for claim in data.get("claims", [])}
    projections = data.get("internal_task_completion_projections", {})
    release_context = data.get("release_phase_context", {})
    ambiguity: list[str] = []

    def producers(token: str, phase: str | None = None, stack: tuple[str, ...] = ()) -> set[str]:
        if token in stack:
            return set()
        stack = (*stack, token)
        if token in TASK_ORDER:
            return {token}
        if token in aliases:
            output: set[str] = set()
            for target in aliases[token].get("task_ids", []):
                output |= producers(target, None, stack)
            return output
        if token in leaves:
            found = _ac_producer(token, phase)
            if found:
                return {found}
            if len(leaves[token].get("phase_tasks", [])) > 1:
                ambiguity.append(f"{token}@{phase}")
            return set()
        if token in releases:
            context = release_context.get(token, phase)
            output = set()
            for target in releases[token]:
                output |= producers(target, context, stack)
            return output
        if token in gates:
            output = set()
            for target in gates[token].get("preconditions", []):
                output |= producers(target, phase, stack)
            return output
        if token in milestones:
            spec = milestones[token]
            targets = list(spec.get("owner_tasks", [])) + list(spec.get("evidence", []))
            for alternative in spec.get("alternatives", []):
                targets.extend(alternative.get("all", []))
            output = set()
            for target in targets:
                output |= producers(target, phase, stack)
            return output
        if token in claims:
            # claim dependencyは先行claimの公開済みtruthを要求する。producer taskを
            # 再展開すると、現claim ownerより後へ移された先行claimが過去taskの
            # 実装要件を持つだけで偽の順序違反になるため、ここではleafとして扱う。
            return set()
        if token in projections:
            spec = projections[token]
            targets = list(spec.get("required_dependencies", [])) + list(
                spec.get("required_scope_ac_refs", [])
            )
            output = set()
            for target in targets:
                output |= producers(target, phase, stack)
            return output
        return set()

    for claim in data.get("claims", []):
        if claim.get("adoption") not in ACTIONABLE:
            continue
        owner = claim["closure_owner_task"]
        shared = {
            token
            for token in claim.get("ac_refs", [])
            if token in leaves and len(leaves[token].get("phase_tasks", [])) > 1
        }
        context = claim.get("ac_evaluation_context", {}) or {}
        result.require(
            set(context) == shared, f"{claim['claim_id']}: shared AC context key exact集合不一致"
        )
        required: set[str] = set()
        for token in claim.get("ac_refs", []):
            phase = context.get(token)
            if token in shared:
                result.require(
                    phase in leaves[token]["phase_tasks"],
                    f"{claim['claim_id']}: shared AC phase不正",
                )
            required |= producers(token, phase)
        for token in claim.get("dependencies", []):
            if not token.startswith("claim:"):
                required |= producers(token)
        # release gateはclaimの受入集合でありowner task自身のcompletionを含み得る。
        # owner割当の下限は直接AC producerと明示task dependencyから検査し、release
        # aggregate全体のtask DAGはcontrol graph検査で別に閉じる。
        later = {
            task for task in required if task in TASK_ORDER and TASK_ORDER[task] > TASK_ORDER[owner]
        }
        valid_finalizer = claim.get("adoption") == "gated" and bool(
            claim.get("closure_finalizer_context")
        )
        result.require(
            not later or valid_finalizer,
            f"{claim['claim_id']}: ownerがproducerより早い: {sorted(later)}",
        )
    result.require(not ambiguity, f"phase未指定のshared AC: {sorted(set(ambiguity))}")
    n602a_scope = set(data["task_catalog"]["task:N-602A"]["scope_ac_refs"])
    result.require("ac:N-602:AC-09" in n602a_scope, "N602A scopeに共有AC09がない")
    for phase in ("task:N-602A", "task:N-602B"):
        task_scope = data["task_catalog"][phase]["scope_ac_refs"]
        profile_scope = data["ac_evaluator_profiles"]["ac-profile:N-602"]["phase_profiles"][phase][
            "covered_ac_set"
        ]["members"]
        result.require(task_scope == profile_scope, f"{phase}: task scopeとphase profileが不一致")
    result.require("ac:N-602:AC-09" in releases.get("release:RA4-PR1", []), "PR1に共有AC09がない")
    result.require(
        releases.get("release:RA4-PR3") == [f"ac:N-600:AC-{n:02d}" for n in range(1, 7)],
        "PR3がN600 AC01-06 exactでない",
    )
    forbidden = {"ac:N-602:AC-11", "ac:N-602:AC-13", "ac:N-602:AC-15"}
    single_sources = [
        data["control_unit_catalog"]["control:R025-single-look"]["preconditions"],
        data["acceptance_gates"]["gate:R025-SINGLE-LOOK-MILESTONE"]["preconditions"],
        releases["release:R025-single-look"],
    ]
    for tokens in single_sources:
        result.require(
            not forbidden.intersection(tokens),
            "single-look preconditionにphaseなし共有AC直参照がある",
        )
        result.require(
            "task:N-602B" in tokens and "release:RA4-PR5" in tokens,
            "single-lookがN602B/PR5を要求しない",
        )


def _recursive_children(data: dict[str, Any], token: str) -> list[str]:
    """P/owner Rが展開を許すnamespaceだけを一意の規則で展開する。"""
    if token in data.get("release_gates", {}):
        return list(data["release_gates"][token])
    if token in data.get("task_aliases", {}):
        return list(data["task_aliases"][token].get("task_ids", []))
    if token in data.get("terminal_conditions", {}):
        milestone = data["terminal_conditions"][token]
        output = list(milestone.get("owner_tasks", [])) + list(milestone.get("evidence", []))
        for alternative in milestone.get("alternatives", []):
            output.extend(alternative.get("all", []))
        return output
    claims = {f"claim:{claim['claim_id']}": claim for claim in data.get("claims", [])}
    if token in claims:
        claim = claims[token]
        return (
            list(claim.get("dependencies", []))
            + list(claim.get("ac_refs", []))
            + [claim["release_gate"]]
        )
    if token in data.get("ac_leaf_registry", {}):
        leaf = data["ac_leaf_registry"][token]
        if leaf.get("kind") == "derived_assertion_leaf":
            return list(leaf.get("assertion_inputs", []))
    if token == "task:N-597":
        task = data["task_catalog"][token]
        return list(task.get("assertion_inputs", [])) + list(task.get("scope_ac_refs", []))
    if token == "ac-profile:N-597":
        profile = data["ac_evaluator_profiles"][token]
        return list(profile.get("assertion_inputs", [])) + list(
            profile.get("covered_ac_set", {}).get("members", [])
        )
    return []


def _recursive_paths(data: dict[str, Any], roots: list[str], target: str) -> set[str]:
    paths: set[str] = set()

    def walk(token: str, path: tuple[str, ...]) -> None:
        if token == target:
            paths.add(" -> ".join(path))
            return
        if token in path[:-1]:
            return
        for child in _recursive_children(data, token):
            walk(child, (*path, child))

    for root in roots:
        walk(root, (root,))
    return paths


def _validate_proposed_post_r(data: dict[str, Any], result: Result) -> None:
    contract = (
        data.get("evidence_registry", {})
        .get("registration_update_contract", {})
        .get("proposed_post_R_state", {})
    )
    result.require(
        contract.get("applies_to_tasks") == list(EXPECTED_NORMAL_TASKS),
        "proposed post-Rの適用task集合が不一致",
    )
    result.require(
        contract.get("stage_order")
        == [
            "R0-batch-overlay",
            "R1-leaf-and-task-candidate",
            "R2-owner-claim-dag",
            "R3-atomic-commit",
        ],
        "proposed post-Rのstage orderがclosedでない",
    )
    expected_stages: dict[str, dict[str, Any]] = {
        "R0-batch-overlay": {
            "kind": "immutable_candidate_overlay",
            "input_source": "S1_required_evidence_roots_and_owner_claim_exact_set",
            "allowed_objects": [
                "new_registry_entries",
                "new_closure_history_events",
                "closure_current_snapshots_derived_from_new_events",
            ],
            "forbidden_objects": [
                "git_tree_mutation",
                "manifest_current_mutation",
                "public_task_token",
            ],
            "output_visibility": "registration_R_only",
        },
        "R1-leaf-and-task-candidate": {
            "kind": "leaf_and_internal_task_candidate",
            "input_source": "R0_candidate_overlay",
            "evaluator_operator": "all",
            "evaluated_inputs": [
                "same_R_acceptance_and_control_leaves",
                "terminal_evidence",
                "published_dependencies",
                "completion_gate",
                "global_prerequisites",
            ],
            "excluded_output": ["public_S2_task_token"],
            "output_visibility": "registration_R_only",
            "unknown_result": False,
        },
        "R2-owner-claim-dag": {
            "kind": "owner_claim_dag_candidate_evaluation",
            "edge_and_order_source": (
                "evidence_registry.registration_update_contract."
                "proposed_post_R_state.measured_contract"
            ),
            "s1_input_scope": [
                "required_evidence_roots_exact_set_and_hash",
                "owner_claim_exact_set",
            ],
            "s1_forbidden_fields": [
                "claim_dependency_edge_hash",
                "same_R_AC_edge_hash",
                "topological_order_hash",
            ],
            "evaluation_order": "prerequisite_first",
            "same_owner_claim_source": "earlier_candidate_truth_in_R2",
            "same_R_AC_source": "candidate_leaf_truth_from_R1",
            "owner_self_path_source": "candidate_internal_completion_from_R1",
            "cross_owner_source": "published_current_or_S2_truth_only",
            "unevaluated_prerequisite_result": False,
            "consumer_first_result": False,
            "cycle_or_partial_order_result": False,
            "output_visibility": "registration_R_only",
        },
        "R3-atomic-commit": {
            "kind": "exact_atomic_registration_commit",
            "input_operator": "all",
            "required_stage_results": [
                "R0-batch-overlay",
                "R1-leaf-and-task-candidate",
                "R2-owner-claim-dag",
            ],
            "pass_diff": [
                "new_registry_entries_exact_set",
                "owner_closure_history_exact_set",
                "owner_closure_current_snapshot_exact_set",
            ],
            "fail_diff": [],
            "fail_state": "closing",
            "claim_truth_visibility": "after_R_commit",
            "task_token_visibility": "after_S2_only",
            "aggregate_and_program_P_evaluation": "after_R_commit",
        },
    }
    for stage_name, expected in expected_stages.items():
        result.require(contract.get(stage_name) == expected, f"{stage_name}: closed契約不一致")

    actionable = {
        claim["claim_id"]: claim
        for claim in data.get("claims", [])
        if claim.get("adoption") in ACTIONABLE
    }
    dependency_edges: list[str] = []
    cross_owner_edges: list[str] = []
    direct_ac_edges: list[str] = []
    recursive_ac_edges: list[str] = []
    release_context = data.get("release_phase_context", {})
    leaves = data.get("ac_leaf_registry", {})

    def recursive_acs(
        token: str, phase: str | None = None, stack: tuple[str, ...] = ()
    ) -> set[tuple[str, str | None]]:
        if token in stack:
            return set()
        stack = (*stack, token)
        if token in leaves:
            leaf = leaves[token]
            if leaf.get("kind") == "derived_assertion_leaf":
                output: set[tuple[str, str | None]] = set()
                for child in leaf.get("assertion_inputs", []):
                    output |= recursive_acs(child, phase, stack)
                return output
            return {(token, _ac_producer(token, phase))}
        if token in data.get("release_gates", {}):
            selected_phase = release_context.get(token, phase)
            output = set()
            for child in data["release_gates"][token]:
                output |= recursive_acs(child, selected_phase, stack)
            return output
        if token in data.get("task_aliases", {}):
            output = set()
            for child in data["task_aliases"][token].get("task_ids", []):
                output |= recursive_acs(child, phase, stack)
            return output
        if token in data.get("terminal_conditions", {}):
            milestone = data["terminal_conditions"][token]
            children = list(milestone.get("owner_tasks", [])) + list(milestone.get("evidence", []))
            for alternative in milestone.get("alternatives", []):
                children.extend(alternative.get("all", []))
            output = set()
            for child in children:
                output |= recursive_acs(child, phase, stack)
            return output
        # claim、canonical task、acceptance/external/control/context/projectionはatomic stop。
        return set()

    for claim_id, claim in actionable.items():
        owner = claim["closure_owner_task"]
        for dependency in claim.get("dependencies", []):
            if not dependency.startswith("claim:"):
                continue
            prerequisite = actionable.get(dependency.removeprefix("claim:"))
            if not prerequisite:
                continue
            prerequisite_owner = prerequisite.get("closure_owner_task")
            if prerequisite_owner == owner:
                dependency_edges.append(f"{owner}|claim:{claim_id}|{dependency}")
            else:
                cross_owner_edges.append(
                    f"{owner}|claim:{claim_id}|{prerequisite_owner}|{dependency}"
                )
                result.require(
                    TASK_ORDER[str(prerequisite_owner)] < TASK_ORDER[owner],
                    f"{claim_id}: cross-owner prerequisiteが未来owner: {dependency}",
                )
        context = claim.get("ac_evaluation_context", {}) or {}
        for ac_ref in claim.get("ac_refs", []):
            leaf = leaves.get(ac_ref)
            if not leaf:
                continue
            phases = leaf.get("phase_tasks", [])
            producer = context.get(ac_ref) or (phases[0] if len(phases) == 1 else None)
            if producer == owner:
                direct_ac_edges.append(f"{owner}|claim:{claim_id}|{ac_ref}")
        roots = (
            list(claim.get("dependencies", []))
            + list(claim.get("ac_refs", []))
            + [claim["release_gate"]]
        )
        for root in roots:
            if root.startswith("claim:"):
                continue
            phase = context.get(root)
            for ac_ref, producer in recursive_acs(root, phase):
                if producer == owner:
                    recursive_ac_edges.append(f"{owner}|claim:{claim_id}|{ac_ref}")

    measured = contract.get("measured_contract", {})
    expected_dependency_hash = set_sha256(dependency_edges)
    expected_cross_owner_hash = set_sha256(cross_owner_edges)
    expected_direct_ac_hash = set_sha256(direct_ac_edges)
    recursive_ac_edges = sorted(set(recursive_ac_edges))
    expected_recursive_ac_hash = set_sha256(recursive_ac_edges)
    result.require(
        measured.get("same_owner_claim_dependency_edge_count") == len(dependency_edges),
        "proposed-R same-owner dependency edge count不一致",
    )
    result.require(
        measured.get("same_owner_claim_dependency_consumer_count")
        == len({edge.split("|")[1] for edge in dependency_edges}),
        "proposed-R same-owner dependency consumer count不一致",
    )
    result.require(
        measured.get("same_owner_claim_dependency_members_sha256") == expected_dependency_hash,
        "proposed-R same-owner dependency hash不一致",
    )
    result.require(
        measured.get("cross_owner_claim_dependency_edge_count") == len(cross_owner_edges),
        "proposed-R cross-owner dependency edge count不一致",
    )
    result.require(
        measured.get("cross_owner_claim_dependency_consumer_count")
        == len({edge.split("|")[1] for edge in cross_owner_edges}),
        "proposed-R cross-owner dependency consumer count不一致",
    )
    result.require(
        measured.get("cross_owner_claim_dependency_members_sha256") == expected_cross_owner_hash,
        "proposed-R cross-owner dependency hash不一致",
    )
    result.require(
        measured.get("direct_same_R_AC_prerequisite_edge_count") == len(direct_ac_edges),
        "proposed-R direct same-R AC edge count不一致",
    )
    result.require(
        measured.get("direct_same_R_AC_prerequisite_claim_count")
        == len({edge.split("|")[1] for edge in direct_ac_edges}),
        "proposed-R direct same-R AC claim count不一致",
    )
    result.require(
        measured.get("direct_same_R_AC_prerequisite_members_sha256") == expected_direct_ac_hash,
        "proposed-R direct same-R AC hash不一致",
    )
    traversal = measured.get("recursive_same_R_AC_traversal", {})
    result.require(
        traversal.get("root_fields") == ["dependencies", "ac_refs", "release_gate"],
        "proposed-R recursive AC root fields不一致",
    )
    result.require(
        traversal.get("expand_namespaces") == ["release", "task_alias", "milestone", "derived_ac"],
        "proposed-R recursive AC expand namespace不一致",
    )
    result.require(
        traversal.get("stop_namespaces")
        == [
            "claim",
            "canonical_task",
            "acceptance_gate",
            "external",
            "control",
            "control_context",
            "projection",
        ],
        "proposed-R recursive AC stop namespace不一致",
    )
    result.require(
        measured.get("recursive_same_R_AC_prerequisite_edge_count") == len(recursive_ac_edges),
        "proposed-R recursive same-R AC edge count不一致",
    )
    result.require(
        measured.get("recursive_same_R_AC_prerequisite_claim_count")
        == len({edge.split("|")[1] for edge in recursive_ac_edges}),
        "proposed-R recursive same-R AC claim count不一致",
    )
    result.require(
        measured.get("recursive_same_R_AC_prerequisite_members_sha256")
        == expected_recursive_ac_hash,
        "proposed-R recursive same-R AC hash不一致",
    )

    owner_hashes: dict[str, str] = {}
    global_order: list[str] = []
    for owner in sorted(EXPECTED_NORMAL_TASKS):
        nodes = {
            f"claim:{claim_id}"
            for claim_id, claim in actionable.items()
            if claim.get("closure_owner_task") == owner
        }
        indegree = {node: 0 for node in nodes}
        children: dict[str, list[str]] = {node: [] for node in nodes}
        for node in nodes:
            claim = actionable[node.removeprefix("claim:")]
            for dependency in claim.get("dependencies", []):
                if dependency in nodes:
                    indegree[node] += 1
                    children[dependency].append(node)
        ready = [node for node, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        order: list[str] = []
        while ready:
            node = heapq.heappop(ready)
            order.append(node)
            for child in sorted(children[node]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, child)
        result.require(len(order) == len(nodes), f"proposed-R same-owner DAG cycle: {owner}")
        owner_hashes[owner] = sha256("".join(f"{node}\n" for node in order).encode())
        global_order.extend(f"{owner}|{node}" for node in order)
    result.require(
        measured.get("owner_topological_order_sha256") == owner_hashes,
        "proposed-R owner topological hash不一致",
    )
    result.require(
        measured.get("topological_order_members_sha256")
        == sha256("".join(f"{item}\n" for item in global_order).encode()),
        "proposed-R global topological hash不一致",
    )
    result.counts["same_owner_dependency_edge_count"] = len(dependency_edges)
    result.counts["cross_owner_dependency_edge_count"] = len(cross_owner_edges)
    result.counts["direct_same_r_ac_edge_count"] = len(direct_ac_edges)
    result.counts["same_r_ac_edge_count"] = len(recursive_ac_edges)
    result.counts["same_r_claim_consumer_count"] = len(
        {edge.split("|")[1] for edge in recursive_ac_edges}
    )


def _validate_recursive_projections(data: dict[str, Any], result: Result) -> None:
    p_contract = data.get("ac_leaf_contract", {}).get("program_closeout_recursive_projection", {})
    roots = [
        "ac-profile:N-597",
        "ac:N-597:AC-01",
        "ac:N-597:AC-02",
        "ac:N-597:AC-03",
        "ac:N-597:AC-04",
        "task:N-597",
        "release:N-597-program-closeout-P",
    ]
    expected_p_keys = {
        "kind",
        "evaluation_context",
        "roots",
        "recursive_namespaces",
        "target",
        "replacement",
        "transform_operation",
        "target_selector",
        "non_target_segment_policy",
        "consumer_scope",
        "original_path_count",
        "original_path_members_sha256",
        "transformed_path_count",
        "transformed_path_members_sha256",
        "canonical_serialization",
        "evaluation_operator",
        "transformed_path_source",
        "failure_conditions",
        "failure_result",
    }
    result.require(
        set(p_contract) == expected_p_keys,
        "program P projection closed field集合不一致",
    )
    result.require(
        p_contract.get("kind") == "program_P_recursive_internal_completion_projection",
        "program P projection kind不一致",
    )
    result.require(
        p_contract.get("evaluation_context") == "phase:N-597-program-closeout-P",
        "program P evaluation context不一致",
    )
    result.require(p_contract.get("roots") == roots, "program P recursive roots不一致")
    result.require(
        p_contract.get("recursive_namespaces")
        == ["release", "task_alias", "milestone", "claim", "derived_ac"],
        "program P recursive namespace不一致",
    )
    result.require(p_contract.get("target") == "task:N-604", "program P target不一致")
    result.require(
        p_contract.get("replacement") == "projection:N-604-INTERNAL-COMPLETION-WITHOUT-S2",
        "program P replacement不一致",
    )
    result.require(
        p_contract.get("transform_operation") == "replace_terminal_target_exactly_once"
        and p_contract.get("target_selector") == "path_terminal_equals_target"
        and p_contract.get("non_target_segment_policy") == "byte_identical"
        and p_contract.get("consumer_scope") == "phase:N-597-program-closeout-P_only",
        "program P transform契約不一致",
    )
    result.require(
        p_contract.get("canonical_serialization")
        == "utf8_bytewise_sorted_lf_terminated_root_to_target_path_v1"
        and p_contract.get("evaluation_operator") == "all"
        and p_contract.get("transformed_path_source")
        == "program_closeout_event_exact_set_and_hash",
        "program P path evaluator契約不一致",
    )
    result.require(
        p_contract.get("failure_conditions")
        == [
            "original_path_set_empty",
            "target_replacement_count_not_exactly_one_per_path",
            "target_remains_after_transform",
            "replacement_added_outside_target_terminal",
            "non_target_segment_changed",
            "consumer_outside_program_P",
        ]
        and p_contract.get("failure_result") is False,
        "program P fail-close条件不一致",
    )
    program_paths = _recursive_paths(data, roots, "task:N-604")
    result.require(
        p_contract.get("original_path_count") == len(program_paths),
        "program P recursive path count不一致",
    )
    result.require(
        p_contract.get("original_path_members_sha256") == set_sha256(program_paths),
        "program P recursive path hash不一致",
    )
    result.require(bool(program_paths), "program PからN604へのpathが0件")
    replacement = str(p_contract.get("replacement"))
    transformed_program_paths: set[str] = set()
    for path in program_paths:
        segments = path.split(" -> ")
        result.require(
            segments[-1] == "task:N-604" and segments.count("task:N-604") == 1,
            f"program P pathのtargetがterminal exact 1件でない: {path}",
        )
        transformed = [*segments[:-1], replacement]
        result.require(
            transformed[:-1] == segments[:-1],
            f"program P transformが非target segmentを変更: {path}",
        )
        result.require(
            transformed.count("task:N-604") == 0 and transformed.count(replacement) == 1,
            f"program P transformのtarget/replacement数不正: {path}",
        )
        transformed_program_paths.add(" -> ".join(transformed))
    result.require(
        p_contract.get("transformed_path_count") == len(transformed_program_paths),
        "program P transformed path count不一致",
    )
    result.require(
        p_contract.get("transformed_path_members_sha256") == set_sha256(transformed_program_paths),
        "program P transformed path hash不一致",
    )

    owner_contract = (
        data.get("evidence_registry", {})
        .get("registration_update_contract", {})
        .get("owner_self_dependency_projection", {})
    )
    expected_owner_keys = {
        "kind",
        "input_fields",
        "recursive_namespaces",
        "canonical_serialization",
        "transform_operation",
        "target_selector",
        "replacement_serialization",
        "non_target_segment_policy",
        "empty_path_behavior",
        "nonempty_path_behavior",
        "failure_conditions",
        "all_owner_fixture_contract",
        "visibility",
        "cross_owner_task_source",
        "control_dependency_source",
    }
    result.require(
        set(owner_contract) == expected_owner_keys,
        "owner self projection closed field集合不一致",
    )
    result.require(
        owner_contract.get("kind") == "owner_R_recursive_self_dependency_projection"
        and owner_contract.get("input_fields") == ["dependencies", "ac_refs", "release_gate"]
        and owner_contract.get("recursive_namespaces")
        == ["release", "task_alias", "milestone", "claim", "derived_ac"],
        "owner self projection入力契約不一致",
    )
    result.require(
        owner_contract.get("canonical_serialization")
        == "utf8_bytewise_sorted_lf_terminated_owner_consumer_path_v1"
        and owner_contract.get("transform_operation") == "replace_terminal_target_exactly_once"
        and owner_contract.get("target_selector") == "path_terminal_equals_closure_owner_task"
        and owner_contract.get("replacement_serialization")
        == "candidate-internal-completion:<owner-task>"
        and owner_contract.get("non_target_segment_policy") == "byte_identical",
        "owner self projection transform契約不一致",
    )
    result.require(
        owner_contract.get("empty_path_behavior") == "evaluate_original_all_of_without_projection"
        and owner_contract.get("nonempty_path_behavior")
        == "transform_all_canonical_paths_then_evaluate_all_of"
        and owner_contract.get("visibility") == "owner_registration_R_only"
        and owner_contract.get("cross_owner_task_source") == "published_S2_truth_only"
        and owner_contract.get("control_dependency_source")
        == "current_or_ancestor_unsuperseded_evidence_only",
        "owner self projection評価/visibility契約不一致",
    )
    result.require(
        owner_contract.get("failure_conditions")
        == [
            "projection_on_empty_path_set",
            "target_replacement_count_not_exactly_one_per_path",
            "missing_or_extra_path",
            "before_or_after_path_hash_mismatch",
            "non_target_segment_changed",
            "replacement_visible_outside_owner_R",
        ],
        "owner self projection fail-close条件不一致",
    )
    fixtures = owner_contract.get("all_owner_fixture_contract", {})
    result.require(
        set(fixtures)
        == {
            "applicable_tasks",
            "owner_claim_count",
            "owner_claim_members_sha256",
            "nonempty_claim_count",
            "nonempty_claim_members_sha256",
            "empty_claim_count",
            "empty_claim_members_sha256",
            "exact_partition_rule",
            "required_empty_path_negative_fixtures",
            "required_nonempty_path_claims",
            "nonempty_path_count",
            "nonempty_path_members_sha256",
            "transformed_path_count",
            "transformed_path_members_sha256",
        },
        "owner projection fixture closed field集合不一致",
    )
    result.require(
        fixtures.get("applicable_tasks") == list(EXPECTED_NORMAL_TASKS),
        "owner projectionが9通常task全体に適用されていない",
    )
    claims = {f"claim:{claim['claim_id']}": claim for claim in data.get("claims", [])}
    owner_paths: dict[str, set[str]] = {}
    for token, claim in claims.items():
        owner = claim.get("closure_owner_task")
        if owner not in EXPECTED_NORMAL_TASKS:
            continue
        roots_for_claim = (
            list(claim.get("dependencies", []))
            + list(claim.get("ac_refs", []))
            + [claim["release_gate"]]
        )
        paths = _recursive_paths(data, roots_for_claim, str(owner))
        if paths:
            owner_paths[token] = {f"{owner}|{token}|{path}" for path in paths}
    nonempty = set(owner_paths)
    owner_claims = {
        token
        for token, claim in claims.items()
        if claim.get("closure_owner_task") in EXPECTED_NORMAL_TASKS
    }
    empty = owner_claims - nonempty
    result.require(
        fixtures.get("owner_claim_count") == len(owner_claims)
        and fixtures.get("owner_claim_members_sha256") == set_sha256(owner_claims),
        "all owner claim exact集合不一致",
    )
    result.require(
        fixtures.get("nonempty_claim_count") == len(nonempty)
        and fixtures.get("nonempty_claim_members_sha256") == set_sha256(nonempty),
        "all owner nonempty self-path partition不一致",
    )
    result.require(
        fixtures.get("empty_claim_count") == len(empty)
        and fixtures.get("empty_claim_members_sha256") == set_sha256(empty),
        "all owner empty self-path partition不一致",
    )
    result.require(
        nonempty.isdisjoint(empty) and nonempty | empty == owner_claims,
        "all owner self-path partitionがdisjoint exactでない",
    )
    result.require(
        fixtures.get("exact_partition_rule") == "disjoint_union_equals_all_owner_claims",
        "all owner self-path partition rule不一致",
    )
    result.require(
        set(fixtures.get("required_nonempty_path_claims", [])) == nonempty,
        "all owner self-path nonempty claim集合不一致",
    )
    required_empty_fixtures = [
        "claim:POS-010",
        "claim:EXCL-002",
        "claim:EXCL-004",
        "claim:STATE-004",
        "claim:PROGRAM-REQ-001",
        "claim:ORDER-001",
        "claim:SOURCE-MANIFEST-001",
        "claim:SAFETY-001",
        "claim:SAFETY-002",
        "claim:SAFETY-003",
        "claim:FINAL-002",
    ]
    result.require(
        fixtures.get("required_empty_path_negative_fixtures") == required_empty_fixtures,
        "all owner required empty-path fixture集合不一致",
    )
    for token in required_empty_fixtures:
        claim = claims.get(token, {})
        roots_for_claim = (
            list(claim.get("dependencies", []))
            + list(claim.get("ac_refs", []))
            + ([claim["release_gate"]] if claim.get("release_gate") else [])
        )
        result.require(
            not _recursive_paths(data, roots_for_claim, str(claim.get("closure_owner_task"))),
            f"owner empty self-path fixtureにpathがある: {token}",
        )
    encoded_paths = set().union(*owner_paths.values()) if owner_paths else set()
    result.require(
        fixtures.get("nonempty_path_count") == len(encoded_paths),
        "all owner self-path count不一致",
    )
    result.require(
        fixtures.get("nonempty_path_members_sha256") == set_sha256(encoded_paths),
        "all owner self-path hash不一致",
    )
    transformed_owner_paths: set[str] = set()
    for encoded in encoded_paths:
        prefix, path = encoded.split("|", 2)[:2], encoded.split("|", 2)[2]
        owner, claim_token = prefix
        segments = path.split(" -> ")
        result.require(
            segments[-1] == owner and segments.count(owner) == 1,
            f"owner self pathのtargetがterminal exact 1件でない: {encoded}",
        )
        replacement_terminal = f"candidate-internal-completion:{owner}"
        transformed = [*segments[:-1], replacement_terminal]
        result.require(
            transformed[:-1] == segments[:-1]
            and transformed.count(owner) == 0
            and transformed.count(replacement_terminal) == 1,
            f"owner self path transform不正: {encoded}",
        )
        transformed_owner_paths.add(f"{owner}|{claim_token}|{' -> '.join(transformed)}")
    result.require(
        fixtures.get("transformed_path_count") == len(transformed_owner_paths),
        "all owner transformed path count不一致",
    )
    result.require(
        fixtures.get("transformed_path_members_sha256") == set_sha256(transformed_owner_paths),
        "all owner transformed path hash不一致",
    )
    result.counts["program_p_recursive_path_count"] = len(program_paths)
    result.counts["owner_self_path_claim_count"] = len(nonempty)
    result.counts["owner_self_path_count"] = len(encoded_paths)
    result.counts["n604_owner_self_path_count"] = len(
        {path for path in encoded_paths if path.startswith("task:N-604|")}
    )


def _validate_program_and_control(data: dict[str, Any], result: Result) -> None:
    tasks = data.get("task_catalog", {})
    result.require(
        tasks.get("task:N-582", {}).get("kind") == "control_program_aggregate",
        "N582がcontrol aggregateでない",
    )
    result.require(
        tasks.get("task:N-597", {}).get("kind") == "program_derived_aggregate",
        "N597がderived aggregateでない",
    )
    result.require(
        tasks.get("task:N-602", {}).get("kind") == "umbrella_aggregate", "N602がumbrellaでない"
    )
    result.require(
        "release:N-597-program-closeout-P" not in tasks["task:N-597"].get("assertion_inputs", []),
        "N597 outputがinputへ自己参照する",
    )
    for claim in data.get("claims", []):
        result.require(
            not any(token.startswith("ac:N-597:") for token in claim.get("ac_refs", [])),
            f"{claim['claim_id']}: actionable graphがN597 derived ACを逆参照",
        )
    schemas = data.get("artifact_schemas", {})
    support = data.get("artifact_support_contracts", {}).get("program_closeout_event", {})
    result.require("schema:program-closeout-event/v1" in schemas, "program closeout schemaがない")
    program_schema = schemas.get("schema:program-closeout-event/v1", {})
    expected_program_fields = [
        "schema",
        "event_id",
        "program_id",
        "evaluation_parent_head",
        "evaluated_at",
        "n604_internal_completion",
        "component_tasks",
        "actionable_claims",
        "derived_ac_results",
        "safety_and_order_results",
        "original_recursive_paths",
        "original_recursive_path_count",
        "original_recursive_paths_sha256",
        "transformed_recursive_paths",
        "transformed_recursive_path_count",
        "transformed_recursive_paths_sha256",
        "replacement_count",
        "nonterminal_segments_unchanged",
        "overall_result",
        "blocking_tokens",
        "writer_binding",
        "validator_binding",
        "corrects_event_id",
        "disposition",
        "old_binding_digest",
        "new_binding_digest",
    ]
    result.require(
        program_schema.get("additional_properties") is False
        and program_schema.get("required_fields") == expected_program_fields,
        "program closeout event closed field集合不一致",
    )
    result.require(
        set(program_schema.get("field_constraints", {})) == set(expected_program_fields),
        "program closeout event field constraint集合不一致",
    )
    projection_text = str(program_schema.get("binding_projection", ""))
    for field_name in expected_program_fields:
        if field_name in {"event_id", "new_binding_digest"}:
            continue
        result.require(
            field_name in projection_text,
            f"program closeout binding projection欠損: {field_name}",
        )
    result.require(
        bool(support.get("writer_path")) and bool(support.get("validator_path")),
        "P writer/validator bindingがない",
    )
    result.require("projection_resolution" in support, "P eventにN604 projection動的解決がない")
    result.require(
        support.get("recursive_path_resolution")
        == {
            "kind": "program_P_event_recursive_path_binding",
            "roots_source": "ac_leaf_contract.program_closeout_recursive_projection.roots",
            "original_fields": [
                "original_recursive_paths",
                "original_recursive_path_count",
                "original_recursive_paths_sha256",
            ],
            "transformed_fields": [
                "transformed_recursive_paths",
                "transformed_recursive_path_count",
                "transformed_recursive_paths_sha256",
            ],
            "transform_operation": "replace_terminal_target_exactly_once",
            "target": "task:N-604",
            "replacement": "projection:N-604-INTERNAL-COMPLETION-WITHOUT-S2",
            "replacement_count_field": "replacement_count",
            "nonterminal_invariant_field": "nonterminal_segments_unchanged",
            "required_nonterminal_invariant": True,
            "evaluation_operator": "all",
            "failure_result": False,
        },
        "program closeout event recursive path binding不一致",
    )
    projections = data.get("internal_task_completion_projections", {})
    projection_id = "projection:N-604-INTERNAL-COMPLETION-WITHOUT-S2"
    result.require(set(projections) == {projection_id}, "N604 internal projectionが一意でない")
    if projection_id in projections:
        projection = projections[projection_id]
        n604 = tasks["task:N-604"]
        result.require(
            projection.get("required_dependencies") == n604.get("dependencies"),
            "N604 projection dependencies不一致",
        )
        result.require(
            projection.get("required_scope_ac_refs") == n604.get("scope_ac_refs"),
            "N604 projection scope不一致",
        )
        result.require(
            projection.get("required_terminal_evidence_types")
            == n604.get("required_terminal_evidence_types"),
            "N604 projection terminal types不一致",
        )
        result.require(
            projection.get("excluded") == ["task:N-604の公開済みS2 tokenだけ"],
            "N604 projectionがS2以外を除外",
        )
        result.require(
            projection.get("verified_projection_evidence") == [],
            "N604 projection reserved evidenceが空でない",
        )
    result.require(
        data.get("release_gates", {}).get("release:N-604-internal-completion-without-S2")
        == [projection_id],
        "N604 internal wrapperがprojection exact 1件でない",
    )
    result.require(
        projection_id in data.get("release_gates", {}).get("release:N-597-program-closeout-P", []),
        "program PがN604 projectionを参照しない",
    )
    cue = data.get("control_unit_catalog", {})
    contexts = data.get("control_context_catalog", {})
    result.require(len(cue) == 6 and len(contexts) == 6, "CUE/control contextが6件exactでない")
    for control, descriptor in cue.items():
        result.require(descriptor.get("control_sequence") == 1, f"{control}: 初回sequenceが1でない")
        roots = descriptor.get("root_descriptors", [])
        result.require(
            [root.get("root_sequence") for root in roots] == list(range(1, len(roots) + 1)),
            f"{control}: root_sequence gap",
        )
        result.require(
            descriptor.get("root_descriptor_count") == len(roots), f"{control}: root count不一致"
        )
        for root in roots:
            subject = root["subject_id"]
            phase = root.get("phase_or_context_id")
            if subject.startswith(("gate:", "milestone:")) and subject not in {
                "gate:RESEARCH-SAFETY-AC",
                "gate:SAFETY-AC-01",
                "gate:SAFETY-AC-02",
                "gate:SAFETY-AC-03",
            }:
                result.require(phase is None, f"{control}: scalar rootがcontextを持つ: {subject}")
    freeze = data.get("release_gates", {}).get("release:R025-freeze-audit", [])
    result.require(
        freeze == ["release:R025-FREEZE-ALL", "gate:R025-FREEZE-INDEPENDENT-ACCEPTANCE"],
        "freeze auditが10条件+独立受入でない",
    )
    for gate in (
        "gate:R025-FREEZE-INDEPENDENT-ACCEPTANCE",
        "gate:R025-IMPLEMENTATION-MANIFEST",
        "gate:R025-IMPLEMENTATION-TESTS",
        "gate:R025-IMPLEMENTATION-NO-LOOK",
        "gate:R025-IMPLEMENTATION-INDEPENDENT-ACCEPTANCE",
    ):
        result.require(
            data["acceptance_gates"][gate].get("required_evidence_type")
            == "evidence-type:control_leaf_result",
            f"{gate}: proof typeがcontrol_leaf_resultでない",
        )


def _validate_semantic_refinements(data: dict[str, Any], result: Result) -> None:
    claims = {claim["claim_id"]: claim for claim in data.get("claims", [])}

    def text(claim_id: str, *fields: str) -> str:
        claim = claims.get(claim_id, {})
        output: list[str] = []
        for field_name in fields:
            value = claim.get(field_name, "")
            output.extend(str(item) for item in value) if isinstance(
                value, list
            ) else output.append(str(value))
        return "\n".join(output)

    required_phrases = {
        "STATE-005": ("immutable snapshot", "最新lineage", "回帰させない"),
        "CS-REQ-002": ("append-only history", "live head", "戻さない"),
        "CS-REQ-003": ("research resume", "live head", "固定値として再掲しない"),
        "FINAL-002": (
            "append-only transition history",
            "N-602A→N-598C→N-594B→R-025",
            "回帰させない",
        ),
        "GAP-006": ("unavailable理由", "next review", "改善"),
        "PREV-011": ("unavailable理由", "next review", "改善"),
        "RETIRE-REQ-001": ("before unavailable", "next review", "改善を主張せずrevised"),
        "ORCH-METRIC-REQ-001": ("unavailable理由", "next review", "改善"),
        "IMP-REQ-007": ("due/decidable", "少なくとも1件", "未確認"),
        "VERDICT-001": ("implemented_verified", "valid_gated_residual", "program-closeout P"),
        "EVI-CAUSE-001": ("Phase B", "4経路", "Phase A"),
        "PREV-010": ("Phase B", "4経路", "全経路"),
        "EVI-REQ-004": ("closed one_of", "summaryだけ", "両方が空"),
        "EVI-REQ-007": ("closed one_of", "chosen_route", "両route選択"),
        "PROGRAM-REQ-001": ("3作業束", "typed exact mapping", "missing/extra/duplicate"),
        "COMPUTE-REQ-003": ("--candidate-manifest", "--check-reservation R-025", "AC-08"),
        "EVI-REQ-009": ("dedupe-key", "unique terminal/event ID", "同じsource event"),
        "POS-009": ("materialization_head snapshot", "outcome未閲覧"),
        "COMPUTE-FACT-001": ("materialization_head snapshot",),
        "COMPUTE-FACT-002": ("materialization snapshot",),
        "PORTFOLIO-FACT-001": ("materialization_head snapshot",),
        "RESEARCH-POS-001": ("draft→frozen→reserved", "no-look evidence"),
        "COMPLETE-001": ("自身はRで初めてregistry-backed", "R後のprogram-closeout P"),
    }
    for claim_id, phrases in required_phrases.items():
        joined = text(
            claim_id,
            "summary",
            "current_state",
            "positive_tests",
            "negative_tests",
            "residual_risk",
        )
        for phrase in phrases:
            result.require(phrase in joined, f"{claim_id}: semantic refinement欠損: {phrase}")

    result.require(
        claims.get("STATE-002", {}).get("adoption") == "non_requirement",
        "STATE-002がimmutable snapshot factへ再分類されていない",
    )
    result.require(
        claims.get("R025-FACT-001", {}).get("adoption") == "adopt_with_refinement"
        and "market-anchored" in text("R025-FACT-001", "summary", "current_state"),
        "R025-FACT-001のmarket-anchored refinement欠損",
    )
    result.require(
        claims.get("PR-RA4-03", {}).get("adoption") == "adopt_with_refinement"
        and claims.get("PR-RA4-03", {}).get("release_gate") == "release:RA4-PR3_then_PR4",
        "PR-RA4-03のN600/N601分割refinement欠損",
    )
    exact_ac_refs = {
        "EVI-FACT-001": {"ac:N-602:AC-01", "ac:N-602:AC-02", "ac:N-602:AC-06"},
        "EVI-FACT-002": {"ac:N-602:AC-02", "ac:N-602:AC-03", "ac:N-602:AC-06"},
        "EVI-REQ-005": {"ac:N-602:AC-01", "ac:N-602:AC-06"},
    }
    for claim_id, expected in exact_ac_refs.items():
        result.require(
            set(claims.get(claim_id, {}).get("ac_refs", [])) == expected,
            f"{claim_id}: evidence P0 AC意味mapping不一致",
        )

    bundle = data.get("source_bundle_refinement", {})
    expected_bundles = [
        ("source-bundle-01-rca-ledger-validator", ["task:N-600", "task:N-601"]),
        (
            "source-bundle-02-recurrence-evidence-generator",
            ["task:N-601", "task:N-602A", "task:N-602B"],
        ),
        ("source-bundle-03-metrics-retirement", ["task:N-603", "task:N-604"]),
    ]
    actual_bundles = [
        (item.get("bundle_id"), item.get("refined_units")) for item in bundle.get("bundles", [])
    ]
    result.require(actual_bundles == expected_bundles, "source 3 bundle refinement mapping不一致")
    members = [f"{bundle_id}|{unit}" for bundle_id, units in expected_bundles for unit in units]
    result.require(bundle.get("bundle_count") == 3, "source bundle count不一致")
    result.require(bundle.get("refined_member_count") == 7, "source bundle member count不一致")
    result.require(
        bundle.get("refined_members_sha256") == set_sha256(members),
        "source bundle mapping hash不一致",
    )


def _validate_temporal_contract(data: dict[str, Any], result: Result) -> None:
    contract = data.get("claim_temporal_contract", {})
    result.require(contract.get("version") == 1, "claim temporal contract version不一致")
    expected_groups: dict[str, list[str]] = {
        "snapshot_only": [],
        "current_to_pre_freeze": [],
        "downstream_future_closure": [],
    }
    pre_freeze_owners = {"task:N-602A", "task:N-598C", "task:N-594B"}
    all_claims: set[str] = set()
    for claim in data.get("claims", []):
        token = f"claim:{claim['claim_id']}"
        all_claims.add(token)
        if claim.get("adoption") == "non_requirement":
            category = "snapshot_only"
        elif claim.get("closure_owner_task") in pre_freeze_owners:
            category = "current_to_pre_freeze"
        else:
            category = "downstream_future_closure"
        expected_groups[category].append(token)

    categories = contract.get("categories", {})
    result.require(set(categories) == set(expected_groups), "claim temporal category集合不一致")
    seen: set[str] = set()
    for category, expected in expected_groups.items():
        entry = categories.get(category, {})
        members = entry.get("expected_members", [])
        result.require(members == expected, f"claim temporal {category} member順序不一致")
        result.require(
            entry.get("expected_count") == len(expected),
            f"claim temporal {category} count不一致",
        )
        result.require(
            entry.get("expected_members_sha256") == set_sha256(expected),
            f"claim temporal {category} hash不一致",
        )
        result.require(not seen.intersection(members), f"claim temporal {category}に重複")
        seen.update(members)
    result.require(seen == all_claims, "claim temporal 3分類が全179 claimのexact partitionでない")
    result.counts["snapshot_only_claim_count"] = len(expected_groups["snapshot_only"])
    result.counts["current_to_pre_freeze_claim_count"] = len(
        expected_groups["current_to_pre_freeze"]
    )
    result.counts["downstream_future_claim_count"] = len(
        expected_groups["downstream_future_closure"]
    )


def _validate_paths(data: dict[str, Any], result: Result) -> None:
    refs: set[str] = set()
    for claim in data.get("claims", []):
        for key in ("current_evidence", "design_refs", "spec_refs", "plan_refs"):
            refs.update(value for value in claim.get(key, []) if isinstance(value, str))
    for profile in data.get("ac_evaluator_profiles", {}).values():
        refs.add(profile.get("canonical_spec", ""))
    for leaf in data.get("ac_leaf_registry", {}).values():
        refs.add(leaf.get("canonical_anchor", ""))
        refs.add(leaf.get("evaluator_source", ""))
    cache: dict[str, set[str]] = {}
    for ref in sorted(refs):
        if not ref or ref.startswith(
            ("claim:", "claims:", "source:", "local-session:", "RA4-", "EVI-", "CS-", "IMP-")
        ):
            continue
        path_part, _, anchor = ref.partition("#")
        if not any(
            path_part.endswith(extension) for extension in (".md", ".yml", ".yaml", ".json", ".py")
        ):
            continue
        path = result.root / path_part
        result.require(path.is_file(), f"local pathがない: {path_part}")
        if anchor and path.is_file() and path.suffix == ".md":
            anchors = cache.setdefault(
                path_part, _markdown_anchors(path.read_text(encoding="utf-8"))
            )
            result.require(anchor in anchors, f"Markdown anchorがない: {ref}")


def _validate_embedded_evidence_hashes(text: str, result: Result) -> None:
    paths = {
        "manifest": result.root / MANIFEST_REL,
        "validator source": result.root / VALIDATOR_REL,
        "validator test source": result.root / VALIDATOR_TEST_REL,
    }
    for label, path in paths.items():
        result.require(path.is_file(), f"{label}がない: {path.relative_to(result.root)}")
        if path.is_file():
            expected = sha256(path.read_bytes())
            result.require(expected in text, f"報告書の{label} SHAが実ファイルと不一致")


def _validate_report(data: dict[str, Any], result: Result, allow_pending: bool) -> None:
    path = result.root / REPORT_REL
    result.require(path.is_file(), f"報告書がない: {REPORT_REL}")
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    required_phrases = (
        SOURCE_SHA256,
        "文書化≠実装",
        "C-001〜C-007",
        "146351.16153754198",
        "53648.83846245802",
        "41.00003754198",
        "tests/scripts/test_review_report_gate.py",
        "git_last_settled_round=35",
        "local_session_resume_round=36",
        "remediation_applied=false",
        "market-anchored",
        "145",
        "68",
        "440",
        "task:R-025",
        "10条件+独立受入",
        "recursive same-R AC 998 edge／115 claim",
        "snapshot-only 34 / current-to-pre-freeze 59 / downstream-future 86",
        "all owner self-path 12 claim / 16 path",
        "event pre/post path binding欠損0",
        "独立した人手再監査",
        "追跡済みsubset",
        "scripts/ai/validate_audit_materialization.py",
    )
    for phrase in required_phrases:
        result.require(phrase in text, f"報告書の必須記述がない: {phrase}")
    _validate_embedded_evidence_hashes(text, result)
    result.require("巡36前" not in text, "報告書に旧current-state表現『巡36前』が残る")
    result.require(
        "146 actionable" not in text and "actionable 146" not in text,
        "報告書に旧actionable件数が残る",
    )
    if not allow_pending:
        result.require(
            data.get("coverage", {}).get("status") == "pass", "coverage.statusがpassでない"
        )


def validate(root: Path = ROOT, *, allow_pending: bool = False) -> Result:
    result = Result(root=root.resolve())
    manifest_path = result.root / MANIFEST_REL
    report_path = result.root / REPORT_REL
    result.require(manifest_path.is_file(), f"manifestがない: {MANIFEST_REL}")
    if not manifest_path.is_file():
        return result
    data = _load_yaml(manifest_path, result)
    if not data:
        return result
    result.hashes["manifest_sha256"] = sha256(manifest_path.read_bytes())
    if report_path.is_file():
        result.hashes["report_sha256"] = sha256(report_path.read_bytes())
    result.hashes["validator_sha256"] = sha256(Path(__file__).read_bytes())
    _validate_source(data, result)
    _validate_claims(data, result)
    _validate_ac(data, result)
    _validate_graph_and_tokens(data, result)
    _validate_phase_and_owner(data, result)
    _validate_proposed_post_r(data, result)
    _validate_recursive_projections(data, result)
    _validate_program_and_control(data, result)
    _validate_semantic_refinements(data, result)
    _validate_temporal_contract(data, result)
    _validate_paths(data, result)
    _validate_report(data, result, allow_pending)
    coverage = data.get("coverage", {})
    for field_name in (
        "uncovered_actionable_leaf_count",
        "duplicate_claim_id_count",
        "required_field_gap_count",
        "missing_local_path_count",
        "missing_markdown_anchor_count",
        "unresolved_ac_count",
        "unresolved_dependency_or_release_token_count",
        "ambiguous_control_token_count",
        "cross_namespace_identifier_collision_count",
        "empty_evaluator_node_count",
        "control_graph_cycle_count",
        "atomic_ac_predicate_gap_count",
        "atomic_ac_source_coverage_gap_count",
        "actionable_owner_gap_count",
    ):
        result.require(coverage.get(field_name) == 0, f"coverage.{field_name}が0でない")
    return result


def _output(result: Result, as_json: bool) -> None:
    payload = {
        "status": "pass" if not result.errors else "fail",
        "errors": result.errors,
        "counts": result.counts,
        "hashes": result.hashes,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        return
    if result.errors:
        for error in result.errors:
            print(f"FAIL: {error}", file=sys.stderr)
        print(
            f"audit materialization validation: FAIL ({len(result.errors)} errors)", file=sys.stderr
        )
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        print("audit materialization validation: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--allow-pending", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = validate(args.root, allow_pending=args.allow_pending)
    _output(result, args.json)
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
