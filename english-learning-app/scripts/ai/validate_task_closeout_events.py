#!/usr/bin/env python3
"""task closeout event の専用 validator CLI（N-602A Phase A・AC-14）。

`docs/specs/N-602-evidence-truthfulness-generation.md`「Task Closeout Event Schema v1」
と `docs/specs/N-598C-exact-current-state-resolver.md` の closeout 契約を機械検査する。
schema／hash 規則の正本は `scripts/ai/task_closeout_event.py` であり、本 CLI は git／
GitHub 側の実測と state-sync diff の allowlist 検査を担当する。

検査項目（いずれか違反で exit 1・fail-close）:

  (0) state-sync allowlist の結線。`ai/coherence-workflow.yml` の allowlist が
      `docs/ai/task-closeout-events/*.yml` を exact に含み、`docs/ai/**` のような広い
      glob を持たず、writer と本 validator が repo に存在すること（専用 validator
      なしの allowlist 拡張＝片側だけの有効化を拒否する）
  (a) `--base/--head` 指定時、closeout state-sync が追加する path が
      `docs/ai/task-closeout-events/*.yml` と既存 state-sync allowlist path だけであること
  (b) 既存 event file が byte 不変であること（base..head の blob OID 比較）
  (c) schema、stable key の一意性、evidence ID 導出、訂正 chain（cycle／branch／
      ordinal gap／別 key への付替え）の検査
  (d) `actual_merge_terminal_sha` が GitHub の merged PR と main first-parent に一致する
      こと。`gh` が使えない場合は inconclusive として fail-close する（pass にしない）
  (e) event anchor（exact path が main first-parent へ初出する commit）が actual
      terminal の真の子孫であること
  (f) `--unit-*` 指定時、main first-parent 上で `T < S1 < R < S2` を満たすこと。R は
      通常 task ではないため、closeout event を新たに生成せず active state も進めない
      （再帰停止条件）
  (g) `--registration-*` 指定時、R の atomic batch が S1 固定集合と exact 一致すること
      （missing＝部分登録／extra／duplicate を拒否する）。さらに（HRAI-REAUDIT-20260815
      P0-01／P1-01・PR-E2）base manifest から `scripts/ai/closeout_expectation` で
      期待集合を再導出し、(g-a) event の root slot 集合が `derive_expected_roots(base,
      task, merged_pr)` と exact 一致すること（訂正 event は `derive_expected_slots` の
      部分集合）、(g-b) `closure_claim_ids` が `derive_owner_open_claims(base, task)` と
      一致すること、(g-c) 追加 entry の subject_id／evidence_type／phase_or_context_id／
      artifact_path／raw_sha256／`postmerge_mapping.merged_pr`／
      `postmerge_mapping.actual_merge_terminal_sha`／`correction_of` が event root と一致
      すること、を検査する。`--assert-unit r` では (g) を必須にし、`--registration-*` を
      省略した場合は base の `docs/plan.md` active block `closeout_registration.event_id`
      から task／merged_pr／sequence／role を導出し、manifest は `git show <base|head>:
      <manifest>` から取得する（working tree を使わない）。明示 flag が導出値と異なれば
      fail する。
  (h) `--check-completeness <task_id>` は既存 event（当該 task の unsuperseded event）の
      root slot 集合を manifest 導出集合（`derive_expected_slots`）と照合し、不足／余分／
      registry 未登録を JSON で stdout へ報告する（不足があれば exit 1）。他の検査は
      行わない（診断専用 mode）。
  (i) post-S2 回復（DEC-20260815-003 決定 6・PR-E3c）。Rcorr'（`correction_ordinal>=1`・
      既存 slot 訂正専用・親 event に無い slot は追加不可）と R₂（`closeout_sequence>=2` の
      新 sequence の original・stable key が別）を区別する。`closeout_sequence>=2` の event は
      同 task／role の `n-1` が存在するときだけ受理し（core `validate_event_chain`）、S1 unit
      検査（`--assert-unit s1`）では head の plan active block が repair-hold
      （`correction_repair.repair_target_task_id == event.task_id`）であることを要求する
      （sequence 1 の S1 は逆に correction_repair null を要求＝repair 途中の通常 closeout を
      拒否）。R₂ の登録 entry は、slot に base の chain があれば Rcorr' が invalidates で
      閉じた末端を `correction_of` に持ち（live な旧 entry の上書きを拒否）、chain の無い slot
      は `correction_of=null`。R validator は task の全 unsuperseded key の root 和集合が
      head registry に登録済みであることも要求する。

使用例::

    uv run python scripts/ai/validate_task_closeout_events.py
    uv run python scripts/ai/validate_task_closeout_events.py --base origin/main --head HEAD
    uv run python scripts/ai/validate_task_closeout_events.py --check-completeness N-594B
"""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[2]
_ROOT_TEXT = str(ROOT)
if _ROOT_TEXT not in sys.path:
    sys.path.insert(0, _ROOT_TEXT)

from scripts.ai import closeout_expectation as expectation
from scripts.ai import validate_current_state as current_state
from scripts.ai.task_closeout_event import (
    EVENT_DIR_REL,
    EVENT_FILE_RE,
    EVENT_PATH_GLOB,
    check_registration_batch,
    current_event_for_key,
    event_stable_key,
    root_slot,
    validate_event_chain,
    validate_event_object,
    validate_root_evidence_ids,
)

WORKFLOW_REL = "ai/coherence-workflow.yml"
WRITER_REL = "scripts/ai/write_task_closeout_event.py"
VALIDATOR_REL = "scripts/ai/validate_task_closeout_events.py"
CORE_REL = "scripts/ai/task_closeout_event.py"
EXPECTATION_REL = "scripts/ai/closeout_expectation.py"
PLAN_REL = "docs/plan.md"
MANIFEST_REL = current_state.MANIFEST_REL

# 広すぎる glob（validator を経ない path を許してしまう）を明示的に拒否する。
FORBIDDEN_ALLOWLIST_PREFIXES = ("", "docs/", "docs/ai/")

ACTIVE_QUEUE_BLOCK_RE = re.compile(
    r"<!-- active-queue:v1 -->\s*```yaml\n(?P<body>.*?)```\s*<!-- /active-queue:v1 -->",
    re.DOTALL,
)


class GitError(RuntimeError):
    """git／gh 呼び出しが失敗した（検証不能＝fail-close の材料）。"""


@dataclass(frozen=True)
class RegistrationCheckInput:
    """R の atomic batch 検査（(g)）に必要な入力。

    `--assert-unit r` で `--registration-*` を省略した場合は、`validate()` が base の plan
    active block と `git show` から task／merged_pr／manifest を導出して同じ形に埋める。
    manifest は Path（実 file）でも dict（`git show` 済み payload）でもよい。
    """

    base_manifest: Path | dict[str, Any]
    head_manifest: Path | dict[str, Any]
    task_id: str
    merged_pr: int
    closeout_sequence: int
    closeout_role: str = "task_terminal"


def _run(args: list[str], *, cwd: Path, timeout: int = 60) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise GitError(f"{' '.join(args)} が失敗した: {result.stderr.strip()}")
    return result.stdout


def resolve_mainline_ref(repo_root: Path) -> str:
    """mainline ref を `origin/main` → `main` → `HEAD` の順で解決する。"""
    for candidate in ("refs/remotes/origin/main", "refs/heads/main"):
        try:
            _run(["git", "rev-parse", "--verify", "--quiet", candidate], cwd=repo_root)
        except GitError:
            continue
        return candidate
    return "HEAD"


