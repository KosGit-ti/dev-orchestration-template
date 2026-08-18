#!/usr/bin/env python3
"""task closeout event（schema v1）を append する writer CLI。

N-602A Phase A（`docs/specs/N-602-evidence-truthfulness-generation.md`
「Task Closeout Event Schema v1」・Migration Phase A 項目 5・AC-14）の成果物である。
merge 後の S1（第 1 state-sync）で 1 closeout につき 1 file を
`docs/ai/task-closeout-events/<event-id>.yml` へ追記する。

schema と hash 規則の正本は `scripts/ai/task_closeout_event.py` にあり、本 CLI は
入力の受け取り・artifact raw SHA の実測・append-only 書き込みだけを行う。

使い方（S1 で実行する）::

    uv run python scripts/ai/write_task_closeout_event.py \\
        --task-id N-602A --merged-pr 1301 --closeout-sequence 1 \\
        --premerge-tested-head <40hex> --actual-merge-terminal-sha <40hex> \\
        --merge-method rebase \\
        --n598b-evidence-ref docs/ai/reviews/<n598b-evidence>.json \\
        --roots docs/ai/task-closeout-events/inputs/<task>-roots.yml

`--roots` は root descriptor（`subject_id` / `evidence_type` /
`phase_or_context_id` / `artifact_path` / 省略可能な `artifact_raw_sha256`）の配列で、
`artifact_raw_sha256` を省略した場合は repo 内の実 file から実測する。root_sequence と
evidence ID は writer が canonical 順で決定的に割り当てるため、呼び出し側が手書き
しない（artifact hash から ID を作らない契約もここで担保する）。

`--roots` が担うのは各 slot への artifact path／raw SHA の割当だけである
（HRAI-REAUDIT-20260815 P0-01／P2-02・PR-E2）。slot 集合そのもの（task terminal・
scope AC leaf・global leaf・owner open claim の closure／gated residual）は
`--manifest`（既定は正本 `docs/audits/audit-materialization-manifest-2026-08-12.yml`）から
`scripts/ai/closeout_expectation.derive_expected_roots` が決定的に導出し、writer は
`--roots` の slot 集合と exact 照合する。missing／extra／duplicate、owner open claim が
非空なのに claim root 0 件、導出不能（manifest 不在／破損・alias／aggregate・
self-deadlock claim の finalizer 未宣言）は exit 2 で S1 を作らない（fail-close）。
artifact 本体が `evidence_id` field を持つ場合は writer が割り当てた root ID と一致
しなければならない（`evidence:` prefix 付きも許す）。訂正 event
（`--correction-ordinal >= 1`）は置換 slot の部分集合を扱うため exact ではなく
「導出集合の部分集合・重複なし」を要求する。

`--emit-registration` は plan／checkpoint／flag へ exact mirror する
`closeout_registration` object を stdout へ出力する。3 情報源の mirror を手書き
させないための出力であり、event の raw SHA-256 も同時に確定する。

resume 先の依存検査（HRAI-REAUDIT-20260815 P0-02・PR-E1）::

    `--resume-task-id` を与えた場合（`--resume-terminal` でない場合）、S1 時点の
    manifest で resume 先 task の `task_catalog.dependencies` を
    `scripts/ai/resolve_current_state.resolve_task_dispatch_truth` で評価する
    （`scheduling_exception=null` 前提＝research wait window 無効。closing task 自身の
    `task:<--task-id>` は R／S2 が公開する予定の truth として投影 true にする）。

    - 判定不能（manifest 不在／破損・task_catalog 未登録・aggregate・token 未解決）は
      `--allow-blocked-resume` の有無に関わらず exit 2（fail-close。未知の resume 先を
      S1 に埋めない）。
    - 未充足（評価できたが false）は既定で exit 2。`--allow-blocked-resume` を明示した
      場合だけ stderr へ警告を出して通す。この場合 S2 では resume 先を
      `state=blocked`（`blocked_by` に未充足 token）で置く運用が必須であり、resolver
      （Decision Rule 7 追記）は ready|in_progress で置かれた未充足 task を issue+null で
      止める。`closeout_registration.resume` は closed schema
      （spec 220-242 行: task_id／substep／terminal のみ）のため `state` field を足さず、
      運用は本 docstring と警告文で明示する。

repair-hold との整合（DEC-20260815-003 決定 6・PR-E3c）::

    repair-hold 検査は sequence に関わらず常に実行する（`--repair-hold-check` は互換の
    ため残置しているが実質 no-op で、`--no-repair-hold-check` でも無効化されない。
    sequence>=2 の S1₂ が repair-hold 中に限ることも、sequence=1 の通常 S1 が
    repair-hold 中に作れないことも契約そのものであり opt-out で外せてはならない）。
    検査は `--repo-root` の
    `docs/plan.md` active block を読み、`--closeout-sequence >= 2`（新 sequence の S1₂）では
    `correction_repair` が存在し `repair_target_task_id == --task-id` であること、
    `--closeout-sequence == 1` では `correction_repair` が null であること（repair 途中の通常
    S1 を拒否）を要求する（違反は exit 2）。訂正 event（`--correction-ordinal >= 1`・Rcorr'）は
    既存 slot の訂正専用で親 event に無い slot を追加できず、欠落 slot は新 sequence
    （S1₂／R₂）でだけ作れる。repair-hold 中は `correction_repair.affected_task_ids` の未回復
    task を resume 先の依存評価で false に倒す（closing task 自身の投影 true は優先）。
"""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable

