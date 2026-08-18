#!/usr/bin/env python3
"""mainline・checkpoint・plan active queue・local flag を合成する current-state resolver。

`docs/specs/N-598C-exact-current-state-resolver.md`（AC-01〜AC-10 実装部分）の成果物。
四情報源を読み、spec「Resolver Output Schema」の JSON を stdout へ出す。

- mainline: `git` の first-parent 履歴（`validate_current_state.resolve_mainline_ref`）
- checkpoint: `docs/ai/task-checkpoint.md`（last settled checkpoint。`checkpoint-next:v1`
  ／`research-resume:v1` marker を含む）
- plan: `docs/plan.md`「## Next」直下の `active-queue:v1` marker（唯一の active task 正本）
- local flag: `.github/full-plan-execution.flag`（git 管理外・full-plan session 専用）

`--context transition` は full-plan flag を必須にしうる通常のセッション遷移、
`--context report` は CI 等の ignored local file 前提（Decision Rule 8）。両 context とも
active block・checkpoint・research_resume の exactness 検査は緩めない（AC-03）。

`validate_current_state.py` の既存関数（marker 抽出・mainline 検査・flag 読込・
task closeout sequencing・`scripts.ai.control_unit_event` 経由の control unit closeout
判定）を import して再利用し、ロジックを複製しない（Decision Rule 1/2/6）。

- AC-06（T→S1→R→S2 汎化）は `validate_current_state.check_task_closeout_sequencing`／
  `resolve_closeout_action` が `subject_kind`（`task_closeout`／`control_unit`）を
  問わず扱う。closing task の task_id は特定 task へ固定しない。
- AC-09（`scheduling_exception`／research wait window）は本 module の
  `validate_scheduling_exception_object`／`resolve_scheduling_state`／
  `preflight_scheduling_dispatch` が実装する。token resolver（`task:`／`ac:`／
  `gate:`／`release:`／`external:`）は materialization manifest が公開する
  `verified_*` field を読む predicate に限定し（field を「揃える」生成器自体は
  スコープ外）、未解決 token は fail-close する。現行 main の実データは
  `scheduling_exception=null` のままであり、本実装は機構と synthetic fixture の
  検証に限る。
- AC-10（R-025 control unit closeout）は `scripts/ai/control_unit_event.py` の
  schema／closed catalog と、本 module 経由で結線される
  `validate_current_state.py` の `subject_kind=control_unit` 分岐が扱う。実 R-025
  の evidence・outcome には触れず、synthetic fixture（`tests/ai/test_control_unit_events.py`）
  でのみ検証する。
- HRAI-REAUDIT-20260815 P0-02（PR-E1）: Decision Rule 7 の `ready|in_progress` でも
  active task の manifest `task_catalog.dependencies` を `resolve_task_dispatch_truth`
  で all-of 評価し、未充足／判定不能なら issue を積んで `next_task.task_id=null` に
  する。`milestone:` token は manifest `terminal_conditions` の any-of として実評価し、
  `gate:R025-RESEARCH-WAIT-WINDOW-ACTIVE` だけを resolver 自身の live window 評価で
  置換する。同じ predicate を S1 writer（`write_task_closeout_event.py`）と
  `validate_current_state._check_closing_block` の resume 検査が再利用する。
- DEC-20260815-003 決定 6（PR-E3c・post-S2 回復）: `correction_repair` は
  `validate_correction_repair_object` が recovery schema v2 の closed object として完全検証し
  （構造＋`scripts/ai/repair_hold.py` による affected task／claim・missing slot・repair target の
  再導出 exact 一致＋discovery artifact／held_active／discovered_at_head の repo 検査）、
  affected task の `task:` token truth は回復完了（当該 task の `closeout_sequence>=2` の
  closeout が R まで到達し registry fold で true）まで false に override する
  （`held_task_ids`）。repair-hold 中は `next_task=null`、`closeout_action.kind` は phase
  （staging→correction_staging／registering→correction_registration／revalidating→
  全 affected 回復済みのときだけ advance_state・それ以外は null で hold 継続）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import closeout_expectation as expectation  # noqa: E402
from scripts.ai import control_unit_event, repair_hold  # noqa: E402
from scripts.ai import validate_current_state as current_state  # noqa: E402

CONTEXT_CHOICES = ("transition", "report")

ACTIVE_QUEUE_MARKER = "active-queue:v1"
CHECKPOINT_NEXT_MARKER = "checkpoint-next:v1"
RESEARCH_RESUME_MARKER = "research-resume:v1"

# spec 156-158 行: `^[A-Z]+-[0-9]+[A-Z]?$` または R 用 `^R-[0-9]{3}$`。
TASK_ID_RE = re.compile(r"^(?:[A-Z]+-[0-9]+[A-Z]?|R-[0-9]{3})$")
# spec 157 行: substep は lower kebab case。
SUBSTEP_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STATE_ENUM = ("ready", "in_progress", "blocked", "closing")
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# spec 171 行「`task:<phase-id>`、`ac:<canonical-spec-id>:<AC-id>`、materialization
# manifestの`gate:<acceptance-gate-id>`、`release:<release-gate-id>`、
# `external:<external-dependency-id>`だけを許し」の namespace prefix。
# Backlog #1331（PR #1330 Round 3 suppressed 指摘）: target_gates.*.preconditions／
# completion_evidence は非空チェックだけでは数値や未知 prefix（`foo:bar`）を
# fail-close できない。要素ごとに str 型＋許可 prefix 正規表現で検証する。
TARGET_GATE_TOKEN_RE = re.compile(
    r"^(?:task|ac|gate|release|external):[A-Za-z0-9][A-Za-z0-9._:\-]*$"
)

# ---------------------------------------------------------------------------
# AC-09: scheduling_exception（research_wait_window・spec 268-316 行）
# ---------------------------------------------------------------------------

SCHEDULING_EXCEPTION_FIELDS: tuple[str, ...] = (
    "kind",
    "started_at",
    "expires_at",
    "owner",
    "blocked_target",
    "reason_code",
    "evidence_refs",
    "resume_conditions",
    "allowed_dispatch",
    "retained_research_task",
)
SCHEDULING_EXCEPTION_KIND = "research_wait_window"
SCHEDULING_BLOCKED_TARGETS: tuple[str, ...] = (
    "R-025-freeze",
    "R-025-reserved",
    "R-025-implementation",
    "R-025-independent-acceptance",
)
SCHEDULING_REASON_CODES: tuple[str, ...] = (
    "external-dependency",
    "scheduled-data-availability",
    "independent-review-wait",
)
# spec 281 行: `allowed_dispatch: [N-600, N-601]`（exact・UTF-8 byte順）。
SCHEDULING_ALLOWED_DISPATCH: tuple[str, ...] = ("N-600", "N-601")
SCHEDULING_RETAINED_RESEARCH_TASK = "R-025"
# spec 292-294 行: 先行3 taskの全truthを先行条件とする（caller指定不可の固定 3 task）。
SCHEDULING_PRECONDITION_TASKS: tuple[str, ...] = ("N-602A", "N-598C", "N-594B")

# ---------------------------------------------------------------------------
# HRAI-REAUDIT-20260815 P0-02（PR-E1）: task dispatch の依存評価
# ---------------------------------------------------------------------------

# manifest `terminal_conditions.milestone:R025-freeze-or-research_wait_window` の
# alternative の一方。この gate の evaluator は N-598C resolver 自身
# （`acceptance_gates.gate:R025-RESEARCH-WAIT-WINDOW-ACTIVE.evaluator_id =
# n598c-r025-research-wait-window`）であるため、manifest の `verified_evidence`
# （事後登録 artifact）ではなく resolver の live 評価（`research_wait_window` かつ
# 候補 task が `allowed_dispatch` に掲載）で置換する。期限到来後の stale な登録
# evidence を dispatch 許可に流用しないための time-bound な判定である。
RESEARCH_WAIT_WINDOW_GATE_TOKEN = "gate:R025-RESEARCH-WAIT-WINDOW-ACTIVE"
# manifest `task_truth_contract.aggregate_kinds`／`task_closeout_state_machine.applies_to`:
# umbrella／control program／program derived の aggregate は通常 task ではなく
# dispatch・T/S1/R/S2 の対象外である。active task として現れた場合は fail-close する。
NON_DISPATCHABLE_TASK_KINDS: tuple[str, ...] = (
    "umbrella_aggregate",
    "control_program_aggregate",
    "program_derived_aggregate",
)
# RFC3339 UTC（`Z` 終端限定・非 UTC offset を拒否する）。
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


def _parse_rfc3339_utc(value: object) -> datetime.datetime | None:
    """`Z` 終端の RFC3339 UTC だけを受理する（非 UTC offset は拒否・spec 506 行）。"""
    if not isinstance(value, str) or not RFC3339_UTC_RE.fullmatch(value):
        return None
    try:
        return datetime.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None


def _validate_typed_token_list(tokens: object, *, label: str) -> list[str]:
    """`evidence_refs`／`resume_conditions` の非空 typed token 配列を検証する。"""
    if not isinstance(tokens, list) or not tokens:
        return [f"{label} は 1 件以上の typed token 配列である必要があります"]
    issues: list[str] = []
    for index, token in enumerate(tokens):
        if not isinstance(token, str) or not TARGET_GATE_TOKEN_RE.fullmatch(token):
            issues.append(
                f"{label}[{index}] は task:/ac:/gate:/release:/external: prefix 付きの"
                f" 文字列 token である必要があります: {token!r}"
            )
    if len(set(tokens)) != len(tokens):
        issues.append(f"{label} に重複 token があります")
    return issues


def validate_scheduling_exception_object(obj: Any) -> list[str]:
    """`scheduling_exception` closed object を検証する（spec 271-283 行・AC-09）。"""
    if not isinstance(obj, dict):
        return ["scheduling_exception は object である必要があります"]

    issues: list[str] = []
    extra_fields = sorted(set(obj) - set(SCHEDULING_EXCEPTION_FIELDS))
    if extra_fields:
        issues.append(f"scheduling_exception に unknown field があります: {extra_fields}")
    missing_fields = [field for field in SCHEDULING_EXCEPTION_FIELDS if field not in obj]
    if missing_fields:
        issues.append(f"scheduling_exception に必須 field が不足しています: {missing_fields}")
        return issues

    if obj.get("kind") != SCHEDULING_EXCEPTION_KIND:
        issues.append(
            f"scheduling_exception.kind は {SCHEDULING_EXCEPTION_KIND!r} 以外を許しません"
            f"（現在 {obj.get('kind')!r}）"
        )

    started_dt = _parse_rfc3339_utc(obj.get("started_at"))
    expires_dt = _parse_rfc3339_utc(obj.get("expires_at"))
    if started_dt is None:
        issues.append(
            "scheduling_exception.started_at は `Z` 終端の RFC3339 UTC である必要があります"
            f"（現在 {obj.get('started_at')!r}）"
        )
    if expires_dt is None:
        issues.append(
            "scheduling_exception.expires_at は `Z` 終端の RFC3339 UTC である必要があります"
            f"（現在 {obj.get('expires_at')!r}）"
        )
    if started_dt is not None and expires_dt is not None and not (started_dt < expires_dt):
        issues.append("scheduling_exception: started_at は expires_at より前である必要があります")

    owner = obj.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        issues.append(f"scheduling_exception.owner は非空文字列である必要があります: {owner!r}")

    if obj.get("blocked_target") not in SCHEDULING_BLOCKED_TARGETS:
        issues.append(
            f"scheduling_exception.blocked_target は {list(SCHEDULING_BLOCKED_TARGETS)} に"
            f" 限ります（現在 {obj.get('blocked_target')!r}）"
        )
    if obj.get("reason_code") not in SCHEDULING_REASON_CODES:
        issues.append(
            f"scheduling_exception.reason_code は {list(SCHEDULING_REASON_CODES)} に"
            f" 限ります（現在 {obj.get('reason_code')!r}）"
        )

    issues.extend(
        _validate_typed_token_list(
            obj.get("evidence_refs"), label="scheduling_exception.evidence_refs"
        )
    )
    issues.extend(
        _validate_typed_token_list(
            obj.get("resume_conditions"), label="scheduling_exception.resume_conditions"
        )
    )

    allowed_dispatch = obj.get("allowed_dispatch")
    if not isinstance(allowed_dispatch, list) or list(allowed_dispatch) != list(
        SCHEDULING_ALLOWED_DISPATCH
    ):
        issues.append(
            f"scheduling_exception.allowed_dispatch は exact {list(SCHEDULING_ALLOWED_DISPATCH)}"
            f" である必要があります（現在 {allowed_dispatch!r}）"
        )

    if obj.get("retained_research_task") != SCHEDULING_RETAINED_RESEARCH_TASK:
        issues.append(
            "scheduling_exception.retained_research_task は"
            f" {SCHEDULING_RETAINED_RESEARCH_TASK!r} 以外を許しません"
            f"（現在 {obj.get('retained_research_task')!r}）"
        )

    return issues


ACTIVE_QUEUE_REQUIRED_FIELDS: tuple[str, ...] = (
    "schema_version",
    "audit_id",
    "task_id",
    "substep",
    "lineage",
    "state",
    "source_revision",
    "source_audit_sha256",
    "blocked_by",
    "research_resume",
    "target_gates",
    "scheduling_exception",
    "closeout_registration",
    "correction_repair",
)

_MAX_HISTORY_WALK = 20000


# ---------------------------------------------------------------------------
# 低レベル git 補助（validate_current_state.py の private helper は再利用せず、
# validate_rebase_merge_contract.py と同じ「script ごとに薄い wrapper を持つ」
# 慣習に合わせる）。
# ---------------------------------------------------------------------------


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _blob_exists(repo_root: Path, revision: str, path: str) -> tuple[bool | None, str | None]:
    result = _run_git(["ls-tree", "--name-only", revision, "--", path], cwd=repo_root)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        return None, f"git ls-tree に失敗しました（{revision}:{path}）: {detail}"
    return bool(result.stdout.strip()), None


def _read_blob(repo_root: Path, revision: str, path: str) -> tuple[str | None, str | None]:
    result = _run_git(["show", f"{revision}:{path}"], cwd=repo_root)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        return None, f"git show に失敗しました（{revision}:{path}）: {detail}"
    return result.stdout, None


def _rev_parse(repo_root: Path, ref: str) -> tuple[str | None, str | None]:
    result = _run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_root)
    if result.returncode != 0 or not result.stdout.strip():
        return None, None  # 解決不能（root commit の親、または不正 ref）は issue にしない
    return result.stdout.strip().lower(), None


# ---------------------------------------------------------------------------
# Decision Rule 3/AC-01: active-queue:v1 のスキーマ検証（純粋関数）
# ---------------------------------------------------------------------------


def validate_active_queue_schema(
    obj: Any,
    *,
    manifest: Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
    events: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """`active-queue:v1` の top-level スキーマを検証する（spec 117-172 行・AC-01）。

    `target_gates.*.preconditions`／`completion_evidence` の token namespace（Backlog
    #1331）と `scheduling_exception` の closed object（AC-09）は本関数が検証する。
    `target_gates` の token resolver（token が実際に true か）の意味評価は
    resolve() 側（AC-09 は `resolve_scheduling_state`）が行い、
    `closeout_registration` の atomic batch 契約は
    `validate_current_state.check_task_closeout_sequencing`／
    `resolve_closeout_action`（AC-06）が担う。ここでは field 過不足・型・pattern・
    enum を検証する。

    `correction_repair`（非 null）は `validate_correction_repair_object` で検証する。
    ``manifest``／``repo_root``／``events`` を渡さない純粋呼出では構造（closed schema）
    だけ、渡した場合は affected set の再導出 exact 一致と repo 検査まで行う（resolve() は
    manifest を読めた場合だけ渡し、読めなければ issue にする）。
    """
    if not isinstance(obj, dict):
        return [f"{ACTIVE_QUEUE_MARKER} は object である必要があります"]

    issues: list[str] = []
    extra_fields = sorted(set(obj) - set(ACTIVE_QUEUE_REQUIRED_FIELDS))
    if extra_fields:
        issues.append(f"{ACTIVE_QUEUE_MARKER} に unknown field があります: {extra_fields}")
    missing_fields = [field for field in ACTIVE_QUEUE_REQUIRED_FIELDS if field not in obj]
    if missing_fields:
        issues.append(f"{ACTIVE_QUEUE_MARKER} に必須 field が不足しています: {missing_fields}")
        return issues

    if obj.get("schema_version") != 1:
        issues.append(f"{ACTIVE_QUEUE_MARKER}.schema_version は 1 である必要があります")

    audit_id = obj.get("audit_id")
    if not isinstance(audit_id, str) or not audit_id.strip():
        issues.append(f"{ACTIVE_QUEUE_MARKER}.audit_id は非空文字列である必要があります")

    task_id = obj.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        issues.append(
            f"{ACTIVE_QUEUE_MARKER}.task_id が canonical task ID 形式ではありません: {task_id!r}"
        )

    substep = obj.get("substep")
    if not isinstance(substep, str) or not SUBSTEP_RE.fullmatch(substep):
        issues.append(
            f"{ACTIVE_QUEUE_MARKER}.substep が lower-kebab-case ではありません: {substep!r}"
        )

    lineage = obj.get("lineage")
    if not isinstance(lineage, str) or not lineage.strip():
        issues.append(f"{ACTIVE_QUEUE_MARKER}.lineage は非空文字列である必要があります")

    state = obj.get("state")
    if state not in STATE_ENUM:
        issues.append(
            f"{ACTIVE_QUEUE_MARKER}.state が未知の値です（許可: {STATE_ENUM}）: {state!r}"
        )

    source_revision = obj.get("source_revision")
    if not isinstance(source_revision, str) or not HEX40_RE.fullmatch(source_revision):
        issues.append(f"{ACTIVE_QUEUE_MARKER}.source_revision が40桁小文字hexではありません")

    source_audit_sha256 = obj.get("source_audit_sha256")
    if not isinstance(source_audit_sha256, str) or not HEX64_RE.fullmatch(source_audit_sha256):
        issues.append(f"{ACTIVE_QUEUE_MARKER}.source_audit_sha256 が64桁小文字hexではありません")

    blocked_by = obj.get("blocked_by")
    if not isinstance(blocked_by, list) or not all(isinstance(item, str) for item in blocked_by):
        issues.append(f"{ACTIVE_QUEUE_MARKER}.blocked_by は文字列配列である必要があります")
    elif state == "blocked" and not blocked_by:
        issues.append(f"{ACTIVE_QUEUE_MARKER}.state=blocked には blocked_by が1件以上必要です")

    research_resume = obj.get("research_resume")
    if research_resume is not None:
        issues.extend(
            f"{ACTIVE_QUEUE_MARKER}.research_resume: {msg}"
            for msg in current_state.validate_research_resume_object(research_resume)
        )

    target_gates = obj.get("target_gates")
    if not isinstance(target_gates, dict) or not target_gates:
        issues.append(f"{ACTIVE_QUEUE_MARKER}.target_gates は非空 object である必要があります")
    else:
        for gate_name, gate_body in target_gates.items():
            if not isinstance(gate_body, dict):
                issues.append(
                    f"{ACTIVE_QUEUE_MARKER}.target_gates.{gate_name} は object である必要があります"
                )
                continue
            preconditions = gate_body.get("preconditions")
            if not isinstance(preconditions, list) or not preconditions:
                issues.append(
                    f"{ACTIVE_QUEUE_MARKER}.target_gates.{gate_name}.preconditions は"
                    " 非空配列である必要があります"
                )
            else:
                for index, token in enumerate(preconditions):
                    if not isinstance(token, str) or not TARGET_GATE_TOKEN_RE.fullmatch(token):
                        issues.append(
                            f"{ACTIVE_QUEUE_MARKER}.target_gates.{gate_name}.preconditions[{index}]"
                            " は task:/ac:/gate:/release:/external: prefix 付きの文字列 token で"
                            f" ある必要があります: {token!r}"
                        )
            completion_evidence = gate_body.get("completion_evidence")
            if not isinstance(completion_evidence, str) or not completion_evidence.strip():
                issues.append(
                    f"{ACTIVE_QUEUE_MARKER}.target_gates.{gate_name}.completion_evidence は"
                    " 非空文字列である必要があります"
                )
            elif not TARGET_GATE_TOKEN_RE.fullmatch(completion_evidence):
                issues.append(
                    f"{ACTIVE_QUEUE_MARKER}.target_gates.{gate_name}.completion_evidence は"
                    " task:/ac:/gate:/release:/external: prefix 付きの単一 token である必要が"
                    f" あります: {completion_evidence!r}"
                )

    # AC-09: scheduling_exception は非 null の場合、closed object を完全検証する。
    # 意味論（時刻境界・先行3 task truth・token resolver）は resolve_scheduling_state()
    # 側で評価する（ここは純粋な schema 検査に限定する）。
    scheduling_exception = obj.get("scheduling_exception")
    if scheduling_exception is not None:
        issues.extend(
            f"{ACTIVE_QUEUE_MARKER}.{msg}"
            for msg in validate_scheduling_exception_object(scheduling_exception)
        )

    # AC-06（closeout_registration／correction_repair の atomic batch 契約）は
    # 主に validate_current_state.py 側（check_task_closeout_sequencing・
    # resolve_closeout_action）が担う。ここでは type だけを検証する。
    closeout_registration = obj.get("closeout_registration")
    if closeout_registration is not None and not isinstance(closeout_registration, dict):
        issues.append(
            f"{ACTIVE_QUEUE_MARKER}.closeout_registration は null または"
            " object である必要があります"
        )

    # DEC-20260815-003 決定 6（PR-E3c）: correction_repair は recovery schema v2 の closed
    # object として完全検証する（型検査だけで通さない）。
    correction_repair = obj.get("correction_repair")
    if correction_repair is not None:
        if not isinstance(correction_repair, dict):
            issues.append(
                f"{ACTIVE_QUEUE_MARKER}.correction_repair は null または object である"
                "必要があります"
            )
        else:
            issues.extend(
                f"{ACTIVE_QUEUE_MARKER}.{msg}"
                for msg in validate_correction_repair_object(
                    correction_repair, manifest=manifest, repo_root=repo_root, events=events
                )
            )
            if state != "closing":
                issues.append(
                    f"{ACTIVE_QUEUE_MARKER}: correction_repair（repair-hold）は state=closing"
                    f" でのみ持てます（現在 {state!r}・repair 途中の通常 dispatch を拒否）"
                )

    return issues


# ---------------------------------------------------------------------------
# DEC-20260815-003 決定 6（PR-E3c）: correction_repair（recovery schema v2）の完全検証と
# repair-hold 中の task truth override
# ---------------------------------------------------------------------------


def task_registry_truth(
    task_id: str, *, manifest: Mapping[str, Any]
) -> tuple[bool | None, int | None, list[str]]:
    """`task:<id>` の registry 側 truth と、true の場合の同一 `merged_pr`（`(truth, merged_pr,
    issues)`）。

    `resolve_task_token_truth` の本体。次を全て満たす場合だけ true
    （`task_truth_contract.completion_truth` の registry 側）:

    (a) `required_terminal_evidence_types` の全 slot `(task:<id>, <type>, null)` が current pass
    (b) `scope_ac_refs`（と completion_gate 展開）の全 slot `(ac, <type>, task:<id>)` が
        current pass
    (c) global leaf（`normal_task_global_prerequisites` 展開）の全 slot
        `(gate, <type>, task:<id>)` が current pass
    (d) 上記 current entry 全件の `postmerge_mapping.merged_pr` が同一

    slot 集合は `closeout_expectation.derive_task_truth_slots` から導出する（S1 writer／R
    validator と同じ導出）。導出不能（task_catalog 未登録・aggregate・AC の phase 不一致・
    registry chain 不正）は None（fail-close）。`verified_terminal_evidence` は読まない。
    """
    canonical = _canonical_task_id(task_id, manifest=manifest)
    slots, issues = expectation.derive_task_truth_slots(manifest, canonical)
    if issues:
        return None, None, [f"task:{task_id}: {issue}" for issue in issues]
    current, registry_issues = expectation.registry_current_entries_and_issues(manifest)
    if registry_issues:
        return (
            None,
            None,
            [f"task:{task_id}: registry chain 不正: {issue}" for issue in registry_issues],
        )
    merged_prs: set[int] = set()
    for slot in slots:
        entry = current.get(slot.slot)
        if entry is None:
            return False, None, []
        merged_pr = expectation.entry_merged_pr(entry)
        if merged_pr is None:
            return False, None, []
        merged_prs.add(merged_pr)
    if len(merged_prs) != 1:
        return False, None, []
    return True, next(iter(merged_prs)), []


def recovered_affected_tasks(
    *,
    manifest: Mapping[str, Any],
    events: Mapping[str, Mapping[str, Any]],
    affected_task_ids: Iterable[str],
) -> set[str]:
    """repair-hold の affected task のうち「回復完了」した task の集合。

    回復完了＝当該 task の unsuperseded closeout event に `closeout_sequence >= 2` の
    ものがあり、その root evidence ID 全件が registry に登録済み（R₂ 済み）で、かつ registry
    fold（`task_registry_truth`）が true でその同一 `merged_pr` が当該 event の
    `merged_pr` に一致する。旧 sequence（不完全 batch）の entry だけでは回復にならない。
    """
    recovered: set[str] = set()
    current_events = repair_hold.unsuperseded_events(events)
    registry = manifest.get("evidence_registry")
    entries = registry.get("entries") if isinstance(registry, Mapping) else None
    entry_ids = set(entries.keys()) if isinstance(entries, Mapping) else set()
    for task_id in affected_task_ids:
        truth, merged_pr, _issues = task_registry_truth(task_id, manifest=manifest)
        if truth is not True or merged_pr is None:
            continue
        for event in current_events.values():
            if event.get("task_id") != task_id:
                continue
            sequence = event.get("closeout_sequence")
            if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 2:
                continue
            if event.get("merged_pr") != merged_pr:
                continue
            roots = repair_hold.event_root_ids(event)
            if roots and all(root_id in entry_ids for root_id in roots):
                recovered.add(task_id)
                break
    return recovered


def held_task_ids_for_block(
    active_block: Mapping[str, Any] | None,
    *,
    manifest: Mapping[str, Any],
    events: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """active block の `correction_repair.affected_task_ids` から未回復 task（truth false
    override 対象）を返す。correction_repair が無ければ空集合。"""
    if not isinstance(active_block, Mapping):
        return set()
    repair = active_block.get("correction_repair")
    if not isinstance(repair, Mapping):
        return set()
    affected = repair.get("affected_task_ids")
    if not isinstance(affected, list):
        return set()
    affected_ids = {item for item in affected if isinstance(item, str)}
    recovered = recovered_affected_tasks(
        manifest=manifest, events=events, affected_task_ids=affected_ids
    )
    return affected_ids - recovered


def _held_task_overrides(held_task_ids: Iterable[str]) -> dict[str, bool]:
    """未回復 affected task の `task:` token を false へ override する閉じた表。"""
    return {f"task:{task_id}": False for task_id in held_task_ids}


def validate_correction_repair_object(
    obj: Any,
    *,
    manifest: Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
    events: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """`correction_repair`（recovery schema v2）の closed object を完全検証する。

    1. 構造（`repair_hold.validate_correction_repair_structure`）: field 過不足・型・pattern・
       enum・配列の UTF-8 昇順／重複・discovery の少なくとも一方・invalid の少なくとも一方。
    2. 再導出 exact 一致（``manifest`` 指定時）: `affected_task_ids`／`affected_claim_ids`／
       `missing_slot_keys` を `repair_hold.derive_recovery_sets` で再導出し missing／extra を
       列挙、`repair_target_task_id` を未回復 affected の canonical order 先頭と照合、
       `invalid_evidence_ids` が registry entry、`invalid_batch_ids` が既存 event で owner が
       affected に含まれること、`discovery_evidence_id` が registry entry であること。
       ``events`` 省略時は ``repo_root`` の event ディレクトリから読む（両方省略なら
       event 0 件として導出し、invalid_batch_ids は解決できず issue になる）。
    3. repo 検査（``repo_root`` 指定時）: discovery artifact の存在と raw SHA、
       `discovered_at_head` の実在、`held_active` と `source_plan_revision` 時点の plan
       active block の exact 一致。
    """
    issues = repair_hold.validate_correction_repair_structure(obj)
    if issues or not isinstance(obj, Mapping):
        return issues

    if manifest is not None:
        loaded_events: Mapping[str, Mapping[str, Any]]
        if events is not None:
            loaded_events = events
        elif repo_root is not None:
            loaded_events, event_issues = repair_hold.load_closeout_events(repo_root)
            issues.extend(f"correction_repair: {issue}" for issue in event_issues)
        else:
            loaded_events = {}

        registry = manifest.get("evidence_registry")
        entries = registry.get("entries") if isinstance(registry, Mapping) else None
        entry_ids = set(entries.keys()) if isinstance(entries, Mapping) else set()
        discovery_id = obj.get("discovery_evidence_id")
        if isinstance(discovery_id, str) and discovery_id not in entry_ids:
            issues.append(
                f"correction_repair.discovery_evidence_id={discovery_id} が manifest"
                " evidence_registry.entries に存在しません（登録前 artifact は"
                " discovery_evidence で指す）"
            )
        for evidence_id in obj.get("invalid_evidence_ids") or []:
            if evidence_id not in entry_ids:
                issues.append(
                    f"correction_repair.invalid_evidence_ids: {evidence_id} が manifest"
                    " evidence_registry.entries に存在しません"
                )

        derived = repair_hold.derive_recovery_sets(
            manifest,
            events=loaded_events,
            invalid_evidence_ids=list(obj.get("invalid_evidence_ids") or []),
            invalid_batch_ids=list(obj.get("invalid_batch_ids") or []),
        )
        issues.extend(f"correction_repair: {issue}" for issue in derived.issues)
        if not derived.issues:
            declared_tasks = list(obj.get("affected_task_ids") or [])
            expected_tasks = list(derived.affected_task_ids)
            if declared_tasks != expected_tasks:
                missing = sorted(set(expected_tasks) - set(declared_tasks))
                extra = sorted(set(declared_tasks) - set(expected_tasks))
                issues.append(
                    "correction_repair.affected_task_ids が manifest 再導出と exact 一致しません"
                    f"（missing={missing}, extra={extra}）"
                )
            declared_claims = list(obj.get("affected_claim_ids") or [])
            expected_claims = list(derived.affected_claim_ids)
            if declared_claims != expected_claims:
                missing = sorted(set(expected_claims) - set(declared_claims))
                extra = sorted(set(declared_claims) - set(expected_claims))
                issues.append(
                    "correction_repair.affected_claim_ids が manifest 再導出と exact 一致しません"
                    f"（missing={missing}, extra={extra}）"
                )
            declared_keys = [
                repair_hold.sort_slot_keys([key])[0] for key in obj.get("missing_slot_keys") or []
            ]
            expected_keys = list(derived.missing_slot_keys)
            if declared_keys != expected_keys:
                declared_set = {tuple(sorted(key.items())) for key in declared_keys}
                expected_set = {tuple(sorted(key.items())) for key in expected_keys}
                missing_keys = sorted(expected_set - declared_set)
                extra_keys = sorted(declared_set - expected_set)
                issues.append(
                    "correction_repair.missing_slot_keys が manifest 再導出と exact 一致しません"
                    f"（missing {len(missing_keys)} 件・extra {len(extra_keys)} 件"
                    f"・宣言 {len(declared_keys)}／導出 {len(expected_keys)}）"
                )
            if declared_tasks == expected_tasks:
                recovered = recovered_affected_tasks(
                    manifest=manifest, events=loaded_events, affected_task_ids=expected_tasks
                )
                expected_target = repair_hold.repair_target_task_id(
                    manifest, affected_task_ids=expected_tasks, recovered_task_ids=recovered
                )
                declared_target = obj.get("repair_target_task_id")
                if expected_target is not None and declared_target != expected_target:
                    issues.append(
                        f"correction_repair.repair_target_task_id={declared_target!r} が canonical"
                        f" order（未回復 affected の最上流）先頭 {expected_target} と一致しません"
                    )

    if repo_root is not None:
        issues.extend(
            f"correction_repair.{issue}"
            for issue in repair_hold.check_discovery_artifact(obj, repo_root=repo_root)
        )
        issues.extend(
            f"correction_repair.{issue}"
            for issue in repair_hold.check_discovered_at_head(obj, repo_root=repo_root)
        )
        issues.extend(
            f"correction_repair.{issue}"
            for issue in repair_hold.check_held_active_against_plan(obj, repo_root=repo_root)
        )
    return issues


# ---------------------------------------------------------------------------
# Decision Rule 3/AC-04: checkpoint-next:v1 のスキーマ検証（純粋関数）
# ---------------------------------------------------------------------------


def validate_checkpoint_next_object(obj: Any) -> list[str]:
    """`checkpoint-next:v1` を検証する（spec 65-78 行・98-99 行）。

    通常時は `{schema_version, task_id, substep}` の3 fieldちょうど、terminal 時は
    `{schema_version, task_id: null, substep: null, terminal: true}` の4 fieldちょうど
    だけを許す。`terminal=false` の明示や unknown field は拒否する。
    """
    if not isinstance(obj, dict):
        return [f"{CHECKPOINT_NEXT_MARKER} は object である必要があります"]

    issues: list[str] = []
    if obj.get("schema_version") != 1:
        issues.append(f"{CHECKPOINT_NEXT_MARKER}.schema_version は 1 である必要があります")

    keys = set(obj)
    if "terminal" in obj:
        if keys != {"schema_version", "task_id", "substep", "terminal"}:
            issues.append(
                f"{CHECKPOINT_NEXT_MARKER} の terminal object は"
                " schema_version/task_id/substep/terminal の4 fieldちょうどである必要があります"
            )
            return issues
        if obj.get("terminal") is not True:
            issues.append(f"{CHECKPOINT_NEXT_MARKER}.terminal は true 以外を許しません")
        if obj.get("task_id") is not None or obj.get("substep") is not None:
            issues.append(
                f"{CHECKPOINT_NEXT_MARKER} の terminal object は task_id/substep がともに"
                " null である必要があります"
            )
        return issues

    if keys != {"schema_version", "task_id", "substep"}:
        issues.append(
            f"{CHECKPOINT_NEXT_MARKER} は schema_version/task_id/substep の3 fieldちょうどである"
            " 必要があります（terminal 以外の unknown field は拒否）"
        )
        return issues

    task_id = obj.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        issues.append(f"{CHECKPOINT_NEXT_MARKER}.task_id が不正です: {task_id!r}")
    substep = obj.get("substep")
    if not isinstance(substep, str) or not SUBSTEP_RE.fullmatch(substep):
        issues.append(
            f"{CHECKPOINT_NEXT_MARKER}.substep が lower-kebab-case ではありません: {substep!r}"
        )
    return issues


def _load_single_marker(
    text: str, marker: str, *, label: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """marker が「不在」か「exactly-one の整形済みペア」かを判定して読む。

    不在（開始/終了タグとも 0 件）は意味論を呼び出し側へ委ねるため issue を出さず
    `(None, [])` を返す。2 件以上／片側欠損等の不正 delimiter、または delimiter は
    正確に 1 組だが YAML が破損している場合は issue を返す（「不在」と混同しない。
    PR #1330 Round 2 是正）。ロジックは
    `validate_current_state.load_marker_object` を再利用し複製しない。
    """
    obj, issue = current_state.load_marker_object(text, marker)
    if issue is not None:
        return None, [f"{label}: {issue}"]
    return obj, []


# ---------------------------------------------------------------------------
# AC-08 準備: source_plan_revision（spec 488-495 行）
# ---------------------------------------------------------------------------


def resolve_source_plan_revision(
    *,
    repo_root: Path,
    main_head: str,
    plan_disk_text: str,
) -> tuple[str | None, str | None]:
    """canonical delimiter span の main first-parent 差分から source_plan_revision を導出する。

    HEAD が main_head と一致する場合に限り、作業treeのspanをHEADのspanと比較し
    dirty span を拒否する（fixture／report context のような HEAD != main_head では
    working tree の意味を持たないため比較しない）。
    """
    working_head, _issue = _rev_parse(repo_root, "HEAD")
    if working_head is not None and working_head == main_head:
        disk_span = current_state.extract_marker_span(plan_disk_text, ACTIVE_QUEUE_MARKER)
        head_text, blob_issue = _read_blob(repo_root, main_head, current_state.PLAN_REL)
        if blob_issue is not None:
            return None, blob_issue
        assert head_text is not None
        head_span = current_state.extract_marker_span(head_text, ACTIVE_QUEUE_MARKER)
        if disk_span != head_span:
            return None, (
                "source_plan_revision: working tree の active-queue:v1 span が HEAD と"
                " 一致しません（dirty span は推測せず fail-close します）"
            )

    current_sha = main_head
    current_text, issue = _read_blob(repo_root, main_head, current_state.PLAN_REL)
    if issue is not None:
        return None, issue
    assert current_text is not None
    open_c, close_c, pair_c = current_state.count_marker_tags(current_text, ACTIVE_QUEUE_MARKER)
    if not (open_c == 1 and close_c == 1 and pair_c == 1):
        return None, (
            "source_plan_revision: main_head の active-queue:v1 span を一意に抽出できません"
        )
    current_span = current_state.extract_marker_span(current_text, ACTIVE_QUEUE_MARKER)
    assert current_span is not None

    for _ in range(_MAX_HISTORY_WALK):
        parent_result = _run_git(
            ["rev-parse", "--verify", "--quiet", f"{current_sha}^1"], cwd=repo_root
        )
        if parent_result.returncode != 0 or not parent_result.stdout.strip():
            return None, (
                "source_plan_revision: root commit まで active-queue:v1 span の差分が"
                "見つからず導出できません"
            )
        parent_sha = parent_result.stdout.strip().lower()

        exists, exists_issue = _blob_exists(repo_root, parent_sha, current_state.PLAN_REL)
        if exists_issue is not None:
            return None, exists_issue
        if not exists:
            return current_sha, None  # introduced sentinel: 親に plan.md 自体が存在しない

        parent_text, parent_issue = _read_blob(repo_root, parent_sha, current_state.PLAN_REL)
        if parent_issue is not None:
            return None, parent_issue
        assert parent_text is not None
        parent_open, parent_close, parent_pair = current_state.count_marker_tags(
            parent_text, ACTIVE_QUEUE_MARKER
        )
        if parent_open == 0 and parent_close == 0:
            return current_sha, None  # introduced sentinel: 親に span がない
        if not (parent_open == 1 and parent_close == 1 and parent_pair == 1):
            return None, (
                f"source_plan_revision: commit {parent_sha} の active-queue:v1 span が"
                "一意に抽出できません（欠損／重複）"
            )
        parent_span = current_state.extract_marker_span(parent_text, ACTIVE_QUEUE_MARKER)
        if parent_span != current_span:
            return current_sha, None
        current_sha = parent_sha

    return None, "source_plan_revision: first-parent 履歴の探索上限に達しました"


# ---------------------------------------------------------------------------
# Decision Rule 8/9: local flag と research_resume の exactness（spec 100-113 行）
# ---------------------------------------------------------------------------


def _flag_is_active(flag_data: dict[str, Any] | None, *, flag_file_exists: bool) -> bool:
    """full-plan flag の active 判定（`scripts/hooks/full_plan_flag.py` と同じ契約）。

    `current_state.load_flag` は「ファイル不在」と「ファイルはあるが JSON として
    読めない／object でない」のどちらも `flag_data=None` を返すため、両者を区別する
    には `flag_file_exists` を別途渡す必要がある。契約は
    `scripts/hooks/full_plan_flag.is_full_plan_execution_active` と同じ:
    不在は inactive（false 側の既定）、破損は fail-safe で active 扱い
    （壊れた flag を「未使用」と誤認して exactness 検査を素通りさせない）。
    """
    if flag_data is not None:
        return flag_data.get("active", True) is not False
    return flag_file_exists


def _commit_exists(repo_root: Path, revision: str) -> bool:
    """revision がリポジトリに実在する commit（または commit へ peel できる object）かを返す。"""
    result = _run_git(["cat-file", "-e", f"{revision}^{{commit}}"], cwd=repo_root)
    return result.returncode == 0


def _commit_reachable_from_any_ref(
    repo_root: Path, revision: str
) -> tuple[bool | None, str | None]:
    """revision がいずれかの ref（branch／remote-tracking／tag）から到達可能かを返す。

    2026-08-14 是正（P-070・spec 欠陥訂正）: 本 repo は rebase merge 一本の運用で
    あり、research branch（例: ``research/r025-audit-loop``）上の commit は構造的に
    mainline（main／origin/main）の祖先になり得ない。R-025 の
    ``git_last_settled_revision`` は research branch 上で確定した round の commit を
    指すことが DEC-20260812-001 で認可されており、mainline ancestry ではなく
    「リポジトリに実在し、いずれかの ref から到達可能」であることだけを要求する。

    PR #1330 Round 1（Copilot）是正: ``git branch -a --contains`` は branch／
    remote-tracking ref しか見ず、tag だけから到達可能な commit（branch 削除後に
    tag だけが残るケース等）を見落とす。``git for-each-ref --contains`` は
    refs/heads・refs/remotes・refs/tags を含む全 ref を対象にするため、こちらへ
    差し替える。
    """
    result = _run_git(["for-each-ref", "--contains", revision], cwd=repo_root)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        return None, f"git for-each-ref --contains の実行に失敗しました（{revision}）: {detail}"
    return bool(result.stdout.strip()), None


def check_research_resume_exactness(
    *,
    plan_value: Any,
    checkpoint_obj: dict[str, Any] | None,
    flag_data: dict[str, Any] | None,
    flag_active: bool,
    local_state_required: bool,
    repo_root: Path,
) -> list[str]:
    """Decision Rule 9: checkpoint／plan／flag の `research_resume` を exact 照合する。"""
    issues: list[str] = []
    if plan_value is None:
        if checkpoint_obj is not None:
            issues.append(
                f"{current_state.PLAN_REL} の research_resume が null ですが"
                f" {current_state.CHECKPOINT_REL} に {RESEARCH_RESUME_MARKER} があります"
                "（併存拒否）"
            )
        if flag_data is not None and flag_data.get("research_resume") is not None:
            issues.append(
                f"{current_state.PLAN_REL} の research_resume が null ですが"
                f" {current_state.FLAG_REL} に research_resume があります（併存拒否）"
            )
        return issues

    schema_issues = current_state.validate_research_resume_object(plan_value)
    issues.extend(f"{current_state.PLAN_REL}.research_resume: {msg}" for msg in schema_issues)
    if schema_issues:
        return issues

    if checkpoint_obj is None:
        issues.append(
            f"{current_state.PLAN_REL} の research_resume が非 null ですが"
            f" {current_state.CHECKPOINT_REL} に {RESEARCH_RESUME_MARKER} がありません"
        )
    else:
        if checkpoint_obj.get("schema_version") != 1:
            issues.append(
                f"{current_state.CHECKPOINT_REL}.{RESEARCH_RESUME_MARKER}.schema_version は"
                " 1 である必要があります"
            )
        checkpoint_normalized = {k: v for k, v in checkpoint_obj.items() if k != "schema_version"}
        checkpoint_schema_issues = current_state.validate_research_resume_object(
            checkpoint_normalized
        )
        issues.extend(
            f"{current_state.CHECKPOINT_REL}.{RESEARCH_RESUME_MARKER}: {msg}"
            for msg in checkpoint_schema_issues
        )
        if not checkpoint_schema_issues and checkpoint_normalized != plan_value:
            issues.append(
                f"{current_state.CHECKPOINT_REL} の {RESEARCH_RESUME_MARKER} が"
                f" {current_state.PLAN_REL} の research_resume と exact 一致しません"
            )

    if local_state_required and flag_active:
        flag_rr = flag_data.get("research_resume") if flag_data is not None else None
        if flag_rr is None:
            issues.append(f"{current_state.FLAG_REL}: research_resume が必須ですが欠落しています")
        else:
            flag_schema_issues = current_state.validate_research_resume_object(flag_rr)
            issues.extend(
                f"{current_state.FLAG_REL}.research_resume: {msg}" for msg in flag_schema_issues
            )
            if not flag_schema_issues and flag_rr != plan_value:
                issues.append(
                    f"{current_state.FLAG_REL}.research_resume が"
                    f" {current_state.PLAN_REL} と exact 一致しません"
                )

    # 2026-08-14 是正（P-070・spec 欠陥訂正）: mainline ancestry ではなく、
    # 「実在し、いずれかの ref から到達可能」を要求する（research branch 上で確定した
    # round を受理するため。docs/specs/N-598C-exact-current-state-resolver.md 107-113 行）。
    revision = plan_value.get("git_last_settled_revision")
    if isinstance(revision, str) and HEX40_RE.fullmatch(revision):
        if not _commit_exists(repo_root, revision):
            issues.append(
                "research_resume.git_last_settled_revision: commit"
                f" {revision} がリポジトリに実在しません"
            )
        else:
            reachable, reachable_issue = _commit_reachable_from_any_ref(repo_root, revision)
            if reachable_issue is not None:
                issues.append(f"research_resume.git_last_settled_revision: {reachable_issue}")
            elif reachable is False:
                issues.append(
                    "research_resume.git_last_settled_revision: commit"
                    f" {revision} はどの ref からも到達不能です（dangling の疑い）"
                )

    remediation_applied = plan_value.get("remediation_applied")
    git_round = plan_value.get("git_last_settled_round")
    local_round = plan_value.get("local_session_resume_round")
    finding_counts = plan_value.get("finding_counts")
    if (
        isinstance(remediation_applied, bool)
        and isinstance(git_round, int)
        and not isinstance(git_round, bool)
        and isinstance(local_round, int)
        and not isinstance(local_round, bool)
    ):
        if remediation_applied is False:
            total_findings = (
                sum(finding_counts.values())
                if isinstance(finding_counts, dict)
                and all(
                    isinstance(v, int) and not isinstance(v, bool) for v in finding_counts.values()
                )
                else None
            )
            if not (local_round > git_round):
                issues.append(
                    "research_resume: remediation_applied=false では"
                    " local_session_resume_round > git_last_settled_round が必須です"
                )
            if total_findings is None or total_findings < 1:
                issues.append(
                    "research_resume: remediation_applied=false では finding_counts の合計が"
                    "1以上必須です"
                )
        else:
            if not (git_round >= local_round):
                issues.append(
                    "research_resume: remediation_applied=true では git_last_settled_round が"
                    " local_session_resume_round 以上である必要があります"
                    "（是正を含む新しい settled revision の代理検証・Decision Rule 9）"
                )

    return issues


# ---------------------------------------------------------------------------
# AC-09: token resolver（spec 307-317 行）・scheduling_exception の意味評価
#
# スコープ: `task:`／`gate:`／`ac:` は materialization manifest `evidence_registry.entries`
# の registry current entry（`truth_entry_selection` 相当・`scripts/ai/closeout_expectation.
# registry_current_entries`）から判定する（HRAI-REAUDIT-20260815 P0-01・PR-E2。旧実装が
# 読んでいた `task_catalog.*.verified_terminal_evidence`／`acceptance_gates.*.
# verified_evidence`／`ac_leaf_registry.*.verified_evidence_by_phase` は誰も更新しない
# 宣言 field であり、registry と乖離するため読まない）。`external:` は
# `external_dependencies.*.verified_evidence` を、`release:` は `release_gates` の member
# 集合を再帰的 all-of で評価する。circular reference・未知 token・manifest 読込不能は
# 全て unresolved（`None`・fail-close の issue）として扱い、pass 側へ倒さない。
# ---------------------------------------------------------------------------


def _load_manifest(repo_root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """manifest を読む（`(payload, issues)`）。

    PR #1334 Round 1（review id 4937094684）是正: `yaml.YAMLError` に加えて
    読込不能（`OSError`）・非 UTF-8 バイト列（`UnicodeDecodeError`）も例外を
    伝播させず issue として返す（P-010・fail-close）。
    """
    manifest_path = repo_root / current_state.MANIFEST_REL
    if not manifest_path.is_file():
        return None, [f"{current_state.MANIFEST_REL} が存在しないため token を解決できません"]
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, [f"{current_state.MANIFEST_REL} を読めません: {exc}"]
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [f"{current_state.MANIFEST_REL} を YAML として読めません: {exc}"]
    if not isinstance(loaded, dict):
        return None, [f"{current_state.MANIFEST_REL} が object ではありません"]
    return loaded, []


def _canonical_task_id(task_id: str, *, manifest: Mapping[str, Any]) -> str:
    """`task_aliases` が単一 task を指す alias なら canonical task ID へ写す（それ以外は不変）。"""
    task_catalog = manifest.get("task_catalog")
    if isinstance(task_catalog, dict) and f"task:{task_id}" in task_catalog:
        return task_id
    aliases = manifest.get("task_aliases")
    alias = aliases.get(f"task:{task_id}") if isinstance(aliases, dict) else None
    alias_ids = alias.get("task_ids") if isinstance(alias, dict) else None
    if isinstance(alias_ids, list) and len(alias_ids) == 1 and isinstance(alias_ids[0], str):
        return str(alias_ids[0]).partition(":")[2] or task_id
    return task_id


def resolve_task_token_truth(
    task_id: str, *, manifest: Mapping[str, Any], held_task_ids: Iterable[str] = ()
) -> tuple[bool | None, list[str]]:
    """`task:<id>` の truth を registry current entry から判定する（PR-E2）。

    判定本体は `task_registry_truth`（terminal＋scope AC＋global leaf の全 slot が current pass
    かつ同一 merged_pr）。``held_task_ids``（repair-hold の未回復 affected task・PR-E3c）に
    含まれる task は registry の状態に関わらず false を返す（DEC-20260815-003 安全床
    「旧 S2 truth は回復まで false 扱い」）。回復判定は `recovered_affected_tasks` が行い、
    純粋関数である本関数は集合を引数で受ける。
    """
    canonical = _canonical_task_id(task_id, manifest=manifest)
    held = set(held_task_ids)
    if task_id in held or canonical in held:
        return False, []
    truth, _merged_pr, issues = task_registry_truth(task_id, manifest=manifest)
    return truth, issues


def resolve_external_token_truth(
    external_id: str, *, manifest: dict[str, Any]
) -> tuple[bool | None, list[str]]:
    """`external:<id>` の truth を `external_dependencies.*.verified_evidence` で判定する。"""
    deps = manifest.get("external_dependencies")
    entry = deps.get(f"external:{external_id}") if isinstance(deps, dict) else None
    if not isinstance(entry, dict):
        return None, [f"external:{external_id} が manifest external_dependencies に見つかりません"]
    verified = entry.get("verified_evidence")
    return bool(isinstance(verified, list) and verified), []


def _subject_has_current_entry(
    subject_id: str,
    *,
    manifest: dict[str, Any],
    required_evidence_type: object,
    phase_or_context_id: str | None,
    require_phase: bool,
    require_phase_present: bool = False,
) -> tuple[bool | None, list[str]]:
    """subject の registry current entry（status=pass・unsuperseded）の有無を返す。

    ``require_phase=True`` は `phase_or_context_id` の slot に exact 一致する entry を要求
    する。False（bare token の評価）は当該 subject の current entry がいずれかの
    phase／context に 1 件でもあれば true とする（bare token には呼出 phase が無く、
    phase exact の束縛は task truth／control unit evaluator が phase を渡して行う）。
    ``require_phase_present=True`` は bare 評価でも phase が null の entry を数えない
    （PR #1387 Round 2 inline 指摘: phase-scoped な subject に null-phase entry が紛れると
    bare 評価が真になり fail-close できない）。``required_evidence_type`` は非空文字列で
    あることを要求し（不正なら判定不能）、evidence_type の exact 一致を常に要求する。
    """
    # PR #1391 Round 2 指摘: required_evidence_type が manifest 側で欠落／不正なとき
    # evidence_type の照合を素通しすると、別種の entry で truth が真になりうる。判定不能
    # として fail-close する（P-010）。
    if not isinstance(required_evidence_type, str) or not required_evidence_type:
        return None, [
            f"{subject_id}: manifest の required_evidence_type が文字列でないため"
            f" registry current entry を評価できません: {required_evidence_type!r}"
        ]
    current, issues = expectation.registry_current_entries_and_issues(manifest)
    if issues:
        return None, [f"{subject_id}: registry chain 不正: {issue}" for issue in issues]
    for (entry_subject, entry_type, entry_phase), _entry in current.items():
        if entry_subject != subject_id:
            continue
        if entry_type != required_evidence_type:
            continue
        if require_phase and entry_phase != phase_or_context_id:
            continue
        if not require_phase and require_phase_present and entry_phase is None:
            continue
        return True, []
    return False, []


def resolve_gate_token_truth(
    gate_id: str, *, manifest: dict[str, Any], phase_or_context_id: str | None = None
) -> tuple[bool | None, list[str]]:
    """`gate:<id>` の truth を registry current entry から判定する（PR-E2）。

    scalar gate は slot `(gate:<id>, <required_evidence_type>, null)`。phase-scoped global
    gate（`kind=phase_scoped_global_acceptance_leaf`）は ``phase_or_context_id`` を渡した
    場合その phase slot に exact 一致、bare（None）ではいずれかの phase／context の current
    entry があれば true。`acceptance_gates.*.verified_evidence` は読まない。
    """
    gates = manifest.get("acceptance_gates")
    token = f"gate:{gate_id}"
    entry = gates.get(token) if isinstance(gates, dict) else None
    if not isinstance(entry, dict):
        return None, [f"{token} が manifest acceptance_gates に見つかりません"]
    phase_scoped = expectation.gate_is_phase_scoped(entry)
    if phase_scoped:
        if phase_or_context_id is not None:
            return _subject_has_current_entry(
                token,
                manifest=manifest,
                required_evidence_type=entry.get("required_evidence_type"),
                phase_or_context_id=phase_or_context_id,
                require_phase=True,
            )
        # bare 評価（PR #1387 Round 2 inline 指摘）: phase-scoped gate の slot は
        # `(gate, <type>, <phase>)` であり null-phase entry は当該 gate の slot ではない。
        # 宣言 phase（verified_evidence_by_phase の key）があれば各 phase を exact 評価し、
        # 宣言が無い場合でも null-phase entry は数えない（fail-close 側へ倒す）。
        declared = entry.get("verified_evidence_by_phase")
        phases = sorted(declared) if isinstance(declared, Mapping) else []
        if phases:
            issues: list[str] = []
            for phase in phases:
                truth, phase_issues = _subject_has_current_entry(
                    token,
                    manifest=manifest,
                    required_evidence_type=entry.get("required_evidence_type"),
                    phase_or_context_id=str(phase),
                    require_phase=True,
                )
                if truth is None:
                    issues.extend(phase_issues)
                    continue
                if truth:
                    return True, []
            if issues:
                return None, issues
            return False, []
        return _subject_has_current_entry(
            token,
            manifest=manifest,
            required_evidence_type=entry.get("required_evidence_type"),
            phase_or_context_id=None,
            require_phase=False,
            require_phase_present=True,
        )
    return _subject_has_current_entry(
        token,
        manifest=manifest,
        required_evidence_type=entry.get("required_evidence_type"),
        phase_or_context_id=None,
        require_phase=True,
    )


def resolve_ac_token_truth(
    ac_id: str, *, manifest: dict[str, Any], phase_or_context_id: str | None = None
) -> tuple[bool | None, list[str]]:
    """`ac:<spec>:<AC-id>` の truth を registry current entry から判定する（PR-E2）。

    ``phase_or_context_id`` を渡した場合は slot `(ac, <required_evidence_type>, <phase>)` に
    exact 一致、bare（None）では `phase_tasks` のいずれかの phase の current entry があれば
    true。`ac_leaf_registry.*.verified_evidence_by_phase` は読まない。
    """
    registry = manifest.get("ac_leaf_registry")
    token = f"ac:{ac_id}"
    entry = registry.get(token) if isinstance(registry, dict) else None
    if not isinstance(entry, dict):
        return None, [f"{token} が manifest ac_leaf_registry に見つかりません"]
    required_evidence_type = entry.get("required_evidence_type")
    if phase_or_context_id is not None:
        return _subject_has_current_entry(
            token,
            manifest=manifest,
            required_evidence_type=required_evidence_type,
            phase_or_context_id=phase_or_context_id,
            require_phase=True,
        )
    # PR #1387 Round 2 suppressed 指摘: bare token を「どの phase の entry でも true」に
    # すると、誤った phase へ登録した AC leaf が release 等の評価を押し上げ、truthfulness
    # contract を弱める。bare は phase_tasks のいずれかの phase slot に current entry が
    # ある場合だけ true とする（phase_tasks が空／非文字列なら判定不能＝fail-close）。
    phase_tasks = [item for item in entry.get("phase_tasks", []) if isinstance(item, str)]
    if not phase_tasks:
        return None, [f"{token} の phase_tasks が空のため bare token を評価できません"]
    issues: list[str] = []
    for phase in phase_tasks:
        truth, phase_issues = _subject_has_current_entry(
            token,
            manifest=manifest,
            required_evidence_type=required_evidence_type,
            phase_or_context_id=phase,
            require_phase=True,
        )
        if truth is None:
            issues.extend(phase_issues)
            continue
        if truth:
            return True, []
    if issues:
        return None, issues
    return False, []


def _resolve_all_of_tokens(
    members: list[Any],
    *,
    label: str,
    manifest: dict[str, Any],
    overrides: Mapping[str, bool] | None,
    _visited: frozenset[str],
) -> tuple[bool | None, list[str], list[str]]:
    """token 配列を all-of 評価する共通 helper（`(truth, unresolved_issues, unmet_tokens)`）。

    未解決（None）の member が 1 件でもあれば、確定 false の member が混在していても
    全体は None（判定不能・fail-close）とし、未解決理由と未充足 token の両方を issue に
    残す。論理的には false ∧ unknown = false だが、dispatch 判定では False（未充足＝
    `--allow-blocked-resume` で blocked 前提の resume を許しうる）と None（判定不能＝
    どの経路でも許さない）を区別しており、未解決 token を「未充足」に丸めると判定不能な
    依存を通しうる（PR #1380 Round 1）。未解決がなく確定 false があれば False、
    全て true なら true。
    """
    issues: list[str] = []
    unmet: list[str] = []
    for member in members:
        if not isinstance(member, str):
            issues.append(f"{label}: member token が文字列ではありません: {member!r}")
            continue
        truth, member_issues = resolve_scheduling_token(
            member, manifest=manifest, overrides=overrides, _visited=_visited
        )
        if truth is None:
            issues.extend(member_issues)
        elif truth is False:
            unmet.append(member)
    if issues:
        if unmet:
            issues.append(f"{label}: 未解決 token と併存する未充足 token: {', '.join(unmet)}")
        return None, issues, unmet
    if unmet:
        return False, [], unmet
    return True, [], []


def resolve_release_token_truth(
    release_id: str,
    *,
    manifest: dict[str, Any],
    overrides: Mapping[str, bool] | None = None,
    _visited: frozenset[str] = frozenset(),
) -> tuple[bool | None, list[str]]:
    """`release:<id>` の truth を `release_gates.*`（member token の all-of）で判定する。"""
    token = f"release:{release_id}"
    if token in _visited:
        return None, [f"{token}: release_gates に循環参照があります"]
    gates = manifest.get("release_gates")
    members = gates.get(token) if isinstance(gates, dict) else None
    if not isinstance(members, list) or not members:
        return None, [f"{token} が manifest release_gates に見つかりません"]
    truth, issues, _unmet = _resolve_all_of_tokens(
        members,
        label=token,
        manifest=manifest,
        overrides=overrides,
        _visited=_visited | {token},
    )
    return truth, issues


def resolve_milestone_token_truth(
    milestone_id: str,
    *,
    manifest: dict[str, Any],
    overrides: Mapping[str, bool] | None = None,
    _visited: frozenset[str] = frozenset(),
) -> tuple[bool | None, list[str]]:
    """`milestone:<id>` の truth を manifest `terminal_conditions.*` で判定する。

    manifest `token_resolution.namespaces.milestone`（terminal_conditions に定義した
    内部状態）に対応する。`operator: any` は `alternatives[].all`（各 all-of）の
    any-of、`operator: all` は `evidence`＋`owner_tasks` の all-of として評価する。
    any-of では確定 true の alternative が 1 件でもあれば true（未解決 alternative が
    混在しても true が優先。all-of で確定 false が優先されるのと双対）。確定 true が
    なく未解決 alternative があれば None（fail-close）、全て確定 false なら false。
    未定義 milestone・未知 operator・空 alternative・循環は None とする。

    HRAI-REAUDIT-20260815 P0-02（PR-E1）で追加。`task_catalog.task:N-600.dependencies`
    の `milestone:R025-freeze-or-research_wait_window` を、従来の「milestone は評価対象
    から除く（skip）」ではなく manifest の any-of（`release:R025-reserved` または
    `gate:R025-RESEARCH-WAIT-WINDOW-ACTIVE`）として実評価するために必要である。
    """
    token = f"milestone:{milestone_id}"
    if token in _visited:
        return None, [f"{token}: terminal_conditions に循環参照があります"]
    conditions = manifest.get("terminal_conditions")
    entry = conditions.get(token) if isinstance(conditions, dict) else None
    if not isinstance(entry, dict):
        return None, [f"{token} が manifest terminal_conditions に見つかりません（未解決）"]
    operator = entry.get("operator")
    nested_visited = _visited | {token}
    if operator == "any":
        alternatives = entry.get("alternatives")
        if not isinstance(alternatives, list) or not alternatives:
            return None, [f"{token}: operator=any には非空の alternatives が必要です（未解決）"]
        issues: list[str] = []
        for index, alternative in enumerate(alternatives):
            members = alternative.get("all") if isinstance(alternative, dict) else None
            if not isinstance(members, list) or not members:
                issues.append(f"{token}: alternatives[{index}].all が非空配列ではありません")
                continue
            truth, alt_issues, _unmet = _resolve_all_of_tokens(
                members,
                label=f"{token}.alternatives[{index}]",
                manifest=manifest,
                overrides=overrides,
                _visited=nested_visited,
            )
            if truth is True:
                return True, []
            if truth is None:
                issues.extend(alt_issues)
        if issues:
            return None, issues
        return False, []
    if operator == "all":
        members = []
        for field in ("evidence", "owner_tasks"):
            values = entry.get(field)
            if isinstance(values, list):
                members.extend(values)
        if not members:
            return None, [f"{token}: operator=all に評価対象 token がありません（未解決）"]
        truth, issues, _unmet = _resolve_all_of_tokens(
            members,
            label=token,
            manifest=manifest,
            overrides=overrides,
            _visited=nested_visited,
        )
        return truth, issues
    return None, [f"{token}: operator が未知です（{operator!r}・許可は any/all）"]


def resolve_scheduling_token(
    token: str,
    *,
    manifest: dict[str, Any],
    overrides: Mapping[str, bool] | None = None,
    _visited: frozenset[str] = frozenset(),
) -> tuple[bool | None, list[str]]:
    """typed token（`task:`／`ac:`／`gate:`／`release:`／`external:`／`milestone:`）の
    truth を解決する。

    `overrides` は token 文字列 exact 一致で truth を差し替える閉じた表であり、
    manifest を読む前に適用する（release／milestone の再帰にも同じ表を渡す）。
    用途は 3 つに限る: (a) `gate:R025-RESEARCH-WAIT-WINDOW-ACTIVE` を resolver 自身の
    live な window 評価（`research_wait_window` かつ `allowed_dispatch` 掲載）へ置換する、
    (b) S1（closing）時点で「R／S2 が公開する予定の closing task 自身の `task:` token」を
    投影 true とする、(c) repair-hold の未回復 affected task の `task:` token を false へ
    倒す（`_held_task_overrides`・PR-E3c）。(a)(b) 以外の token を overrides で pass に
    しない（(c) は false 側の override であり fail-close を弱めない）。
    """
    if not isinstance(token, str) or ":" not in token:
        return None, [f"{token!r}: prefix 付き token ではありません（未解決）"]
    if overrides and token in overrides:
        return bool(overrides[token]), []
    prefix, _, ident = token.partition(":")
    if prefix == "task":
        return resolve_task_token_truth(ident, manifest=manifest)
    if prefix == "external":
        return resolve_external_token_truth(ident, manifest=manifest)
    if prefix == "gate":
        return resolve_gate_token_truth(ident, manifest=manifest)
    if prefix == "ac":
        return resolve_ac_token_truth(ident, manifest=manifest)
    if prefix == "release":
        return resolve_release_token_truth(
            ident, manifest=manifest, overrides=overrides, _visited=_visited
        )
    if prefix == "milestone":
        return resolve_milestone_token_truth(
            ident, manifest=manifest, overrides=overrides, _visited=_visited
        )
    return None, [f"{token!r}: 未知の namespace prefix です（未解決）"]


def resolve_scheduling_state(
    *,
    scheduling_exception: Any,
    repo_root: Path,
    now: datetime.datetime | None = None,
    held_task_ids: Iterable[str] = (),
) -> tuple[dict[str, Any], list[str]]:
    """`active_queue.scheduling_exception` から Resolver Output Schema の
    `scheduling` block を導出する（AC-09・spec 268-316 行・Decision Rule 12）。

    schema 不正・期限切れ・先行 task 未完・unknown token は
    ``research_wait_window=False`` に倒す。ただし「解決できない token」
    （manifest に存在しない・循環参照）だけを issue として fail-close し、
    「解決できたが現在 false」は正常値として扱う（P-010: 判断不能と否定的結果を
    混同しない）。`resume_conditions` のいずれかが true になった場合は
    「通常gate成立、resume condition成立...時はwindowをfalseにし」（spec 296 行）
    に従い window を閉じる（解釈: `evidence_refs` は window 開始理由の typed
    evidence であり truth 値そのものを window 判定へは使わない。`resume_conditions`
    だけが window の終了条件である）。
    """
    output: dict[str, Any] = {
        "research_wait_window": False,
        "started_at": None,
        "expires_at": None,
        "owner": None,
        "blocked_target": None,
        "allowed_dispatch": [],
    }
    if scheduling_exception is None:
        return output, []

    schema_issues = validate_scheduling_exception_object(scheduling_exception)
    if isinstance(scheduling_exception, dict):
        output["started_at"] = scheduling_exception.get("started_at")
        output["expires_at"] = scheduling_exception.get("expires_at")
        output["owner"] = scheduling_exception.get("owner")
        output["blocked_target"] = scheduling_exception.get("blocked_target")
        allowed_dispatch = scheduling_exception.get("allowed_dispatch")
        if isinstance(allowed_dispatch, list):
            output["allowed_dispatch"] = list(allowed_dispatch)
    if schema_issues:
        return output, schema_issues

    started_dt = _parse_rfc3339_utc(scheduling_exception["started_at"])
    expires_dt = _parse_rfc3339_utc(scheduling_exception["expires_at"])
    assert started_dt is not None and expires_dt is not None  # schema 検査済み
    current_time = now if now is not None else datetime.datetime.now(datetime.UTC)
    if not (started_dt <= current_time < expires_dt):
        return output, []  # 未開始／期限到来は正常な false（spec 296 行）

    manifest, manifest_issues = _load_manifest(repo_root)
    if manifest is None:
        return output, manifest_issues

    held = list(held_task_ids)
    overrides = _held_task_overrides(held)
    for task_id in SCHEDULING_PRECONDITION_TASKS:
        truth, task_issues = resolve_task_token_truth(
            task_id, manifest=manifest, held_task_ids=held
        )
        if truth is None:
            return output, task_issues
        if truth is not True:
            return output, []  # 先行 task 未完（repair-hold 中の false 含む）は正常な false

    resolve_issues: list[str] = []
    for token in scheduling_exception["evidence_refs"]:
        _truth, token_issues = resolve_scheduling_token(
            token, manifest=manifest, overrides=overrides
        )
        resolve_issues.extend(token_issues)
    if resolve_issues:
        return output, resolve_issues

    for token in scheduling_exception["resume_conditions"]:
        truth, token_issues = resolve_scheduling_token(
            token, manifest=manifest, overrides=overrides
        )
        if truth is None:
            return output, token_issues
        if truth is True:
            return output, []  # resume condition 成立 -> window を閉じる（正常な false）

    output["research_wait_window"] = True
    return output, []


def _window_dispatch_override(
    task_id: str, scheduling_output: Mapping[str, Any] | None
) -> dict[str, bool]:
    """`gate:R025-RESEARCH-WAIT-WINDOW-ACTIVE` の live 置換表を作る。

    resolver 出力の `scheduling` block（`research_wait_window`／`allowed_dispatch`）
    から「window が有効で、かつ対象 task が `allowed_dispatch` に掲載されている」
    場合だけ true とする。`scheduling_output=None`（S1 writer／closing 検査のように
    `scheduling_exception=null` が前提の呼び出し）は window 無効として扱う。
    """
    active = False
    if isinstance(scheduling_output, Mapping):
        allowed = scheduling_output.get("allowed_dispatch")
        active = bool(
            scheduling_output.get("research_wait_window") is True
            and isinstance(allowed, list)
            and task_id in allowed
        )
    return {RESEARCH_WAIT_WINDOW_GATE_TOKEN: active}


def _manifest_passing_subjects(manifest: Mapping[str, Any]) -> set[str]:
    """manifest `evidence_registry.entries` から `status=pass` の `subject_id` 集合を返す。

    `validate_current_state._load_manifest_passing_subjects` と同じ predicate だが、
    読込済みの manifest dict を受け取る（本 module は manifest を 1 回だけ読む）。
    """
    registry = manifest.get("evidence_registry")
    entries = registry.get("entries") if isinstance(registry, Mapping) else None
    subjects: set[str] = set()
    if isinstance(entries, Mapping):
        for entry in entries.values():
            if isinstance(entry, Mapping) and entry.get("status") == "pass":
                subject_id = entry.get("subject_id")
                if isinstance(subject_id, str):
                    subjects.add(subject_id)
    return subjects


def _current_control_unit(
    manifest: Mapping[str, Any],
) -> control_unit_event.ControlUnitRow | None:
    """R-025 の closed control unit 表で、`produced_tokens` が manifest registry へ
    未登録（`status=pass` の subject として全件揃っていない）な最初の unit を
    execution_order 順に返す。全 unit 登録済みなら None。

    「直前公開 state」の判定は `validate_current_state._check_control_unit_ordering`
    と同じ predicate（produced_tokens の pass 登録）を使い、別の順序規則を作らない。
    """
    passing = _manifest_passing_subjects(manifest)
    for row in sorted(control_unit_event.CONTROL_UNIT_TABLE, key=lambda r: r.execution_order):
        if not all(token in passing for token in row.produced_tokens):
            return row
    return None


def resolve_task_dispatch_truth(
    task_id: str,
    *,
    repo_root: Path,
    scheduling_output: Mapping[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    assume_true_tokens: Iterable[str] = (),
    held_task_ids: Iterable[str] = (),
) -> tuple[bool | None, list[str]]:
    """task が「いま dispatch してよいか」を manifest `task_catalog.<task>.dependencies`
    の all-of で 3 値判定する（`(truth, reasons)`）。

    HRAI-REAUDIT-20260815 P0-02（PR-E1）の中核 predicate。戻り値の意味:

    - ``True``: 依存全件が true（``reasons`` は空）。
    - ``False``: 依存を評価できたが未充足（``reasons`` に未充足 token を列挙）。
      「S2 で state=blocked を置く」対象であり、fail-close の unknown とは区別する
      （P-010: 判断不能と否定的結果を混同しない）。
    - ``None``: 判定不能（manifest 不在／破損、task_catalog 未登録、aggregate kind、
      依存 token 未解決）。``reasons`` に理由を返し、呼び出し側は必ず fail-close する
      （未知の task を pass にしない）。

    評価規則:

    - `task_catalog.task:<id>` が無い task は None（`task_aliases` は aggregate／
      別名であり dispatch 対象ではないため解決しない）。
    - `kind` が `NON_DISPATCHABLE_TASK_KINDS`（umbrella／control program／program
      derived aggregate）の entry は None（`task_closeout_state_machine.applies_to`
      が通常 task に限定するため）。
    - `dependencies` 欠落・空は「依存なし」として true（実 manifest の `task:N-602A`
      が該当。存在するが配列でない場合は None）。
    - `milestone:R025-freeze-or-research_wait_window` は manifest `terminal_conditions`
      の any-of（`release:R025-reserved` または `gate:R025-RESEARCH-WAIT-WINDOW-ACTIVE`）
      として実評価し、後者の gate は `scheduling_output` の live 値（window 有効 かつ
      task_id ∈ `allowed_dispatch`）で置換する（`RESEARCH_WAIT_WINDOW_GATE_TOKEN` 参照）。
    - `assume_true_tokens` は S1（closing）時点で closing task 自身の `task:` token を
      投影 true にするためだけに使う（R／S2 が公開する予定の truth。S2 側は
      registry truth を再検査する契約〔spec 400-403 行〕なので、S1 の予告で
      dispatch を許可するわけではない）。
    - `held_task_ids` は repair-hold の未回復 affected task（PR-E3c）。当該 task の
      `task:` token を false へ倒す。`assume_true_tokens`（closing task 自身の投影）と
      衝突した場合は投影 true が優先する＝新 sequence の S1₂ で closing task 自身を
      R₂／S2₂ が公開する予定の truth として扱う（他の held task は false のまま）。

    R-025 の分岐（docstring 内に判断理由を残す）: `task_catalog.task:R-025` は
    reserved 後の測定実装 task（`dependencies: [release:R025-reserved]`）だが、active
    block の `task_id=R-025` は freeze audit／freeze transition／reservation preflight／
    reserved transition の control unit（U0）段階でも現れる（spec 264-266 行・N-602
    control unit 表）。control unit 段階に catalog の測定実装依存を課すと reserved 前の
    R-025 を構造的に dispatch 不能にしてしまうため、R-025 だけは
    「catalog dependencies の all-of」または「未登録の最初の control unit
    （`control_unit_event.CONTROL_UNIT_TABLE` の execution_order 順・produced_tokens が
    registry へ pass 登録済みかで判定）の preconditions all-of」の any-of で判定する。
    substep から unit を一意に写像する閉じた表は spec に無く、substep 文字列から
    推測しない（どちらの経路も manifest の typed token だけで閉じる）。両経路とも
    未充足なら False、両経路とも未解決なら None とする。
    """
    if manifest is None:
        manifest, manifest_issues = _load_manifest(repo_root)
        if manifest is None:
            return None, manifest_issues

    task_catalog = manifest.get("task_catalog")
    entry = task_catalog.get(f"task:{task_id}") if isinstance(task_catalog, dict) else None
    if not isinstance(entry, dict):
        return None, [
            f"task:{task_id} が manifest task_catalog に未登録のため dispatch 可否を判定できません"
            "（未知 task を pass にしない・fail-close）"
        ]
    kind = entry.get("kind")
    if isinstance(kind, str) and kind in NON_DISPATCHABLE_TASK_KINDS:
        return None, [
            f"task:{task_id} は kind={kind} の aggregate であり通常 task として dispatch できません"
        ]
    dependencies = entry.get("dependencies")
    if dependencies is None:
        dependencies = []
    if not isinstance(dependencies, list):
        return None, [f"task:{task_id}.dependencies が配列ではありません（未解決）"]

    overrides: dict[str, bool] = _window_dispatch_override(task_id, scheduling_output)
    overrides.update(_held_task_overrides(held_task_ids))
    for token in assume_true_tokens:
        overrides[token] = True

    catalog_truth, catalog_issues, catalog_unmet = _resolve_all_of_tokens(
        dependencies,
        label=f"task:{task_id}.dependencies",
        manifest=manifest,
        overrides=overrides,
        _visited=frozenset(),
    )

    if task_id != control_unit_event.CONTROL_UNIT_TASK_ID:
        if catalog_truth is True:
            return True, []
        if catalog_truth is False:
            return False, [
                f"task:{task_id} は manifest dependencies が未充足のため dispatch できません"
                f"（未充足: {', '.join(catalog_unmet)}）"
            ]
        return None, catalog_issues

    # R-025: catalog dependencies（測定実装 task）と現行 control unit preconditions の any-of。
    if catalog_truth is True:
        return True, []
    unit = _current_control_unit(manifest)
    unit_truth: bool | None = False
    unit_issues: list[str] = []
    unit_unmet: list[str] = []
    unit_label = "（全 control unit 登録済み）"
    if unit is not None:
        unit_label = f"control:{unit.control_id}"
        unit_truth, unit_issues, unit_unmet = _resolve_all_of_tokens(
            list(unit.preconditions),
            label=f"{unit_label}.preconditions",
            manifest=manifest,
            overrides=overrides,
            _visited=frozenset(),
        )
    if unit_truth is True:
        return True, []
    if catalog_truth is None or unit_truth is None:
        # any-of で確定 true がなく未解決経路が残る場合は fail-close（milestone の
        # any-of と同じ規則）。
        return None, catalog_issues + unit_issues
    unit_part = f"未充足: {', '.join(unit_unmet)}" if unit is not None else "対象 unit なし"
    return False, [
        f"task:{task_id} は測定実装 task の dependencies（未充足: {', '.join(catalog_unmet)}）も"
        f"現行 control unit {unit_label} の preconditions（{unit_part}）も満たさないため"
        " dispatch できません"
    ]


def evaluate_task_dispatch_dependencies(
    task_id: str,
    *,
    repo_root: Path,
    scheduling_output: Mapping[str, Any] | None,
    manifest: dict[str, Any] | None = None,
    assume_true_tokens: Iterable[str] = (),
    held_task_ids: Iterable[str] = (),
) -> tuple[bool, list[str]]:
    """`resolve_task_dispatch_truth` の 2 値 wrapper（`(dispatchable, issues)`）。

    未充足（False）と判定不能（None）をどちらも「dispatch 不可」に倒し、理由を
    issue 文字列で返す。resolver の Decision Rule 7 分岐と、判定不能を区別しない
    呼び出し側向け。判定不能と未充足を区別したい呼び出し側（S1 writer の
    `--allow-blocked-resume`）は 3 値版を直接使う。
    """
    truth, reasons = resolve_task_dispatch_truth(
        task_id,
        repo_root=repo_root,
        scheduling_output=scheduling_output,
        manifest=manifest,
        assume_true_tokens=assume_true_tokens,
        held_task_ids=held_task_ids,
    )
    return truth is True, reasons


def preflight_scheduling_dispatch(
    candidate_task_id: str,
    *,
    repo_root: Path,
    now: datetime.datetime | None = None,
) -> tuple[bool, list[str]]:
    """Decision Rule 7 の唯一の例外（Rule 12）: window 開始 state-sync 前に
    caller 候補を検査する preflight。候補が `allowed_dispatch` と exact 一致し、
    window 自体が有効で、候補 task の manifest `task_catalog.dependencies` が
    同じ snapshot で all-of true の場合だけ eligible=True を返す。state-sync 完了
    前に `next_task.task_id` へ候補を出すことはない（本関数は呼び出し側が
    state-sync を実行してよいかを判断するための独立した preflight）。

    依存評価は `resolve_task_dispatch_truth` へ一本化した（HRAI-REAUDIT-20260815
    P0-02・PR-E1）。旧実装は `milestone:` token を評価対象から除いていたが、
    milestone は manifest `terminal_conditions` の any-of として実評価し、
    `gate:R025-RESEARCH-WAIT-WINDOW-ACTIVE` 側だけを本 preflight が確定させた
    live window（有効かつ候補が allowed_dispatch 掲載）で置換する。従来どおり
    「依存未充足」は issue を伴わない正常な false、「未解決」だけを issue にする。
    """
    plan_path = repo_root / current_state.PLAN_REL
    if not plan_path.is_file():
        return False, [f"{current_state.PLAN_REL} が存在しません"]
    try:
        plan_text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, [f"{current_state.PLAN_REL} を読めません: {exc}"]
    active_queue_raw, load_issue = _load_single_marker(
        plan_text, ACTIVE_QUEUE_MARKER, label=current_state.PLAN_REL
    )
    if load_issue:
        return False, load_issue
    if active_queue_raw is None:
        return False, [f"{ACTIVE_QUEUE_MARKER} が存在しません"]
    if active_queue_raw.get("correction_repair") is not None:
        # repair-hold 中の通常 dispatch は fail-close（spec 415-416 行「repair途中の通常
        # dispatchをfail-close」・PR-E3c）。
        return False, [
            f"{ACTIVE_QUEUE_MARKER}: repair-hold 中（correction_repair 非 null）は"
            " 通常 dispatch の preflight を拒否します"
        ]

    scheduling_output, issues = resolve_scheduling_state(
        scheduling_exception=active_queue_raw.get("scheduling_exception"),
        repo_root=repo_root,
        now=now,
    )
    if issues:
        return False, issues
    if not scheduling_output["research_wait_window"]:
        return False, ["scheduling_exception が有効な research_wait_window ではありません"]
    if candidate_task_id not in scheduling_output["allowed_dispatch"]:
        return False, [f"{candidate_task_id} は allowed_dispatch に含まれません"]

    truth, reasons = resolve_task_dispatch_truth(
        candidate_task_id, repo_root=repo_root, scheduling_output=scheduling_output
    )
    if truth is None:
        return False, reasons
    if truth is False:
        return False, []  # 依存未充足は正常な false
    return True, []


# ---------------------------------------------------------------------------
# active block 0 件（terminal）判定（spec 171-172 行・AC-01）
# ---------------------------------------------------------------------------


def _check_zero_block_terminal(
    *,
    plan_text: str,
    checkpoint_text: str | None,
    checkpoint_next_obj: dict[str, Any] | None,
    checkpoint_next_issues: list[str],
    flag_active: bool,
) -> list[str]:
    issues: list[str] = []
    plan_issue = current_state.check_terminal_plan_queue(plan_text)
    if plan_issue is not None:
        issues.append(plan_issue)

    if checkpoint_text is None:
        issues.append(
            f"{ACTIVE_QUEUE_MARKER} が 0 件ですが {current_state.CHECKPOINT_REL} を読めないため"
            " terminal 条件を確認できません"
        )
    elif checkpoint_next_issues:
        pass  # 既にグローバル issues 側で報告済み（ここで同じ issue を重複させない）
    elif checkpoint_next_obj is None or "terminal" not in checkpoint_next_obj:
        issues.append(
            f"{ACTIVE_QUEUE_MARKER} が 0 件のとき {current_state.CHECKPOINT_REL} の"
            f" {CHECKPOINT_NEXT_MARKER} は terminal sentinel である必要があります"
        )
    elif checkpoint_next_obj.get("terminal") is not True:
        issues.append(
            f"{ACTIVE_QUEUE_MARKER} が 0 件のとき {current_state.CHECKPOINT_REL} の"
            f" {CHECKPOINT_NEXT_MARKER}.terminal は true である必要があります"
        )

    if flag_active:
        issues.append(
            f"{ACTIVE_QUEUE_MARKER} が 0 件のとき {current_state.FLAG_REL} は inactive"
            " である必要があります"
        )
    return issues


# ---------------------------------------------------------------------------
# resolve(): 全体オーケストレーション
# ---------------------------------------------------------------------------


def _empty_result() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "main_head": None,
        "last_completed": {"task_id": None, "pr": None, "terminal_sha": None},
        "active_queue": {
            "audit_id": None,
            "task_id": None,
            "substep": None,
            "lineage": None,
            "state": "terminal",
            "source_plan_revision": None,
            "source_revision": None,
            "source_audit_sha256": None,
        },
        "research_resume": {
            "task_id": None,
            "git_last_settled_round": None,
            "git_last_settled_revision": None,
            "local_session_resume_round": None,
            "remediation_applied": None,
            "finding_counts": {"blocker": 0, "backlog": 0, "invalid": 0},
            "local_session_evidence_ref": None,
        },
        "scheduling": {
            "research_wait_window": False,
            "started_at": None,
            "expires_at": None,
            "owner": None,
            "blocked_target": None,
            "allowed_dispatch": [],
        },
        "next_task": {"task_id": None},
        "closeout_action": None,
        "local_state_required": False,
        "local_state_present": False,
        "checkpoint_fresh": False,
        "issues": [],
    }


def resolve(*, repo_root: Path, context: str) -> dict[str, Any]:
    """4情報源を合成し、Resolver Output Schema の dict（`issues` 込み）を返す。"""
    if context not in CONTEXT_CHOICES:
        result = _empty_result()
        result["issues"] = [f"不明な --context 値です: {context!r}"]
        return result

    result = _empty_result()
    issues: list[str] = []
    mode = "transition" if context == "transition" else "report"

    # Decision Rule 1/2/6: mainline・checkpoint 鮮度検査は N-598B の既存実装をそのまま使う。
    # `issues` には validate() の全結果（次タスク token 整合・N-602A bootstrap
    # closeout・execution-ledger stale heading を含む）を積む。`checkpoint_fresh`
    # 自体は checkpoint が mainline に対してどれだけ新鮮かだけを表す、より狭い
    # 意味（PR #1330 Round 1 Copilot 指摘対応）なので、後段で
    # `check_checkpoint_freshness` を使って別途計算する。
    validate_issues = current_state.validate(
        repo_root=repo_root,
        staleness_threshold=current_state.DEFAULT_STALENESS_THRESHOLD,
        mode=mode,
    )
    issues.extend(validate_issues)

    mainline_ref, mainline_issue = current_state.resolve_mainline_ref(repo_root=repo_root)
    main_head: str | None = None
    if mainline_issue is not None:
        issues.append(mainline_issue)
    else:
        assert mainline_ref is not None
        main_head, _rp_issue = _rev_parse(repo_root, mainline_ref)
        if main_head is None:
            issues.append(f"mainline ref {mainline_ref} の commit sha を解決できません")
    result["main_head"] = main_head

    # --- checkpoint（last settled checkpoint。AC-04） ---
    checkpoint_path = repo_root / current_state.CHECKPOINT_REL
    checkpoint_text: str | None = None
    if not checkpoint_path.is_file():
        issues.append(f"{current_state.CHECKPOINT_REL} が存在しません")
    else:
        checkpoint_text = checkpoint_path.read_text(encoding="utf-8")

    checkpoint_next_obj: dict[str, Any] | None = None
    checkpoint_next_issues: list[str] = []
    research_resume_checkpoint_obj: dict[str, Any] | None = None
    checkpoint_sha: str | None = None
    if checkpoint_text is not None:
        task_match = current_state.CHECKPOINT_TASK_RE.search(checkpoint_text)
        if task_match is None:
            issues.append(f"{current_state.CHECKPOINT_REL} から直前完了タスクを抽出できません")
        else:
            tokens = current_state.extract_task_tokens(task_match.group(1))
            if not tokens:
                issues.append(
                    f"{current_state.CHECKPOINT_REL} の完了タスク行からタスク ID を抽出できません"
                )
            else:
                result["last_completed"]["task_id"] = tokens[0]
        checkpoint_sha = current_state.extract_checkpoint_sha(checkpoint_text)
        checkpoint_pr_number = current_state.extract_checkpoint_pr_number(checkpoint_text)
        result["last_completed"]["terminal_sha"] = checkpoint_sha
        result["last_completed"]["pr"] = (
            int(checkpoint_pr_number) if checkpoint_pr_number is not None else None
        )
        if checkpoint_sha is not None:
            # `checkpoint_fresh`: PR/SHA 整合・ancestor・merge-freshness だけを表す
            # 狭い意味論（validate() 全体の issues とは別に計算する。PR #1330 Round 1
            # Copilot 指摘対応）。
            freshness_issues = current_state.check_checkpoint_freshness(
                repo_root=repo_root,
                sha=checkpoint_sha,
                pr_number=checkpoint_pr_number,
                mode=mode,
                staleness_threshold=current_state.DEFAULT_STALENESS_THRESHOLD,
            )
            result["checkpoint_fresh"] = not freshness_issues

        checkpoint_next_obj, checkpoint_next_issues = _load_single_marker(
            checkpoint_text, CHECKPOINT_NEXT_MARKER, label=current_state.CHECKPOINT_REL
        )
        if checkpoint_next_obj is None and not checkpoint_next_issues:
            checkpoint_next_issues = [
                f"{current_state.CHECKPOINT_REL} に {CHECKPOINT_NEXT_MARKER} marker がありません"
                "（AC-04 必須）"
            ]
        elif checkpoint_next_obj is not None:
            checkpoint_next_issues = [
                f"{current_state.CHECKPOINT_REL}: {msg}"
                for msg in validate_checkpoint_next_object(checkpoint_next_obj)
            ]
        issues.extend(checkpoint_next_issues)

        research_resume_checkpoint_obj, rr_issues = _load_single_marker(
            checkpoint_text, RESEARCH_RESUME_MARKER, label=current_state.CHECKPOINT_REL
        )
        issues.extend(rr_issues)

    # --- local flag（Decision Rule 8） ---
    # report context では local flag は ignored local file であり（Decision Rule 8）、
    # 破損 JSON／object でない等の parse issue も report では issues へ積まない
    # （transition だけが local state を必須にしうる）。
    flag_file_exists = (repo_root / current_state.FLAG_REL).is_file()
    flag_data, flag_load_issues = current_state.load_flag(repo_root)
    if context == "transition":
        issues.extend(flag_load_issues)
    flag_active = _flag_is_active(flag_data, flag_file_exists=flag_file_exists)
    local_state_present = flag_data is not None
    local_state_required = context == "transition" and flag_active
    result["local_state_required"] = local_state_required
    result["local_state_present"] = local_state_present
    if context == "transition" and local_state_required and not local_state_present:
        issues.append(
            f"{current_state.FLAG_REL} が必須ですが読み込めません（full-plan session active）"
        )

    # --- plan の active-queue:v1（AC-01） ---
    plan_path = repo_root / current_state.PLAN_REL
    plan_text: str | None = None
    if not plan_path.is_file():
        issues.append(f"{current_state.PLAN_REL} が存在しません")
    else:
        plan_text = plan_path.read_text(encoding="utf-8")

    active_queue_raw: dict[str, Any] | None = None
    # repair-hold 中の未回復 affected task（`task:` truth を false へ override する集合）。
    held_task_ids: set[str] = set()
    if plan_text is not None:
        aq_open, aq_close, aq_pair = current_state.count_marker_tags(plan_text, ACTIVE_QUEUE_MARKER)
        if aq_open == 0 and aq_close == 0:
            issues.extend(
                _check_zero_block_terminal(
                    plan_text=plan_text,
                    checkpoint_text=checkpoint_text,
                    checkpoint_next_obj=checkpoint_next_obj,
                    checkpoint_next_issues=checkpoint_next_issues,
                    flag_active=flag_active,
                )
            )
        elif aq_open == 1 and aq_close == 1 and aq_pair == 1:
            active_queue_raw = current_state.extract_active_queue_block(plan_text)
            if active_queue_raw is None:
                issues.append(
                    f"{current_state.PLAN_REL} の {ACTIVE_QUEUE_MARKER} YAML を object として"
                    " 解析できません"
                )
            elif active_queue_raw.get("correction_repair") is not None:
                # repair-hold（PR-E3c）: correction_repair の完全検証には manifest と closeout
                # event が要る。manifest を読めなければ issue（fail-close）にし、構造検査だけは
                # 常に行う。
                hold_manifest, hold_manifest_issues = _load_manifest(repo_root)
                if hold_manifest is None:
                    issues.extend(
                        f"{ACTIVE_QUEUE_MARKER}.correction_repair: {msg}（affected set を再導出"
                        "できないため fail-close）"
                        for msg in hold_manifest_issues
                    )
                    issues.extend(validate_active_queue_schema(active_queue_raw))
                else:
                    hold_events, hold_event_issues = repair_hold.load_closeout_events(repo_root)
                    issues.extend(
                        f"{ACTIVE_QUEUE_MARKER}.correction_repair: {msg}"
                        for msg in hold_event_issues
                    )
                    issues.extend(
                        validate_active_queue_schema(
                            active_queue_raw,
                            manifest=hold_manifest,
                            repo_root=repo_root,
                            events=hold_events,
                        )
                    )
                    held_task_ids = held_task_ids_for_block(
                        active_queue_raw, manifest=hold_manifest, events=hold_events
                    )
            else:
                issues.extend(validate_active_queue_schema(active_queue_raw))
        else:
            issues.append(
                f"{current_state.PLAN_REL} に {ACTIVE_QUEUE_MARKER} block が複数、または"
                " delimiter が不正です"
            )

    if active_queue_raw is not None:
        active_task_id = active_queue_raw.get("task_id")
        active_substep = active_queue_raw.get("substep")
        active_state_value = active_queue_raw.get("state")

        result["active_queue"].update(
            {
                "audit_id": active_queue_raw.get("audit_id"),
                "task_id": active_task_id,
                "substep": active_substep,
                "lineage": active_queue_raw.get("lineage"),
                "state": active_state_value,
                "source_revision": active_queue_raw.get("source_revision"),
                "source_audit_sha256": active_queue_raw.get("source_audit_sha256"),
            }
        )

        # Decision Rule 4/5: checkpoint-next:v1 と active-queue:v1 の task/substep exact 照合。
        if checkpoint_next_obj is not None and not checkpoint_next_issues:
            if "terminal" in checkpoint_next_obj:
                issues.append(
                    f"{ACTIVE_QUEUE_MARKER} が存在するのに {current_state.CHECKPOINT_REL} の"
                    f" {CHECKPOINT_NEXT_MARKER} が terminal を宣言しています（併存拒否）"
                )
            else:
                if checkpoint_next_obj.get("task_id") != active_task_id:
                    issues.append(
                        f"{current_state.CHECKPOINT_REL} の {CHECKPOINT_NEXT_MARKER}.task_id が"
                        f" {ACTIVE_QUEUE_MARKER}.task_id と exact 一致しません"
                    )
                if checkpoint_next_obj.get("substep") != active_substep:
                    issues.append(
                        f"{current_state.CHECKPOINT_REL} の {CHECKPOINT_NEXT_MARKER}.substep が"
                        f" {ACTIVE_QUEUE_MARKER}.substep と exact 一致しません"
                    )

        # Decision Rule 8: full-plan flag の current_task_id/current_substep exact 照合。
        if context == "transition" and flag_data is not None:
            flag_task_id = flag_data.get("current_task_id")
            flag_substep = flag_data.get("current_substep")
            if not flag_active:
                if flag_task_id is not None or flag_substep is not None:
                    issues.append(
                        f"{current_state.FLAG_REL}: inactive では current_task_id/current_substep"
                        " を null にする必要があります"
                    )
            else:
                if flag_task_id != active_task_id or flag_substep != active_substep:
                    issues.append(
                        f"{current_state.FLAG_REL}: current_task_id/current_substep が"
                        f" {ACTIVE_QUEUE_MARKER} と exact 一致しません"
                    )

        # Decision Rule 9: research_resume の三正本 exact 照合。
        issues.extend(
            check_research_resume_exactness(
                plan_value=active_queue_raw.get("research_resume"),
                checkpoint_obj=research_resume_checkpoint_obj,
                flag_data=flag_data,
                flag_active=flag_active,
                local_state_required=local_state_required,
                repo_root=repo_root,
            )
        )
        research_resume_value = active_queue_raw.get("research_resume")
        if isinstance(research_resume_value, dict):
            result["research_resume"].update(research_resume_value)

        # AC-09: scheduling_exception（research wait window）の完全評価。
        scheduling_output, scheduling_issues = resolve_scheduling_state(
            scheduling_exception=active_queue_raw.get("scheduling_exception"),
            repo_root=repo_root,
            held_task_ids=held_task_ids,
        )
        result["scheduling"] = scheduling_output
        issues.extend(scheduling_issues)

        # source_plan_revision（AC-08 準備）。
        if main_head is not None and plan_text is not None:
            source_plan_revision, spr_issue = resolve_source_plan_revision(
                repo_root=repo_root, main_head=main_head, plan_disk_text=plan_text
            )
            if spr_issue is not None:
                issues.append(spr_issue)
            result["active_queue"]["source_plan_revision"] = source_plan_revision

        # Decision Rule 7: next_task。
        if active_state_value in ("ready", "in_progress"):
            # HRAI-REAUDIT-20260815 P0-02（PR-E1）: ready|in_progress でも active task の
            # manifest `task_catalog.dependencies` を all-of 評価し、未充足／判定不能なら
            # issue を積んで next_task.task_id=null のままにする（Decision Rule 7 追記・
            # Rule 12「候補task自身の manifest dependencies を免除しない」を通常 dispatch
            # にも適用）。`blocked` は従来どおり評価せず null（issue 追加なし）。
            if isinstance(active_task_id, str) and TASK_ID_RE.fullmatch(active_task_id):
                dispatchable, dispatch_issues = evaluate_task_dispatch_dependencies(
                    active_task_id,
                    repo_root=repo_root,
                    scheduling_output=scheduling_output,
                    held_task_ids=held_task_ids,
                )
                if dispatchable:
                    result["next_task"]["task_id"] = active_task_id
                else:
                    issues.extend(
                        f"{ACTIVE_QUEUE_MARKER}.state={active_state_value} ですが dispatch"
                        f" できません（state=blocked へ置くか依存充足を待ってください）: {reason}"
                        for reason in dispatch_issues
                    )
        elif active_state_value == "closing":
            # AC-06 の完全な S1/R/S2 検査（subject_kind=task_closeout／control_unit の
            # 両方）と repair-hold 規則は `current_state.validate()`（内部で
            # `check_task_closeout_sequencing` を呼ぶ）が既に行っている。ここでは汎化済みの
            # `resolve_closeout_action` を再利用して closeout_action.kind だけ導出する。
            action_kind, action_issues = current_state.resolve_closeout_action(repo_root=repo_root)
            issues.extend(action_issues)
            registration = active_queue_raw.get("closeout_registration")
            correction_repair = active_queue_raw.get("correction_repair")
            correction_targets: list[str] = []
            subject_kind: str | None = (
                registration.get("subject_kind") if isinstance(registration, dict) else None
            )
            if isinstance(correction_repair, dict):
                # repair-hold（PR-E3c）: correction_targets は invalid_evidence_ids ∪
                # invalid_batch_ids。spec 419-420 行「staging／correction registration時だけ
                # 非空、registration／advance時は空」に従い、hold 継続（kind null）も含めて
                # 訂正系だけ非空にする。
                if action_kind not in ("evidence_registration", "advance_state"):
                    correction_targets = repair_hold.correction_targets(correction_repair)
                if subject_kind is None:
                    subject_kind = current_state.SUBJECT_KIND_TASK_CLOSEOUT
            result["closeout_action"] = {
                "kind": action_kind,
                "subject_kind": subject_kind,
                "event_id": registration.get("event_id")
                if isinstance(registration, dict)
                else None,
                "correction_targets": correction_targets,
            }
        # state == "blocked" は next_task.task_id=null のまま（後続候補を dispatch しない）。

    result["issues"] = issues
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--context",
        choices=CONTEXT_CHOICES,
        default=None,
        help=(
            "transition: セッション遷移（local flag 必須になりうる）／report: CI report mode。"
            "--scheduling-preflight-candidate 指定時は不要。"
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="リポジトリルート（既定はスクリプト位置から算出。fixture テストで override）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="stdout と同じ byte 列を追加で書き出すパス（省略時は stdout のみ）",
    )
    parser.add_argument(
        "--scheduling-preflight-candidate",
        type=str,
        default=None,
        metavar="TASK_ID",
        help=(
            "AC-09 Decision Rule 7/12 の preflight: window の state-sync 実施前に"
            " candidate task の dispatch 可否だけを判定する（通常の resolve() 出力の"
            " 代わりに {candidate_task_id, eligible, issues} を出力する独立モード）"
        ),
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()

    if args.scheduling_preflight_candidate is not None:
        eligible, issues = preflight_scheduling_dispatch(
            args.scheduling_preflight_candidate, repo_root=repo_root
        )
        payload = (
            json.dumps(
                {
                    "candidate_task_id": args.scheduling_preflight_candidate,
                    "eligible": eligible,
                    "issues": issues,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=False,
            )
            + "\n"
        )
        sys.stdout.write(payload)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        return 0 if eligible else 1

    if args.context is None:
        parser.error("--context is required unless --scheduling-preflight-candidate is given")

    result = resolve(repo_root=repo_root, context=args.context)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False) + "\n"

    sys.stdout.write(payload)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")

    return 1 if result["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