def first_parent_shas(repo_root: Path, mainline_ref: str) -> list[str]:
    """mainline の first-parent commit 列（新しい順）を返す。"""
    out = _run(["git", "rev-list", "--first-parent", mainline_ref], cwd=repo_root)
    return [line.strip() for line in out.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# (0) state-sync allowlist の結線検査
# ---------------------------------------------------------------------------


def _collect_key(node: object, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_collect_key(value, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_key(item, key))
    return found


def parse_allowlist(raw: str) -> list[str]:
    """`"a, b, c only"` 形式の allowlist 文字列を pattern 配列へ分解する。"""
    cleaned = raw.strip()
    if cleaned.endswith(" only"):
        cleaned = cleaned[: -len(" only")]
    return [part.strip() for part in cleaned.split(",") if part.strip()]


def path_matches(pattern: str, path: str) -> bool:
    """allowlist pattern（exact／`dir/**`／`dir/*.ext`）と path を照合する。"""
    if pattern.endswith("/**"):
        return path.startswith(pattern[: -len("**")])
    if "*" in pattern:
        prefix, _, basename_pattern = pattern.rpartition("/")
        directory, _, basename = path.rpartition("/")
        if directory != prefix:
            return False
        return (
            re.fullmatch(re.escape(basename_pattern).replace(r"\*", "[^/]*"), basename) is not None
        )
    return pattern == path


def load_state_sync_allowlist(repo_root: Path) -> tuple[list[str], list[str]]:
    """`ai/coherence-workflow.yml` から state-sync 用 allowlist（sync_allowed_paths）を読む。

    anchor 用（`anchor_commit_allowed_paths`）は意図的に合成しない。合成すると
    anchor 専用 path（docs/ai/reviews/*.json 等）が state-sync diff 検査でも
    許可され、sync unit への証跡混入を検出できなくなる（fail-close 弱化）。
    anchor 用は `load_anchor_allowlist()` で別途読む。
    """
    issues: list[str] = []
    workflow_path = repo_root / WORKFLOW_REL
    if not workflow_path.is_file():
        return [], [f"{WORKFLOW_REL} が存在しない"]
    loaded = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    sync_values = _collect_key(loaded, "sync_allowed_paths")
    if len(sync_values) != 1:
        issues.append(
            f"{WORKFLOW_REL}: sync_allowed_paths が {len(sync_values)} 件ある（1 件が必要）"
        )
        return [], issues
    return parse_allowlist(sync_values[0]), issues


def load_anchor_allowlist(repo_root: Path) -> tuple[list[str], list[str]]:
    """`ai/coherence-workflow.yml` から anchor 用 allowlist を読む（sync とは分離）。"""
    workflow_path = repo_root / WORKFLOW_REL
    if not workflow_path.is_file():
        return [], [f"{WORKFLOW_REL} が存在しない"]
    loaded = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    patterns: list[str] = []
    for value in _collect_key(loaded, "anchor_commit_allowed_paths"):
        patterns.extend(parse_allowlist(value))
    return patterns, []


def check_allowlist_wiring(repo_root: Path) -> list[str]:
    """allowlist と専用 validator が同時に結線されていることを検査する。"""
    patterns, issues = load_state_sync_allowlist(repo_root)
    if issues:
        return issues
    anchor_patterns, anchor_issues = load_anchor_allowlist(repo_root)
    issues.extend(anchor_issues)
    if EVENT_PATH_GLOB not in patterns:
        issues.append(
            f"{WORKFLOW_REL}: state-sync allowlist に {EVENT_PATH_GLOB} が結線されていない"
        )
    for pattern in patterns + anchor_patterns:
        if pattern.endswith("/**") and pattern[: -len("**")] in FORBIDDEN_ALLOWLIST_PREFIXES:
            issues.append(f"{WORKFLOW_REL}: 広すぎる allowlist glob を拒否する: {pattern}")
        if pattern in {"docs/ai/*", "docs/*", "**", "*"}:
            issues.append(f"{WORKFLOW_REL}: 広すぎる allowlist glob を拒否する: {pattern}")
    if EVENT_PATH_GLOB in patterns:
        for rel in (WRITER_REL, VALIDATOR_REL, CORE_REL, EXPECTATION_REL):
            if not (repo_root / rel).is_file():
                issues.append(
                    f"allowlist だけを広げて {rel} を結線していない"
                    "（専用 validator なしの allowlist 拡張を拒否する）"
                )
    return issues


# ---------------------------------------------------------------------------
# event file の読み込みと (c) schema／chain 検査
# ---------------------------------------------------------------------------


def load_event_files(repo_root: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """event ディレクトリを走査し `event_id -> event object` を返す。"""
    issues: list[str] = []
    events: dict[str, dict[str, Any]] = {}
    event_dir = repo_root / EVENT_DIR_REL
    if not event_dir.is_dir():
        return events, issues
    for path in sorted(event_dir.iterdir()):
        if path.is_dir():
            continue
        if not EVENT_FILE_RE.match(path.name):
            issues.append(f"{EVENT_DIR_REL}/{path.name}: file 名は `<event-id>.yml` に限る")
            continue
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        rel = f"{EVENT_DIR_REL}/{path.name}"
        event_issues = validate_event_object(loaded, label=rel)
        if event_issues:
            issues.extend(event_issues)
            continue
        assert isinstance(loaded, dict)
        issues.extend(validate_root_evidence_ids(loaded, label=rel))
        event_id = str(loaded["event_id"])
        if path.name != f"{event_id}.yml":
            issues.append(f"{rel}: file 名が event_id と一致しない")
            continue
        events[event_id] = loaded
    return events, issues


# ---------------------------------------------------------------------------
# (a)(b) state-sync diff 検査
# ---------------------------------------------------------------------------


def diff_name_status(repo_root: Path, base: str, head: str) -> list[tuple[str, str]]:
    """`git diff --name-status` を `(status, path)` 配列で返す。"""
    out = _run(
        ["git", "diff", "--no-renames", "--name-status", f"{base}..{head}"],
        cwd=repo_root,
    )
    entries: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        entries.append((parts[0].strip(), parts[-1].strip()))
    return entries


def check_state_sync_diff(
    repo_root: Path, *, base: str, head: str, patterns: list[str]
) -> list[str]:
    """closeout state-sync の追加 path が allowlist 内に収まることを検査する。"""
    issues: list[str] = []
    entries = diff_name_status(repo_root, base, head)
    added_events = 0
    for status, path in entries:
        if path.startswith(f"{EVENT_DIR_REL}/"):
            if status != "A":
                issues.append(
                    f"{path}: 既存 event の変更／削除を拒否する（status={status}・append-only）"
                )
                continue
            if not EVENT_FILE_RE.match(path.rsplit("/", 1)[-1]):
                issues.append(f"{path}: event file 名が `<event-id>.yml` ではない")
                continue
            added_events += 1
            continue
        if not any(path_matches(pattern, path) for pattern in patterns):
            issues.append(f"{path}: state-sync allowlist 外の path を追加している")
    if added_events == 0:
        issues.append(
            f"{base}..{head} に {EVENT_DIR_REL}/ への追加がない（closeout state-sync ではない）"
        )
    return issues


def check_existing_events_unchanged(repo_root: Path, *, base: str, head: str) -> list[str]:
    """base 時点に存在した event file が byte 不変であることを検査する。"""
    issues: list[str] = []
    try:
        listing = _run(["git", "ls-tree", "-r", "--name-only", base, EVENT_DIR_REL], cwd=repo_root)
    except GitError:
        return issues
    for rel in [line.strip() for line in listing.splitlines() if line.strip()]:
        try:
            base_oid = _run(["git", "rev-parse", f"{base}:{rel}"], cwd=repo_root).strip()
        except GitError:
            continue
        try:
            head_oid = _run(["git", "rev-parse", f"{head}:{rel}"], cwd=repo_root).strip()
        except GitError:
            issues.append(f"{rel}: 既存 event が削除されている（append-only 違反）")
            continue
        if base_oid != head_oid:
            issues.append(f"{rel}: 既存 event が上書きされている（byte 不変違反）")
    return issues


# ---------------------------------------------------------------------------
# (d)(e) actual terminal と event anchor の検査
# ---------------------------------------------------------------------------


def check_actual_terminal_on_mainline(
    event: dict[str, Any], *, first_parents: list[str], label: str
) -> list[str]:
    """actual merge terminal が main first-parent 上にあることを検査する。"""
    actual = str(event.get("actual_merge_terminal_sha"))
    if actual not in first_parents:
        return [f"{label}: actual_merge_terminal_sha が main first-parent 上にない（{actual}）"]
    return []


def verify_github_merge(
    repo_root: Path, event: dict[str, Any], *, label: str, timeout: int
) -> list[str]:
    """GitHub の merged PR と actual terminal／premerge head を照合する。

    `gh` が使えない場合や JSON が読めない場合は inconclusive として fail-close する。
    """
    if shutil.which("gh") is None:
        return [f"{label}: inconclusive（gh CLI が無く merged PR を照合できない・fail-close）"]
    pr = event.get("merged_pr")
    try:
        out = _run(
            [
                "gh",
                "pr",
                "view",
                str(pr),
                "--json",
                "state,mergeCommit,headRefOid,baseRefName",
            ],
            cwd=repo_root,
            timeout=timeout,
        )
    except (GitError, subprocess.TimeoutExpired) as exc:
        return [f"{label}: inconclusive（gh pr view #{pr} を実行できない: {exc}）"]
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return [f"{label}: inconclusive（gh pr view #{pr} の JSON を解析できない）"]
    if not isinstance(payload, dict):
        return [f"{label}: inconclusive（gh pr view #{pr} の応答が object ではない）"]

    issues: list[str] = []
    if payload.get("state") != "MERGED":
        issues.append(f"{label}: PR #{pr} が MERGED ではない（state={payload.get('state')!r}）")
    merge_commit = payload.get("mergeCommit")
    oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    if not isinstance(oid, str) or not oid:
        issues.append(f"{label}: inconclusive（PR #{pr} の mergeCommit を取得できない）")
    elif oid != event.get("actual_merge_terminal_sha"):
        issues.append(
            f"{label}: actual_merge_terminal_sha が PR #{pr} の merge commit と一致しない"
            f"（github={oid}）"
        )
    head_oid = payload.get("headRefOid")
    if isinstance(head_oid, str) and head_oid:
        if head_oid != event.get("premerge_tested_head"):
            issues.append(
                f"{label}: premerge_tested_head が PR #{pr} の head と一致しない"
                f"（github={head_oid}）"
            )
        if head_oid == event.get("actual_merge_terminal_sha"):
            issues.append(f"{label}: PR head を actual merge terminal に代用している")
    base_ref = payload.get("baseRefName")
    if isinstance(base_ref, str) and base_ref and base_ref != "main":
        issues.append(f"{label}: PR #{pr} の base が main ではない（{base_ref}）")
    return issues


def find_event_anchor(repo_root: Path, *, rel_path: str, mainline_ref: str) -> str | None:
    """exact path が main first-parent へ初出する commit を導出する。"""
    out = _run(
        [
            "git",
            "log",
            "--first-parent",
            "--diff-filter=A",
            "--format=%H",
            mainline_ref,
            "--",
            rel_path,
        ],
        cwd=repo_root,
    )
    shas = [line.strip() for line in out.splitlines() if line.strip()]
    return shas[-1] if shas else None


def check_event_anchor(
    repo_root: Path,
    event: dict[str, Any],
    *,
    rel_path: str,
    mainline_ref: str,
    require_anchor: bool,
    label: str,
) -> list[str]:
    """event anchor が actual terminal の真の子孫であることを検査する。"""
    anchor = find_event_anchor(repo_root, rel_path=rel_path, mainline_ref=mainline_ref)
    if anchor is None:
        if require_anchor:
            return [f"{label}: event anchor を main first-parent 上に導出できない（S1 未 merge）"]
        return []
    actual = str(event.get("actual_merge_terminal_sha"))
    if anchor == actual:
        return [f"{label}: event anchor が actual terminal と同一（真の子孫でない）"]
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", actual, anchor],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return [f"{label}: event anchor {anchor} が actual terminal {actual} の子孫ではない"]
    return []


# ---------------------------------------------------------------------------
# (f) S1／R／S2 の順序契約と R の state-neutral 検査
# ---------------------------------------------------------------------------


def check_unit_order(
    *,
    first_parents: list[str],
    terminal_sha: str,
    s1_sha: str,
    r_sha: str,
    s2_sha: str,
) -> list[str]:
    """main first-parent 上で `T < S1 < R < S2` を満たすことを検査する。

    R は通常 task ではない closeout-internal unit であり、この検査は R 自身の
    closeout event を要求しない（再帰停止条件）。
    """
    issues: list[str] = []
    order = {sha: index for index, sha in enumerate(first_parents)}
    labelled = (
        ("T", terminal_sha),
        ("S1", s1_sha),
        ("R", r_sha),
        ("S2", s2_sha),
    )
    for label, sha in labelled:
        if sha not in order:
            issues.append(f"{label}={sha} が main first-parent 上にない")
    if issues:
        return issues
    shas = [sha for _, sha in labelled]
    if len(set(shas)) != len(shas):
        issues.append(
            "T／S1／R／S2 に同一 commit を使っている（同じ未 merge branch での実施を拒否）"
        )
        return issues
    # first_parents は新しい順のため index が小さいほど新しい。
    for (prev_label, prev_sha), (next_label, next_sha) in zip(labelled, labelled[1:], strict=False):
        if order[prev_sha] <= order[next_sha]:
            issues.append(
                f"main first-parent 上で {prev_label} < {next_label} を満たさない"
                f"（{prev_sha} / {next_sha}）"
            )
    return issues


def parse_active_queue(plan_text: str) -> dict[str, Any] | None:
    """plan の `active-queue:v1` block を dict で返す（parse 不能なら None）。"""
    match = ACTIVE_QUEUE_BLOCK_RE.search(plan_text)
    if match is None:
        return None
    loaded = yaml.safe_load(match.group("body"))
    return loaded if isinstance(loaded, dict) else None


def _plan_text_at(repo_root: Path, ref: str) -> str | None:
    try:
        return _run(["git", "show", f"{ref}:{PLAN_REL}"], cwd=repo_root)
    except GitError:
        return None


def check_registration_unit(repo_root: Path, *, base: str, head: str) -> list[str]:
    """R（registration-only unit）が state-neutral かつ非再帰であることを検査する。"""
    issues: list[str] = []
    for status, path in diff_name_status(repo_root, base, head):
        if path.startswith(f"{EVENT_DIR_REL}/"):
            issues.append(
                f"{path}: R は closeout event を生成／変更しない（status={status}・再帰停止条件）"
            )
    base_plan = _plan_text_at(repo_root, base)
    head_plan = _plan_text_at(repo_root, head)
    if base_plan is None or head_plan is None:
        issues.append(
            f"{PLAN_REL} を {base}／{head} から読めないため R の state-neutral を検証できない"
        )
        return issues
    base_block = parse_active_queue(base_plan)
    head_block = parse_active_queue(head_plan)
    if base_block is None or head_block is None:
        issues.append(f"{PLAN_REL}: active-queue:v1 block を解析できない")
        return issues
    for field in ("task_id", "state", "substep"):
        if base_block.get(field) != head_block.get(field):
            issues.append(
                f"R が active block の {field} を変更している"
                f"（{base_block.get(field)!r} -> {head_block.get(field)!r}）"
            )
    return issues


def check_s1_state_sync(
    repo_root: Path, *, base: str, head: str, event: dict[str, Any]
) -> list[str]:
    """S1 が前 task を closing に保ち Done へ移していないことを検査する。"""
    issues: list[str] = []
    task_id = str(event.get("task_id"))
    base_plan = _plan_text_at(repo_root, base)
    head_plan = _plan_text_at(repo_root, head)
    if base_plan is None or head_plan is None:
        return [f"{PLAN_REL} を {base}／{head} から読めないため S1 契約を検証できない"]
    head_block = parse_active_queue(head_plan)
    if head_block is None:
        return [f"{PLAN_REL}: active-queue:v1 block を解析できない"]
    if head_block.get("task_id") != task_id:
        issues.append(
            f"S1 の active block が closeout 対象 task と一致しない"
            f"（{head_block.get('task_id')!r} != {task_id!r}）"
        )
    if head_block.get("state") != "closing":
        issues.append(f"S1 は前 task を state=closing に保つ（現在 {head_block.get('state')!r}）")
    if head_block.get("substep") != "post-merge-evidence-registration":
        issues.append(
            "S1 の substep は post-merge-evidence-registration でなければならない"
            f"（現在 {head_block.get('substep')!r}）"
        )
    if _done_section_mentions(head_plan, task_id) and not _done_section_mentions(
        base_plan, task_id
    ):
        issues.append(f"S1 で {task_id} を Done へ移してはならない")
    issues.extend(check_s1_repair_hold_consistency(event=event, head_block=head_block))
    return issues


def check_s1_repair_hold_consistency(
    *, event: Mapping[str, Any], head_block: Mapping[str, Any]
) -> list[str]:
    """(i): S1 の closeout_sequence と head plan の repair-hold の整合を検査する（PR-E3c）。

    - `closeout_sequence >= 2`（R₂ 系列の S1₂）は head の active block に `correction_repair`
      があり、その `repair_target_task_id` が event の task と一致するときだけ受理する
      （repair target 以外の sequence 2 は fail）。
    - `closeout_sequence == 1` は head の `correction_repair` が null であることを要求する
      （repair 途中の通常 closeout＝通常 dispatch を拒否）。
    """
    issues: list[str] = []
    task_id = str(event.get("task_id"))
    sequence = event.get("closeout_sequence")
    repair = head_block.get("correction_repair")
    if isinstance(sequence, int) and not isinstance(sequence, bool) and sequence >= 2:
        if not isinstance(repair, dict):
            issues.append(
                f"closeout_sequence={sequence} の S1 は repair-hold（plan active block の"
                " correction_repair）中にだけ作れる（現在 null）"
            )
        elif repair.get("repair_target_task_id") != task_id:
            issues.append(
                f"closeout_sequence={sequence} の S1 は correction_repair.repair_target_task_id"
                f"（{repair.get('repair_target_task_id')!r}）と一致する task にだけ作れる"
                f"（event task={task_id}）"
            )
    elif repair is not None:
        issues.append(
            "repair-hold（correction_repair 非 null）中は closeout_sequence=1 の通常 S1 を"
            f" 作れない（event task={task_id}・repair 途中の通常 dispatch を拒否）"
        )
    return issues


def check_s1_added_events(repo_root: Path, *, base: str, head: str) -> list[str]:
    """S1 が追加した各 event に対し plan／state の closing 契約を検査する。"""
    issues: list[str] = []
    for status, path in diff_name_status(repo_root, base, head):
        if status != "A" or not path.startswith(f"{EVENT_DIR_REL}/"):
            continue
        try:
            raw = _run(["git", "show", f"{head}:{path}"], cwd=repo_root)
        except GitError as exc:
            issues.append(f"{path}: head から event を読めない（{exc}）")
            continue
        loaded = yaml.safe_load(raw)
        if not isinstance(loaded, dict):
            issues.append(f"{path}: event file が object ではない")
            continue
        issues.extend(check_s1_state_sync(repo_root, base=base, head=head, event=loaded))
    return issues


def _done_section_mentions(plan_text: str, task_id: str) -> bool:
    match = re.search(r"^## Done\b.*$", plan_text, re.MULTILINE)
    if match is None:
        return False
    start = match.end()
    next_heading = re.search(r"^## ", plan_text[start:], re.MULTILINE)
    end = start + (next_heading.start() if next_heading else len(plan_text) - start)
    return re.search(rf"\b{re.escape(task_id)}\b", plan_text[start:end]) is not None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def validate(
    *,
    repo_root: Path,
    base: str | None,
    head: str,
    mainline_ref: str | None,
    require_anchor: bool,
    github_timeout: int,
    unit_shas: tuple[str, str, str, str] | None,
    registration: RegistrationCheckInput | None,
    assert_unit: str | None = None,
) -> list[str]:
    """全検査を実行し違反理由の一覧を返す（空なら pass）。"""
    issues: list[str] = check_allowlist_wiring(repo_root)

    events, event_issues = load_event_files(repo_root)
    issues.extend(event_issues)
    issues.extend(validate_event_chain(events))

    try:
        resolved_mainline = mainline_ref or resolve_mainline_ref(repo_root)
        parents = first_parent_shas(repo_root, resolved_mainline)
    except (GitError, subprocess.TimeoutExpired) as exc:
        return issues + [f"inconclusive（git 履歴を読めない: {exc}）"]

    for event_id, event in sorted(events.items()):
        rel_path = f"{EVENT_DIR_REL}/{event_id}.yml"
        issues.extend(
            check_actual_terminal_on_mainline(event, first_parents=parents, label=rel_path)
        )
        issues.extend(verify_github_merge(repo_root, event, label=rel_path, timeout=github_timeout))
        issues.extend(
            check_event_anchor(
                repo_root,
                event,
                rel_path=rel_path,
                mainline_ref=resolved_mainline,
                require_anchor=require_anchor,
                label=rel_path,
            )
        )

    if base is not None:
        patterns, allowlist_issues = load_state_sync_allowlist(repo_root)
        issues.extend(allowlist_issues)
        try:
            issues.extend(check_existing_events_unchanged(repo_root, base=base, head=head))
            if assert_unit == "r":
                issues.extend(check_registration_unit(repo_root, base=base, head=head))
            else:
                issues.extend(
                    check_state_sync_diff(repo_root, base=base, head=head, patterns=patterns)
                )
                if assert_unit == "s1":
                    issues.extend(check_s1_added_events(repo_root, base=base, head=head))
        except (GitError, subprocess.TimeoutExpired) as exc:
            issues.append(f"inconclusive（state-sync diff を取得できない: {exc}）")

    if assert_unit == "r":
        # R では registration 検査（(g)）を必須にする（HRAI-REAUDIT-20260815 P1-01・PR-E2）。
        # `--registration-*` 省略時は base の plan active block と git show から導出し、
        # 明示 flag が導出値と異なれば fail する。base 未指定では導出できず fail-close。
        if base is None:
            issues.append("--assert-unit r には --base が必要（registration 検査を省略できない）")
        else:
            derived, derive_issues = derive_registration_input(
                repo_root, base=base, head=head, events=events
            )
            if registration is None:
                if derived is None:
                    issues.extend(
                        f"registration 検査入力を導出できない（fail-close）: {issue}"
                        for issue in derive_issues
                    )
                registration = derived
            elif derived is not None:
                issues.extend(_compare_registration_inputs(registration, derived))
            elif derive_issues:
                issues.extend(
                    f"明示 --registration-* を base の導出値と照合できない: {issue}"
                    for issue in derive_issues
                )

    if unit_shas is not None:
        terminal_sha, s1_sha, r_sha, s2_sha = unit_shas
        issues.extend(
            check_unit_order(
                first_parents=parents,
                terminal_sha=terminal_sha,
                s1_sha=s1_sha,
                r_sha=r_sha,
                s2_sha=s2_sha,
            )
        )

    if registration is not None:
        issues.extend(
            check_registration_manifest_diff(
                events,
                base_manifest=registration.base_manifest,
                head_manifest=registration.head_manifest,
                task_id=registration.task_id,
                closeout_sequence=registration.closeout_sequence,
                merged_pr=registration.merged_pr,
                closeout_role=registration.closeout_role,
            )
        )
    return issues


def _load_manifest(source: Path | dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """manifest を Path（実 file）または dict（`git show` 済み payload）から読む。"""
    if isinstance(source, dict):
        return source, []
    if not source.is_file():
        return None, [f"{source}: manifest が存在しない"]
    try:
        loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, [f"{source}: manifest を読めない: {exc}"]
    if not isinstance(loaded, dict):
        return None, [f"{source}: manifest が object ではない"]
    return loaded, []


def manifest_at_revision(repo_root: Path, ref: str) -> tuple[dict[str, Any] | None, list[str]]:
    """`git show <ref>:<manifest>` から manifest dict を得る（working tree を読まない）。"""
    try:
        raw = _run(["git", "show", f"{ref}:{MANIFEST_REL}"], cwd=repo_root)
    except (GitError, subprocess.TimeoutExpired) as exc:
        return None, [f"{ref}:{MANIFEST_REL} を git から読めない（{exc}）"]
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, [f"{ref}:{MANIFEST_REL} を YAML として読めない: {exc}"]
    if not isinstance(loaded, dict):
        return None, [f"{ref}:{MANIFEST_REL} が object ではない"]
    return loaded, []


def derive_registration_input(
    repo_root: Path,
    *,
    base: str,
    head: str,
    events: dict[str, dict[str, Any]],
) -> tuple[RegistrationCheckInput | None, list[str]]:
    """R unit の registration 検査入力を base の plan active block と git から導出する。

    base 時点の `docs/plan.md` active block（state=closing）の
    `closeout_registration.event_id` から対象 event を引き、その task_id／merged_pr／
    closeout_sequence／closeout_role を採る。manifest は base／head の `git show` から得る。
    導出できなければ `(None, issues)`（fail-close）。
    """
    base_plan = _plan_text_at(repo_root, base)
    if base_plan is None:
        return None, [f"{PLAN_REL} を {base} から読めないため registration 検査入力を導出できない"]
    block = parse_active_queue(base_plan)
    if block is None:
        return None, [f"{base}:{PLAN_REL}: active-queue:v1 block を解析できない"]
    registration = block.get("closeout_registration")
    if not isinstance(registration, dict):
        return None, [
            f"{base}:{PLAN_REL}: active block に closeout_registration がない"
            "（R は S1 が置いた closing block を base に持つ）"
        ]
    event_id = registration.get("event_id")
    if not isinstance(event_id, str) or event_id not in events:
        return None, [
            f"{base}:{PLAN_REL}: closeout_registration.event_id={event_id!r} の event が"
            " 読み込めない（S1 event が存在しない、または schema 不正）"
        ]
    event = events[event_id]
    base_manifest, base_issues = manifest_at_revision(repo_root, base)
    head_manifest, head_issues = manifest_at_revision(repo_root, head)
    if base_manifest is None or head_manifest is None:
        return None, base_issues + head_issues
    # PR #1387 Round 2 inline 指摘: schema 不正 event で int() が ValueError/TypeError を
    # 送出すると validator 自体が例外終了し、fail-close の理由一覧を返せなくなる（P-010）。
    # 変換失敗は issue 化して (None, issues) を返す。
    numeric_issues: list[str] = []
    numeric_values: dict[str, int] = {}
    for field in ("merged_pr", "closeout_sequence"):
        raw = event.get(field, 0)
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            numeric_issues.append(
                f"{base}:{PLAN_REL}: event {event_id} の {field} が整数として読めない: {raw!r}"
            )
            continue
        try:
            numeric_values[field] = int(raw)
        except ValueError:
            numeric_issues.append(
                f"{base}:{PLAN_REL}: event {event_id} の {field} が整数として読めない: {raw!r}"
            )
    if numeric_issues:
        return None, numeric_issues
    return (
        RegistrationCheckInput(
            base_manifest=base_manifest,
            head_manifest=head_manifest,
            task_id=str(event.get("task_id")),
            merged_pr=numeric_values["merged_pr"],
            closeout_sequence=numeric_values["closeout_sequence"],
            closeout_role=str(event.get("closeout_role")),
        ),
        [],
    )


def _compare_registration_inputs(
    explicit: RegistrationCheckInput, derived: RegistrationCheckInput
) -> list[str]:
    """明示 `--registration-*` と base から導出した値の不一致を返す。

    manifest path は比較しない（明示 path はそのまま使う）。
    """
    issues: list[str] = []
    for field_name in ("task_id", "merged_pr", "closeout_sequence", "closeout_role"):
        explicit_value = getattr(explicit, field_name)
        derived_value = getattr(derived, field_name)
        if explicit_value != derived_value:
            issues.append(
                f"--registration-{field_name.replace('_', '-')}={explicit_value!r} が base の"
                f" closeout_registration から導出した値 {derived_value!r} と一致しない"
            )
    return issues


def _registry_entries(manifest: dict[str, Any]) -> dict[str, Any]:
    registry = manifest.get("evidence_registry")
    entries = registry.get("entries") if isinstance(registry, dict) else None
    return {str(key): value for key, value in entries.items()} if isinstance(entries, dict) else {}


def _closure_histories(manifest: dict[str, Any]) -> dict[str, list[Any]]:
    histories: dict[str, list[Any]] = {}
    claims = manifest.get("claims")
    if not isinstance(claims, list):
        return histories
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        closure = claim.get("closure")
        history = closure.get("history") if isinstance(closure, dict) else None
        histories[str(claim.get("claim_id"))] = list(history) if isinstance(history, list) else []
    return histories


def check_event_expectation(
    event: Mapping[str, Any], *, base_manifest: dict[str, Any], label: str
) -> list[str]:
    """(g-a)(g-b): event の root slot 集合と closure_claim_ids を base manifest 導出集合と照合する。

    original は `derive_expected_roots(base, task, merged_pr)` と exact（missing／extra／
    duplicate 拒否）、`closure_claim_ids == derive_owner_open_claims(base, task)`。訂正 event
    （correction_ordinal >= 1）は置換 slot の部分集合を扱うため `derive_expected_slots` の
    部分集合（extra／duplicate 拒否）、closure_claim_ids は owner open claim の部分集合とする。
    導出 issue（alias／aggregate／deadlock claim の finalizer 未宣言）はそのまま fail にする。
    """
    issues: list[str] = []
    task_id = str(event.get("task_id"))
    merged_pr = event.get("merged_pr")
    ordinal = event.get("correction_ordinal")
    verification = event.get("terminal_verification")
    if not isinstance(verification, dict) or not isinstance(merged_pr, int):
        return [f"{label}: terminal_verification／merged_pr を読めないため期待集合と照合できない"]
    roots = verification.get("evidence_roots")
    root_list = (
        [root for root in roots if isinstance(root, dict)] if isinstance(roots, list) else []
    )
    actual_slots = [root_slot(root) for root in root_list]
    if ordinal == 0:
        expected, derive_issues = expectation.derive_expected_roots(
            base_manifest, task_id, merged_pr=merged_pr
        )
    else:
        expected, derive_issues = expectation.derive_expected_slots(base_manifest, task_id)
    issues.extend(f"{label}: manifest 導出不能: {issue}" for issue in derive_issues)
    missing, extra, duplicate = expectation.compare_slot_sets(expected, actual_slots)
    if duplicate:
        issues.append(f"{label}: event root に同じ slot が重複している: {duplicate!r}")
    if extra:
        issues.append(
            f"{label}: event root に manifest 導出集合外の slot がある（extra）: {extra!r}"
        )
    if ordinal == 0 and missing:
        issues.append(
            f"{label}: event root に manifest 導出集合の slot が欠けている"
            f"（missing {len(missing)} 件）: {missing!r}"
        )

    owner_claims = expectation.derive_owner_open_claims(base_manifest, task_id)
    claim_ids = [str(item) for item in verification.get("closure_claim_ids") or []]
    if ordinal == 0:
        if claim_ids != owner_claims:
            issues.append(
                f"{label}: closure_claim_ids が manifest 導出の owner open claim 集合と一致しない"
                f"（event={claim_ids!r} / manifest={owner_claims!r}）"
            )
    else:
        unexpected = sorted(set(claim_ids) - set(owner_claims))
        if unexpected:
            issues.append(
                f"{label}: 訂正 event の closure_claim_ids に owner open claim 外の claim がある:"
                f" {', '.join(unexpected)}"
            )
    return issues


_ENTRY_ROOT_FIELDS = (
    ("subject_id", "subject_id"),
    ("evidence_type", "evidence_type"),
    ("phase_or_context_id", "phase_or_context_id"),
    ("artifact_path", "artifact_path"),
    ("raw_sha256", "artifact_raw_sha256"),
)


def _slot_chain_terminals(
    entries: dict[str, Any],
) -> dict[tuple[Any, Any, Any], tuple[str, dict[str, Any]]]:
    """slot ごとの `correction_of` chain の unsuperseded 末端 `(entry_id, entry)`。

    複数末端（branch）・original 不在・dangling `correction_of`（参照先 entry が無い）の slot は
    「末端を一意に解決できない」ため含めない。この場合 `check_added_entries_match_roots()` は
    当該 slot を「chain 無し」として扱い `correction_of=null` を要求する（broken chain の末端へ
    繋がせない）。壊れた chain 自体は `registry_current_entries` 側の chain 不正 issue が別途
    fail-close する（PR #1394 Round 3 指摘: 旧 docstring は「chain 無しとして扱わない」と書いて
    おり実挙動と矛盾していた）。
    """
    by_slot: dict[tuple[Any, Any, Any], dict[str, dict[str, Any]]] = {}
    for entry_id, entry in entries.items():
        if isinstance(entry, dict) and isinstance(entry.get("subject_id"), str):
            by_slot.setdefault(root_slot(entry), {})[entry_id] = entry
    terminals: dict[tuple[Any, Any, Any], tuple[str, dict[str, Any]]] = {}
    for slot, members in by_slot.items():
        superseded = {
            str(entry.get("correction_of"))
            for entry in members.values()
            if entry.get("correction_of") is not None
        }
        # PR #1394 Round 1 指摘: docstring は「original 不在の slot は含めない」と書いていたが、
        # 実装は original の存在も correction_of の参照整合も見ていなかった。broken chain
        # （original 不在・参照先 entry 欠落）を「末端を一意に解決できる」と誤判定しないよう、
        # 両方を満たす slot だけ terminals に含める（fail-close は呼出側と registry 側が行う）。
        has_original = any(entry.get("correction_of") is None for entry in members.values())
        dangling = any(
            str(entry.get("correction_of")) not in members
            for entry in members.values()
            if entry.get("correction_of") is not None
        )
        if not has_original or dangling:
            continue
        leaves = [entry_id for entry_id in members if entry_id not in superseded]
        if len(leaves) == 1:
            terminals[slot] = (leaves[0], members[leaves[0]])
    return terminals


def check_added_entries_match_roots(
    event: Mapping[str, Any],
    *,
    added_entries: dict[str, Any],
    base_entries: dict[str, Any],
    label: str,
) -> list[str]:
    """(g-c): 追加 entry の field が対応 event root と一致することを検査する。

    subject_id／evidence_type／phase_or_context_id／artifact_path／raw_sha256、
    `postmerge_mapping.merged_pr`／`actual_merge_terminal_sha`、original では
    `correction_of=null`、訂正（Rcorr'）では `correction_of` が同 slot の既存 entry を指すことを
    要求する。root に対応しない追加 entry は (g) の extra 検査が拒否するためここでは扱わない。

    R₂（`correction_ordinal=0` かつ `closeout_sequence>=2`・PR-E3c）: 同 slot に base の chain が
    無ければ `correction_of=null`、chain があれば Rcorr' が `disposition=invalidates` で閉じた
    unsuperseded 末端を `correction_of` に持つことを要求する（live な旧 entry を新 sequence で
    上書きしない。`closeout_expectation.registry_current_entries` は slot ごとに original 1 件
    を要求するため、旧 chain の末端へ繋がなければ current を解決できない）。
    """
    issues: list[str] = []
    verification = event.get("terminal_verification")
    roots = verification.get("evidence_roots") if isinstance(verification, dict) else None
    roots_by_id = {
        str(root.get("evidence_id")): root
        for root in (roots if isinstance(roots, list) else [])
        if isinstance(root, dict)
    }
    ordinal = event.get("correction_ordinal")
    sequence = event.get("closeout_sequence")
    new_sequence = (
        ordinal == 0
        and isinstance(sequence, int)
        and not isinstance(sequence, bool)
        and sequence >= 2
    )
    base_terminals = _slot_chain_terminals(base_entries) if new_sequence else {}
    for entry_id in sorted(added_entries):
        root = roots_by_id.get(entry_id)
        if root is None:
            continue
        entry = added_entries[entry_id]
        if not isinstance(entry, dict):
            issues.append(f"{label}: registry entry {entry_id} が object ではない")
            continue
        for entry_field, root_field in _ENTRY_ROOT_FIELDS:
            if entry.get(entry_field) != root.get(root_field):
                issues.append(
                    f"{label}: registry entry {entry_id}.{entry_field}="
                    f"{entry.get(entry_field)!r} が event root の {root_field}="
                    f"{root.get(root_field)!r} と一致しない"
                )
        mapping = entry.get("postmerge_mapping")
        if not isinstance(mapping, dict):
            issues.append(f"{label}: registry entry {entry_id} に postmerge_mapping がない")
        else:
            if mapping.get("merged_pr") != event.get("merged_pr"):
                issues.append(
                    f"{label}: registry entry {entry_id}.postmerge_mapping.merged_pr="
                    f"{mapping.get('merged_pr')!r} が event の merged_pr={event.get('merged_pr')!r}"
                    " と一致しない"
                )
            if mapping.get("actual_merge_terminal_sha") != event.get("actual_merge_terminal_sha"):
                issues.append(
                    f"{label}: registry entry {entry_id}.postmerge_mapping."
                    "actual_merge_terminal_sha が event の actual_merge_terminal_sha と一致しない"
                )
        correction_of = entry.get("correction_of")
        if new_sequence:
            terminal = base_terminals.get(root_slot(root))
            if terminal is None:
                if correction_of is not None:
                    issues.append(
                        f"{label}: R₂ の登録 entry {entry_id} は base に同 slot の chain が無いため"
                        f" correction_of=null が必要（現在 {correction_of!r}）"
                    )
            else:
                terminal_id, terminal_entry = terminal
                if terminal_entry.get("disposition") != "invalidates":
                    issues.append(
                        f"{label}: R₂ の登録 entry {entry_id} の slot {root_slot(root)!r} は"
                        f" base に live な chain 末端 {terminal_id} が残っている（Rcorr' で"
                        " invalidates にしてから新 sequence を登録する）"
                    )
                elif correction_of != terminal_id:
                    issues.append(
                        f"{label}: R₂ の登録 entry {entry_id} は base chain の invalidates 末端"
                        f" {terminal_id} を correction_of に持つ必要がある"
                        f"（現在 {correction_of!r}）"
                    )
        elif ordinal == 0:
            if correction_of is not None:
                issues.append(
                    f"{label}: original event の登録 entry {entry_id} は correction_of=null が必要"
                    f"（現在 {correction_of!r}）"
                )
        else:
            previous = base_entries.get(correction_of) if isinstance(correction_of, str) else None
            if not isinstance(previous, dict):
                issues.append(
                    f"{label}: 訂正 entry {entry_id} の correction_of={correction_of!r} が"
                    " base の既存 entry を指していない"
                )
            elif root_slot(previous) != root_slot(root):
                issues.append(
                    f"{label}: 訂正 entry {entry_id} の correction_of が別 slot の entry を指す"
                )
    return issues


def check_registration_manifest_diff(
    events: dict[str, dict[str, Any]],
    *,
    base_manifest: Path | dict[str, Any],
    head_manifest: Path | dict[str, Any],
    task_id: str,
    closeout_sequence: int,
    merged_pr: int,
    closeout_role: str = "task_terminal",
) -> list[str]:
    """R の atomic batch が S1 固定集合と exact 一致するかを manifest 差分で検査する。

    追加された `evidence_registry.entries` key と、末尾へ append された closure
    history event を集め、対象 event の `required_evidence_roots` ／
    `closure_claim_ids` と exact 照合する（部分登録・extra・duplicate を拒否）。
    既存 entry ／既存 history の変更も拒否する。さらに (g-a)(g-b)(g-c)（module
    docstring）で event 自体を base manifest 導出集合と照合し、追加 entry の field を
    event root と照合する（PR-E2）。

    (i)（PR-E3c）: 対象 task の全 unsuperseded stable key（旧 sequence を含む）の root 和集合が
    head registry に登録済みであることも要求する（R₂ 後に旧 sequence の root が欠けたまま
    「登録済み」と見なさない）。
    """
    base, issues = _load_manifest(base_manifest)
    head, head_issues = _load_manifest(head_manifest)
    issues = issues + head_issues
    if base is None or head is None:
        return issues

    key = (task_id, merged_pr, closeout_role, closeout_sequence)
    current = current_event_for_key(events, key)
    if current is None:
        return issues + [f"stable key {key!r} の unsuperseded event を一意に解決できない"]
    head_entry_ids = set(_registry_entries(head))
    superseded_ids = {
        str(event.get("corrects_event_id"))
        for event in events.values()
        if event.get("corrects_event_id") is not None
    }
    for other_id, other in sorted(events.items()):
        if other.get("task_id") != task_id or other_id in superseded_ids or other is current:
            continue
        other_roots = other.get("terminal_verification")
        other_root_list = (
            other_roots.get("evidence_roots") if isinstance(other_roots, dict) else None
        )
        other_ids = (
            [str(root.get("evidence_id")) for root in other_root_list if isinstance(root, dict)]
            if isinstance(other_root_list, list)
            else []
        )
        unregistered = [root_id for root_id in other_ids if root_id not in head_entry_ids]
        if unregistered:
            issues.append(
                f"task {task_id} の別 stable key {event_stable_key(other)!r}（event {other_id}）の"
                f" root {len(unregistered)}/{len(other_ids)} 件が head registry に未登録"
                "（全 unsuperseded key の和集合を要求）"
            )
    verification = current.get("terminal_verification")
    if not isinstance(verification, dict):
        return issues + [f"stable key {key!r} の terminal_verification を読めない"]
    roots = verification.get("evidence_roots")
    required_ids = (
        [str(root["evidence_id"]) for root in roots if isinstance(root, dict)]
        if isinstance(roots, list)
        else []
    )
    required_claims = [str(item) for item in verification.get("closure_claim_ids", [])]
    label = f"event {current.get('event_id')}"

    issues.extend(check_event_expectation(current, base_manifest=base, label=label))

    base_entries = _registry_entries(base)
    head_entries = _registry_entries(head)
    for entry_id, value in base_entries.items():
        if entry_id not in head_entries:
            issues.append(f"registry entry {entry_id} が削除されている（byte 不変違反）")
        elif head_entries[entry_id] != value:
            issues.append(f"registry entry {entry_id} が変更されている（byte 不変違反）")
    added_ids = [entry_id for entry_id in head_entries if entry_id not in base_entries]
    issues.extend(
        check_added_entries_match_roots(
            current,
            added_entries={entry_id: head_entries[entry_id] for entry_id in added_ids},
            base_entries=base_entries,
            label=label,
        )
    )

    base_histories = _closure_histories(base)
    head_histories = _closure_histories(head)
    appended_claims: list[str] = []
    for claim_id, head_history in head_histories.items():
        base_history = base_histories.get(claim_id, [])
        if head_history[: len(base_history)] != base_history:
            issues.append(f"claim {claim_id} の既存 closure history が変更されている")
            continue
        appended = len(head_history) - len(base_history)
        if appended == 1:
            appended_claims.append(claim_id)
        elif appended > 1:
            issues.append(
                f"claim {claim_id} へ {appended} 件の history を append している（1 件のみ）"
            )

    issues.extend(
        check_registration_batch(
            required_evidence_ids=required_ids,
            required_claim_ids=required_claims,
            registered_evidence_ids=added_ids,
            registered_claim_ids=appended_claims,
        )
    )
    return issues


def _slot_descriptor(slot: expectation.SlotKey) -> dict[str, Any]:
    return {"subject_id": slot[0], "evidence_type": slot[1], "phase_or_context_id": slot[2]}


def check_completeness(
    *,
    events: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    task_id: str,
) -> tuple[dict[str, Any], bool]:
    """(h) 既存 event の root slot 集合を manifest 導出集合と照合し `(report, complete)` を返す。

    対象は `task_id` の original stable key ごとの unsuperseded event。各 event について
    `derive_expected_slots(manifest, task_id)` との missing／extra、および registry の
    current entry（同一 merged_pr）が無い期待 slot を列挙する。event が 0 件、導出 issue、
    missing／extra があれば ``complete=False``。
    """
    keys = sorted(
        {
            (
                str(event.get("task_id")),
                event.get("merged_pr"),
                str(event.get("closeout_role")),
                event.get("closeout_sequence"),
            )
            for event in events.values()
            if event.get("task_id") == task_id
        },
        key=repr,
    )
    expected, derive_issues = expectation.derive_expected_slots(manifest, task_id)
    current, registry_issues = expectation.registry_current_entries_and_issues(manifest)
    report: dict[str, Any] = {
        "task_id": task_id,
        "expected_slot_count": len(expected),
        "expected_slots": [slot.to_descriptor() for slot in expected],
        "issues": [*derive_issues, *registry_issues],
        "events": [],
    }
    complete = not report["issues"] and bool(keys)
    if not keys:
        report["issues"].append(f"task:{task_id} の closeout event が存在しない")
    for key in keys:
        event = current_event_for_key(events, key)
        if event is None:
            report["issues"].append(
                f"stable key {key!r} の unsuperseded event を一意に解決できない"
            )
            complete = False
            continue
        verification = event.get("terminal_verification")
        roots = verification.get("evidence_roots") if isinstance(verification, dict) else None
        root_list = [r for r in roots if isinstance(r, dict)] if isinstance(roots, list) else []
        actual = [root_slot(root) for root in root_list]
        missing, extra, duplicate = expectation.compare_slot_sets(expected, actual)
        merged_pr = event.get("merged_pr")
        registered = [
            slot
            for slot in expected
            if slot.slot in current and expectation.entry_merged_pr(current[slot.slot]) == merged_pr
        ]
        unregistered = [slot for slot in expected if slot not in registered]
        entry: dict[str, Any] = {
            "event_id": event.get("event_id"),
            "merged_pr": merged_pr,
            "closeout_sequence": event.get("closeout_sequence"),
            "correction_ordinal": event.get("correction_ordinal"),
            "event_root_count": len(actual),
            "missing_count": len(missing),
            "missing": [_slot_descriptor(slot) for slot in missing],
            "extra_count": len(extra),
            "extra": [_slot_descriptor(slot) for slot in extra],
            "duplicate": [_slot_descriptor(slot) for slot in duplicate],
            "registered_current_count": len(registered),
            "unregistered_expected_count": len(unregistered),
            # PR #1387 Round 3 suppressed 指摘（Backlog #1390）: 件数だけでは欠落 slot を
            # 特定できず、回復 PR が毎回 closeout_expectation から再導出する必要があった。
            "unregistered_expected": [_slot_descriptor(slot.slot) for slot in unregistered],
        }
        report["events"].append(entry)
        if missing or extra or duplicate:
            complete = False
    report["complete"] = complete
    return report, complete


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument(
        "--check-completeness",
        metavar="TASK_ID",
        default=None,
        help=(
            "診断専用 mode。既存 event の root slot 集合を manifest 導出集合と照合し JSON を"
            " stdout へ出す（不足があれば exit 1）。他の検査は行わない"
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=f"--check-completeness が読む manifest（既定: <repo-root>/{MANIFEST_REL}）",
    )
    parser.add_argument("--base", default=None, help="state-sync diff の base ref")
    parser.add_argument("--head", default="HEAD", help="state-sync diff の head ref")
    parser.add_argument("--mainline-ref", default=None)
    parser.add_argument(
        "--require-anchor",
        action="store_true",
        help="event anchor が main first-parent へ出現済みであることを要求する（S1 merge 後）",
    )
    parser.add_argument("--github-timeout", type=int, default=60)
    parser.add_argument(
        "--assert-unit",
        choices=("s1", "r"),
        default=None,
        help="base..head の unit 種別（s1: closing 同期契約、r: state-neutral な登録専用 unit）",
    )
    parser.add_argument("--unit-terminal", default=None)
    parser.add_argument("--unit-s1", default=None)
    parser.add_argument("--unit-r", default=None)
    parser.add_argument("--unit-s2", default=None)
    parser.add_argument("--registration-base-manifest", type=Path, default=None)
    parser.add_argument("--registration-head-manifest", type=Path, default=None)
    parser.add_argument("--registration-task-id", default=None)
    parser.add_argument("--registration-merged-pr", type=int, default=None)
    parser.add_argument("--registration-closeout-sequence", type=int, default=1)
    parser.add_argument("--registration-closeout-role", default="task_terminal")
    args = parser.parse_args(argv)
    repo_root: Path = args.repo_root.resolve()

    if args.check_completeness is not None:
        # (h) 診断専用 mode: event の root slot 集合を manifest 導出集合と照合して JSON を出す。
        events, event_issues = load_event_files(repo_root)
        manifest_path: Path = (
            args.manifest if args.manifest is not None else repo_root / MANIFEST_REL
        )
        manifest, manifest_issues = _load_manifest(manifest_path)
        if manifest is None:
            report: dict[str, Any] = {
                "task_id": args.check_completeness,
                "issues": [*event_issues, *manifest_issues],
                "events": [],
                "complete": False,
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        report, complete = check_completeness(
            events=events, manifest=manifest, task_id=args.check_completeness
        )
        if event_issues:
            report["issues"] = [*event_issues, *report["issues"]]
            complete = False
            report["complete"] = False
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if complete else 1

    unit_values = (args.unit_terminal, args.unit_s1, args.unit_r, args.unit_s2)
    if any(value is not None for value in unit_values) and not all(
        value is not None for value in unit_values
    ):
        print(
            "--unit-terminal／--unit-s1／--unit-r／--unit-s2 は 4 つ揃えて指定する",
            file=sys.stderr,
        )
        return 2
    unit_shas = (
        (args.unit_terminal, args.unit_s1, args.unit_r, args.unit_s2)
        if all(value is not None for value in unit_values)
        else None
    )

    registration_values = (
        args.registration_base_manifest,
        args.registration_head_manifest,
        args.registration_task_id,
        args.registration_merged_pr,
    )
    if any(value is not None for value in registration_values) and not all(
        value is not None for value in registration_values
    ):
        print(
            "--registration-base-manifest／--registration-head-manifest／"
            "--registration-task-id／--registration-merged-pr は揃えて指定する",
            file=sys.stderr,
        )
        return 2
    registration = (
        RegistrationCheckInput(
            base_manifest=args.registration_base_manifest,
            head_manifest=args.registration_head_manifest,
            task_id=args.registration_task_id,
            merged_pr=args.registration_merged_pr,
            closeout_sequence=args.registration_closeout_sequence,
            closeout_role=args.registration_closeout_role,
        )
        if all(value is not None for value in registration_values)
        else None
    )

    issues = validate(
        repo_root=repo_root,
        base=args.base,
        head=args.head,
        mainline_ref=args.mainline_ref,
        require_anchor=args.require_anchor,
        github_timeout=args.github_timeout,
        unit_shas=unit_shas,
        registration=registration,
        assert_unit=args.assert_unit,
    )
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        print(f"task closeout event NG（{len(issues)} 件）", file=sys.stderr)
        return 1
    print("task closeout event OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