ROOT = Path(__file__).resolve().parents[2]
_ROOT_TEXT = str(ROOT)
if _ROOT_TEXT not in sys.path:
    sys.path.insert(0, _ROOT_TEXT)

from scripts.ai import closeout_expectation as expectation
from scripts.ai import repair_hold
from scripts.ai import resolve_current_state as dispatch_resolver
from scripts.ai import validate_current_state as current_state
from scripts.ai.task_closeout_event import (
    EVENT_DIR_REL,
    ROOT_DESCRIPTOR_FIELDS,
    TRACKED_PATH_RE,
    TaskCloseoutEventError,
    build_event,
    build_evidence_roots,
    event_filename,
    root_slot,
    sha256_bytes,
    validate_event_object,
    validate_root_evidence_ids,
)

# resume 先の依存が未充足／判定不能で S1 を作らない場合の exit code（schema／build
# エラーの 1 と区別する）。
EXIT_RESUME_DEPENDENCY_REJECTED = 2
# manifest 導出の期待集合と `--roots` が一致しない／導出不能で S1 を作らない場合の
# exit code（PR-E2）。resume 拒否と同じ「S1 の前提が満たされない」系として 2 に揃える。
EXIT_EXPECTATION_REJECTED = 2
# repair-hold との不整合（closeout_sequence>=2 で correction_repair が無い／repair target 不一致、
# sequence 1 で repair-hold 中）で S1 を作らない場合の exit code（PR-E3c）。同じ前提違反系。
EXIT_REPAIR_HOLD_REJECTED = 2


def _load_yaml_mapping_list(path: Path) -> list[dict[str, Any]]:
    """root descriptor の YAML 配列を読む（型を厳密に検査する）。"""
    raw = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw)
    if not isinstance(loaded, list) or not loaded:
        raise TaskCloseoutEventError(f"{path}: root descriptor は 1 件以上の配列が必要である")
    descriptors: list[dict[str, Any]] = []
    for index, item in enumerate(loaded, start=1):
        if not isinstance(item, dict):
            raise TaskCloseoutEventError(f"{path}: descriptor[{index}] は object が必要である")
        unknown = sorted(set(item) - set(ROOT_DESCRIPTOR_FIELDS))
        if unknown:
            raise TaskCloseoutEventError(
                f"{path}: descriptor[{index}] に未知 field がある: {', '.join(unknown)}"
            )
        # fail-close: build_evidence_roots() が前提とする必須キーの欠落・型不正は
        # CLI 層で明示 error にする（素の KeyError で stacktrace 落ちさせない）。
        missing = sorted(
            field
            for field in ("subject_id", "evidence_type", "artifact_path")
            if not isinstance(item.get(field), str) or not item.get(field)
        )
        if missing:
            raise TaskCloseoutEventError(
                f"{path}: descriptor[{index}] の必須 field が欠落または非文字列である: "
                f"{', '.join(missing)}"
            )
        raw_phase = item.get("phase_or_context_id")
        if raw_phase is not None and not isinstance(raw_phase, str):
            raise TaskCloseoutEventError(
                f"{path}: descriptor[{index}] の phase_or_context_id は"
                "文字列または null が必要である"
            )
        raw_sha = item.get("artifact_raw_sha256")
        if raw_sha is not None and not isinstance(raw_sha, str):
            raise TaskCloseoutEventError(
                f"{path}: descriptor[{index}] の artifact_raw_sha256 は"
                "文字列または null が必要である"
            )
        descriptors.append(dict(item))
    return descriptors


