#!/usr/bin/env python3
"""materialization manifest の `evidence_registry.entries` を artifact 実体と照合する CLI。

DEC-20260815-003（B+）と HRAI-REAUDIT-20260815 P0-01（PR-E3b）。peer 助言「registry key の
存在だけで truth にする経路が残る。artifact を指定 commit の `git show <ref>:<path>` から読み、
blob hash・schema・subject・head を検証する。working tree の path だけを読まない」の実装で
ある。各 entry について次を検査し、entry ごとの conformance と全体の errors／warnings を JSON
report へ出す。

(a) closed field: `schema:evidence-registry-entry/v1` の 15 field exact 集合と型（hex40／hex64／
    enum／positive integer）。`subject_id` prefix と `subject_kind` の対応。
(b) `artifact_schema` の解決: manifest `artifact_schemas` **または** validator source schema
    （`scripts/ai/claim_closure.py` の `schema:claim-closure/v2`／`schema:gated-residual/v2`）。
    どちらにも無ければ error。`evidence_type` は manifest `terminal_evidence_types` へ解決する。
(c) blob: `git show <postmerge_mapping.actual_evidence_anchor_head>:<artifact_path>` の raw bytes
    SHA-256 が `raw_sha256` と一致し、評価 head（既定 HEAD）の同 path blob も byte 一致する
    （append-only・差替え検出）。actual terminal／anchor が評価 head の first-parent 上にあり
    terminal <= anchor であることを検査する。premerge 側（tested_head／evidence_anchor_head）は
    commit がローカルに存在する場合だけ祖先関係と blob を検査し、無ければ warning（rebase merge
    後は PR branch commit が取得されないことがある）。
(d) artifact parse（duplicate key 拒否・format は拡張子）→ 宣言 schema の required／prohibited／
    closed 照合、`evidence_id == key`（artifact に evidence_id がある場合）、head 束縛 adapter:
    `n602-v2`: `head_sha`／`correction-v2`: `current_head_sha`／`review-v1`:
    `subject_pr.reviewed_commit` 優先・`head_sha` 次点／`claim-closure-v2`・`gated-residual-v2`:
    `heads.implementation_head`／その他: 導出不能。候補のいずれかが
    `premerge_pair.tested_head` と一致すれば束縛成立。conformance は
    `strict`（宣言 schema へ完全適合 ∧ head 束縛成立）／`legacy_binding`（宣言 schema へ
    不適合だが head 束縛成立＝旧登録）／`none`（head 束縛を導出できない・不一致）。
    `none` は error、`legacy_binding` は warning。
(e) `correction_of` chain（`closeout_expectation.registry_current_entries_and_issues`）: 複数
    original／branch／cycle／dangling／別 slot を error。

exit code: 既定は JSON report を出して常に 0。`--strict` のときだけ errors 非空で 1。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import claim_closure, independent_acceptance  # noqa: E402
from scripts.ai import closeout_expectation as expectation  # noqa: E402
from scripts.ai.evidence_contract import canonical_content_sha256  # noqa: E402

MANIFEST_REL: Final = "docs/audits/audit-materialization-manifest-2026-08-12.yml"
REGISTRY_ENTRY_SCHEMA: Final = "schema:evidence-registry-entry/v1"

ENTRY_FIELDS: Final = (
    "artifact_schema",
    "artifact_schema_definition_sha256",
    "subject_kind",
    "subject_id",
    "phase_or_context_id",
    "subject_leaf_definition_sha256",
    "evidence_type",
    "evidence_type_definition_sha256",
    "artifact_path",
    "raw_sha256",
    "premerge_pair",
    "postmerge_mapping",
    "correction_of",
    "disposition",
    "status",
)
PREMERGE_FIELDS: Final = ("tested_head", "evidence_anchor_head")
POSTMERGE_FIELDS: Final = (
    "merged_pr",
    "merge_method",
    "actual_merge_terminal_sha",
    "actual_evidence_anchor_head",
    "n598b_mapping_evidence_ref",
    "n598b_mapping_evidence_raw_sha256",
)
SUBJECT_KIND_PREFIX: Final[Mapping[str, str]] = {
    "claim": "claim:",
    "task": "task:",
    "ac": "ac:",
    "gate": "gate:",
    "external": "external:",
    "milestone": "milestone:",
    "control_context": "control-context:",
}
MERGE_METHODS: Final = frozenset({"rebase", "squash", "merge"})
DISPOSITIONS: Final = frozenset({"supersedes", "invalidates"})
CONFORMANCE_LEVELS: Final = ("strict", "legacy_binding", "none")

ARTIFACT_KIND_N602_V2: Final = "n602-v2"
ARTIFACT_KIND_CORRECTION_V2: Final = "correction-v2"
ARTIFACT_KIND_REVIEW_V1: Final = "review-v1"
ARTIFACT_KIND_CLAIM_CLOSURE_V2: Final = "claim-closure-v2"
ARTIFACT_KIND_GATED_RESIDUAL_V2: Final = "gated-residual-v2"
ARTIFACT_KIND_UNKNOWN: Final = "unknown"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_hex40(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)
    )


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


class _Git:
    """repo_root に対する読み取り専用 git 呼び出し（結果を memo する）。"""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._blob_cache: dict[tuple[str, str], bytes | None] = {}
        self._commit_cache: dict[str, bool] = {}
        self._first_parent: dict[str, list[str] | None] = {}

    def _run(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            capture_output=True,
            check=False,
        )

    def resolve(self, rev: str) -> str | None:
        completed = self._run("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
        if completed.returncode != 0:
            return None
        return completed.stdout.decode().strip() or None

    def commit_exists(self, sha: str) -> bool:
        if sha not in self._commit_cache:
            completed = self._run("cat-file", "-e", f"{sha}^{{commit}}")
            self._commit_cache[sha] = completed.returncode == 0
        return self._commit_cache[sha]

    def show_blob(self, commit: str, path: str) -> bytes | None:
        key = (commit, path)
        if key not in self._blob_cache:
            completed = self._run("show", f"{commit}:{path}")
            self._blob_cache[key] = completed.stdout if completed.returncode == 0 else None
        return self._blob_cache[key]

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        completed = self._run("merge-base", "--is-ancestor", ancestor, descendant)
        return completed.returncode == 0

    def first_parent_chain(self, head: str) -> list[str] | None:
        if head not in self._first_parent:
            completed = self._run("rev-list", "--first-parent", head)
            if completed.returncode != 0:
                self._first_parent[head] = None
            else:
                self._first_parent[head] = completed.stdout.decode().split()
        return self._first_parent[head]


# ---------------------------------------------------------------------------
# artifact 形状検出と head 束縛 adapter
# ---------------------------------------------------------------------------


def detect_artifact_kind(doc: object) -> str:
    """artifact の形状から head 束縛 adapter の種別を決める（closed・未知は unknown）。"""
    if not isinstance(doc, Mapping):
        return ARTIFACT_KIND_UNKNOWN
    schema = doc.get("schema")
    if schema == claim_closure.CLAIM_CLOSURE_SCHEMA:
        return ARTIFACT_KIND_CLAIM_CLOSURE_V2
    if schema == claim_closure.GATED_RESIDUAL_SCHEMA:
        return ARTIFACT_KIND_GATED_RESIDUAL_V2
    if "correction_kind" in doc or ("corrects" in doc and "current_head_sha" in doc):
        return ARTIFACT_KIND_CORRECTION_V2
    if doc.get("schema_version") == 2 and "artifact_kind" in doc and "head_sha" in doc:
        return ARTIFACT_KIND_N602_V2
    if "subject_pr" in doc and "provider" in doc:
        return ARTIFACT_KIND_REVIEW_V1
    return ARTIFACT_KIND_UNKNOWN


def head_binding_candidates(kind: str, doc: Mapping[str, Any]) -> list[tuple[str, object]]:
    """adapter 種別ごとの head 候補（優先順）。導出不能なら空。"""
    if kind == ARTIFACT_KIND_N602_V2:
        return [("head_sha", doc.get("head_sha"))]
    if kind == ARTIFACT_KIND_CORRECTION_V2:
        return [("current_head_sha", doc.get("current_head_sha"))]
    if kind == ARTIFACT_KIND_REVIEW_V1:
        subject = doc.get("subject_pr")
        reviewed = subject.get("reviewed_commit") if isinstance(subject, Mapping) else None
        return [("subject_pr.reviewed_commit", reviewed), ("head_sha", doc.get("head_sha"))]
    if kind in {ARTIFACT_KIND_CLAIM_CLOSURE_V2, ARTIFACT_KIND_GATED_RESIDUAL_V2}:
        heads = doc.get("heads")
        value = heads.get("implementation_head") if isinstance(heads, Mapping) else None
        return [("heads.implementation_head", value)]
    return []


# ---------------------------------------------------------------------------
# schema 解決と適合検査
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedSchema:
    token: str
    resolved_by: str  # manifest | validator_source
    required_fields: tuple[str, ...]
    prohibited_fields: tuple[str, ...]
    additional_properties: bool
    expected_schema_value: str | None
    definition_sha256: str | None


def resolve_artifact_schema(token: object, manifest: Mapping[str, Any]) -> ResolvedSchema | None:
    """`schema:` token を manifest `artifact_schemas` または validator source schema へ解決する。"""
    if not isinstance(token, str):
        return None
    definition = claim_closure.artifact_schema_for(token)
    if definition is not None:
        return ResolvedSchema(
            token=token,
            resolved_by="validator_source",
            required_fields=definition.required_fields,
            prohibited_fields=definition.prohibited_fields,
            additional_properties=False,
            expected_schema_value=definition.schema,
            definition_sha256=canonical_content_sha256(definition.to_descriptor()),
        )
    schemas = manifest.get("artifact_schemas")
    entry = schemas.get(token) if isinstance(schemas, Mapping) else None
    if not isinstance(entry, Mapping):
        return None
    required = tuple(
        str(item) for item in entry.get("required_fields", []) if isinstance(item, str)
    )
    prohibited = tuple(
        str(item) for item in entry.get("prohibited_fields", []) if isinstance(item, str)
    )
    # artifact_entry_field_mapping: entry.artifact_schema=schema:<name>、artifact.schema=<name>。
    expected_value = token[len("schema:") :] if token.startswith("schema:") else token
    return ResolvedSchema(
        token=token,
        resolved_by="manifest",
        required_fields=required,
        prohibited_fields=prohibited,
        additional_properties=bool(entry.get("additional_properties", False)),
        expected_schema_value=expected_value,
        definition_sha256=canonical_content_sha256(entry),
    )


def schema_conformance_issues(doc: object, resolved: ResolvedSchema) -> list[str]:
    """artifact が宣言 schema の required／prohibited／closed 制約へ適合するかを返す。"""
    if not isinstance(doc, Mapping):
        return ["artifact root が object ではない"]
    issues: list[str] = []
    keys = set(doc)
    missing = sorted(set(resolved.required_fields) - keys)
    if missing:
        issues.append(f"required field 欠落: {missing}")
    prohibited = sorted(set(resolved.prohibited_fields) & keys)
    if prohibited:
        issues.append(f"prohibited field 混入: {prohibited}")
    if not resolved.additional_properties and resolved.required_fields:
        extra = sorted(keys - set(resolved.required_fields) - set(resolved.prohibited_fields))
        if extra:
            issues.append(f"closed schema に無い field: {extra}")
    if resolved.expected_schema_value is not None:
        actual = doc.get("schema")
        if actual != resolved.expected_schema_value:
            issues.append(
                f"artifact.schema が宣言 schema と一致しない: {actual!r} !="
                f" {resolved.expected_schema_value!r}"
            )
    return issues


# ---------------------------------------------------------------------------
# entry 検査
# ---------------------------------------------------------------------------


@dataclass
class EntryReport:
    evidence_id: str
    artifact_schema: str | None = None
    schema_resolved_by: str | None = None
    artifact_kind: str = ARTIFACT_KIND_UNKNOWN
    conformance: str = "none"
    head_binding: dict[str, Any] = field(default_factory=dict)
    schema_conformance_issues: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "artifact_schema": self.artifact_schema,
            "schema_resolved_by": self.schema_resolved_by,
            "artifact_kind": self.artifact_kind,
            "conformance": self.conformance,
            "head_binding": dict(self.head_binding),
            "schema_conformance_issues": list(self.schema_conformance_issues),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _check_closed_entry_fields(entry: Mapping[str, Any], report: EntryReport) -> bool:
    keys = set(entry)
    missing = sorted(set(ENTRY_FIELDS) - keys)
    extra = sorted(keys - set(ENTRY_FIELDS))
    if missing:
        report.errors.append(f"entry 必須 field 欠落: {missing}")
    if extra:
        report.errors.append(f"entry に closed schema 外の field がある: {extra}")
    if missing or extra:
        return False
    ok = True

    def fail(message: str) -> None:
        nonlocal ok
        ok = False
        report.errors.append(message)

    kind = entry.get("subject_kind")
    subject = entry.get("subject_id")
    if kind not in SUBJECT_KIND_PREFIX:
        fail(f"subject_kind が closed enum ではない: {kind!r}")
    elif not isinstance(subject, str) or not subject.startswith(SUBJECT_KIND_PREFIX[str(kind)]):
        fail(f"subject_id {subject!r} が subject_kind {kind} の prefix と一致しない")
    phase = entry.get("phase_or_context_id")
    if phase is not None and (
        not isinstance(phase, str) or not phase.startswith(("task:", "control-context:"))
    ):
        fail(f"phase_or_context_id が task:／control-context: token でも null でもない: {phase!r}")
    for name in (
        "artifact_schema_definition_sha256",
        "subject_leaf_definition_sha256",
        "evidence_type_definition_sha256",
        "raw_sha256",
    ):
        if not _is_hex64(entry.get(name)):
            fail(f"{name} が 64 桁 lowercase hex ではない")
    evidence_type = entry.get("evidence_type")
    if not isinstance(evidence_type, str) or not evidence_type.startswith("evidence-type:"):
        fail(f"evidence_type が evidence-type: token ではない: {evidence_type!r}")
    path = entry.get("artifact_path")
    if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
        fail(f"artifact_path が repo 相対 path ではない: {path!r}")
    premerge = entry.get("premerge_pair")
    if not isinstance(premerge, Mapping) or set(premerge) != set(PREMERGE_FIELDS):
        fail("premerge_pair が closed object {tested_head, evidence_anchor_head} ではない")
    else:
        for name in PREMERGE_FIELDS:
            if not _is_hex40(premerge.get(name)):
                fail(f"premerge_pair.{name} が 40 桁 hex ではない")
        if premerge.get("tested_head") == premerge.get("evidence_anchor_head"):
            fail("premerge_pair.tested_head と evidence_anchor_head が同一（anchor は真の子孫）")
    postmerge = entry.get("postmerge_mapping")
    if not isinstance(postmerge, Mapping) or set(postmerge) != set(POSTMERGE_FIELDS):
        fail("postmerge_mapping が closed object ではない")
    else:
        merged_pr = postmerge.get("merged_pr")
        if isinstance(merged_pr, bool) or not isinstance(merged_pr, int) or merged_pr < 1:
            fail(f"postmerge_mapping.merged_pr が positive integer ではない: {merged_pr!r}")
        if postmerge.get("merge_method") not in MERGE_METHODS:
            fail("postmerge_mapping.merge_method が closed enum ではない")
        for name in ("actual_merge_terminal_sha", "actual_evidence_anchor_head"):
            if not _is_hex40(postmerge.get(name)):
                fail(f"postmerge_mapping.{name} が 40 桁 hex ではない")
        if not isinstance(postmerge.get("n598b_mapping_evidence_ref"), str):
            fail("postmerge_mapping.n598b_mapping_evidence_ref が文字列ではない")
        if not _is_hex64(postmerge.get("n598b_mapping_evidence_raw_sha256")):
            fail("postmerge_mapping.n598b_mapping_evidence_raw_sha256 が 64 桁 hex ではない")
    correction_of = entry.get("correction_of")
    disposition = entry.get("disposition")
    if correction_of is None:
        if disposition is not None:
            fail("original entry（correction_of=null）に disposition がある")
    else:
        if not isinstance(correction_of, str) or not correction_of.startswith("EV-"):
            fail(f"correction_of が EV- ID ではない: {correction_of!r}")
        if disposition not in DISPOSITIONS:
            fail(f"訂正 entry の disposition が supersedes|invalidates ではない: {disposition!r}")
    if not isinstance(entry.get("status"), str):
        fail("status が文字列ではない")
    return ok


def _check_definition_shas(
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any],
    resolved: ResolvedSchema,
    report: EntryReport,
) -> None:
    """entry の definition SHA を現 manifest／validator source の定義と照合する（差は warning）。"""
    if resolved.definition_sha256 is not None and (
        entry.get("artifact_schema_definition_sha256") != resolved.definition_sha256
    ):
        report.warnings.append(
            "artifact_schema_definition_sha256 が現 definition の canonical hash と一致しない"
            "（tested_head 時点定義との照合）"
        )
    types = manifest.get("terminal_evidence_types")
    evidence_type = entry.get("evidence_type")
    type_entry = types.get(evidence_type) if isinstance(types, Mapping) else None
    if not isinstance(type_entry, Mapping):
        report.errors.append(
            f"evidence_type {evidence_type!r} が manifest terminal_evidence_types へ解決しない"
        )
    elif entry.get("evidence_type_definition_sha256") != canonical_content_sha256(type_entry):
        report.warnings.append(
            "evidence_type_definition_sha256 が現 manifest 定義の canonical hash と一致しない"
        )
    subject_kind = entry.get("subject_kind")
    subject_id = str(entry.get("subject_id"))
    subject_mapping: Mapping[str, Any] | None = None
    if subject_kind == "task":
        catalog = manifest.get("task_catalog")
        candidate = catalog.get(subject_id) if isinstance(catalog, Mapping) else None
        subject_mapping = candidate if isinstance(candidate, Mapping) else None
    elif subject_kind == "ac":
        registry = manifest.get("ac_leaf_registry")
        candidate = registry.get(subject_id) if isinstance(registry, Mapping) else None
        subject_mapping = candidate if isinstance(candidate, Mapping) else None
    elif subject_kind == "gate":
        gates = manifest.get("acceptance_gates")
        candidate = gates.get(subject_id) if isinstance(gates, Mapping) else None
        subject_mapping = candidate if isinstance(candidate, Mapping) else None
    elif subject_kind == "claim":
        claim = claim_closure.claim_definition(manifest, subject_id)
        if claim is not None:
            # closure は R で動く mutable field。immutable audit field だけを定義対象にする。
            subject_mapping = {key: value for key, value in claim.items() if key != "closure"}
    if subject_mapping is None:
        report.warnings.append(
            f"subject {subject_id} の定義 mapping を manifest から解決できず"
            " subject_leaf_definition_sha256 を照合していない"
        )
    elif entry.get("subject_leaf_definition_sha256") != canonical_content_sha256(subject_mapping):
        report.warnings.append(
            "subject_leaf_definition_sha256 が現 manifest の subject 定義 canonical hash と"
            "一致しない"
        )


def _check_git_bindings(
    entry: Mapping[str, Any],
    *,
    git: _Git,
    evaluation_head: str,
    first_parent: Sequence[str] | None,
    report: EntryReport,
) -> bytes | None:
    """(c) blob と commit 関係を検査し、actual anchor の raw bytes を返す（不一致は None）。"""
    path = str(entry.get("artifact_path"))
    raw_sha = str(entry.get("raw_sha256"))
    premerge = entry.get("premerge_pair")
    postmerge = entry.get("postmerge_mapping")
    assert isinstance(premerge, Mapping) and isinstance(postmerge, Mapping)
    tested = str(premerge.get("tested_head"))
    premerge_anchor = str(premerge.get("evidence_anchor_head"))
    terminal = str(postmerge.get("actual_merge_terminal_sha"))
    anchor = str(postmerge.get("actual_evidence_anchor_head"))

    if first_parent is None:
        report.errors.append(f"評価 head {evaluation_head} の first-parent 履歴を取得できない")
    else:
        positions = {sha: index for index, sha in enumerate(first_parent)}
        if anchor not in positions:
            report.errors.append(
                f"actual_evidence_anchor_head {anchor[:12]} が評価 head の first-parent 上にない"
            )
        if terminal not in positions:
            report.errors.append(
                f"actual_merge_terminal_sha {terminal[:12]} が評価 head の first-parent 上にない"
            )
        if (
            anchor in positions
            and terminal in positions
            and positions[terminal] < positions[anchor]
        ):
            report.errors.append(
                "actual_merge_terminal_sha が actual_evidence_anchor_head より後にある"
            )

    blob: bytes | None = None
    if not git.commit_exists(anchor):
        report.errors.append(f"actual_evidence_anchor_head {anchor[:12]} がローカルに存在しない")
    else:
        blob = git.show_blob(anchor, path)
        if blob is None:
            report.errors.append(f"git show {anchor[:12]}:{path} が blob を返さない")
        elif _sha256(blob) != raw_sha:
            report.errors.append(
                f"actual anchor の blob SHA-256 が raw_sha256 と一致しない（{_sha256(blob)[:12]}…）"
            )
            blob = None
    head_blob = git.show_blob(evaluation_head, path)
    if head_blob is None:
        report.errors.append(f"評価 head に {path} が存在しない（削除・移動は append-only 違反）")
    elif _sha256(head_blob) != raw_sha:
        report.errors.append(
            "評価 head の blob が raw_sha256 と一致しない（既存 artifact の差替え）"
        )

    if git.commit_exists(tested) and git.commit_exists(premerge_anchor):
        if tested == premerge_anchor or not git.is_ancestor(tested, premerge_anchor):
            report.errors.append(
                "premerge_pair.tested_head が evidence_anchor_head の真の祖先ではない"
            )
        premerge_blob = git.show_blob(premerge_anchor, path)
        if premerge_blob is None:
            report.errors.append(
                f"git show {premerge_anchor[:12]}:{path}（premerge anchor）が blob を返さない"
            )
        elif _sha256(premerge_blob) != raw_sha:
            report.errors.append("premerge anchor の blob SHA-256 が raw_sha256 と一致しない")
    else:
        report.warnings.append(
            "premerge commit（tested_head／evidence_anchor_head）がローカルに無く premerge 側の"
            "祖先関係と blob を検証していない"
        )
    return blob


def _check_artifact(
    entry: Mapping[str, Any],
    *,
    entry_id: str,
    raw: bytes,
    manifest: Mapping[str, Any],
    resolved: ResolvedSchema,
    report: EntryReport,
) -> None:
    """(d) parse・schema 適合・evidence_id・head 束縛・conformance 分類。"""
    path = str(entry.get("artifact_path"))
    fmt = claim_closure.format_for_path(path)
    if fmt is None:
        report.errors.append(f"artifact format を拡張子から決められない: {path}")
        return
    doc, parse_issues = claim_closure.load_closure_document(raw, fmt=fmt)
    if parse_issues:
        report.errors.extend(f"{path}: {issue}" for issue in parse_issues)
        return
    report.schema_conformance_issues = schema_conformance_issues(doc, resolved)
    # independent acceptance（AC-15）は closed schema 検査だけでは独立性規則
    # （L1／L2／L3・sentinel・scope 差集合・provenance）を検証できないため、
    # 専用 validator を registry 経路から必ず呼ぶ（実装が使われない状態を作らない）。
    if entry.get("artifact_schema") == independent_acceptance.SCHEMA_KEY:
        # scope 差集合と head 束縛は artifact 単体では判定できないので、registry entry と
        # manifest から実値を渡す（渡さないと両検査が実運用経路で無効になる）。
        expected_head: str | None = None
        premerge = entry.get("premerge_pair")
        if isinstance(premerge, Mapping):
            raw_head = premerge.get("tested_head")
            if isinstance(raw_head, str):
                expected_head = raw_head
        scope_refs: list[str] | None = None
        target = doc.get("target_subject_id") if isinstance(doc, Mapping) else None
        catalog = manifest.get("task_catalog")
        if isinstance(target, str) and isinstance(catalog, Mapping):
            task_entry = catalog.get(target)
            if isinstance(task_entry, Mapping):
                raw_scope = task_entry.get("scope_ac_refs")
                if isinstance(raw_scope, list):
                    scope_refs = [item for item in raw_scope if isinstance(item, str)]
        report.schema_conformance_issues.extend(
            independent_acceptance.validate_artifact(
                doc,
                manifest=manifest,
                scope_ac_refs=scope_refs,
                expected_tested_head=expected_head,
            )
        )
    if resolved.resolved_by == "validator_source" and isinstance(doc, Mapping):
        report.schema_conformance_issues.extend(
            claim_closure.validate_claim_closure_artifact(
                doc, manifest=manifest, claim_id=str(entry.get("subject_id"))
            )
        )
        if entry.get("subject_kind") != "claim":
            report.errors.append(
                "validator source schema の entry は subject_kind=claim でなければならない"
            )
        if entry.get("phase_or_context_id") is not None:
            report.errors.append(
                "claim closure entry の phase_or_context_id は null でなければならない"
            )
        definition = claim_closure.artifact_schema_for(resolved.token)
        if definition is not None and entry.get("evidence_type") != definition.evidence_type:
            report.errors.append(
                f"evidence_type が schema {resolved.token} の {definition.evidence_type} と"
                "一致しない"
            )
        if isinstance(doc, Mapping) and doc.get("claim_id") != entry.get("subject_id"):
            report.errors.append("artifact.claim_id が entry.subject_id と一致しない")
        if (
            isinstance(doc, Mapping)
            and entry.get("status") == "pass"
            and definition is not None
            and doc.get("status") != definition.status
        ):
            report.errors.append(
                f"artifact.status {doc.get('status')!r} が {definition.status} ではないのに"
                " entry.status=pass"
            )
    if isinstance(doc, Mapping) and "evidence_id" in doc and doc.get("evidence_id") != entry_id:
        report.errors.append(
            f"artifact.evidence_id {doc.get('evidence_id')!r} が registry key {entry_id} と"
            "一致しない"
        )
    kind = detect_artifact_kind(doc)
    report.artifact_kind = kind
    premerge = entry.get("premerge_pair")
    tested = str(premerge.get("tested_head")) if isinstance(premerge, Mapping) else ""
    candidates = head_binding_candidates(kind, doc) if isinstance(doc, Mapping) else []
    matched_field: str | None = None
    for name, value in candidates:
        if isinstance(value, str) and value == tested:
            matched_field = name
            break
    report.head_binding = {
        "candidates": [{"field": name, "value": value} for name, value in candidates],
        "tested_head": tested,
        "matched_field": matched_field,
    }
    if isinstance(doc, Mapping) and kind in {
        ARTIFACT_KIND_CLAIM_CLOSURE_V2,
        ARTIFACT_KIND_GATED_RESIDUAL_V2,
    }:
        heads = doc.get("heads")
        postmerge = entry.get("postmerge_mapping")
        if (
            isinstance(heads, Mapping)
            and isinstance(premerge, Mapping)
            and isinstance(postmerge, Mapping)
        ):
            anchor = heads.get("evidence_anchor_head")
            if anchor is not None and anchor != premerge.get("evidence_anchor_head"):
                report.errors.append(
                    "artifact heads.evidence_anchor_head（非 null）が entry premerge_pair"
                    ".evidence_anchor_head と一致しない"
                )
            terminal = heads.get("actual_merge_terminal_sha")
            if terminal is not None and terminal != postmerge.get("actual_merge_terminal_sha"):
                report.errors.append(
                    "artifact heads.actual_merge_terminal_sha（非 null）が entry postmerge_mapping"
                    ".actual_merge_terminal_sha と一致しない"
                )
    if not candidates:
        report.conformance = "none"
        report.errors.append(
            f"artifact 形状 {kind} から head 束縛を導出できない（tested_head との対応を"
            "検証できない・fail-close）"
        )
    elif matched_field is None:
        report.conformance = "none"
        report.errors.append(
            f"artifact の head 候補 {[value for _, value in candidates]!r} が"
            f" premerge_pair.tested_head {tested} と一致しない"
        )
    elif report.schema_conformance_issues:
        report.conformance = "legacy_binding"
        report.warnings.append(
            f"legacy_binding: 宣言 schema {resolved.token} へ artifact 形状 {kind} が適合しないが"
            f" {matched_field} が tested_head と一致する（旧登録・再 closeout で v2 へ置換対象）"
        )
    else:
        report.conformance = "strict"


def validate_entry(
    entry_id: str,
    entry: object,
    *,
    manifest: Mapping[str, Any],
    git: _Git,
    evaluation_head: str,
    first_parent: Sequence[str] | None,
) -> EntryReport:
    report = EntryReport(evidence_id=entry_id)
    if not isinstance(entry, Mapping):
        report.errors.append("entry が object ではない")
        return report
    report.artifact_schema = (
        str(entry.get("artifact_schema")) if isinstance(entry.get("artifact_schema"), str) else None
    )
    if not _check_closed_entry_fields(entry, report):
        return report
    resolved = resolve_artifact_schema(entry.get("artifact_schema"), manifest)
    if resolved is None:
        report.errors.append(
            f"artifact_schema {entry.get('artifact_schema')!r} が manifest artifact_schemas にも"
            f" validator source schema（{sorted(claim_closure.VALIDATOR_SOURCE_SCHEMAS)}）にも"
            "解決しない"
        )
        return report
    report.schema_resolved_by = resolved.resolved_by
    _check_definition_shas(entry, manifest, resolved, report)
    blob = _check_git_bindings(
        entry,
        git=git,
        evaluation_head=evaluation_head,
        first_parent=first_parent,
        report=report,
    )
    if blob is None:
        report.errors.append(
            "artifact raw bytes を anchor から確定できないため artifact 検査を省略"
        )
        return report
    _check_artifact(
        entry, entry_id=entry_id, raw=blob, manifest=manifest, resolved=resolved, report=report
    )
    return report


# ---------------------------------------------------------------------------
# registry 全体
# ---------------------------------------------------------------------------


def validate_registry(
    manifest: Mapping[str, Any],
    *,
    repo_root: Path,
    evaluation_head: str = "HEAD",
) -> dict[str, Any]:
    """manifest の registry 全 entry を検査し JSON 化可能な report を返す。"""
    git = _Git(repo_root)
    resolved_head = git.resolve(evaluation_head)
    errors: list[str] = []
    warnings: list[str] = []
    entries_report: dict[str, dict[str, Any]] = {}
    counts = {level: 0 for level in CONFORMANCE_LEVELS}
    registry = manifest.get("evidence_registry")
    entries = registry.get("entries") if isinstance(registry, Mapping) else None
    if resolved_head is None:
        errors.append(f"評価 head {evaluation_head!r} を commit へ解決できない")
    if isinstance(registry, Mapping) and registry.get("schema") != REGISTRY_ENTRY_SCHEMA:
        errors.append(f"evidence_registry.schema が {REGISTRY_ENTRY_SCHEMA} ではない")
    if entries is None:
        entries = {}
    if not isinstance(entries, Mapping):
        errors.append("evidence_registry.entries が object ではない")
        entries = {}
    first_parent = git.first_parent_chain(resolved_head) if resolved_head else None
    for entry_id in entries:
        key = str(entry_id)
        if not key.startswith("EV-") or not _is_hex64(key[3:]):
            errors.append(f"registry key が EV-<64 hex> ではない: {key}")
        report = validate_entry(
            key,
            entries[entry_id],
            manifest=manifest,
            git=git,
            evaluation_head=resolved_head or evaluation_head,
            first_parent=first_parent,
        )
        counts[report.conformance] += 1
        entries_report[key] = report.to_descriptor()
        errors.extend(f"{key}: {message}" for message in report.errors)
        warnings.extend(f"{key}: {message}" for message in report.warnings)
    _current, chain_issues = expectation.registry_current_entries_and_issues(manifest)
    errors.extend(f"correction chain: {issue}" for issue in chain_issues)
    return {
        "schema": "evidence-registry-validation-report/v1",
        "manifest": MANIFEST_REL,
        "evaluation_head": resolved_head,
        "entry_count": len(entries_report),
        "conformance_counts": counts,
        "entries": entries_report,
        "errors": errors,
        "warnings": warnings,
        "status": "pass" if not errors else "fail",
    }


def load_manifest(path: Path) -> Mapping[str, Any]:
    """manifest YAML を読む（duplicate key は fail-close で拒否）。"""
    raw = path.read_bytes()
    doc, issues = claim_closure.load_closure_document(raw, fmt="yaml")
    if issues:
        raise ValueError(f"{path}: {'; '.join(issues)}")
    if not isinstance(doc, Mapping):
        raise ValueError(f"{path}: root が mapping ではない")
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument(
        "--manifest", type=Path, default=None, help=f"既定: <repo-root>/{MANIFEST_REL}"
    )
    parser.add_argument("--evaluation-head", default="HEAD")
    parser.add_argument(
        "--strict", action="store_true", help="errors が 1 件でもあれば exit 1（既定は常に 0）"
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    manifest_path = args.manifest if args.manifest is not None else repo_root / MANIFEST_REL
    try:
        manifest = load_manifest(manifest_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        report: dict[str, Any] = {
            "schema": "evidence-registry-validation-report/v1",
            "status": "fail",
            "errors": [f"manifest を読めない: {exc}"],
            "warnings": [],
            "entries": {},
            "entry_count": 0,
            "conformance_counts": {level: 0 for level in CONFORMANCE_LEVELS},
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if args.strict else 0
    report = validate_registry(manifest, repo_root=repo_root, evaluation_head=args.evaluation_head)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if (args.strict and report["status"] != "pass") else 0


if __name__ == "__main__":
    raise SystemExit(main())
