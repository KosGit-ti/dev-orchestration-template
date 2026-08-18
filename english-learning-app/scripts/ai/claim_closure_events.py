#!/usr/bin/env python3
"""claim closure の registry 側 append-only event chain（`claim-closure-event/v1`）。

DEC-20260815-003 決定 4（closure history は Manifest に持たず registry 側 append-only event
chain へ置き、current status は chain の fold だけから生成する）と決定 8（P／E／M の分離）の
実装である。P-070 正本順位に従い schema の正本は本 module（validator source・rank 4）にある。

## event file

`docs/ai/claim-closure-events/<claim-id-slug>/<event-id>.yml`（1 event 1 file・content-addressed）。
`<claim-id-slug>` は `claim:` prefix を除いた claim ID（`[A-Za-z0-9][A-Za-z0-9._-]*`）。
`event_id` は `event_id=null` にした closed event を canonical-content-v1（RFC 8785 相当・
`scripts/ai/evidence_contract.py`）で hash した lowercase SHA-256 へ `CCE-` を付けたもので、
file 名は `<event_id>.yml` と exact 一致する。既存 event file は byte 不変とし、writer は
新 file の追加だけを行う（`append_claim_closure_event`）。

## fields（closed・記載順）

- `schema`: exact `claim-closure-event/v1`
- `event_id`: `CCE-<64 hex>`（validator が再計算し一致を要求）
- `claim_id`: `claim:<id>`
- `sequence`: 1 始まりの連続 positive integer（gap／重複は fail-close）
- `previous_event_id`: 直前 event の `event_id`（先頭は null）
- `previous_event_sha256`: 直前 event **file raw bytes** の SHA-256（先頭は null）。file 単位の
  byte 不変性を chain で拘束する（content-addressed ID は body、raw SHA は serialization まで守る）
- `from_state`／`to_state`: `open|implemented_verified|valid_gated_residual|invalidated`。
  先頭は `from_state=open`。`from_state` は直前 event の `to_state` と exact 一致する
- `disposition`: `original|supersedes|invalidates`。先頭は `original`（`to_state` は
  implemented_verified|valid_gated_residual）。2 件目以降は `supersedes`（`to_state` は
  implemented_verified|valid_gated_residual）または `invalidates`（`to_state=invalidated`）。
  `invalidated` から再び閉じる場合は `supersedes` で新 closure を積む（履歴は消さない）
- `evidence_id`: registry entry key `EV-<64 hex>`（invalidates では訂正 entry の ID）
- `artifact_path`／`artifact_raw_sha256`: 当該 registry entry の artifact path／raw SHA
- `heads`: closed `{implementation_head（P）, evidence_anchor_head（E）,
  actual_merge_terminal_sha（M）}` 全て 40 hex 必須（R 時点で確定済み）。P と E／M は別 field で
  代用しない
- `recorded_at`: RFC 3339
- `registered_by`: closed `{merged_pr: <positive integer>}`（登録 R unit の PR）

## fold（`fold_claim_history`）

event を `sequence` 順に並べ、(1) 各 event の closed schema と `event_id` 再計算、(2) 全 event の
`claim_id` 一致、(3) `sequence` が 1..n 連続、(4) 2 件目以降の `previous_event_id`／
`previous_event_sha256`／`from_state` が直前 event と exact 一致、(5) 先頭が原初 event の形、を
検査し、1 件でも破れば `state=None`（fail-close・current なし）。branch（同じ直前 event を指す
複数 event・sequence 重複）、cycle／forward reference（直前以外を指す previous_event_id）、
gap、previous hash 不一致は全てここで拒否する。有効な chain では末尾 event が唯一の
unsuperseded leaf であり、`state=leaf.to_state`（`invalidated` は closure obligation false）。
event が 0 件なら `state=open`。

`current_closure_status(manifest, events_root)` は manifest の actionable claim 全件と events_root
配下の全 slug について fold を返す（manifest に無い slug は issue）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
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

from scripts.ai.evidence_contract import (  # noqa: E402
    CanonicalContentError,
    canonical_hash_with_null_fields,
)

__all__ = [
    "CLOSED_STATES",
    "DISPOSITIONS",
    "EVENTS_DIR_REL",
    "EVENT_FIELDS",
    "EVENT_ID_PREFIX",
    "EVENT_SCHEMA",
    "HEADS_FIELDS",
    "REGISTERED_BY_FIELDS",
    "STATES",
    "ClaimClosureEventError",
    "ClaimCurrent",
    "LoadedEvent",
    "append_claim_closure_event",
    "build_event",
    "claim_slug",
    "current_closure_status",
    "derive_event_id",
    "fold_claim_history",
    "load_claim_events",
    "serialize_event",
    "validate_event_object",
    "validate_transition",
]

EVENTS_DIR_REL: Final = "docs/ai/claim-closure-events"
EVENT_SCHEMA: Final = "claim-closure-event/v1"
EVENT_ID_PREFIX: Final = "CCE-"

STATES: Final = ("open", "implemented_verified", "valid_gated_residual", "invalidated")
CLOSED_STATES: Final = frozenset({"implemented_verified", "valid_gated_residual"})
DISPOSITIONS: Final = ("original", "supersedes", "invalidates")

EVENT_FIELDS: Final = (
    "schema",
    "event_id",
    "claim_id",
    "sequence",
    "previous_event_id",
    "previous_event_sha256",
    "from_state",
    "to_state",
    "disposition",
    "evidence_id",
    "artifact_path",
    "artifact_raw_sha256",
    "heads",
    "recorded_at",
    "registered_by",
)
HEADS_FIELDS: Final = ("implementation_head", "evidence_anchor_head", "actual_merge_terminal_sha")
REGISTERED_BY_FIELDS: Final = ("merged_pr",)

_HEX40_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ID_RE: Final = re.compile(r"^CCE-[0-9a-f]{64}$")
_EVIDENCE_ID_RE: Final = re.compile(r"^EV-[0-9a-f]{64}$")
_CLAIM_TOKEN_RE: Final = re.compile(r"^claim:[A-Za-z0-9][A-Za-z0-9._-]*$")
_SLUG_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TRACKED_PATH_RE: Final = re.compile(r"^docs/[A-Za-z0-9][A-Za-z0-9._/\-]*$")
_RFC3339_RE: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_ACTIONABLE_DEFAULT: Final = ("adopt", "adopt_with_refinement", "gated")


class ClaimClosureEventError(ValueError):
    """event の生成・追記契約違反（writer 側 fail-close）。"""


@dataclass(frozen=True)
class LoadedEvent:
    """読込済み event（raw bytes と parse 済み document の組）。"""

    raw: bytes
    doc: Mapping[str, Any]
    path: Path | None = None

    @property
    def raw_sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def event_id(self) -> str | None:
        value = self.doc.get("event_id")
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class ClaimCurrent:
    """chain の fold 結果（`state=None` は fail-close＝current なし）。"""

    claim_id: str
    state: str | None
    event_count: int
    leaf_event_id: str | None = None
    evidence_id: str | None = None
    artifact_path: str | None = None
    artifact_raw_sha256: str | None = None
    heads: Mapping[str, str] | None = None
    issues: tuple[str, ...] = field(default_factory=tuple)

    @property
    def valid(self) -> bool:
        return self.state is not None and not self.issues

    @property
    def closure_obligation_satisfied(self) -> bool:
        """有効な chain の末尾が implemented_verified|valid_gated_residual なら true。"""
        return self.valid and self.state in CLOSED_STATES

    @property
    def implementation_verified(self) -> bool:
        return self.valid and self.state == "implemented_verified"

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "state": self.state,
            "event_count": self.event_count,
            "leaf_event_id": self.leaf_event_id,
            "evidence_id": self.evidence_id,
            "artifact_path": self.artifact_path,
            "artifact_raw_sha256": self.artifact_raw_sha256,
            "heads": dict(self.heads) if self.heads is not None else None,
            "closure_obligation_satisfied": self.closure_obligation_satisfied,
            "implementation_verified": self.implementation_verified,
            "issues": list(self.issues),
        }


# ---------------------------------------------------------------------------
# primitive
# ---------------------------------------------------------------------------


def claim_slug(claim_id: str) -> str:
    """`claim:<id>`／`<id>` から file system 用 slug（`<id>`）を返す（不正は例外）。"""
    plain = claim_id[len("claim:") :] if claim_id.startswith("claim:") else claim_id
    if not _SLUG_RE.match(plain):
        raise ClaimClosureEventError(f"claim ID を slug にできない: {claim_id!r}")
    return plain


def derive_event_id(event: Mapping[str, Any]) -> str:
    """`event_id=null` にした closed event の canonical hash から event ID を導出する。"""
    projection = {name: event.get(name) for name in EVENT_FIELDS}
    return EVENT_ID_PREFIX + canonical_hash_with_null_fields(projection, "event_id")


def serialize_event(event: Mapping[str, Any]) -> bytes:
    """event を記載順の YAML（UTF-8・sort なし・flow なし）へ serialize する。"""
    ordered = {name: event.get(name) for name in EVENT_FIELDS}
    text = yaml.safe_dump(
        ordered, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000
    )
    return str(text).encode("utf-8")


def _is_hex40(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX40_RE.match(value))


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX64_RE.match(value))


def _closed(value: object, fields: Sequence[str], *, label: str, issues: list[str]) -> bool:
    if not isinstance(value, Mapping):
        issues.append(f"{label} が object ではない")
        return False
    keys = set(value)
    expected = set(fields)
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    if missing:
        issues.append(f"{label}: 必須 field 欠落: {missing}")
    if extra:
        issues.append(f"{label}: 未知 field（closed schema）: {extra}")
    return not (missing or extra)


def validate_event_object(doc: object) -> list[str]:
    """単一 event の closed schema・型・`event_id` 再計算を検査する（chain 関係は別）。"""
    issues: list[str] = []
    if not isinstance(doc, Mapping):
        return ["event が object ではない"]
    if not _closed(doc, EVENT_FIELDS, label="event", issues=issues):
        return issues
    if doc.get("schema") != EVENT_SCHEMA:
        issues.append(f"schema が {EVENT_SCHEMA} ではない: {doc.get('schema')!r}")
    event_id = doc.get("event_id")
    if not isinstance(event_id, str) or not _EVENT_ID_RE.match(event_id):
        issues.append(f"event_id が CCE-<64 hex> ではない: {event_id!r}")
    claim_id = doc.get("claim_id")
    if not isinstance(claim_id, str) or not _CLAIM_TOKEN_RE.match(claim_id):
        issues.append(f"claim_id が claim:<id> ではない: {claim_id!r}")
    sequence = doc.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        issues.append(f"sequence が positive integer ではない: {sequence!r}")
    previous_id = doc.get("previous_event_id")
    if previous_id is not None and (
        not isinstance(previous_id, str) or not _EVENT_ID_RE.match(previous_id)
    ):
        issues.append(f"previous_event_id が CCE-<64 hex> でも null でもない: {previous_id!r}")
    previous_sha = doc.get("previous_event_sha256")
    if previous_sha is not None and not _is_hex64(previous_sha):
        issues.append("previous_event_sha256 が 64 hex でも null でもない")
    if (previous_id is None) != (previous_sha is None):
        issues.append(
            "previous_event_id と previous_event_sha256 は同時に null／非 null でなければならない"
        )
    from_state = doc.get("from_state")
    to_state = doc.get("to_state")
    if from_state not in STATES:
        issues.append(f"from_state が closed enum ではない: {from_state!r}")
    if to_state not in STATES or to_state == "open":
        issues.append(
            "to_state が implemented_verified|valid_gated_residual|invalidated ではない:"
            f" {to_state!r}"
        )
    disposition = doc.get("disposition")
    if disposition not in DISPOSITIONS:
        issues.append(f"disposition が closed enum ではない: {disposition!r}")
    else:
        if disposition == "original":
            if previous_id is not None or from_state != "open" or sequence != 1:
                issues.append(
                    "disposition=original は sequence=1・from_state=open・previous_event_id=null"
                    "でなければならない"
                )
            if to_state not in CLOSED_STATES:
                issues.append(
                    "disposition=original の to_state は implemented_verified|valid_gated_residual"
                )
        elif previous_id is None or from_state == "open":
            issues.append(f"disposition={disposition} は先頭 event（from_state=open）に使えない")
        if disposition == "invalidates" and to_state != "invalidated":
            issues.append("disposition=invalidates の to_state は invalidated でなければならない")
        if disposition == "supersedes" and to_state not in CLOSED_STATES:
            issues.append(
                "disposition=supersedes の to_state は implemented_verified|valid_gated_residual"
            )
    if not isinstance(doc.get("evidence_id"), str) or not _EVIDENCE_ID_RE.match(
        str(doc.get("evidence_id"))
    ):
        issues.append(f"evidence_id が EV-<64 hex> ではない: {doc.get('evidence_id')!r}")
    artifact_path = doc.get("artifact_path")
    if not isinstance(artifact_path, str) or not _TRACKED_PATH_RE.match(artifact_path):
        issues.append(f"artifact_path が追跡 path ではない: {artifact_path!r}")
    elif ".." in artifact_path.split("/"):
        # PR #1394 Round 1 指摘: regex は `.` と `/` を許すため `docs/a/../b` のような
        # path traversal 形式を通す。後段で `root / artifact_path` として実ファイルへ
        # アクセスしうるので、`..` セグメントを含む path は fail-close する。
        issues.append(f"artifact_path に .. セグメントが含まれる: {artifact_path!r}")
    if not _is_hex64(doc.get("artifact_raw_sha256")):
        issues.append("artifact_raw_sha256 が 64 hex ではない")
    heads = doc.get("heads")
    if _closed(heads, HEADS_FIELDS, label="heads", issues=issues) and isinstance(heads, Mapping):
        for name in HEADS_FIELDS:
            if not _is_hex40(heads.get(name)):
                issues.append(f"heads.{name} が 40 hex ではない")
        implementation = heads.get("implementation_head")
        for name in ("evidence_anchor_head", "actual_merge_terminal_sha"):
            if heads.get(name) == implementation and _is_hex40(implementation):
                issues.append(
                    f"heads.{name} が implementation_head と同一（P と E／M を代用しない）"
                )
    recorded_at = doc.get("recorded_at")
    if not isinstance(recorded_at, str) or not _RFC3339_RE.match(recorded_at):
        issues.append(f"recorded_at が RFC 3339 ではない: {recorded_at!r}")
    registered_by = doc.get("registered_by")
    if _closed(registered_by, REGISTERED_BY_FIELDS, label="registered_by", issues=issues):
        merged_pr = registered_by.get("merged_pr") if isinstance(registered_by, Mapping) else None
        if isinstance(merged_pr, bool) or not isinstance(merged_pr, int) or merged_pr < 1:
            issues.append(f"registered_by.merged_pr が positive integer ではない: {merged_pr!r}")
    if not issues and isinstance(event_id, str):
        try:
            expected = derive_event_id(doc)
        except CanonicalContentError as exc:
            issues.append(f"event を canonical 化できない: {exc}")
        else:
            if expected != event_id:
                issues.append(
                    f"event_id が canonical body から再計算した値と一致しない（{expected}）"
                )
    return issues


def validate_transition(previous: LoadedEvent | None, event: Mapping[str, Any]) -> list[str]:
    """直前 event（file bytes 付き）と event の chain 関係を検査する。"""
    issues: list[str] = []
    if previous is None:
        if event.get("previous_event_id") is not None:
            issues.append("先頭 event の previous_event_id が null ではない")
        if event.get("previous_event_sha256") is not None:
            issues.append("先頭 event の previous_event_sha256 が null ではない")
        if event.get("from_state") != "open":
            issues.append("先頭 event の from_state が open ではない")
        if event.get("disposition") != "original":
            issues.append("先頭 event の disposition が original ではない")
        if event.get("sequence") != 1:
            issues.append("先頭 event の sequence が 1 ではない")
        return issues
    prev_doc = previous.doc
    if event.get("previous_event_id") != prev_doc.get("event_id"):
        issues.append(
            "previous_event_id が直前 event の event_id と一致しない"
            "（branch／cycle／forward reference）: "
            f"{event.get('previous_event_id')!r} != {prev_doc.get('event_id')!r}"
        )
    if event.get("previous_event_sha256") != previous.raw_sha256:
        issues.append(
            "previous_event_sha256 が直前 event file の raw SHA-256 と一致しない"
            "（既存 event の改変）"
        )
    if event.get("from_state") != prev_doc.get("to_state"):
        issues.append(
            f"from_state が直前 event の to_state と一致しない: {event.get('from_state')!r} !="
            f" {prev_doc.get('to_state')!r}"
        )
    prev_sequence = prev_doc.get("sequence")
    if not isinstance(prev_sequence, int) or event.get("sequence") != prev_sequence + 1:
        issues.append(
            f"sequence が連続していない（gap／重複）: {prev_sequence!r} ->"
            f" {event.get('sequence')!r}"
        )
    if event.get("disposition") == "original":
        issues.append("2 件目以降の event は disposition=original を使えない")
    if event.get("claim_id") != prev_doc.get("claim_id"):
        issues.append("claim_id が直前 event と一致しない")
    return issues


# ---------------------------------------------------------------------------
# fold
# ---------------------------------------------------------------------------


def _fail(claim_id: str, count: int, issues: list[str]) -> ClaimCurrent:
    return ClaimCurrent(claim_id=claim_id, state=None, event_count=count, issues=tuple(issues))


def fold_claim_history(
    events: Sequence[LoadedEvent], *, claim_id: str | None = None
) -> ClaimCurrent:
    """event 列を fold し、唯一の unsuperseded leaf の state を返す（不正は state=None）。"""
    label = claim_id if claim_id is not None else "<unknown>"
    if not events:
        return ClaimCurrent(claim_id=label, state="open", event_count=0)
    issues: list[str] = []
    for loaded in events:
        for issue in validate_event_object(loaded.doc):
            issues.append(f"{loaded.event_id or loaded.path or '<event>'}: {issue}")
        if (
            loaded.path is not None
            and loaded.event_id is not None
            and loaded.path.name != f"{loaded.event_id}.yml"
        ):
            issues.append(f"{loaded.path}: file 名が event_id と一致しない")
    if issues:
        return _fail(label, len(events), issues)
    claim_ids = {str(loaded.doc.get("claim_id")) for loaded in events}
    if len(claim_ids) != 1:
        issues.append(f"claim_id が複数混在している: {sorted(claim_ids)}")
        return _fail(label, len(events), issues)
    resolved_claim = next(iter(claim_ids))
    if (
        claim_id is not None
        and resolved_claim != claim_id
        and resolved_claim != f"claim:{claim_id}"
    ):
        issues.append(f"event の claim_id {resolved_claim} が要求 {claim_id} と一致しない")
        return _fail(label, len(events), issues)
    ids = [str(loaded.event_id) for loaded in events]
    if len(ids) != len(set(ids)):
        issues.append("event_id が重複している")
        return _fail(resolved_claim, len(events), issues)
    ordered = sorted(events, key=lambda item: int(item.doc.get("sequence") or 0))
    sequences = [int(item.doc.get("sequence") or 0) for item in ordered]
    if sequences != list(range(1, len(ordered) + 1)):
        issues.append(f"sequence が 1..n の連続ではない（gap／重複＝branch）: {sequences}")
        return _fail(resolved_claim, len(events), issues)
    previous: LoadedEvent | None = None
    for loaded in ordered:
        for issue in validate_transition(previous, loaded.doc):
            issues.append(f"{loaded.event_id}: {issue}")
        previous = loaded
    if issues:
        return _fail(resolved_claim, len(events), issues)
    leaf = ordered[-1]
    heads_raw = leaf.doc.get("heads")
    heads = (
        {name: str(heads_raw.get(name)) for name in HEADS_FIELDS}
        if isinstance(heads_raw, Mapping)
        else None
    )
    return ClaimCurrent(
        claim_id=resolved_claim,
        state=str(leaf.doc.get("to_state")),
        event_count=len(events),
        leaf_event_id=leaf.event_id,
        evidence_id=str(leaf.doc.get("evidence_id")),
        artifact_path=str(leaf.doc.get("artifact_path")),
        artifact_raw_sha256=str(leaf.doc.get("artifact_raw_sha256")),
        heads=heads,
    )


# ---------------------------------------------------------------------------
# file 読込
# ---------------------------------------------------------------------------


def _parse_event_bytes(raw: bytes) -> tuple[Mapping[str, Any] | None, str | None]:
    # duplicate key を後勝ちで潰さない（claim_closure.parse_yaml_strict と同じ方針）。
    try:
        text = raw.decode("utf-8")
        node = yaml.compose(text, Loader=yaml.SafeLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, f"YAML として parse できない: {exc}"
    duplicate = _find_duplicate_key(node, "")
    if duplicate is not None:
        return None, f"YAML mapping key が重複している: {duplicate}"
    doc = yaml.safe_load(text)
    if not isinstance(doc, Mapping):
        return None, "event root が mapping ではない"
    return doc, None


def _find_duplicate_key(node: Any, path: str) -> str | None:
    if isinstance(node, yaml.MappingNode):
        seen: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                identity = (str(key_node.tag), str(key_node.value))
                if identity in seen:
                    return f"{path}/{key_node.value}"
                seen.add(identity)
                found = _find_duplicate_key(value_node, f"{path}/{key_node.value}")
            else:
                found = _find_duplicate_key(value_node, f"{path}/<複合 key>")
            if found is not None:
                return found
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            found = _find_duplicate_key(item, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def load_claim_events(events_root: Path, claim_id: str) -> tuple[list[LoadedEvent], list[str]]:
    """`events_root/<slug>/*.yml` を読み `(events, issues)` を返す（file 不正は issue）。"""
    slug = claim_slug(claim_id)
    directory = events_root / slug
    if directory.is_symlink():
        # PR #1396 の指摘: directory symlink は `is_dir()` が真になり、events_root 配下の
        # 想定外 path（repo 外を含む）を読み、writer が symlink 先へ append しうる。
        # event chain は append-only・tracked path・改変検出が中核なので fail-close する。
        return [], [f"{directory}: event directory が symlink（symlink は許さない）"]
    if not directory.is_dir():
        return [], []
    events: list[LoadedEvent] = []
    issues: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue
        if not path.is_file() or path.suffix != ".yml":
            issues.append(f"{path}: event directory に .yml 以外の項目がある")
            continue
        if path.is_symlink():
            issues.append(f"{path}: symlink は許さない")
            continue
        raw = path.read_bytes()
        doc, error = _parse_event_bytes(raw)
        if doc is None:
            issues.append(f"{path}: {error}")
            continue
        events.append(LoadedEvent(raw=raw, doc=doc, path=path))
    return events, issues


def _actionable_claim_ids(manifest: Mapping[str, Any]) -> list[str]:
    truth = manifest.get("claim_truth")
    actionable = (
        [item for item in truth.get("actionable_adoptions", []) if isinstance(item, str)]
        if isinstance(truth, Mapping)
        else []
    )
    allowed = frozenset(actionable) if actionable else frozenset(_ACTIONABLE_DEFAULT)
    claims = manifest.get("claims")
    result: list[str] = []
    if isinstance(claims, list):
        for claim in claims:
            if (
                isinstance(claim, Mapping)
                and isinstance(claim.get("claim_id"), str)
                and claim.get("adoption") in allowed
            ):
                result.append(str(claim["claim_id"]))
    return sorted(set(result), key=lambda item: item.encode("utf-8"))


def current_closure_status(
    manifest: Mapping[str, Any], events_root: Path
) -> dict[str, ClaimCurrent]:
    """actionable claim ごとの fold 結果（key は plain claim ID）。

    manifest に無い slug（events_root 配下の余分な directory）も issue 付きで含める。
    """
    result: dict[str, ClaimCurrent] = {}
    known = _actionable_claim_ids(manifest)
    for claim_id in known:
        events, issues = load_claim_events(events_root, claim_id)
        current = fold_claim_history(events, claim_id=f"claim:{claim_id}")
        if issues:
            current = ClaimCurrent(
                claim_id=f"claim:{claim_id}",
                state=None,
                event_count=len(events),
                issues=tuple([*issues, *current.issues]),
            )
        result[claim_id] = current
    if events_root.is_dir():
        known_set = set(known)
        for path in sorted(events_root.iterdir()):
            if path.name.startswith("."):
                continue
            if path.is_symlink():
                # 同上（PR #1396 指摘）: symlink directory は走査対象にしない。
                # known claim 側は load_claim_events が issue として拾う。
                result[path.name] = ClaimCurrent(
                    claim_id=f"claim:{path.name}",
                    state=None,
                    evidence_id=None,
                    heads=None,
                    event_count=0,
                    issues=(f"{path}: event directory が symlink（symlink は許さない）",),
                )
                continue
            if not path.is_dir():
                continue
            if path.name in known_set:
                continue
            events, issues = (
                load_claim_events(events_root, path.name) if _SLUG_RE.match(path.name) else ([], [])
            )
            result[path.name] = ClaimCurrent(
                claim_id=f"claim:{path.name}",
                state=None,
                event_count=len(events),
                issues=tuple(
                    [
                        f"manifest の actionable claim に無い closure event directory: {path.name}",
                        *issues,
                    ]
                ),
            )
    return result


# ---------------------------------------------------------------------------
# writer（append-only）
# ---------------------------------------------------------------------------


def build_event(
    *,
    claim_id: str,
    sequence: int,
    previous: LoadedEvent | None,
    from_state: str,
    to_state: str,
    disposition: str,
    evidence_id: str,
    artifact_path: str,
    artifact_raw_sha256: str,
    heads: Mapping[str, str],
    recorded_at: str,
    merged_pr: int,
) -> dict[str, Any]:
    """closed event を組み立て、event_id を導出して返す（検査は呼出側）。"""
    event: dict[str, Any] = {
        "schema": EVENT_SCHEMA,
        "event_id": None,
        "claim_id": claim_id if claim_id.startswith("claim:") else f"claim:{claim_id}",
        "sequence": sequence,
        "previous_event_id": previous.event_id if previous is not None else None,
        "previous_event_sha256": previous.raw_sha256 if previous is not None else None,
        "from_state": from_state,
        "to_state": to_state,
        "disposition": disposition,
        "evidence_id": evidence_id,
        "artifact_path": artifact_path,
        "artifact_raw_sha256": artifact_raw_sha256,
        "heads": {name: heads.get(name) for name in HEADS_FIELDS},
        "recorded_at": recorded_at,
        "registered_by": {"merged_pr": merged_pr},
    }
    event["event_id"] = derive_event_id(event)
    return event


def append_claim_closure_event(
    events_root: Path,
    *,
    claim_id: str,
    to_state: str,
    disposition: str,
    evidence_id: str,
    artifact_path: str,
    artifact_raw_sha256: str,
    heads: Mapping[str, str],
    merged_pr: int,
    recorded_at: str | None = None,
) -> Path:
    """既存 chain の末尾へ新 event file を 1 件追加する（既存 file は byte 不変）。

    既存 chain が不正、遷移が契約違反、同一 event_id の file が既にある場合は
    `ClaimClosureEventError`（変更 0 件）。
    """
    events, load_issues = load_claim_events(events_root, claim_id)
    if load_issues:
        raise ClaimClosureEventError("既存 event を読めない: " + "; ".join(load_issues))
    current = fold_claim_history(events, claim_id=f"claim:{claim_slug(claim_id)}")
    if current.state is None:
        raise ClaimClosureEventError("既存 chain が不正: " + "; ".join(current.issues))
    ordered = sorted(events, key=lambda item: int(item.doc.get("sequence") or 0))
    previous = ordered[-1] if ordered else None
    stamp = recorded_at or _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = build_event(
        claim_id=claim_id,
        sequence=len(ordered) + 1,
        previous=previous,
        from_state=current.state,
        to_state=to_state,
        disposition=disposition,
        evidence_id=evidence_id,
        artifact_path=artifact_path,
        artifact_raw_sha256=artifact_raw_sha256,
        heads=heads,
        recorded_at=stamp,
        merged_pr=merged_pr,
    )
    issues = validate_event_object(event) + validate_transition(previous, event)
    if issues:
        raise ClaimClosureEventError("event が契約に違反する: " + "; ".join(issues))
    raw = serialize_event(event)
    reparsed, error = _parse_event_bytes(raw)
    if reparsed is None or dict(reparsed) != event:
        raise ClaimClosureEventError(f"serialize した event を同値に再読込できない: {error}")
    directory = events_root / claim_slug(claim_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{event['event_id']}.yml"
    if target.exists():
        raise ClaimClosureEventError(f"同一 event_id の file が既にある: {target}")
    with target.open("xb") as handle:
        handle.write(raw)
    return target