def _resolve_raw_sha256(descriptor: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    """`artifact_raw_sha256` 未指定の descriptor へ実 file の raw SHA を埋める。"""
    resolved = dict(descriptor)
    if resolved.get("artifact_raw_sha256"):
        return resolved
    artifact_path = resolved.get("artifact_path")
    if not isinstance(artifact_path, str):
        raise TaskCloseoutEventError("descriptor に artifact_path がない")
    # fail-close: repo 外読み取り（`../` 等の traversal・絶対 path・symlink 逸脱）を
    # raw SHA 実測の読み取り前に拒否する。event schema 検査より前に file read が
    # 走るため、この層で封じる必要がある。
    if not TRACKED_PATH_RE.match(artifact_path) or ".." in artifact_path.split("/"):
        raise TaskCloseoutEventError(
            f"artifact_path は `docs/` 配下の相対 path が必要である（traversal 拒否）: "
            f"{artifact_path}"
        )
    target = (repo_root / artifact_path).resolve()
    if not target.is_relative_to(repo_root.resolve()):
        raise TaskCloseoutEventError(
            f"artifact_path が repo 外へ解決されるため拒否する: {artifact_path}"
        )
    if not target.is_file():
        raise TaskCloseoutEventError(
            f"artifact が存在しないため raw SHA を実測できない: {artifact_path}"
        )
    resolved["artifact_raw_sha256"] = sha256_bytes(target.read_bytes())
    return resolved


def _load_event_file(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TaskCloseoutEventError(f"{path}: event file が object ではない")
    return loaded


def _previous_roots(repo_root: Path, corrects_event_id: str) -> list[dict[str, Any]]:
    path = repo_root / EVENT_DIR_REL / f"{corrects_event_id}.yml"
    if not path.is_file():
        raise TaskCloseoutEventError(f"訂正対象 event が存在しない: {path}")
    previous = _load_event_file(path)
    verification = previous.get("terminal_verification")
    if not isinstance(verification, dict):
        raise TaskCloseoutEventError(f"{path}: terminal_verification がない")
    roots = verification.get("evidence_roots")
    if not isinstance(roots, list):
        raise TaskCloseoutEventError(f"{path}: evidence_roots がない")
    return [dict(root) for root in roots if isinstance(root, dict)]


def dump_event_bytes(event: dict[str, Any]) -> bytes:
    """event object を決定的な YAML byte 列へ落とす。"""
    text: str = yaml.safe_dump(
        event,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=4096,
    )
    return text.encode("utf-8")


def build_registration_object(
    event: dict[str, Any],
    *,
    event_path: str,
    event_sha256: str,
    resume_task_id: str | None,
    resume_substep: str | None,
    resume_terminal: bool,
) -> dict[str, Any]:
    """plan／checkpoint／flag へ exact mirror する `closeout_registration` を作る。"""
    verification = event["terminal_verification"]
    return {
        "kind": "evidence_registration",
        "subject_kind": "task_closeout",
        "event_id": event["event_id"],
        "event_path": event_path,
        "event_sha256": event_sha256,
        "required_evidence_roots": [dict(root) for root in verification["evidence_roots"]],
        "required_evidence_roots_sha256": verification["evidence_roots_sha256"],
        "required_closure_claim_ids": list(verification["closure_claim_ids"]),
        "required_closure_claim_ids_sha256": verification["closure_claim_ids_sha256"],
        "resume": {
            "task_id": resume_task_id,
            "substep": resume_substep,
            "terminal": resume_terminal,
        },
    }


def load_manifest(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """S1 が読む materialization manifest を `(payload, issues)` で返す（読込不能は issue）。"""
    if not path.is_file():
        return None, [f"manifest が存在しない: {path}"]
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, [f"manifest を読めない: {path}: {exc}"]
    if not isinstance(loaded, dict):
        return None, [f"manifest が object ではない: {path}"]
    return loaded, []


def check_root_expectation(
    *,
    manifest: dict[str, Any],
    task_id: str,
    merged_pr: int,
    roots: list[dict[str, Any]],
    correction_ordinal: int,
) -> list[str]:
    """`--roots` の slot 集合を manifest 導出集合と照合し違反理由を返す（空なら pass）。

    original（``correction_ordinal == 0``）は `derive_expected_roots` と exact 一致
    （missing／extra／duplicate を拒否）。訂正 event は置換 slot の部分集合を扱うため
    `derive_expected_slots` の部分集合（extra／duplicate 拒否・missing は許す）。導出 issue
    （alias／aggregate／deadlock claim の finalizer 未宣言 等）はそのまま拒否理由にする。
    """
    if correction_ordinal == 0:
        expected, issues = expectation.derive_expected_roots(manifest, task_id, merged_pr=merged_pr)
    else:
        expected, issues = expectation.derive_expected_slots(manifest, task_id)
    messages = [f"manifest 導出不能: {issue}" for issue in issues]
    actual = [root_slot(root) for root in roots]
    missing, extra, duplicate = expectation.compare_slot_sets(expected, actual)
    if duplicate:
        messages.append(f"--roots に同じ slot が重複している: {duplicate!r}")
    if extra:
        messages.append(f"--roots に manifest 導出集合外の slot がある（extra）: {extra!r}")
    if correction_ordinal == 0 and missing:
        messages.append(f"--roots に manifest 導出集合の slot が欠けている（missing）: {missing!r}")
        owner_claims = expectation.derive_owner_open_claims(manifest, task_id)
        claim_roots = [slot for slot in actual if slot[0].startswith("claim:")]
        if owner_claims and not claim_roots:
            messages.append(
                f"owner open claim が {len(owner_claims)} 件あるのに claim root が 0 件である"
                f"（{', '.join(owner_claims)}）"
            )
    return messages


def _load_artifact_object(path: Path) -> dict[str, Any] | None:
    """artifact を JSON／YAML として読み、object なら返す（それ以外は None）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    loaded: object
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError:
            return None
    return loaded if isinstance(loaded, dict) else None


def check_artifact_evidence_ids(*, repo_root: Path, roots: list[dict[str, Any]]) -> list[str]:
    """artifact 本体が `evidence_id` を持つ場合、writer が割り当てた root ID と一致を要求する。

    `evidence:` prefix 付き（manifest の token 形）も同一視する。artifact が object として
    読めない、または `evidence_id` を持たない場合は検査しない（schema 照合は別 validator）。
    """
    issues: list[str] = []
    for root in roots:
        artifact_path = str(root.get("artifact_path"))
        target = (repo_root / artifact_path).resolve()
        if not target.is_relative_to(repo_root.resolve()) or not target.is_file():
            continue
        payload = _load_artifact_object(target)
        if payload is None or "evidence_id" not in payload:
            continue
        declared = payload.get("evidence_id")
        if isinstance(declared, str) and declared.startswith("evidence:"):
            declared = declared[len("evidence:") :]
        if declared != root.get("evidence_id"):
            issues.append(
                f"{artifact_path}: artifact の evidence_id={declared!r} が root ID"
                f" {root.get('evidence_id')} と一致しない（slot={root_slot(root)!r}）"
            )
    return issues


def check_resume_dependencies(
    *,
    repo_root: Path,
    closing_task_id: str,
    resume_task_id: str | None,
    resume_terminal: bool,
    allow_blocked_resume: bool,
    manifest: dict[str, Any] | None = None,
    held_task_ids: Iterable[str] = (),
) -> tuple[int, list[str]]:
    """resume 先の依存を S1 時点の manifest で検査し `(exit_code, messages)` を返す。

    ``exit_code`` 0 は続行可（``messages`` は警告。`--allow-blocked-resume` で未充足を
    通した場合だけ非空）、``EXIT_RESUME_DEPENDENCY_REJECTED`` は S1 を作らない
    （``messages`` に理由）。`--resume-terminal` または resume 先未指定では何も検査
    しない（terminal S1 は次 task を持たない）。判定は module docstring の
    「resume 先の依存検査」に従い、判定不能は flag に関わらず reject する。
    ``manifest`` を渡した場合はそれを使い（`--manifest` と同一 snapshot）、None なら
    `repo_root` の正本 path から読む。``held_task_ids``（repair-hold の未回復 affected task）
    は依存評価で false に倒す（closing task 自身の投影 true が優先）。
    """
    if resume_terminal or resume_task_id is None:
        return 0, []
    truth, reasons = dispatch_resolver.resolve_task_dispatch_truth(
        resume_task_id,
        repo_root=repo_root,
        scheduling_output=None,
        manifest=manifest,
        assume_true_tokens=[f"task:{closing_task_id}"],
        held_task_ids=held_task_ids,
    )
    if truth is True:
        return 0, []
    label = f"resume 先 task:{resume_task_id}"
    if truth is None:
        return EXIT_RESUME_DEPENDENCY_REJECTED, [
            f"{label} の依存を判定できないため S1 を作らない（fail-close・"
            "--allow-blocked-resume でも通さない）: " + "; ".join(reasons)
        ]
    if allow_blocked_resume:
        return 0, [
            f"warning: {label} は依存未充足だが --allow-blocked-resume により通す。"
            "S2 では resume 先を state=blocked（blocked_by に未充足 token）で置くこと"
            "（resolver は ready|in_progress の未充足 task を issue+null で止める）: "
            + "; ".join(reasons)
        ]
    return EXIT_RESUME_DEPENDENCY_REJECTED, [
        f"{label} は依存未充足のため S1 を作らない（S2 で state=blocked を置く前提で"
        "通すには --allow-blocked-resume を明示する）: " + "; ".join(reasons)
    ]


def check_repair_hold(
    *,
    repo_root: Path,
    task_id: str,
    closeout_sequence: int,
    manifest: dict[str, Any] | None,
) -> tuple[int, list[str], set[str]]:
    """plan の active block と `--closeout-sequence` の整合を検査し `(exit_code, messages,
    held_task_ids)` を返す（PR-E3c・module docstring「repair-hold との整合」）。

    - sequence >= 2: active block の `correction_repair` が存在し
      `repair_target_task_id == task_id` でなければ reject（repair target 以外の sequence 2 を
      拒否）。
    - sequence == 1: `correction_repair` が非 null なら reject（repair 途中の通常 S1 を拒否）。
    - **plan file 自体が無い**場合は sequence >= 2 では reject、sequence 1 では通す（新規 repo／
      fixture 互換。判定対象の plan が存在しないため hold の有無を問えない）。
    - plan は存在するが active block を判定できない（delimiter はあるが YAML が壊れている等で
      `load_issue` が返る）場合は sequence 1 でも reject する。hold の有無が不明なまま通すと
      バイパスになるため fail-close する（PR #1392 Round 3 指摘。PR #1393 Round 2 で docstring
      を実挙動へ追随）。
    - `held_task_ids` は correction_repair.affected_task_ids のうち未回復の task（manifest と
      event から `resolve_current_state.held_task_ids_for_block` で判定）。
    """
    plan_path = repo_root / current_state.PLAN_REL
    block: dict[str, Any] | None = None
    load_issue: str | None = None
    if plan_path.is_file():
        try:
            plan_text = plan_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            load_issue = f"{current_state.PLAN_REL} を読めない: {exc}"
        else:
            block, load_issue = current_state.load_marker_object(plan_text, "active-queue:v1")
    else:
        load_issue = f"{current_state.PLAN_REL} が存在しない"
    repair = block.get("correction_repair") if isinstance(block, dict) else None
    if closeout_sequence >= 2:
        if load_issue is not None or block is None:
            return (
                EXIT_REPAIR_HOLD_REJECTED,
                [
                    f"closeout_sequence={closeout_sequence} の S1 は plan の repair-hold"
                    f"（correction_repair）を要求するが active block を読めない: {load_issue}"
                ],
                set(),
            )
        if not isinstance(repair, dict):
            return (
                EXIT_REPAIR_HOLD_REJECTED,
                [
                    f"closeout_sequence={closeout_sequence} の S1 は repair-hold（plan active"
                    " block の correction_repair）中にだけ作れる（現在 null・"
                    "新 sequence の根拠は plan に要る）"
                ],
                set(),
            )
        target = repair.get("repair_target_task_id")
        if target != task_id:
            return (
                EXIT_REPAIR_HOLD_REJECTED,
                [
                    f"closeout_sequence={closeout_sequence} の S1 は correction_repair."
                    f"repair_target_task_id（{target!r}）と一致する task にだけ作れる"
                    f"（--task-id={task_id}）"
                ],
                set(),
            )
    elif plan_path.is_file() and (load_issue is not None or block is None):
        # PR #1392 Round 3／#1393 Round 3 指摘: plan が存在するのに active block を判定
        # できない場合、repair-hold の有無が不明なまま sequence=1 の通常 S1 を通すと hold の
        # バイパスになる。marker 破損（load_issue あり）だけでなく marker 不在（block=None・
        # 誤編集や削除）も同じく判定不能なので fail-close する。plan 自体が無い環境
        # （新規 repo・fixture）とは区別する。
        reason = load_issue or f"{current_state.PLAN_REL} に active-queue:v1 marker がない"
        return (
            EXIT_REPAIR_HOLD_REJECTED,
            [
                f"closeout_sequence={closeout_sequence} の S1 は repair-hold の有無を"
                f" plan の active block から判定できる必要があるが読めない: {reason}"
            ],
            set(),
        )
    elif repair is not None:
        return (
            EXIT_REPAIR_HOLD_REJECTED,
            [
                "repair-hold（plan active block の correction_repair 非 null）中は"
                f" closeout_sequence=1 の通常 S1 を作れない（--task-id={task_id}・repair 途中の"
                "通常 dispatch を拒否）"
            ],
            set(),
        )
    held: set[str] = set()
    if isinstance(repair, dict) and manifest is not None:
        events, event_issues = repair_hold.load_closeout_events(repo_root)
        if event_issues:
            # PR #1394 Round 3 指摘: event 読み取り失敗を捨てると held_task_ids が空のまま
            # 進み、resume 依存評価の「hold 中 task を false に倒す」前提が崩れて判定が緩む。
            # 読めない event がある間は held を確定できないので fail-close する（P-010）。
            return (
                EXIT_REPAIR_HOLD_REJECTED,
                [
                    "repair-hold 中の held_task_ids を確定できない（closeout event を読めない）: "
                    + "; ".join(event_issues)
                ],
                set(),
            )
        held = dispatch_resolver.held_task_ids_for_block(block, manifest=manifest, events=events)
    return 0, [], held


def _resume_shape_issues(
    *, resume_task_id: str | None, resume_substep: str | None, resume_terminal: bool
) -> list[str]:
    """`closeout_registration.resume` の closed schema（spec 260-261 行）を writer 側で検査する。

    `terminal=true` では task_id／substep をともに null、`terminal=false` ではともに
    必須とする。validator（`_check_closing_block`）が S1 後に検出する形状不正を、
    S1 生成前に fail-close する（P-065 fix-on-discovery）。
    """
    issues: list[str] = []
    if resume_terminal:
        if resume_task_id is not None or resume_substep is not None:
            issues.append(
                "--resume-terminal では --resume-task-id／--resume-substep を指定できない"
                "（resume.terminal=true は task_id／substep をともに null にする）"
            )
    elif resume_task_id is None or resume_substep is None:
        issues.append(
            "--emit-registration では --resume-task-id と --resume-substep の両方、"
            "または --resume-terminal が必要（resume.terminal=false は両 field 必須）"
        )
    return issues


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--merged-pr", type=int, required=True)
    parser.add_argument("--closeout-role", default="task_terminal")
    parser.add_argument("--closeout-sequence", type=int, default=1)
    parser.add_argument("--premerge-tested-head", required=True)
    parser.add_argument("--actual-merge-terminal-sha", required=True)
    parser.add_argument("--merge-method", default="rebase")
    parser.add_argument("--n598b-evidence-ref", required=True)
    parser.add_argument(
        "--n598b-evidence-sha256",
        default=None,
        help="省略時は --n598b-evidence-ref の実 file から raw SHA-256 を実測する",
    )
    parser.add_argument("--roots", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "S1 が root slot 集合と closure claim 集合を導出する materialization manifest"
            f"（既定: <repo-root>/{current_state.MANIFEST_REL}）"
        ),
    )
    parser.add_argument("--corrects-event-id", default=None)
    parser.add_argument("--correction-ordinal", type=int, default=0)
    parser.add_argument("--status", default="verified")
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="file へ書かず生成結果だけを stdout へ出す（dry run）",
    )
    parser.add_argument(
        "--emit-registration",
        action="store_true",
        help="plan／checkpoint／flag 用の closeout_registration を stdout へ出す",
    )
    parser.add_argument("--resume-task-id", default=None)
    parser.add_argument("--resume-substep", default=None)
    parser.add_argument("--resume-terminal", action="store_true")
    parser.add_argument(
        "--repair-hold-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "plan の active block と --closeout-sequence の整合（sequence>=2 は repair-hold の"
            " repair target に限る・sequence 1 は repair-hold 中に作れない）を検査する。"
            "【互換のため残置・実質 no-op】この検査は repair-hold 契約そのものなので"
            " --no-repair-hold-check を渡しても無効化されない（PR #1396 Round 1 指摘）"
        ),
    )
    parser.add_argument(
        "--allow-blocked-resume",
        action="store_true",
        help=(
            "resume 先 task の manifest dependencies が未充足でも警告付きで S1 を作る"
            "（S2 で state=blocked を置く運用が必須。判定不能は本 flag でも通さない）"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root: Path = args.repo_root.resolve()

    # S1 の期待集合（root slot／closure claim）と resume 先依存は同じ manifest snapshot から
    # 導出する。manifest 不在／破損では S1 を作らない（fail-close・PR-E2）。
    manifest_path: Path = (
        args.manifest if args.manifest is not None else repo_root / current_state.MANIFEST_REL
    )
    manifest, manifest_issues = load_manifest(manifest_path)
    if manifest is None:
        for issue in manifest_issues:
            print(
                f"task closeout event NG: {issue}（S1 の期待集合を導出できない）", file=sys.stderr
            )
        return EXIT_EXPECTATION_REJECTED

    # PR #1393 Round 1／PR #1396 Round 1 指摘: repair-hold 検査は `--emit-registration` にも
    # `--no-repair-hold-check` にも依存させない。sequence>=2 の S1₂ が repair-hold 中に限る
    # ことも、sequence=1 の通常 S1 が repair-hold 中に作れないことも契約そのものであり、
    # opt-out で外せてはならない。よって sequence に関わらず常に検査する（fail-close）。
    hold_exit, hold_messages, held_task_ids = check_repair_hold(
        repo_root=repo_root,
        task_id=args.task_id,
        closeout_sequence=args.closeout_sequence,
        manifest=manifest,
    )
    for message in hold_messages:
        print(f"task closeout event NG: {message}", file=sys.stderr)
    if hold_exit != 0:
        return hold_exit

    # resume は `closeout_registration` だけの概念なので、`--emit-registration` なしで
    # resume 系 option を渡された場合は「検査だけ走って出力に反映されない」不整合を避けるため
    # 明示的に error にする（PR #1385 Round 1 指摘）。
    if not args.emit_registration:
        stray_resume_options = [
            name
            for name, value in (
                ("--resume-task-id", args.resume_task_id),
                ("--resume-substep", args.resume_substep),
                ("--resume-terminal", args.resume_terminal or None),
                ("--allow-blocked-resume", args.allow_blocked_resume or None),
            )
            if value is not None
        ]
        if stray_resume_options:
            joined = ", ".join(stray_resume_options)
            print(
                "task closeout event NG: resume 系 option は --emit-registration と併用する"
                f"（closeout_registration にのみ反映されるため）: {joined}",
                file=sys.stderr,
            )
            return 1
    else:
        # resume 形状（closed schema）と resume 先の依存を、event 生成・書き込みより前に
        # 検査する（reject 時に file を残さない）。
        shape_issues = _resume_shape_issues(
            resume_task_id=args.resume_task_id,
            resume_substep=args.resume_substep,
            resume_terminal=args.resume_terminal,
        )
        if shape_issues:
            for issue in shape_issues:
                print(f"task closeout event NG: {issue}", file=sys.stderr)
            return 1
        resume_exit, resume_messages = check_resume_dependencies(
            repo_root=repo_root,
            closing_task_id=args.task_id,
            resume_task_id=args.resume_task_id,
            resume_terminal=args.resume_terminal,
            allow_blocked_resume=args.allow_blocked_resume,
            manifest=manifest,
            held_task_ids=held_task_ids,
        )
        for message in resume_messages:
            print(f"# {message}", file=sys.stderr)
        if resume_exit != 0:
            return resume_exit

    try:
        descriptors = [
            _resolve_raw_sha256(descriptor, repo_root=repo_root)
            for descriptor in _load_yaml_mapping_list(args.roots)
        ]
        previous_roots = (
            _previous_roots(repo_root, args.corrects_event_id)
            if args.corrects_event_id is not None
            else None
        )
        roots = build_evidence_roots(
            descriptors,
            task_id=args.task_id,
            merged_pr=args.merged_pr,
            closeout_role=args.closeout_role,
            closeout_sequence=args.closeout_sequence,
            correction_ordinal=args.correction_ordinal,
            corrects_event_id=args.corrects_event_id,
            previous_roots=previous_roots,
        )
    except TaskCloseoutEventError as exc:
        print(f"task closeout event NG: {exc}", file=sys.stderr)
        return 1

    # `--roots` の slot 集合を manifest 導出集合と exact 照合し、artifact 本体の evidence_id
    # と root ID の一致を検査する（呼出側が slot 集合を手書きしない契約・PR-E2）。
    expectation_issues = check_root_expectation(
        manifest=manifest,
        task_id=args.task_id,
        merged_pr=args.merged_pr,
        roots=roots,
        correction_ordinal=args.correction_ordinal,
    )
    expectation_issues.extend(check_artifact_evidence_ids(repo_root=repo_root, roots=roots))
    if expectation_issues:
        for issue in expectation_issues:
            print(f"task closeout event NG: {issue}", file=sys.stderr)
        print(
            f"task closeout event NG（manifest 導出集合との照合 {len(expectation_issues)} 件・"
            f"manifest={manifest_path}）",
            file=sys.stderr,
        )
        return EXIT_EXPECTATION_REJECTED

    try:
        evidence_sha = args.n598b_evidence_sha256
        if evidence_sha is None:
            evidence_file = repo_root / args.n598b_evidence_ref
            if not evidence_file.is_file():
                raise TaskCloseoutEventError(
                    f"N-598B evidence が存在しない: {args.n598b_evidence_ref}"
                )
            evidence_sha = sha256_bytes(evidence_file.read_bytes())
        event = build_event(
            task_id=args.task_id,
            merged_pr=args.merged_pr,
            closeout_role=args.closeout_role,
            closeout_sequence=args.closeout_sequence,
            premerge_tested_head=args.premerge_tested_head,
            actual_merge_terminal_sha=args.actual_merge_terminal_sha,
            merge_method=args.merge_method,
            n598b_evidence_ref=args.n598b_evidence_ref,
            n598b_evidence_sha256=evidence_sha,
            evidence_roots=roots,
            corrects_event_id=args.corrects_event_id,
            correction_ordinal=args.correction_ordinal,
            status=args.status,
        )
    except TaskCloseoutEventError as exc:
        print(f"task closeout event NG: {exc}", file=sys.stderr)
        return 1

    issues = validate_event_object(event)
    if args.correction_ordinal == 0:
        issues.extend(validate_root_evidence_ids(event))
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        print(f"task closeout event NG（{len(issues)} 件）", file=sys.stderr)
        return 1

    payload = dump_event_bytes(event)
    rel_path = f"{EVENT_DIR_REL}/{event_filename(event)}"
    target = repo_root / rel_path

    if not args.print_only:
        if target.exists():
            print(
                f"既存 event を上書きできない（append-only）: {rel_path}",
                file=sys.stderr,
            )
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    event_sha256 = sha256_bytes(payload)
    if args.emit_registration:
        registration = build_registration_object(
            event,
            event_path=rel_path,
            event_sha256=event_sha256,
            resume_task_id=args.resume_task_id,
            resume_substep=args.resume_substep,
            resume_terminal=args.resume_terminal,
        )
        print(
            yaml.safe_dump(
                {"closeout_registration": registration},
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
                width=4096,
            ),
            end="",
        )
    else:
        print(payload.decode("utf-8"), end="")
    print(f"# event_path: {rel_path}", file=sys.stderr)
    print(f"# event_sha256: {event_sha256}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
