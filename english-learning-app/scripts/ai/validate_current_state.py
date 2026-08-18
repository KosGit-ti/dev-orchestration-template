#!/usr/bin/env python3
"""current state SSOT（`docs/ai/task-checkpoint.md`）の鮮度を機械検証する CLI。

N-598 AC-02（`docs/specs/N-598-current-state-ssot-freshness.md` §6.2）の成果物。
N-598B（`docs/specs/N-598B-semantic-freshness-validator.md`）で意味的検証へ拡張した
（2026-07-29 再監査 P0-1: 従来の bounded-staleness は merge の中身を見ず件数のみを
数える構造的弱点を持っていた）。標準ライブラリと既存 git subprocess 呼び出しのみで
構成し、新規外部依存を持たない。

検証内容（いずれか違反で exit 1・fail-close）:

  (a) checkpoint の merge SHA が main の first-parent 履歴に存在すること
      （通常の ancestor 検査だけでは second-parent 側の feature commit を許すため、
      mainline へ実際に統合された終端 SHA だけを受理する）
  (b) checkpoint の「- PR: <N>」と「- merge SHA: <sha>」が対応していること
      （通常 merge は canonical 件名と 2 親以上、rebase merge は厳密な終端
      anchor `[PR #N; commits=M]` と 1 親を要求する。checkpoint が別 PR や
      feature branch の SHA を記録した場合を検出する）
  (c) checkpoint 以降に mainline へ積まれた first-parent 全 commit を、通常
      merge または終端 anchor `[PR #N; commits=M]` 付き rebase PR の unit に束ねる。
      diff パスが全て state-neutral パターン（自動データ取込・レビュー証跡等）に
      収まる neutral と、それ以外の state-changing に分類する。anchor のない
      1-parent commit は neutral だけを許可し、state-changing は fail-close する。
      terminal state-sync は可変長 sync series + evidence-only anchor の専用 rebase
      unit とし、marker、親、checkpoint の PR/SHA、plan の意味的な Next→Done 遷移、
      ledger append-only、許可 path を全て検証できた場合だけ表現済みとみなす。
      - ``--mode transition``（既定）: 未表現の state-changing merge が
        1 件でもあれば fail する（閾値なし）。state-neutral merge には従来の
        閾値（既定 3・``--staleness-threshold`` で上書き可）を適用する。
      - ``--mode report``: state-changing merge の件数のみを閾値と比較する
        従来の bounded-staleness 相当（state-neutral merge は件数から除外）。
  (d) checkpoint の「## 次タスク」節から抽出したタスク ID らしきトークン
      （``N-598B`` ``R-023`` ``G3`` 等）のうち少なくとも 1 つが、
      ``docs/plan.md`` の「## Next」節から抽出した完全なタスク ID と一致すること。
      terminal sentinel の場合は、full-plan 完了判定と同じ意味で Next と自動実行
      Backlog が空であること（次タスクと計画キューの乖離を検出する）。
  (e) ``docs/ai/execution-ledger.md`` 冒頭に「## 現在状態（...・最新）」を
      名乗る見出しが存在しないこと（current state は checkpoint に一本化済み
      のため、「最新」ラベルは execution-ledger 側に残ってはならない）

checkpoint から必要な情報を抽出できない場合や git コマンドが失敗する場合
（shallow clone で対象 SHA が不明等）も「検証不能」を pass として扱わず
fail-close する（P-010）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# N-598C AC-10: control unit（R-025 の freeze／reservation／single-look）closeout の
# schema・catalog 正本。`scripts.ai` パッケージ経由の import は resolve_current_state.py
# と同じ ROOT 挿入パターンを使う（直接 CLI 実行・importlib 単独 load の両方で解決できる
# ようにするため）。
from scripts.ai import control_unit_event  # noqa: E402

CHECKPOINT_REL = "docs/ai/task-checkpoint.md"
LEDGER_REL = "docs/ai/execution-ledger.md"
PLAN_REL = "docs/plan.md"
DEFAULT_STALENESS_THRESHOLD = 3
DEFAULT_MODE = "transition"
MODE_CHOICES = ("transition", "report")

# N-598B: checkpoint 以降の merge diff がこれらのパス接頭辞に完全に収まる場合に限り
# state-neutral（自動データ取込・PR レビュー証跡の記録等）として扱う。実際の
# auto-ingest / daily report / レビュー証跡 PR のパスを `git log` で実測して確定した
# （直下の `docs/ai/reviews/*.json` は独立監査コメントの記録先、
# `docs/ai/pre-pr-reviews/**` は
# PR 前批判レビューの記録先、`data/**` は auto-ingest データ本体と日次レポート
# `data/reports/**` を含む）。`docs/reports/**` は現時点で実体パスが存在しないが、
# 将来レポート出力先が docs 配下へ移動した場合に備えて neutral 側へ含めておく
# （fail-close の方向を弱めない・存在しないパスを含めても false negative は増えない）。
NEUTRAL_PATH_PREFIXES = (
    "data/",
    "docs/reports/",
    "docs/ai/pre-pr-reviews/",
)
REVIEW_REPORT_DIR = "docs/ai/reviews/"

MERGE_SHA_RE = re.compile(r"^- merge SHA: ([0-9a-fA-F]{40})\s*$", re.MULTILINE)
# 独立監査 Should-3 是正: 全角/半角括弧の表記ゆれと行末の空白（改行以外）に頑健化する
# （false negative を塞ぐ方向のみ・"." は改行にマッチしないため貪欲マッチによる
# 複数行への over-match は発生しない）。
STALE_HEADING_RE = re.compile(r"^## 現在状態[（(].*・最新[）)][ \t]*$", re.MULTILINE)

# N-598B (a): checkpoint の「- PR: <N>」行。
PR_NUMBER_RE = re.compile(r"^- PR: (\d+)[ \t]*$", re.MULTILINE)

# N-598B (d): 「## 次タスク」「## Next」節の抽出と、節内のタスク ID らしきトークン。
NEXT_TASK_HEADING_RE = re.compile(r"^## 次タスク[ \t]*$", re.MULTILINE)
PLAN_NEXT_HEADING_RE = re.compile(r"^## Next\b.*$", re.MULTILINE)
# 汎用の「## 」見出し（節の終端検出に使う）。
SECTION_HEADING_RE = re.compile(r"^## .*$", re.MULTILINE)
# N-\d+［英大文字1桁の suffix 可］・R-\d+・G\d+ 等（例: N-598B・R-023・G3）。
# 2 文字以上のプレフィックス（PR-1120・FR-172・NFR-070・AC-01・DEC-...）は
# 直前の文字が単語構成文字となり \b が成立しないため誤検出しない。
TASK_TOKEN_RE = re.compile(r"\b(?:[A-Z]-\d+[A-Z]?|G\d+)\b")
CHECKPOINT_TASK_RE = re.compile(r"^- タスク: (.+?)[ \t]*$", re.MULTILINE)

TERMINAL_NEXT_TASK_SENTINEL = "[TERMINAL] 自動実行対象なし"
TERMINAL_SENTINEL_LOOKING_RE = re.compile(r"\[[ \t]*TERMINAL\b", re.IGNORECASE)
PLAN_TERMINAL_NONE_LINE = "- 現在なし"
PLAN_BACKLOG_HEADING_RE = re.compile(r"^## Backlog[ \t]*$", re.MULTILINE)
PLAN_DONE_HEADING_RE = re.compile(r"^## Done\b.*$", re.MULTILINE)
# actionable task は ``### N-*`` 等の見出しだけで識別する。``### 完了タスク`` 以降の
# ``- **N-* ** ✅`` は archive の箇条書きであり、active block ではない。
PLAN_TASK_ENTRY_RE = re.compile(
    r"^###[ \t]+((?:[NBR]-\d+[A-Z]?|G\d+))\b.*$",
    re.MULTILINE,
)
PLAN_COMPLETED_ARCHIVE_HEADING_RE = re.compile(
    r"^###[ \t]+完了タスク(?:[ \t（(].*)?$",
    re.MULTILINE,
)
COMPLETED_MARKER_RE = re.compile(r"(?<!未)完了")

# 通常 merge は GitHub の canonical 件名、rebase merge は終端 commit の厳密な
# suffix anchor で PR 境界を識別する。rebase 側へ commit 数を含めることで、
# 直前の state-neutral な 1-parent commit を別 PR から誤って束ねない。
NORMAL_MERGE_PR_RE = re.compile(r"^Merge pull request #([1-9][0-9]*)(?:\s|$)")
REBASE_PR_ANCHOR_RE = re.compile(r"\[PR #([1-9][0-9]*); commits=([1-9][0-9]*)\]")
REBASE_PR_ANCHOR_MARKER_RE = re.compile(r"\[[Pp][Rr][ \t]*#")

# terminal state-sync は通常の neutral path を広げず、専用 unit の検証成功時だけ
# neutral 扱いする。marker は最初の（非 anchor）commit 件名末尾に 1 件だけ置く。
STATE_SYNC_MARKER_RE = re.compile(r"\[STATE-SYNC previous_pr=([1-9][0-9]*)\]")
STATE_SYNC_MARKER_LOOKING_RE = re.compile(r"\[[ \t]*STATE-SYNC\b", re.IGNORECASE)
STATE_SYNC_SYNC_PATHS = frozenset((CHECKPOINT_REL, LEDGER_REL, PLAN_REL))
STATE_SYNC_PRE_PR_PATH_RE = re.compile(r"^docs/ai/pre-pr-reviews/.+\.md$")
STATE_SYNC_ANCHOR_PATH_RE = re.compile(r"^docs/ai/reviews/[^/]+\.json$")
TERMINAL_SIGNAL_NONE = "none"
TERMINAL_SIGNAL_VALID = "valid"
TERMINAL_SIGNAL_INVALID = "invalid"

# ---------------------------------------------------------------------------
# task closeout sequencing（N-598C AC-06: T→S1→R→S2 の汎化 resolver）
#
# N-602A 限定 bootstrap（`BOOTSTRAP_TASK_ID="N-602A"`／`BOOTSTRAP_RESUME_TASK_ID="N-598C"`
# の固定判定）は N-598C 受入により削除した（N-602 spec Migration Phase A 項目 7・
# design.md「N-598C は同じ fixture を共通 resolver へ移し、受入後に bootstrap 分岐を
# 削除する」）。以下は任意 task の closeout chain（`subject_kind=task_closeout`）と、
# R-025 の control unit closeout chain（`subject_kind=control_unit`・
# `scripts/ai/control_unit_event.py`）の双方を汎化して扱う。N-602A の実データ
# （closeout event `TCE-28040ba6…`・registry 4 entry）は本汎化後も同じ predicate
# （required evidence root／closure claim の manifest 登録状態）で valid であり続ける。
#
# task closeout event の schema の正本は `scripts/ai/task_closeout_event.py`、
# control unit event の schema の正本は `scripts/ai/control_unit_event.py` にある。
# 本 file は比較に必要な最小の定数・parse だけをここへ持ち、hash 再計算や event
# schema 検査は専用 validator（`scripts/ai/validate_task_closeout_events.py`）に委ねる。
# ---------------------------------------------------------------------------

# spec「Plan Active Block Schema」156 行の task ID 形式（`resolve_current_state.TASK_ID_RE`
# と同じ pattern）。closing task_id や resume.task_id の一般検証に使う（特定 task への
# 固定は行わない）。
CANONICAL_TASK_ID_RE = re.compile(r"^(?:[A-Z]+-[0-9]+[A-Z]?|R-[0-9]{3})$")
CLOSING_STATE = "closing"
CLOSING_SUBSTEP = "post-merge-evidence-registration"
SUBJECT_KIND_TASK_CLOSEOUT = "task_closeout"
SUBJECT_KIND_CONTROL_UNIT = "control_unit"
SUBJECT_KINDS = (SUBJECT_KIND_TASK_CLOSEOUT, SUBJECT_KIND_CONTROL_UNIT)
# control unit（R-025）の closing は spec「closeout_registration」258 行のとおり
# task_id=R-025／substep=control-evidence-registration へ固定する（他 task を汎化する
# 対象ではなく、R-025 の control unit 機構自体が spec 上そう定義されているため）。
CONTROL_UNIT_TASK_ID = control_unit_event.CONTROL_UNIT_TASK_ID
CONTROL_CLOSING_SUBSTEP = control_unit_event.CONTROL_CLOSING_SUBSTEP
# N-598C `closeout_action.kind` のうち本 resolver が扱う 4 値
# （`program_closeout` は N-604／N-597 の担当）。
CLOSEOUT_ACTIONS = (
    "evidence_registration",
    "correction_staging",
    "correction_registration",
    "advance_state",
)
# repair-hold の phase→action 写像は `resolve_repair_hold_action` が担う（PR-E3c。旧
# `CORRECTION_PHASE_TO_ACTION` の revalidating→advance_state 固定写像は「全 affected task が
# 回復完了したときだけ advance_state」へ置換した）。
EVENT_DIR_REL = "docs/ai/task-closeout-events"
CONTROL_EVENT_DIR_REL = control_unit_event.CONTROL_EVENT_DIR_REL
FLAG_REL = ".github/full-plan-execution.flag"
MANIFEST_REL = "docs/audits/audit-materialization-manifest-2026-08-12.yml"
CLOSEOUT_REGISTRATION_FIELDS = (
    "kind",
    "subject_kind",
    "event_id",
    "event_path",
    "event_sha256",
    "required_evidence_roots",
    "required_evidence_roots_sha256",
    "required_closure_claim_ids",
    "required_closure_claim_ids_sha256",
    "resume",
)
# checkpoint／flag が mirror する field（2 集合の hash は 3 情報源すべてで必須。
# 集合本体は plan と event が正本であり、mirror 側に置く場合は exact 一致を要求する）。
MIRROR_REQUIRED_FIELDS = (
    "event_id",
    "event_path",
    "event_sha256",
    "required_evidence_roots_sha256",
    "required_closure_claim_ids_sha256",
)
MIRROR_OPTIONAL_SET_FIELDS = (
    "required_evidence_roots",
    "required_closure_claim_ids",
)


@dataclass(frozen=True)
class MainlineUnit:
    """mainline へ 1 PR 相当で取り込まれた commit 群。"""

    kind: str
    commits: tuple[str, ...]
    terminal_sha: str
    pr_number: str | None = None


@dataclass
class PlanTaskBlocks:
    """plan 節の actionable block と、機械的に保全する archive suffix。"""

    preamble: str
    blocks: dict[str, list[str]]
    archive_suffix: str


def extract_checkpoint_sha(checkpoint_text: str) -> str | None:
    """checkpoint 本文から `- merge SHA: <40hex>` 行を抽出する。

    見つからない・書式不正の場合は None を返す（呼び出し側で fail-close する）。
    """
    match = MERGE_SHA_RE.search(checkpoint_text)
    return match.group(1) if match else None


def extract_checkpoint_pr_number(checkpoint_text: str) -> str | None:
    """checkpoint 本文から `- PR: <数字>` 行を抽出する。

    見つからない場合は None を返す（呼び出し側で fail-close する）。
    """
    match = PR_NUMBER_RE.search(checkpoint_text)
    return match.group(1) if match else None


def find_stale_headings(ledger_text: str) -> list[str]:
    """execution-ledger 本文から「## 現在状態（...・最新）」見出しを列挙する。"""
    return [m.group(0) for m in STALE_HEADING_RE.finditer(ledger_text)]


def extract_section_after_heading(text: str, heading_re: re.Pattern[str]) -> str | None:
    """heading_re にマッチする最初の見出し行の直後から、次の `## ` 見出し（または EOF）までを返す。

    見出し自体が見つからない場合は None を返す。
    """
    match = heading_re.search(text)
    if match is None:
        return None
    start = match.end()
    next_heading = SECTION_HEADING_RE.search(text, start)
    end = next_heading.start() if next_heading else len(text)
    return text[start:end]


def extract_task_tokens(text: str) -> list[str]:
    """タスク ID らしきトークン（``N-598B`` ``R-023`` ``G3`` 等）を本文から抽出する。"""
    return TASK_TOKEN_RE.findall(text)


def _has_active_auto_backlog_task(backlog_section: str) -> bool:
    """full_plan_completion と同じ契約で自動実行 Backlog の残存を判定する。

    自動実行対象は ``- B-数字`` のみとし、打ち消し、完了、Merged、
    integrated、または N-* 昇格行は完了済みとして除外する。
    """
    for raw_line in backlog_section.splitlines():
        line = raw_line.strip()
        if not re.match(r"^- \*{0,2}B-\d+", line):
            continue
        if (
            "~~" in line
            or "✅" in line
            or COMPLETED_MARKER_RE.search(line)
            or "Merged" in line
            or "integrated" in line
        ):
            continue
        if "N-" in line and "昇格" in line:
            continue
        return True
    return False


def _terminal_checkpoint_section_is_exact(next_task_section: str) -> bool:
    """次タスク節が terminal sentinel 1 行だけかを判定する。"""
    lines = [line.strip() for line in next_task_section.splitlines() if line.strip()]
    return lines == [f"- {TERMINAL_NEXT_TASK_SENTINEL}"]


def check_terminal_plan_queue(plan_text: str) -> str | None:
    """plan の Next と自動実行 Backlog が terminal 状態かを検証する。

    full_plan_completion と同じ ``現在なし``／``- B-数字`` 契約を使い、
    Next にタスク項目が併記された偽の「現在なし」は追加で拒否する。
    """
    plan_next_section = extract_section_after_heading(plan_text, PLAN_NEXT_HEADING_RE)
    if plan_next_section is None:
        return f"{PLAN_REL} に「## Next」見出しが見つかりません"
    plan_next_parts = _plan_task_blocks(plan_next_section)
    none_lines = [
        line.strip() for line in plan_next_parts.preamble.splitlines() if "現在なし" in line
    ]
    if none_lines != [PLAN_TERMINAL_NONE_LINE]:
        return (
            f"{PLAN_REL} の「## Next」節は canonical な"
            f" `{PLAN_TERMINAL_NONE_LINE}` を厳密に 1 行だけ持つ必要があります"
        )
    if plan_next_parts.blocks:
        return f"{PLAN_REL} の「## Next」節に terminal 状態と両立しないタスク項目が残っています"

    backlog_section = extract_section_after_heading(plan_text, PLAN_BACKLOG_HEADING_RE)
    if backlog_section is None:
        return f"{PLAN_REL} に「## Backlog」見出しが見つかりません"
    if _has_active_auto_backlog_task(backlog_section):
        return f"{PLAN_REL} の自動実行対象 Backlog に未完了タスクが残っています"
    return None


def _plan_task_blocks(section: str) -> PlanTaskBlocks:
    """plan 節を前置き、actionable block、完了 archive suffix に分割する。"""
    archive_match = PLAN_COMPLETED_ARCHIVE_HEADING_RE.search(section)
    actionable_end = archive_match.start() if archive_match is not None else len(section)
    actionable_text = section[:actionable_end]
    archive_suffix = section[actionable_end:]

    matches = list(PLAN_TASK_ENTRY_RE.finditer(actionable_text))
    if not matches:
        return PlanTaskBlocks(
            preamble=actionable_text,
            blocks={},
            archive_suffix=archive_suffix,
        )

    blocks: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(actionable_text)
        blocks.setdefault(match.group(1), []).append(actionable_text[match.start() : end])
    return PlanTaskBlocks(
        preamble=actionable_text[: matches[0].start()],
        blocks=blocks,
        archive_suffix=archive_suffix,
    )


def _resolve_terminal_queue_task(
    checkpoint_task: str,
    base_next_blocks: dict[str, list[str]],
) -> tuple[str | None, str | None]:
    """checkpoint task と一致する actionable 見出し、または一意な umbrella 親を返す。"""
    direct_blocks = base_next_blocks.get(checkpoint_task, [])
    if direct_blocks:
        if len(direct_blocks) != 1:
            return None, (
                f"{PLAN_REL} の base Next に checkpoint task {checkpoint_task} の"
                f" actionable block が {len(direct_blocks)} 件あります"
            )
        return checkpoint_task, None

    parent_candidates: list[str] = []
    for parent_task, blocks in base_next_blocks.items():
        for block in blocks:
            _heading, separator, body = block.partition("\n")
            block_body = body if separator else ""
            if checkpoint_task in set(extract_task_tokens(block_body)):
                parent_candidates.append(parent_task)

    if len(parent_candidates) != 1:
        return None, (
            f"{PLAN_REL} の base Next で checkpoint subtask {checkpoint_task} を"
            "本文に完全 token として含む actionable 親 block は 1 件だけ必要です"
            f"（検出 {len(parent_candidates)} 件）"
        )
    return parent_candidates[0], None


def _strip_terminal_none_lines(text: str) -> str:
    """Next 前置きから terminal の「現在なし」行だけを除き比較可能にする。"""
    return "\n".join(
        line for line in text.splitlines() if line.strip() != PLAN_TERMINAL_NONE_LINE
    ).strip()


def _normalize_moved_task_block(text: str) -> str:
    """Next→Done 移動時に許容する Markdown 完了装飾だけを除く。"""
    normalized = text.replace("**", "").replace("~~", "").replace("✅", "")
    return "\n".join(line.rstrip() for line in normalized.splitlines()).strip()


def _mask_plan_queue_sections(plan_text: str) -> tuple[str | None, str | None]:
    """Next／Backlog／Done の本文を mask し、それ以外の byte-level 変更を検出する。"""
    masked = plan_text
    for name, heading_re in (
        ("NEXT", PLAN_NEXT_HEADING_RE),
        ("BACKLOG", PLAN_BACKLOG_HEADING_RE),
        ("DONE", PLAN_DONE_HEADING_RE),
    ):
        match = heading_re.search(masked)
        if match is None:
            return None, f"{PLAN_REL} に対象見出し（{name}）が見つかりません"
        next_heading = SECTION_HEADING_RE.search(masked, match.end())
        end = next_heading.start() if next_heading else len(masked)
        masked = masked[: match.end()] + f"\n\n<{name}_SECTION>\n" + masked[end:]
    return masked, None


def check_terminal_plan_transition(
    base_plan_text: str,
    result_plan_text: str,
    *,
    checkpoint_text: str,
) -> str | None:
    """terminal state-sync の plan 差分を Next→Done だけに制限する。

    checkpoint の完了タスク 1 件を base Next から除き result Done へ 1 件追加する。
    terminal では Backlog→Next 昇格は存在しないため Backlog は byte 同一、result
    Next は空でなければならない。Next／Backlog／Done 以外の変更も拒否する。
    """
    queue_issue = check_terminal_plan_queue(result_plan_text)
    if queue_issue is not None:
        return queue_issue

    task_match = CHECKPOINT_TASK_RE.search(checkpoint_text)
    if task_match is None:
        return f"{CHECKPOINT_REL} から完了タスクを抽出できません"
    checkpoint_tokens = extract_task_tokens(task_match.group(1))
    if not checkpoint_tokens:
        return f"{CHECKPOINT_REL} の完了タスク行からタスク ID を抽出できません"
    checkpoint_task = checkpoint_tokens[0]
    checkpoint_pr = extract_checkpoint_pr_number(checkpoint_text)
    checkpoint_sha = extract_checkpoint_sha(checkpoint_text)
    if checkpoint_pr is None or checkpoint_sha is None:
        return f"{CHECKPOINT_REL} の PR／merge SHA を plan 完了注記と照合できません"

    base_next = extract_section_after_heading(base_plan_text, PLAN_NEXT_HEADING_RE)
    result_next = extract_section_after_heading(result_plan_text, PLAN_NEXT_HEADING_RE)
    base_backlog = extract_section_after_heading(base_plan_text, PLAN_BACKLOG_HEADING_RE)
    result_backlog = extract_section_after_heading(result_plan_text, PLAN_BACKLOG_HEADING_RE)
    base_done = extract_section_after_heading(base_plan_text, PLAN_DONE_HEADING_RE)
    result_done = extract_section_after_heading(result_plan_text, PLAN_DONE_HEADING_RE)
    if any(
        section is None
        for section in (
            base_next,
            result_next,
            base_backlog,
            result_backlog,
            base_done,
            result_done,
        )
    ):
        return f"{PLAN_REL} の Next／Backlog／Done 節を比較できません"
    assert base_next is not None
    assert result_next is not None
    assert base_backlog is not None
    assert result_backlog is not None
    assert base_done is not None
    assert result_done is not None

    base_next_parts = _plan_task_blocks(base_next)
    result_next_parts = _plan_task_blocks(result_next)
    base_done_parts = _plan_task_blocks(base_done)
    result_done_parts = _plan_task_blocks(result_done)

    queue_task, queue_task_issue = _resolve_terminal_queue_task(
        checkpoint_task,
        base_next_parts.blocks,
    )
    if queue_task_issue is not None:
        return queue_task_issue
    assert queue_task is not None

    completed_next_blocks = base_next_parts.blocks.pop(queue_task, [])
    if len(completed_next_blocks) != 1:
        return f"{PLAN_REL} の base Next に移動対象 {queue_task} が 1 件だけ存在する必要があります"
    if base_next_parts.blocks or result_next_parts.blocks:
        return (
            f"{PLAN_REL} の terminal 遷移で {queue_task} 以外の"
            " Next タスクを変更または残存させています"
        )
    if _strip_terminal_none_lines(base_next_parts.preamble) != _strip_terminal_none_lines(
        result_next_parts.preamble
    ):
        return f"{PLAN_REL} の Next 前置きに terminal sentinel 以外の変更があります"
    if base_next_parts.archive_suffix != result_next_parts.archive_suffix:
        return f"{PLAN_REL} の Next 完了タスク archive が変更されています"
    if base_backlog != result_backlog:
        return f"{PLAN_REL} の terminal 遷移で Backlog が変更されています"
    if base_done_parts.preamble != result_done_parts.preamble:
        return f"{PLAN_REL} の Done 前置きが変更されています"
    if base_done_parts.archive_suffix != result_done_parts.archive_suffix:
        return f"{PLAN_REL} の Done archive が変更されています"

    base_completed_done = base_done_parts.blocks.pop(queue_task, [])
    result_completed_done = result_done_parts.blocks.pop(queue_task, [])
    if base_done_parts.blocks != result_done_parts.blocks:
        return f"{PLAN_REL} の Done で完了タスク以外の項目が変更されています"
    if len(result_completed_done) != len(base_completed_done) + 1:
        return f"{PLAN_REL} の Done に移動対象 {queue_task} が 1 件だけ追加されていません"
    if result_completed_done[: len(base_completed_done)] != base_completed_done:
        return f"{PLAN_REL} の Done にある既存 {queue_task} 項目が変更されています"

    moved_from_next = _normalize_moved_task_block(completed_next_blocks[0])
    moved_to_done = _normalize_moved_task_block(result_completed_done[-1])
    expected_completion_note = f"- 完了: PR #{checkpoint_pr} / merge SHA: {checkpoint_sha}"
    moved_to_done_lines = moved_to_done.splitlines()
    if not moved_to_done_lines or moved_to_done_lines[-1] != expected_completion_note:
        return (
            f"{PLAN_REL} の Done へ追加した {queue_task} は末尾に"
            f" `{expected_completion_note}` だけを完了注記として追加する必要があります"
        )
    moved_to_done_without_note = "\n".join(moved_to_done_lines[:-1]).strip()
    if moved_from_next != moved_to_done_without_note:
        return (
            f"{PLAN_REL} の Done へ追加した {queue_task} の本文が"
            " base Next と一致しません（完了装飾と規定の完了注記以外は変更不可）"
        )

    base_masked, base_mask_issue = _mask_plan_queue_sections(base_plan_text)
    result_masked, result_mask_issue = _mask_plan_queue_sections(result_plan_text)
    if base_mask_issue is not None or result_mask_issue is not None:
        return base_mask_issue or result_mask_issue
    if base_masked != result_masked:
        return f"{PLAN_REL} の Next／Backlog／Done 以外が変更されています"
    return None


def is_neutral_path(path: str) -> bool:
    """パスが state-neutral（自動データ取込・レビュー証跡等）かどうかを判定する。"""
    normalized = path.replace("\\", "/")
    if normalized.startswith(NEUTRAL_PATH_PREFIXES):
        return True
    if not normalized.startswith(REVIEW_REPORT_DIR):
        return False
    relative = normalized.removeprefix(REVIEW_REPORT_DIR)
    return bool(relative) and "/" not in relative and relative.endswith(".json")


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """git サブコマンドを実行する（呼び出し側で returncode を判定する前提・raise しない）。"""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _read_git_blob(
    revision: str,
    path: str,
    *,
    repo_root: Path,
) -> tuple[bytes | None, str | None]:
    """指定 revision の blob を byte 列で読み、append-only 比較に使う。"""
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        return None, f"git show に失敗しました（{revision}:{path}）: {stderr}"
    return result.stdout, None


def _decode_utf8_blob(blob: bytes, *, revision: str, path: str) -> tuple[str | None, str | None]:
    """git blob を UTF-8 として厳密に decode する。"""
    try:
        return blob.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return None, f"{revision}:{path} を UTF-8 として読めません: {exc}"


def check_ancestor(sha: str, *, repo_root: Path, target_ref: str = "HEAD") -> str | None:
    """checkpoint の merge SHA が target_ref の祖先であることを確認する。

    問題なければ None、違反または git コマンド失敗時は違反理由の文字列を返す。
    """
    result = _run_git(["merge-base", "--is-ancestor", sha, target_ref], cwd=repo_root)
    if result.returncode == 0:
        return None
    if result.returncode == 1:
        return (
            f"checkpoint の merge SHA {sha} は mainline ref {target_ref} の祖先ではありません"
            "（別ブランチ由来の疑い）"
        )
    stderr = result.stderr.strip() or f"exit code {result.returncode}"
    return f"git merge-base --is-ancestor の実行に失敗しました（{sha} -> {target_ref}）: {stderr}"


def count_merges_since(sha: str, *, repo_root: Path) -> tuple[int | None, str | None]:
    """checkpoint 以降に HEAD へ積まれた merge commit 数を数える（単純カウント。分類なし）。

    (count, issue) を返す。git コマンド失敗・出力解析不能時は count=None かつ
    issue に理由を入れる。N-598B 以降 ``validate()`` からは直接使わないが、単純な
    merge 総数のみが必要な用途向けに公開 API として残す。
    """
    result = _run_git(["rev-list", "--count", f"{sha}..HEAD", "--merges"], cwd=repo_root)
    if result.returncode != 0:
        stderr = result.stderr.strip() or f"exit code {result.returncode}"
        return None, f"git rev-list --count の実行に失敗しました（{sha}..HEAD）: {stderr}"
    raw = result.stdout.strip()
    try:
        return int(raw), None
    except ValueError:
        return None, f"git rev-list --count の出力を整数として解析できません: {raw!r}"


def check_pr_matches_merge_subject(pr_number: str, sha: str, *, repo_root: Path) -> str | None:
    """checkpoint の PR 番号と merge SHA のコミット件名が対応することを検証する（N-598B (a)）。

    通常 merge は GitHub canonical 件名 `Merge pull request #N` と 2 親以上、
    rebase merge は 1 親かつ終端 suffix `[PR #N; commits=M]` 1 件だけを許可する。
    """
    result = _run_git(["log", "-1", "--format=%P%x00%s", sha], cwd=repo_root)
    if result.returncode != 0:
        stderr = result.stderr.strip() or f"exit code {result.returncode}"
        return f"git log -1 の実行に失敗しました（{sha}）: {stderr}"
    raw = result.stdout.rstrip("\n")
    parents_raw, separator, subject = raw.partition("\x00")
    if not separator:
        return f"merge SHA {sha} の commit metadata を解析できませんでした"
    if not subject:
        return f"merge SHA {sha} のコミット件名を取得できませんでした（空）"
    parent_count = len(parents_raw.split())

    normal_match = NORMAL_MERGE_PR_RE.match(subject)
    if normal_match is not None and normal_match.group(1) == pr_number and parent_count >= 2:
        return None

    anchor = parse_rebase_pr_anchor(subject)
    if anchor is not None:
        anchor_pr_number, commit_count = anchor
        if anchor_pr_number == pr_number and parent_count == 1:
            return check_rebase_checkpoint_unit(
                sha,
                commit_count=commit_count,
                repo_root=repo_root,
            )

    return (
        f"{CHECKPOINT_REL} の PR 番号 #{pr_number} が merge SHA {sha} の件名／親数"
        f"（「{subject}」、parents={parent_count}）と対応しません"
        "（通常 merge の canonical 件名、または rebase 終端の"
        " `[PR #N; commits=M]` 1 件が必要です。PR 番号と merge SHA の対応が"
        "取れていない疑いがあります）"
    )


def list_merge_commits_since(sha: str, *, repo_root: Path) -> tuple[list[str] | None, str | None]:
    """checkpoint 以降に HEAD へ積まれた first-parent merge commit の SHA 一覧を古い順で返す。

    (shas, issue) を返す。git コマンド失敗時は shas=None かつ issue に理由を入れる。
    """
    result = _run_git(
        ["rev-list", "--first-parent", "--merges", "--reverse", f"{sha}..HEAD"],
        cwd=repo_root,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or f"exit code {result.returncode}"
        return (
            None,
            f"git rev-list --first-parent --merges の実行に失敗しました（{sha}..HEAD）: {stderr}",
        )
    raw = result.stdout.strip()
    if not raw:
        return [], None
    return raw.splitlines(), None


def resolve_mainline_ref(*, repo_root: Path) -> tuple[str | None, str | None]:
    """検査対象の mainline ref を決める。

    local main 上ではその branch を使う。feature branch や pull_request の合成 HEAD
    では remote-tracking main を使う。origin 自体がない fixture／offline repo に限り
    local main へ退避し、origin があるのに tracking ref がなければ fetch 漏れとして
    fail-close する。
    """
    branch_result = _run_git(["branch", "--show-current"], cwd=repo_root)
    if branch_result.returncode != 0:
        stderr = branch_result.stderr.strip() or f"exit code {branch_result.returncode}"
        return None, f"現在 branch の解決に失敗しました: {stderr}"
    current_branch = branch_result.stdout.strip()

    candidates: tuple[str, ...]
    if current_branch == "main":
        candidates = ("refs/heads/main", "refs/remotes/origin/main")
    else:
        origin_main = "refs/remotes/origin/main"
        result = _run_git(["rev-parse", "--verify", "--quiet", origin_main], cwd=repo_root)
        if result.returncode == 0 and result.stdout.strip():
            return origin_main, None
        if result.returncode not in (0, 1):
            stderr = result.stderr.strip() or f"exit code {result.returncode}"
            return None, f"mainline ref {origin_main} の解決に失敗しました: {stderr}"

        origin_result = _run_git(["remote", "get-url", "origin"], cwd=repo_root)
        if origin_result.returncode == 0:
            return (
                None,
                "origin remote は存在しますが refs/remotes/origin/main がありません"
                "（fetch 後に再実行してください）",
            )
        if origin_result.returncode not in (2, 128):
            stderr = origin_result.stderr.strip() or f"exit code {origin_result.returncode}"
            return None, f"origin remote の確認に失敗しました: {stderr}"
        candidates = ("refs/heads/main",)

    for ref in candidates:
        result = _run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_root)
        if result.returncode == 0 and result.stdout.strip():
            return ref, None
        if result.returncode not in (0, 1):
            stderr = result.stderr.strip() or f"exit code {result.returncode}"
            return None, f"mainline ref {ref} の解決に失敗しました: {stderr}"
    return None, "mainline ref（origin/main または main）を解決できませんでした"


def list_mainline_commits_since(
    sha: str, *, mainline_ref: str, repo_root: Path
) -> tuple[list[str] | None, str | None]:
    """checkpoint 以降の first-parent 全 commit を古い順に返す。"""
    result = _run_git(
        ["rev-list", "--first-parent", "--reverse", f"{sha}..{mainline_ref}"],
        cwd=repo_root,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or f"exit code {result.returncode}"
        return (
            None,
            f"git rev-list --first-parent の実行に失敗しました（{sha}..{mainline_ref}）: {stderr}",
        )
    raw = result.stdout.strip()
    return (raw.splitlines() if raw else []), None


def get_commit_metadata(
    sha: str, *, repo_root: Path
) -> tuple[tuple[str, tuple[str, ...]] | None, str | None]:
    """commit の subject と親 SHA を返す。"""
    result = _run_git(["log", "-1", "--format=%P%x00%s", sha], cwd=repo_root)
    if result.returncode != 0:
        stderr = result.stderr.strip() or f"exit code {result.returncode}"
        return None, f"git log -1 に失敗しました（{sha}）: {stderr}"
    raw = result.stdout.rstrip("\n")
    parents_raw, separator, subject = raw.partition("\x00")
    if not separator or not subject:
        return None, f"commit metadata を解析できませんでした（{sha}）"
    return (subject, tuple(parents_raw.split())), None


def parse_rebase_pr_anchor(subject: str) -> tuple[str, int] | None:
    """厳密な rebase 終端 suffix anchor を解析する。

    anchor は件名末尾に 1 件だけ存在し、PR 番号と commit 数は先頭 0 のない正整数とする。
    """
    anchors = list(REBASE_PR_ANCHOR_RE.finditer(subject))
    markers = list(REBASE_PR_ANCHOR_MARKER_RE.finditer(subject))
    if len(anchors) != 1 or len(markers) != 1 or anchors[0].end() != len(subject):
        return None
    return anchors[0].group(1), int(anchors[0].group(2))


def check_first_parent_mainline(sha: str, *, repo_root: Path, mainline_ref: str) -> str | None:
    """checkpoint SHA が mainline の first-parent 履歴へ実際に属することを確認する。"""
    result = _run_git(["rev-list", "--first-parent", mainline_ref], cwd=repo_root)
    if result.returncode != 0:
        stderr = result.stderr.strip() or f"exit code {result.returncode}"
        return f"git rev-list --first-parent の実行に失敗しました（{mainline_ref}）: {stderr}"
    if sha.lower() not in (line.lower() for line in result.stdout.splitlines()):
        return (
            f"checkpoint の merge SHA {sha} は mainline ref {mainline_ref} の"
            " first-parent 履歴に存在しません"
            "（feature branch／second-parent 側の SHA を記録した疑いがあります）"
        )
    return None


def check_rebase_checkpoint_unit(sha: str, *, commit_count: int, repo_root: Path) -> str | None:
    """checkpoint の rebase anchor が既存 PR 境界を跨がないことを検査する。

    GitHub の実 PR commit 数との一致は merge 前 validator が担う。ここでは mainline
    履歴上で M 件を確保でき、M 件の途中に別 merge／anchor が無いことを確認する。
    """
    result = _run_git(
        ["rev-list", "--first-parent", f"--max-count={commit_count}", sha],
        cwd=repo_root,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or f"exit code {result.returncode}"
        return f"rebase checkpoint unit の列挙に失敗しました（{sha}）: {stderr}"
    commits = result.stdout.splitlines()
    if len(commits) != commit_count:
        return (
            f"rebase checkpoint anchor の commit 数 {commit_count} に対し、mainline から"
            f" {len(commits)} 件しか取得できません"
        )
    for grouped_sha in commits[1:]:
        metadata, issue = get_commit_metadata(grouped_sha, repo_root=repo_root)
        if issue is not None:
            return issue
        assert metadata is not None
        grouped_subject, grouped_parents = metadata
        if len(grouped_parents) != 1 or REBASE_PR_ANCHOR_MARKER_RE.search(grouped_subject):
            return (
                f"rebase checkpoint anchor の commit 数 {commit_count} が別の mainline 境界"
                f"（{grouped_sha[:8]}: {grouped_subject}）を跨いでいます"
            )
    return None


def build_mainline_units(
    commits: list[str], *, repo_root: Path
) -> tuple[list[MainlineUnit] | None, list[str]]:
    """first-parent commits を merge/rebase PR 単位へ束ねる。

    anchor のない 1-parent commit は単独 unit として残す。後段で state-neutral のみ
    許可するため、自動 data/review commit は保守的に 1 件ずつ数え、state-changing
    な direct commit や anchor 漏れは mode に関係なく fail-close できる。
    """
    units: list[MainlineUnit] = []
    pending: list[str] = []
    issues: list[str] = []

    def flush_unanchored(shas: list[str]) -> None:
        for unanchored_sha in shas:
            units.append(
                MainlineUnit(
                    kind="unanchored",
                    commits=(unanchored_sha,),
                    terminal_sha=unanchored_sha,
                )
            )

    for commit_sha in commits:
        metadata, metadata_issue = get_commit_metadata(commit_sha, repo_root=repo_root)
        if metadata_issue is not None:
            issues.append(metadata_issue)
            continue
        assert metadata is not None
        subject, parents = metadata

        if len(parents) >= 2:
            merge_match = NORMAL_MERGE_PR_RE.match(subject)
            flush_unanchored(pending)
            pending = []
            units.append(
                MainlineUnit(
                    kind="merge",
                    commits=(commit_sha,),
                    terminal_sha=commit_sha,
                    pr_number=merge_match.group(1) if merge_match is not None else None,
                )
            )
            continue
        if len(parents) != 1:
            issues.append(
                f"checkpoint 後の mainline commit {commit_sha} の親数が {len(parents)} です"
                "（1-parent rebase または 2-parent 以上の merge 以外は検証不能）"
            )
            continue

        pending.append(commit_sha)
        anchor = parse_rebase_pr_anchor(subject)
        if REBASE_PR_ANCHOR_MARKER_RE.search(subject) is not None and anchor is None:
            issues.append(
                f"rebase PR anchor の書式または一意性が不正です（{commit_sha[:8]}: {subject}）"
            )
            continue
        if anchor is None:
            continue

        pr_number, commit_count = anchor
        if commit_count > len(pending):
            issues.append(
                f"rebase PR #{pr_number} の anchor commit 数 {commit_count} が"
                f" checkpoint 後の未束縛 commit 数 {len(pending)} を超えています"
            )
            pending = []
            continue
        prefix = pending[:-commit_count]
        grouped = pending[-commit_count:]
        flush_unanchored(prefix)
        units.append(
            MainlineUnit(
                kind="rebase",
                commits=tuple(grouped),
                terminal_sha=commit_sha,
                pr_number=pr_number,
            )
        )
        pending = []

    flush_unanchored(pending)
    if issues:
        return None, issues
    return units, []


def get_unit_changed_paths(
    unit: MainlineUnit, *, repo_root: Path
) -> tuple[list[str] | None, str | None]:
    """mainline unit 全体が直前状態に対して変更したパスを返す。"""
    first_sha = unit.commits[0]
    result = _run_git(
        ["diff", "--name-only", f"{first_sha}^1", unit.terminal_sha],
        cwd=repo_root,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or f"exit code {result.returncode}"
        return None, (
            "git diff --name-only の実行に失敗しました"
            f"（{first_sha}^1..{unit.terminal_sha}）: {stderr}"
        )
    raw = result.stdout.strip()
    return (raw.splitlines() if raw else []), None


def get_commit_changed_paths(
    commit_sha: str,
    *,
    repo_root: Path,
) -> tuple[list[str] | None, str | None]:
    """1-parent commit が第一親に対して変更したパスを返す。"""
    result = _run_git(
        ["diff", "--name-only", f"{commit_sha}^1", commit_sha],
        cwd=repo_root,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or f"exit code {result.returncode}"
        return None, f"commit 差分の取得に失敗しました（{commit_sha}）: {stderr}"
    raw = result.stdout.strip()
    return (raw.splitlines() if raw else []), None


def parse_terminal_state_sync_marker(subject: str) -> str | None:
    """件名末尾の厳密な terminal state-sync marker から previous PR を返す。"""
    exact_markers = list(STATE_SYNC_MARKER_RE.finditer(subject))
    looking_markers = list(STATE_SYNC_MARKER_LOOKING_RE.finditer(subject))
    if (
        len(exact_markers) != 1
        or len(looking_markers) != 1
        or exact_markers[0].end() != len(subject)
    ):
        return None
    return exact_markers[0].group(1)


def is_terminal_state_sync_candidate(
    unit: MainlineUnit,
    *,
    repo_root: Path,
) -> tuple[bool, str | None]:
    """terminal signal の tri-state 判定を既存 bool API として返す。"""
    signal_state, signal_issue = classify_terminal_state_sync_signals(
        unit,
        repo_root=repo_root,
    )
    return signal_state == TERMINAL_SIGNAL_VALID, signal_issue


def classify_terminal_state_sync_signals(
    unit: MainlineUnit,
    *,
    repo_root: Path,
) -> tuple[str, str | None]:
    """terminal signal を none・valid・invalid の三値で判定する。

    checkpoint／ledger／plan path の存在だけでは terminal とみなさない。marker と
    sentinel の両方なしだけを通常 unit とし、片側だけ・書式不正・位置不正は
    report mode でも即 fail-close できる issue を返す。
    """
    if not unit.commits:
        return TERMINAL_SIGNAL_INVALID, "terminal state-sync signal の commit がありません"

    subjects: list[str] = []
    for commit_sha in unit.commits:
        metadata, metadata_issue = get_commit_metadata(commit_sha, repo_root=repo_root)
        if metadata_issue is not None:
            return TERMINAL_SIGNAL_INVALID, metadata_issue
        assert metadata is not None
        subjects.append(metadata[0])
    marker_looking_count = sum(
        len(STATE_SYNC_MARKER_LOOKING_RE.findall(subject)) for subject in subjects
    )
    marker_is_strict = (
        marker_looking_count == 1 and parse_terminal_state_sync_marker(subjects[0]) is not None
    )

    changed_paths, changed_paths_issue = get_unit_changed_paths(unit, repo_root=repo_root)
    if changed_paths_issue is not None:
        return TERMINAL_SIGNAL_INVALID, changed_paths_issue
    assert changed_paths is not None
    checkpoint_changed = CHECKPOINT_REL in changed_paths
    sentinel_looking = False
    sentinel_is_exact = False
    if checkpoint_changed:
        checkpoint_blob, checkpoint_blob_issue = _read_git_blob(
            unit.terminal_sha,
            CHECKPOINT_REL,
            repo_root=repo_root,
        )
        if checkpoint_blob_issue is not None:
            return TERMINAL_SIGNAL_INVALID, checkpoint_blob_issue
        assert checkpoint_blob is not None
        checkpoint_text, checkpoint_decode_issue = _decode_utf8_blob(
            checkpoint_blob,
            revision=unit.terminal_sha,
            path=CHECKPOINT_REL,
        )
        if checkpoint_decode_issue is None:
            assert checkpoint_text is not None
            next_task_section = extract_section_after_heading(
                checkpoint_text,
                NEXT_TASK_HEADING_RE,
            )
            if next_task_section is not None:
                sentinel_looking = (
                    TERMINAL_SENTINEL_LOOKING_RE.search(next_task_section) is not None
                )
                sentinel_is_exact = _terminal_checkpoint_section_is_exact(next_task_section)

    if marker_looking_count == 0 and not sentinel_looking:
        return TERMINAL_SIGNAL_NONE, None
    if marker_is_strict and sentinel_is_exact:
        return TERMINAL_SIGNAL_VALID, None
    return (
        TERMINAL_SIGNAL_INVALID,
        "terminal state-sync signal は最初の commit 件名末尾の厳密な"
        " `[STATE-SYNC previous_pr=N]` 1 件と、結果 checkpoint の exact terminal"
        " sentinel の両方が必要です",
    )


def _is_state_sync_candidate(
    unit: MainlineUnit,
    *,
    repo_root: Path,
) -> tuple[bool, str | None]:
    """後方互換の内部 alias。"""
    return is_terminal_state_sync_candidate(unit, repo_root=repo_root)


def check_git_regular_nonempty_file(
    revision: str,
    path: str,
    *,
    repo_root: Path,
) -> str | None:
    """revision の path が通常ファイルの非空 blob として存在することを確認する。"""
    tree_result = _run_git(["ls-tree", revision, "--", path], cwd=repo_root)
    if tree_result.returncode != 0:
        stderr = tree_result.stderr.strip() or f"exit code {tree_result.returncode}"
        return f"{revision}:{path} の git tree entry を取得できません: {stderr}"
    raw_entry = tree_result.stdout.rstrip("\n")
    if not raw_entry:
        return f"{revision}:{path} が final tree に存在しません"
    metadata, separator, actual_path = raw_entry.partition("\t")
    metadata_parts = metadata.split()
    if (
        not separator
        or actual_path != path
        or len(metadata_parts) != 3
        or metadata_parts[0] not in {"100644", "100755"}
        or metadata_parts[1] != "blob"
    ):
        return f"{revision}:{path} は通常ファイルの git blob ではありません（entry={raw_entry!r}）"
    blob, blob_issue = _read_git_blob(revision, path, repo_root=repo_root)
    if blob_issue is not None:
        return blob_issue
    assert blob is not None
    if not blob:
        return f"{revision}:{path} は空ファイルです"
    return None


def check_neutral_mainline_gap(
    checkpoint_sha: str,
    sync_parent_sha: str,
    *,
    repo_root: Path,
    staleness_threshold: int,
) -> list[str]:
    """checkpoint 終端から state-sync 親までの state-neutral unit だけを許可する。"""
    if checkpoint_sha.lower() == sync_parent_sha.lower():
        return []

    issues: list[str] = []
    ancestor_issue = check_ancestor(
        checkpoint_sha,
        repo_root=repo_root,
        target_ref=sync_parent_sha,
    )
    if ancestor_issue is not None:
        issues.append(
            "terminal state-sync の checkpoint merge SHA から sync commit の第一親へ"
            f"到達できません: {ancestor_issue}"
        )
        return issues
    first_parent_issue = check_first_parent_mainline(
        checkpoint_sha,
        repo_root=repo_root,
        mainline_ref=sync_parent_sha,
    )
    if first_parent_issue is not None:
        issues.append(
            "terminal state-sync の neutral gap が first-parent mainline 上にありません: "
            f"{first_parent_issue}"
        )
        return issues

    commits, commits_issue = list_mainline_commits_since(
        checkpoint_sha,
        mainline_ref=sync_parent_sha,
        repo_root=repo_root,
    )
    if commits_issue is not None:
        return [commits_issue]
    assert commits is not None
    units, unit_issues = build_mainline_units(commits, repo_root=repo_root)
    if unit_issues:
        return [
            f"terminal state-sync の neutral gap を PR unit として解釈できません: {issue}"
            for issue in unit_issues
        ]
    assert units is not None

    for gap_unit in units:
        if gap_unit.kind == "merge" and gap_unit.pr_number is None:
            issues.append(
                "terminal state-sync の neutral gap に canonical PR 件名を持たない"
                f" merge commit があります: {gap_unit.terminal_sha[:8]}"
            )
            continue
        signal_state, signal_issue = classify_terminal_state_sync_signals(
            gap_unit,
            repo_root=repo_root,
        )
        if signal_issue is not None or signal_state != TERMINAL_SIGNAL_NONE:
            issues.append(
                "terminal state-sync の neutral gap に通常の neutral unit ではない"
                f" terminal signal があります: {signal_issue or signal_state}"
            )
            continue
        paths, path_issue = get_unit_changed_paths(gap_unit, repo_root=repo_root)
        if path_issue is not None:
            issues.append(path_issue)
            continue
        assert paths is not None
        if not all(is_neutral_path(path) for path in paths):
            issues.append(
                "terminal state-sync の neutral gap に state-changing path を含む unit が"
                f"あります（{gap_unit.terminal_sha[:8]}）: {', '.join(paths) or '（空）'}"
            )

    if len(units) > staleness_threshold:
        issues.append(
            "terminal state-sync の checkpoint から sync 親までの state-neutral unit 数が"
            f" {len(units)} 件で閾値 {staleness_threshold} を超えています"
        )
    return issues


def check_terminal_state_sync_unit(
    unit: MainlineUnit,
    *,
    checkpoint_sha: str,
    repo_root: Path,
    staleness_threshold: int = DEFAULT_STALENESS_THRESHOLD,
) -> list[str]:
    """terminal state-sync unit の全契約を検証する。

    広い neutral path は追加せず、この関数が全条件を検証できた unit だけを
    ``check_merge_freshness`` が表現済みとして除外する。
    """
    if unit.kind != "rebase" or len(unit.commits) < 2:
        return [
            "terminal state-sync unit は 1 件以上の sync/correction commit と"
            " final evidence-only anchor からなる rebase である必要があります"
            f"（kind={unit.kind},"
            f" commits={len(unit.commits)}）"
        ]

    sync_sha = unit.commits[0]
    anchor_sha = unit.commits[-1]
    metadata: list[tuple[str, tuple[str, ...]]] = []
    for commit_sha in unit.commits:
        commit_metadata, metadata_issue = get_commit_metadata(
            commit_sha,
            repo_root=repo_root,
        )
        if metadata_issue is not None:
            return [metadata_issue]
        assert commit_metadata is not None
        metadata.append(commit_metadata)
    all_subjects = tuple(item[0] for item in metadata)
    sync_subject, sync_parents = metadata[0]
    anchor_subject, anchor_parents = metadata[-1]

    issues: list[str] = []
    marker_looking_count = sum(
        len(STATE_SYNC_MARKER_LOOKING_RE.findall(subject)) for subject in all_subjects
    )
    marker_pr_number = parse_terminal_state_sync_marker(sync_subject)
    if marker_looking_count != 1 or marker_pr_number is None:
        issues.append(
            "terminal state-sync marker は最初の非 anchor commit 件名末尾に"
            " `[STATE-SYNC previous_pr=N]` を厳密に 1 件だけ置く必要があります"
        )

    anchor = parse_rebase_pr_anchor(anchor_subject)
    actual_commit_count = len(unit.commits)
    if (
        anchor is None
        or anchor[1] != actual_commit_count
        or unit.pr_number != anchor[0]
        or len(anchor_parents) != 1
    ):
        issues.append(
            "terminal state-sync の final commit は実 commit 数 M と一致する"
            " `[PR #N; commits=M]` で終わる 1-parent anchor である必要があります"
        )
    if any(len(parents) != 1 for _subject, parents in metadata):
        issues.append("terminal state-sync の全 commit は 1-parent である必要があります")
        return issues
    for index in range(1, len(unit.commits)):
        if metadata[index][1][0].lower() != unit.commits[index - 1].lower():
            issues.append(
                "terminal state-sync の commit 群が連続した first-parent chain では"
                f"ありません（index={index}）"
            )

    sync_parent_sha = sync_parents[0]
    issues.extend(
        check_neutral_mainline_gap(
            checkpoint_sha,
            sync_parent_sha,
            repo_root=repo_root,
            staleness_threshold=staleness_threshold,
        )
    )

    first_sync_paths: list[str] = []
    for index, commit_sha in enumerate(unit.commits[:-1]):
        commit_paths, commit_paths_issue = get_commit_changed_paths(
            commit_sha,
            repo_root=repo_root,
        )
        if commit_paths_issue is not None:
            issues.append(commit_paths_issue)
            continue
        assert commit_paths is not None
        if index == 0:
            first_sync_paths = commit_paths
        invalid_commit_paths = [
            path
            for path in commit_paths
            if path not in STATE_SYNC_SYNC_PATHS
            and STATE_SYNC_PRE_PR_PATH_RE.fullmatch(path) is None
        ]
        if invalid_commit_paths:
            issues.append(
                "terminal state-sync の各 sync/correction commit は"
                " checkpoint／ledger／plan と docs/ai/pre-pr-reviews/**/*.md"
                " だけを変更できます"
                f"（{commit_sha[:8]}）: {', '.join(invalid_commit_paths)}"
            )

    first_sync_path_set = set(first_sync_paths)
    first_pre_pr_paths = [
        path for path in first_sync_paths if STATE_SYNC_PRE_PR_PATH_RE.fullmatch(path) is not None
    ]
    if STATE_SYNC_SYNC_PATHS - first_sync_path_set or not first_pre_pr_paths:
        issues.append(
            "terminal state-sync の先頭 sync commit 自身が checkpoint／ledger／plan の"
            " 3 SSOT と pre-PR .md 証跡 1 件以上を変更する必要があります:"
            f" {', '.join(first_sync_paths) or '（空）'}"
        )
    for evidence_path in first_pre_pr_paths:
        evidence_issue = check_git_regular_nonempty_file(
            sync_sha,
            evidence_path,
            repo_root=repo_root,
        )
        if evidence_issue is not None:
            issues.append(
                "terminal state-sync の先頭 sync commit にある pre-PR 証跡が不正です:"
                f" {evidence_issue}"
            )

    anchor_paths, anchor_paths_issue = get_commit_changed_paths(anchor_sha, repo_root=repo_root)
    if anchor_paths_issue is not None:
        issues.append(anchor_paths_issue)
        return issues
    assert anchor_paths is not None
    if not anchor_paths or not all(
        STATE_SYNC_ANCHOR_PATH_RE.fullmatch(path) for path in anchor_paths
    ):
        issues.append(
            "terminal state-sync の anchor commit は docs/ai/reviews/*.json だけを"
            f"変更する必要があります: {', '.join(anchor_paths) or '（空）'}"
        )

    net_paths, net_paths_issue = get_unit_changed_paths(unit, repo_root=repo_root)
    if net_paths_issue is not None:
        issues.append(net_paths_issue)
        return issues
    assert net_paths is not None
    net_path_set = set(net_paths)
    missing_sync_paths = STATE_SYNC_SYNC_PATHS - net_path_set
    net_pre_pr_paths = [
        path for path in net_paths if STATE_SYNC_PRE_PR_PATH_RE.fullmatch(path) is not None
    ]
    invalid_net_paths = [
        path
        for path in net_paths
        if path not in STATE_SYNC_SYNC_PATHS
        and STATE_SYNC_PRE_PR_PATH_RE.fullmatch(path) is None
        and STATE_SYNC_ANCHOR_PATH_RE.fullmatch(path) is None
    ]
    if missing_sync_paths or not net_pre_pr_paths or invalid_net_paths:
        issues.append(
            "terminal state-sync の aggregate/net 差分は checkpoint／ledger／plan の"
            " 3 SSOT と非空の pre-PR .md 証跡を含み、許可 path だけである必要があります:"
            f" {', '.join(net_paths) or '（空）'}"
        )
    for evidence_path in sorted(set(net_pre_pr_paths)):
        evidence_issue = check_git_regular_nonempty_file(
            unit.terminal_sha,
            evidence_path,
            repo_root=repo_root,
        )
        if evidence_issue is not None:
            issues.append(f"terminal state-sync の pre-PR 証跡が不正です: {evidence_issue}")
    for evidence_path in anchor_paths:
        evidence_issue = check_git_regular_nonempty_file(
            unit.terminal_sha,
            evidence_path,
            repo_root=repo_root,
        )
        if evidence_issue is not None:
            issues.append(f"terminal state-sync の anchor 証跡が不正です: {evidence_issue}")
            continue
        evidence_blob, evidence_blob_issue = _read_git_blob(
            unit.terminal_sha,
            evidence_path,
            repo_root=repo_root,
        )
        if evidence_blob_issue is not None:
            issues.append(f"terminal state-sync の anchor JSON を読めません: {evidence_blob_issue}")
            continue
        assert evidence_blob is not None
        try:
            json.loads(evidence_blob)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            issues.append(
                "terminal state-sync の anchor 証跡を JSON として解析できません"
                f"（{evidence_path}）: {exc}"
            )

    checkpoint_blob, checkpoint_blob_issue = _read_git_blob(
        unit.terminal_sha,
        CHECKPOINT_REL,
        repo_root=repo_root,
    )
    if checkpoint_blob_issue is not None:
        issues.append(checkpoint_blob_issue)
        return issues
    assert checkpoint_blob is not None
    checkpoint_text, checkpoint_decode_issue = _decode_utf8_blob(
        checkpoint_blob,
        revision=unit.terminal_sha,
        path=CHECKPOINT_REL,
    )
    if checkpoint_decode_issue is not None:
        issues.append(checkpoint_decode_issue)
        return issues
    assert checkpoint_text is not None

    resulting_sha = extract_checkpoint_sha(checkpoint_text)
    resulting_pr = extract_checkpoint_pr_number(checkpoint_text)
    if resulting_sha is None or resulting_sha.lower() != checkpoint_sha.lower():
        issues.append(
            "terminal state-sync 後の checkpoint merge SHA が直前タスクの"
            f" terminal SHA（{checkpoint_sha}）と一致しません"
        )
    if marker_pr_number is None or resulting_pr != marker_pr_number:
        issues.append("terminal state-sync marker の previous_pr と checkpoint PR が一致しません")
    if marker_pr_number is not None:
        pr_issue = check_pr_matches_merge_subject(
            marker_pr_number,
            checkpoint_sha,
            repo_root=repo_root,
        )
        if pr_issue is not None:
            issues.append(pr_issue)

    next_task_section = extract_section_after_heading(checkpoint_text, NEXT_TASK_HEADING_RE)
    if next_task_section is None or not _terminal_checkpoint_section_is_exact(next_task_section):
        issues.append(
            f"terminal state-sync 後の {CHECKPOINT_REL} は「## 次タスク」節に"
            f" `{TERMINAL_NEXT_TASK_SENTINEL}` だけを持つ必要があります"
        )

    base_ledger, base_ledger_issue = _read_git_blob(
        sync_parent_sha,
        LEDGER_REL,
        repo_root=repo_root,
    )
    result_ledger, result_ledger_issue = _read_git_blob(
        unit.terminal_sha,
        LEDGER_REL,
        repo_root=repo_root,
    )
    ledger_issues = [
        issue for issue in (base_ledger_issue, result_ledger_issue) if issue is not None
    ]
    if ledger_issues:
        issues.extend(ledger_issues)
    else:
        assert base_ledger is not None
        assert result_ledger is not None
        if result_ledger == base_ledger or not result_ledger.startswith(base_ledger):
            issues.append(
                "terminal state-sync の execution ledger は base blob を byte prefix とする"
                " append-only 更新である必要があります"
            )

    base_plan_blob, base_plan_issue = _read_git_blob(
        sync_parent_sha,
        PLAN_REL,
        repo_root=repo_root,
    )
    result_plan_blob, result_plan_issue = _read_git_blob(
        unit.terminal_sha,
        PLAN_REL,
        repo_root=repo_root,
    )
    plan_blob_issues = [
        issue for issue in (base_plan_issue, result_plan_issue) if issue is not None
    ]
    if plan_blob_issues:
        issues.extend(plan_blob_issues)
    else:
        assert base_plan_blob is not None
        assert result_plan_blob is not None
        base_plan_text, base_plan_decode_issue = _decode_utf8_blob(
            base_plan_blob,
            revision=sync_parent_sha,
            path=PLAN_REL,
        )
        result_plan_text, result_plan_decode_issue = _decode_utf8_blob(
            result_plan_blob,
            revision=unit.terminal_sha,
            path=PLAN_REL,
        )
        decode_issues = [
            issue
            for issue in (base_plan_decode_issue, result_plan_decode_issue)
            if issue is not None
        ]
        if decode_issues:
            issues.extend(decode_issues)
        else:
            assert base_plan_text is not None
            assert result_plan_text is not None
            plan_transition_issue = check_terminal_plan_transition(
                base_plan_text,
                result_plan_text,
                checkpoint_text=checkpoint_text,
            )
            if plan_transition_issue is not None:
                issues.append(plan_transition_issue)
    return issues


def get_merge_changed_paths(
    merge_sha: str, *, repo_root: Path
) -> tuple[list[str] | None, str | None]:
    """merge commit がその第一親（マージ前の mainline）に対して変更したパス一覧を返す。

    clean な `--no-ff` merge では `git show` の既定（combined diff）は空になるため、
    代わりに `git diff --name-only <merge>^1 <merge>` でマージ前後の実差分を取る。
    """
    result = _run_git(["diff", "--name-only", f"{merge_sha}^1", merge_sha], cwd=repo_root)
    if result.returncode != 0:
        stderr = result.stderr.strip() or f"exit code {result.returncode}"
        return None, f"git diff --name-only の実行に失敗しました（{merge_sha}）: {stderr}"
    raw = result.stdout.strip()
    if not raw:
        return [], None
    return raw.splitlines(), None


def classify_merge(merge_sha: str, *, repo_root: Path) -> tuple[bool | None, str | None]:
    """merge commit が state-neutral か state-changing かを判定する（N-598B (c)）。

    変更パスが 1 件も無い場合、または全パスが state-neutral パターンに収まる場合は
    neutral（True）。1 件でも収まらないパスがあれば state-changing（False）。
    """
    paths, issue = get_merge_changed_paths(merge_sha, repo_root=repo_root)
    if issue is not None:
        return None, issue
    assert paths is not None
    return all(is_neutral_path(p) for p in paths), None


def check_merge_freshness(
    sha: str,
    *,
    repo_root: Path,
    mode: str,
    staleness_threshold: int,
    mainline_ref: str | None = None,
) -> list[str]:
    """checkpoint 以降の merge/rebase unit を分類し、mode に応じて鮮度判定する。

    - transition モード（既定）: state-changing unit が 1 件でもあれば fail する
      （閾値なし）。state-neutral merge には従来の閾値（既定 3）を適用する
      （2026-07-29 再監査 P0-1: 従来の「3 merge まで許容」は neutral のみへ縮小適用）。
    - report モード: state-changing unit の件数のみを閾値と比較する
      （state-neutral merge は件数から除外＝従来の bounded-staleness 相当）。
    - terminal state-sync: 1 件以上の sync/correction と final evidence anchor からなる
      専用 unit の全契約を検証できた場合だけ表現済みとして件数から除外する。
      checkpoint／ledger／plan を一般 neutral prefix へ加えることはしない。

    ``mode`` が既知の値でない場合は、呼び出し元の実装ミス（argparse の choices を
    経由しない直接呼び出し等）として、mainline 一覧取得より前に fail-close する。
    """
    if mode not in MODE_CHOICES:
        return [f"不明な --mode 値です: {mode!r}"]

    if mainline_ref is None:
        mainline_ref, ref_issue = resolve_mainline_ref(repo_root=repo_root)
        if ref_issue is not None:
            return [ref_issue]
    assert mainline_ref is not None

    mainline_commits, list_issue = list_mainline_commits_since(
        sha, mainline_ref=mainline_ref, repo_root=repo_root
    )
    if list_issue is not None:
        return [list_issue]
    assert mainline_commits is not None

    units, unit_issues = build_mainline_units(mainline_commits, repo_root=repo_root)
    if unit_issues:
        return unit_issues
    assert units is not None

    state_changing: list[str] = []
    neutral: list[str] = []
    classify_issues: list[str] = []
    unanchored_state_changing: list[str] = []
    for unit in units:
        paths, classify_issue = get_unit_changed_paths(unit, repo_root=repo_root)
        if classify_issue is not None:
            classify_issues.append(classify_issue)
            continue
        assert paths is not None

        is_state_sync, candidate_issue = _is_state_sync_candidate(unit, repo_root=repo_root)
        if candidate_issue is not None:
            classify_issues.append(candidate_issue)
            continue
        if is_state_sync:
            state_sync_issues = check_terminal_state_sync_unit(
                unit,
                checkpoint_sha=sha,
                repo_root=repo_root,
                staleness_threshold=staleness_threshold,
            )
            if state_sync_issues:
                classify_issues.extend(state_sync_issues)
            else:
                # terminal checkpoint が直前 gap を意味的に表現したため、それ以前の
                # neutral 件数を reset する。terminal 後の unit は改めて数える。
                neutral.clear()
            continue

        is_neutral = all(is_neutral_path(path) for path in paths)
        (neutral if is_neutral else state_changing).append(unit.terminal_sha)
        if unit.kind == "unanchored" and not is_neutral:
            unanchored_state_changing.append(unit.terminal_sha)

    if classify_issues:
        return classify_issues

    issues: list[str] = []
    if unanchored_state_changing:
        short_shas = "、".join(sha[:8] for sha in unanchored_state_changing)
        issues.append(
            "checkpoint 以降の mainline に PR anchor のない state-changing 1-parent commit が"
            f" {len(unanchored_state_changing)} 件あります（mode に関係なく fail-close）:"
            f" {short_shas}"
        )
    if mode == "transition":
        if state_changing:
            short_shas = "、".join(s[:8] for s in state_changing)
            issues.append(
                "checkpoint 以降に未表現の state-changing merge/rebase unit が"
                f" {len(state_changing)} 件"
                f"あります（transition mode は 0 件を要求します。{CHECKPOINT_REL} の再生成が"
                f"必要です）: {short_shas}"
            )
        if len(neutral) > staleness_threshold:
            issues.append(
                f"checkpoint 以降の state-neutral merge/rebase unit 数が {len(neutral)} 件で"
                f"閾値 {staleness_threshold} を超えています（stale の疑い。"
                f"{CHECKPOINT_REL} の再生成が必要です）"
            )
    else:  # mode == "report"（関数冒頭で MODE_CHOICES 外は既に fail-close 済み）
        if len(state_changing) > staleness_threshold:
            issues.append(
                f"checkpoint 以降の state-changing merge/rebase unit 数が"
                f" {len(state_changing)} 件で"
                f"閾値 {staleness_threshold} を超えています（report mode の bounded-staleness。"
                f"{CHECKPOINT_REL} の再生成が必要です）"
            )
    return issues


def check_next_task_matches_plan(checkpoint_text: str, plan_text: str) -> str | None:
    """checkpoint の「## 次タスク」節と ``docs/plan.md`` の「## Next」節の整合を検証する

    （N-598B (d)）。terminal sentinel は plan queue が真に空の場合だけ受理する。
    通常時は checkpoint／plan 双方で抽出した task token の集合を比較し、部分文字列
    （例: N-59 と N-598B）を一致扱いしない。
    """
    next_task_section = extract_section_after_heading(checkpoint_text, NEXT_TASK_HEADING_RE)
    if next_task_section is None:
        return f"{CHECKPOINT_REL} に「## 次タスク」見出しが見つかりません"

    if TERMINAL_NEXT_TASK_SENTINEL in next_task_section:
        if not _terminal_checkpoint_section_is_exact(next_task_section):
            return (
                f"{CHECKPOINT_REL} の terminal sentinel は「## 次タスク」節に"
                " 1 行だけ置く必要があります"
            )
        return check_terminal_plan_queue(plan_text)

    tokens = extract_task_tokens(next_task_section)
    if not tokens:
        return (
            f"{CHECKPOINT_REL} の「## 次タスク」節からタスク ID らしきトークン"
            "（N-数字・R-数字・G数字等）を抽出できません"
        )

    plan_next_section = extract_section_after_heading(plan_text, PLAN_NEXT_HEADING_RE)
    if plan_next_section is None:
        return f"{PLAN_REL} に「## Next」見出しが見つかりません"

    plan_tokens = extract_task_tokens(plan_next_section)
    if not set(tokens).intersection(plan_tokens):
        token_list = "、".join(tokens)
        return (
            f"{CHECKPOINT_REL} の次タスクトークン（{token_list}）が {PLAN_REL} の"
            "「## Next」節に見当たりません（次タスクと計画キューの不整合の疑いがあります）"
        )
    return None


def _marker_block_re(marker: str) -> re.Pattern[str]:
    """`<!-- marker -->` で挟まれた YAML code fence を抽出する正規表現を返す。"""
    return re.compile(
        rf"<!-- {re.escape(marker)} -->\s*```yaml\n(?P<body>.*?)```\s*"
        rf"<!-- /{re.escape(marker)} -->",
        re.DOTALL,
    )


def _marker_open_tag_re(marker: str) -> re.Pattern[str]:
    """marker の開始 delimiter コメント（`<!-- marker -->`）だけを緩く検出する。"""
    return re.compile(rf"<!--[ \t]*{re.escape(marker)}[ \t]*-->")


def _marker_close_tag_re(marker: str) -> re.Pattern[str]:
    """marker の終了 delimiter コメント（`<!-- /marker -->`）だけを緩く検出する。"""
    return re.compile(rf"<!--[ \t]*/{re.escape(marker)}[ \t]*-->")


def count_marker_tags(text: str, marker: str) -> tuple[int, int, int]:
    """marker の (開始タグ数, 終了タグ数, 整形済みペア数) を返す（N-598C AC-01/AC-04）。

    3 値が全て 1 のときだけ「delimiter が正確に 1 組」とみなせる。開始タグ数と
    整形済みペア数が食い違う場合（例: 開始タグが 2 つあるのに、非貪欲マッチが
    最初の終了タグまでしか届かず 1 ペアしか見つからない入れ子/欠損ケース）を
    重複・不正 delimiter として検出できるようにするため、3 値を別々に数える。
    """
    open_count = len(_marker_open_tag_re(marker).findall(text))
    close_count = len(_marker_close_tag_re(marker).findall(text))
    pair_count = len(_marker_block_re(marker).findall(text))
    return open_count, close_count, pair_count


def extract_marker_yaml(text: str, marker: str) -> dict[str, Any] | None:
    """marker block の YAML を dict で返す（block 不在／object 以外／不正 YAML なら None）。

    fail-close（P-010）: 呼び出し側が None を「解析不能」として issue へ積めるよう、
    ``yaml.safe_load`` の例外を吸収して None を返す（例外を伝播させて呼び出し元を
    丸ごとクラッシュさせない）。
    """
    match = _marker_block_re(marker).search(text)
    if match is None:
        return None
    try:
        loaded = yaml.safe_load(match.group("body"))
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, dict) else None


def extract_marker_span(text: str, marker: str) -> str | None:
    """marker の開始〜終了 delimiter を含む canonical byte span を返す（N-598C AC-08 準備）。

    ``source_plan_revision`` の導出は「span 全体への最後の byte 変更 commit」を
    一意に特定する必要があるため、YAML を意味解析した dict ではなく raw な
    部分文字列（`match.group(0)`）をそのまま比較対象として使う。
    """
    match = _marker_block_re(marker).search(text)
    return match.group(0) if match is not None else None


def extract_active_queue_block(plan_text: str) -> dict[str, Any] | None:
    """plan の `active-queue:v1` block を dict で返す。"""
    return extract_marker_yaml(plan_text, "active-queue:v1")


def load_marker_object(text: str, marker: str) -> tuple[dict[str, Any] | None, str | None]:
    """marker block を読み、(payload, issue) を返す（PR #1330 Round 2 是正）。

    ``extract_marker_yaml`` は「marker 不在」と「delimiter はあるが YAML が
    破損／object でない」のどちらも None を返すため、区別せずに None を
    「不在」として扱う呼び出し側は、破損 YAML を「marker なし」と誤認して
    fail-close をすり抜けうる（既存 Round 2 Copilot 指摘）。本関数は
    ``count_marker_tags`` で delimiter の有無を先に判定し、両者を明確に
    分離する。

    - delimiter が完全に不在（開始/終了タグとも 0 件）: ``(None, None)``。
      呼び出し側が「marker なし」を正当な状態として扱ってよい。
    - delimiter が不正／重複（``count_marker_tags`` が ``(1, 1, 1)`` 以外）、
      または delimiter は正確に 1 組だが YAML が object として解析できない:
      ``(None, issue)``。呼び出し側は必ず issue を fail-close へ積むこと。
    - 正常: ``(payload, None)``。
    """
    open_count, close_count, pair_count = count_marker_tags(text, marker)
    if open_count == 0 and close_count == 0:
        return None, None
    if not (open_count == 1 and close_count == 1 and pair_count == 1):
        return None, f"`{marker}` の delimiter が不正または重複しています"
    payload = extract_marker_yaml(text, marker)
    if payload is None:
        return None, f"`{marker}` の YAML を object として解析できません（delimiter は存在）"
    return payload, None


RESEARCH_RESUME_FIELDS: tuple[str, ...] = (
    "task_id",
    "git_last_settled_round",
    "git_last_settled_revision",
    "local_session_resume_round",
    "remediation_applied",
    "finding_counts",
    "local_session_evidence_ref",
)
RESEARCH_RESUME_FINDING_KEYS: tuple[str, ...] = ("blocker", "backlog", "invalid")
RESEARCH_RESUME_TASK_ID_RE = re.compile(r"^R-[0-9]{3}$")
RESEARCH_RESUME_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
RESEARCH_RESUME_EVIDENCE_REF_RE = re.compile(r"^local-session:\S+$")


def validate_research_resume_object(obj: Any) -> list[str]:
    """`research_resume` closed object（schema_version を除く 7 field）の構造を検証する。

    N-598C spec「Checkpoint and Local-State Schemas」の closed object 定義（`schema_version`
    を除いた 7 field）を対象に、field 過不足・型・pattern だけを検証する（純粋関数）。
    mainline ancestor 検証や checkpoint／plan／flag 間の exact 比較は呼び出し側
    （resolver）の責務とし、本関数は単一 object の schema 検査に限定する。
    """
    if not isinstance(obj, dict):
        return ["research_resume は object である必要があります"]

    issues: list[str] = []
    extra_fields = sorted(set(obj) - set(RESEARCH_RESUME_FIELDS))
    if extra_fields:
        issues.append(f"research_resume に unknown field があります: {extra_fields}")
    missing_fields = [field for field in RESEARCH_RESUME_FIELDS if field not in obj]
    if missing_fields:
        issues.append(f"research_resume に必須 field が不足しています: {missing_fields}")
        return issues

    task_id = obj["task_id"]
    if not isinstance(task_id, str) or not RESEARCH_RESUME_TASK_ID_RE.fullmatch(task_id):
        issues.append(f"research_resume.task_id が R-nnn 形式ではありません: {task_id!r}")

    for round_field in ("git_last_settled_round", "local_session_resume_round"):
        value = obj[round_field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            issues.append(
                f"research_resume.{round_field} は非負整数である必要があります: {value!r}"
            )

    revision = obj["git_last_settled_revision"]
    if not isinstance(revision, str) or not RESEARCH_RESUME_REVISION_RE.fullmatch(revision):
        issues.append(
            f"research_resume.git_last_settled_revision が40桁小文字hexではありません: {revision!r}"
        )

    remediation_applied = obj["remediation_applied"]
    if not isinstance(remediation_applied, bool):
        issues.append(
            "research_resume.remediation_applied は真偽値である必要があります:"
            f" {remediation_applied!r}"
        )

    finding_counts = obj["finding_counts"]
    if not isinstance(finding_counts, dict) or set(finding_counts) != set(
        RESEARCH_RESUME_FINDING_KEYS
    ):
        issues.append(
            "research_resume.finding_counts は blocker/backlog/invalid の3 keyちょうどである"
            f" 必要があります: {finding_counts!r}"
        )
    else:
        for key in RESEARCH_RESUME_FINDING_KEYS:
            value = finding_counts[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                issues.append(
                    f"research_resume.finding_counts.{key} は非負整数である必要があります:"
                    f" {value!r}"
                )

    evidence_ref = obj["local_session_evidence_ref"]
    if not isinstance(evidence_ref, str) or not RESEARCH_RESUME_EVIDENCE_REF_RE.fullmatch(
        evidence_ref
    ):
        issues.append(
            "research_resume.local_session_evidence_ref が local-session:<opaque-id> 形式では"
            f" ありません: {evidence_ref!r}"
        )

    return issues


def load_flag(repo_root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """full-plan flag（ローカル運用 file）を読む。不在は None を返す。"""
    flag_path = repo_root / FLAG_REL
    if not flag_path.is_file():
        return None, []
    try:
        loaded = json.loads(flag_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"{FLAG_REL} を JSON として読めません: {exc}"]
    if not isinstance(loaded, dict):
        return None, [f"{FLAG_REL} が object ではありません"]
    return loaded, []


def _read_text_file(path: Path) -> tuple[str | None, str | None]:
    """path のテキストを読む（`(text, issue)`）。

    PR #1334 Round 1（review id 4937094684）是正: 読込不能（`OSError`）・非 UTF-8
    バイト列（`UnicodeDecodeError`）を例外のまま伝播させず issue 文字列で返す
    （P-010）。file 不在の判定は呼び出し側が `is_file()` で行う。
    """
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{path} を読めません: {exc}"


def _read_yaml_file(path: Path) -> tuple[Any | None, str | None]:
    """path を読み `yaml.safe_load` した結果を返す（`(payload, issue)`）。

    PR #1334 Round 1（review id 4937094684）是正: 破損 YAML（`yaml.YAMLError`）・
    読込不能（`OSError`）・非 UTF-8 バイト列（`UnicodeDecodeError`）のいずれでも
    例外を伝播させて validator をクラッシュさせず、issue 文字列を返す
    （P-010・`load_marker_object` と同じ「破損は issue・不在は呼び出し側の責務」の
    分離規約に揃える）。file 不在の判定は呼び出し側が `is_file()` で行う。
    """
    text, issue = _read_text_file(path)
    if issue is not None:
        return None, issue
    try:
        return yaml.safe_load(text), None
    except yaml.YAMLError as exc:
        return None, f"{path} を YAML として読めません: {exc}"


def _load_manifest_registry(repo_root: Path) -> tuple[set[str] | None, list[str]]:
    """materialization manifest の `evidence_registry.entries` key 集合を返す。"""
    manifest_path = repo_root / MANIFEST_REL
    if not manifest_path.is_file():
        return None, [f"{MANIFEST_REL} が存在しないため登録状態を判定できません（fail-close）"]
    loaded, issue = _read_yaml_file(manifest_path)
    if issue is not None:
        return None, [issue]
    registry = loaded.get("evidence_registry") if isinstance(loaded, dict) else None
    entries = registry.get("entries") if isinstance(registry, dict) else None
    if entries is None:
        return set(), []
    if not isinstance(entries, dict):
        return None, [f"{MANIFEST_REL}: evidence_registry.entries が object ではありません"]
    return {str(key) for key in entries}, []


def _load_task_events(repo_root: Path, task_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """指定 task の closeout event を読み、unsuperseded な現行 event 群を返す。"""
    event_dir = repo_root / EVENT_DIR_REL
    if not event_dir.is_dir():
        return [], []
    events: list[dict[str, Any]] = []
    issues: list[str] = []
    for path in sorted(event_dir.glob("*.yml")):
        loaded, issue = _read_yaml_file(path)
        if issue is not None:
            issues.append(f"{EVENT_DIR_REL}/{path.name}: {issue}")
            continue
        if not isinstance(loaded, dict):
            issues.append(f"{EVENT_DIR_REL}/{path.name}: event file が object ではありません")
            continue
        events.append(loaded)
    superseded = {
        str(event.get("corrects_event_id"))
        for event in events
        if event.get("corrects_event_id") is not None
    }
    current = [
        event
        for event in events
        if event.get("task_id") == task_id and str(event.get("event_id")) not in superseded
    ]
    return current, issues


def _required_evidence_ids(registration: dict[str, Any]) -> list[str]:
    roots = registration.get("required_evidence_roots")
    if not isinstance(roots, list):
        return []
    return [str(root.get("evidence_id")) for root in roots if isinstance(root, dict)]


def _load_manifest_passing_subjects(repo_root: Path) -> tuple[set[str] | None, list[str]]:
    """manifest `evidence_registry.entries` から `status=pass` の `subject_id` 集合を返す。

    entry の key は evidence ID（`EV-...`）だが、control unit の catalog 順序検査
    （AC-10）は `produced_tokens`（`gate:...`／`ac:...` 等の typed token）が登録済みかを
    見る必要があるため、`_load_manifest_registry` の key 集合とは別に entry の
    `subject_id` value 集合を読む（correction chain の supersede 判定は本 predicate の
    スコープ外とし、素朴な「1 件以上 status=pass で存在するか」に限定する）。
    """
    manifest_path = repo_root / MANIFEST_REL
    if not manifest_path.is_file():
        return None, [f"{MANIFEST_REL} が存在しないため登録状態を判定できません（fail-close）"]
    loaded, issue = _read_yaml_file(manifest_path)
    if issue is not None:
        return None, [issue]
    registry = loaded.get("evidence_registry") if isinstance(loaded, dict) else None
    entries = registry.get("entries") if isinstance(registry, dict) else None
    if entries is None:
        return set(), []
    if not isinstance(entries, dict):
        return None, [f"{MANIFEST_REL}: evidence_registry.entries が object ではありません"]
    subjects: set[str] = set()
    for entry in entries.values():
        if isinstance(entry, dict) and entry.get("status") == "pass":
            subject_id = entry.get("subject_id")
            if isinstance(subject_id, str):
                subjects.add(subject_id)
    return subjects, []


def _load_repair_hold() -> Any:
    """`scripts.ai.repair_hold` を遅延 import して返す（repair_hold は本 module を top-level で
    import するため、逆方向は関数内 import にして循環 import を避ける）。"""
    from scripts.ai import repair_hold

    return repair_hold


def resolve_repair_hold_action(
    *, repo_root: Path, block: dict[str, Any], repair: dict[str, Any]
) -> tuple[str | None, list[str]]:
    """repair-hold（`correction_repair` 非 null・`closeout_registration` null）の
    `closeout_action.kind` を phase から導出する（PR-E3c・DEC-20260815-003 決定 6）。

    - `staging` → `correction_staging`（C: 訂正 artifact／訂正 event の append）
    - `registering` → `correction_registration`（Rcorr': 不完全 batch の invalidation・既存
      slot 訂正専用）
    - `revalidating` → 全 affected task が回復完了（`resolve_current_state.
      recovered_affected_tasks`＝新 sequence の R₂ 済みで registry fold true）なら
      `advance_state`（held state を新 state-sync で復元）、false が残れば None（issue なし・
      hold 継続。repair target を `repair` substep で再 closeout する）。
    - 未知 phase は issue。manifest／event を読めなければ issue（fail-close）。
    """
    phase = repair.get("phase")
    repair_hold = _load_repair_hold()
    if phase not in repair_hold.REPAIR_PHASES:
        return None, [
            f"{PLAN_REL}: correction_repair.phase が未知です"
            f"（{phase!r}・許可は {list(repair_hold.REPAIR_PHASES)}）"
        ]
    if phase == "staging":
        return "correction_staging", []
    if phase == "registering":
        return "correction_registration", []
    manifest_path = repo_root / MANIFEST_REL
    if not manifest_path.is_file():
        return None, [f"{MANIFEST_REL} が存在しないため revalidating の回復判定ができません"]
    manifest, issue = _read_yaml_file(manifest_path)
    if issue is not None or not isinstance(manifest, dict):
        return None, [issue or f"{MANIFEST_REL} が object ではありません"]
    events, event_issues = repair_hold.load_closeout_events(repo_root)
    if event_issues:
        return None, [f"{PLAN_REL}: {msg}" for msg in event_issues]
    resolver = _load_dispatch_resolver()
    held = resolver.held_task_ids_for_block(block, manifest=manifest, events=events)
    if held:
        return None, []
    return "advance_state", []


def resolve_closeout_action(*, repo_root: Path) -> tuple[str | None, list[str]]:
    """`state=closing` に対応する `closeout_action.kind` を導出する。

    `subject_kind`（`task_closeout`／`control_unit`）を問わず、repair-hold（`correction_repair`
    非 null かつ `closeout_registration` null）は `resolve_repair_hold_action` の phase 由来、
    それ以外（通常の S1 と、repair-hold 中の新 sequence S1₂）は required evidence root の
    登録状態から `evidence_registration`／`advance_state` を返す。部分登録は action を返さず
    fail-close する（R は atomic batch であり中間状態を許さない）。
    """
    plan_path = repo_root / PLAN_REL
    if not plan_path.is_file():
        return None, []
    plan_text, read_issue = _read_text_file(plan_path)
    if read_issue is not None or plan_text is None:
        return None, [read_issue or f"{PLAN_REL} を読めません"]
    block, block_issue = load_marker_object(plan_text, "active-queue:v1")
    if block_issue is not None:
        # delimiter はあるが YAML が破損している場合、「marker なし」と混同して
        # closing 検査を skip しない（PR #1330 Round 2 是正）。
        return None, [f"{PLAN_REL}: {block_issue}"]
    if block is None or block.get("state") != CLOSING_STATE:
        return None, []

    repair = block.get("correction_repair")
    registration = block.get("closeout_registration")
    if repair is not None:
        if not isinstance(repair, dict):
            return None, [f"{PLAN_REL}: correction_repair は object でなければなりません"]
        if registration is None:
            return resolve_repair_hold_action(repo_root=repo_root, block=block, repair=repair)
        # closeout_registration を伴う場合は repair-hold 中の新 sequence S1₂／R₂であり、
        # 通常の登録状態判定へ落とす（phase は hold の状態を表し、この S1 の R 判定には使わない
        # が、未知 phase は引き続き拒否する）。
        phase = repair.get("phase")
        if phase not in _load_repair_hold().REPAIR_PHASES:
            return None, [
                f"{PLAN_REL}: correction_repair.phase が未知です"
                f"（{phase!r}・許可は {list(_load_repair_hold().REPAIR_PHASES)}）"
            ]

    if not isinstance(registration, dict):
        return None, [f"{PLAN_REL}: state=closing に closeout_registration がありません"]
    required = _required_evidence_ids(registration)
    if not required:
        return None, [f"{PLAN_REL}: required_evidence_roots が空です"]
    registered, issues = _load_manifest_registry(repo_root)
    if registered is None:
        return None, issues
    present = [evidence_id for evidence_id in required if evidence_id in registered]
    if not present:
        return "evidence_registration", []
    if len(present) == len(required):
        return "advance_state", []
    missing = [evidence_id for evidence_id in required if evidence_id not in registered]
    return None, [
        f"{MANIFEST_REL}: R の部分登録を検出しました"
        f"（未登録 {len(missing)}/{len(required)} 件・atomic batch 違反）"
    ]


def _mirror_issues(*, label: str, expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """checkpoint／flag の mirror が plan の closeout_registration と exact 一致するか。"""
    issues: list[str] = []
    for field in MIRROR_REQUIRED_FIELDS:
        if field not in actual:
            issues.append(f"{label}: closeout mirror に {field} がありません")
        elif actual[field] != expected.get(field):
            issues.append(
                f"{label}: closeout mirror の {field} が {PLAN_REL} と一致しません"
                f"（{actual[field]!r} != {expected.get(field)!r}）"
            )
    for field in MIRROR_OPTIONAL_SET_FIELDS:
        if field in actual and actual[field] != expected.get(field):
            issues.append(f"{label}: closeout mirror の {field} が {PLAN_REL} と一致しません")
    return issues


def _check_control_unit_ordering(*, repo_root: Path, event_path: str) -> list[str]:
    """control unit の catalog 実行順序（`execution_order`）を検査する（AC-10）。

    対象 CUE の `control_id` より `execution_order` が小さい catalog 上の全 unit が、
    manifest へ `produced_tokens` を（`status=pass` の `subject_id` として）登録済みで
    あることを要求する。U2 先行・順序違反を拒否する（spec「Tests 負例」の
    「U2先行、通常task phaseの安全証跡流用を拒否する」の順序部分に対応）。
    """
    # `_check_event_mirror` と同一規則のパス検証。`..` 等でリポジトリ外を
    # 読みに行かない（パス不正の issue 化は mirror 検査側が担当済み）。
    if not event_path.startswith(f"{CONTROL_EVENT_DIR_REL}/") or ".." in event_path.split("/"):
        return []
    event_file = repo_root / event_path
    if not event_file.is_file():
        return []  # 存在チェックは _check_event_mirror 側が担当済み
    loaded, issue = _read_yaml_file(event_file)
    if issue is not None:
        return [issue]
    if not isinstance(loaded, dict):
        return []
    control_id = loaded.get("control_id")
    if not isinstance(control_id, str) or control_id not in control_unit_event.CONTROL_UNIT_ORDER:
        return [f"{event_path}: control_id が closed catalog に存在しません: {control_id!r}"]
    predecessors = control_unit_event.units_with_lower_execution_order(control_id)
    if not predecessors:
        return []
    registered_subjects, issues = _load_manifest_passing_subjects(repo_root)
    if registered_subjects is None:
        return issues
    missing_units = [
        predecessor_id
        for predecessor_id in predecessors
        if not all(
            token in registered_subjects
            for token in control_unit_event.CONTROL_UNIT_ORDER[predecessor_id].produced_tokens
        )
    ]
    if missing_units:
        issues.append(
            f"{event_path}: control unit 実行順序違反です（先行 unit 未完了: "
            f"{', '.join(missing_units)}）"
        )
    return issues


def _load_dispatch_resolver() -> Any:
    """`scripts.ai.resolve_current_state` を遅延 import して返す。

    resolve_current_state.py は本 module を top-level で import するため、逆方向は
    関数内 import にして循環 import を避ける（CLI 直接実行・`scripts.ai` package
    経由・importlib 単独 load のいずれでも `ROOT` が sys.path にあり解決できる）。
    """
    from scripts.ai import resolve_current_state as dispatch_resolver

    return dispatch_resolver


def check_closing_resume_dispatchability(
    *, repo_root: Path, block: dict[str, Any], registration: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """closing block の `closeout_registration.resume` 先が実行順契約上 dispatch 可能かを
    S1 時点の manifest で検査し `(issues, advisories)` を返す（HRAI-REAUDIT-20260815
    P0-02・PR-E1）。

    判定は `resolve_current_state.resolve_task_dispatch_truth`（resolver の Decision
    Rule 7 追記・S1 writer と同一 predicate）に一本化する。`scheduling_exception=null`
    が closing の必須条件（spec 261-262 行）なので research wait window は無効として
    評価し、`subject_kind=task_closeout` では closing task 自身の `task:<id>` を R／S2 が
    公開する予定の truth として投影 true にする（`subject_kind=control_unit` の closing
    task_id=R-025 は control unit であって `task:R-025`（測定実装 task）の truth では
    ないため投影しない）。

    fail-close（issues）と advisory の切り分け（spec 382-403 行・Decision Rule 10 を
    読んで決めた理由）:

    - **判定不能は issue（fail-close）**: manifest 不在／破損、resume 先が task_catalog
      未登録、aggregate kind、依存 token 未解決。S2 が構造的に dispatch できない resume
      先を S1 に埋めた状態であり、他の closing 検査（manifest 不在で
      `_load_manifest_registry` が fail する等）と同じく「検証不能を pass にしない」
      （P-010）。
    - **未充足（評価できたが false）は advisory（issue にしない）**: spec 400-403 行が
      S2 に要求する再検査は「registry truth」（closing task 自身の completion truth）で
      あり、resume 先の依存充足ではない。S2 は resume 先を `state=blocked` で置くことが
      できる（現 main の N-594B→N-600 も同型）ため、S1 の resume が未充足であること
      自体は closing の不整合ではない。ここで fail-close にすると、(a) 全後続 task が
      未充足のとき S1 に置ける resume 先が無くなり closeout chain が deadlock する、
      (b) S1 writer が `--allow-blocked-resume` で明示的に通した S1 を validator が常に
      落とし、writer の acknowledgment 経路が無意味になる。未充足 resume を ready で
      置く誤りは resolver の Decision Rule 7 追記（issue+null）が S2 側で止める。
      advisory は `_check_closing_block` が stderr へ「注記」として出し exit code に
      影響させない（validate() の戻り値契約は issues のみのため）。
    - `correction_repair` が非 null の間は S1 の resume を正本にしない（spec 213 行
      「既に公開したS1のresumeは再利用せず」）ため検査しない。
    - `resume.terminal=true`、resume.task_id が canonical 形式でない場合は既存の
      形状検査に委ね検査しない。
    """
    if block.get("correction_repair") is not None:
        return [], []
    resume = registration.get("resume")
    if not isinstance(resume, dict) or resume.get("terminal") is not False:
        return [], []
    resume_task_id = resume.get("task_id")
    if not isinstance(resume_task_id, str) or not CANONICAL_TASK_ID_RE.fullmatch(resume_task_id):
        return [], []
    closing_task_id = block.get("task_id")
    assume_true: list[str] = []
    if (
        registration.get("subject_kind") == SUBJECT_KIND_TASK_CLOSEOUT
        and isinstance(closing_task_id, str)
        and CANONICAL_TASK_ID_RE.fullmatch(closing_task_id)
    ):
        assume_true.append(f"task:{closing_task_id}")
    dispatch_resolver = _load_dispatch_resolver()
    truth, reasons = dispatch_resolver.resolve_task_dispatch_truth(
        resume_task_id,
        repo_root=repo_root,
        scheduling_output=None,
        assume_true_tokens=assume_true,
    )
    if truth is True:
        return [], []
    if truth is None:
        return [
            f"{PLAN_REL}: closeout_registration.resume 先 task:{resume_task_id} の依存を"
            f"判定できません（S2 が dispatch できない resume 先・fail-close）: {reason}"
            for reason in reasons
        ], []
    return [], [
        f"{PLAN_REL}: resume 先 task:{resume_task_id} は依存未充足。S2 は state=blocked で置くか"
        f" R-025 へ戻す（resolver は ready|in_progress の未充足 task を止める）: {reason}"
        for reason in reasons
    ]


def _check_correction_repair(*, repo_root: Path, block: dict[str, Any]) -> list[str]:
    """`correction_repair`（recovery schema v2）を manifest／event／repo で完全検証する
    （PR-E3c）。manifest 不在／破損は fail-close の issue にする。"""
    repair = block.get("correction_repair")
    if not isinstance(repair, dict):
        return [f"{PLAN_REL}: correction_repair は object でなければなりません"]
    manifest_path = repo_root / MANIFEST_REL
    resolver = _load_dispatch_resolver()
    if not manifest_path.is_file():
        issues = [
            f"{PLAN_REL}: correction_repair の affected set を再導出する {MANIFEST_REL} が"
            "存在しません（fail-close）"
        ]
        issues.extend(
            f"{PLAN_REL}: {msg}" for msg in resolver.validate_correction_repair_object(repair)
        )
        return issues
    manifest, issue = _read_yaml_file(manifest_path)
    if issue is not None or not isinstance(manifest, dict):
        return [issue or f"{MANIFEST_REL} が object ではありません"]
    repair_hold = _load_repair_hold()
    events, event_issues = repair_hold.load_closeout_events(repo_root)
    issues = [f"{PLAN_REL}: {msg}" for msg in event_issues]
    issues.extend(
        f"{PLAN_REL}: {msg}"
        for msg in resolver.validate_correction_repair_object(
            repair, manifest=manifest, repo_root=repo_root, events=events
        )
    )
    return issues


def _check_repair_hold_block(
    *,
    repo_root: Path,
    block: dict[str, Any],
    repair: dict[str, Any],
    checkpoint_text: str,
) -> list[str]:
    """repair-hold 中（`correction_repair` 非 null・`closeout_registration` null）の active block
    規則と checkpoint／flag mirror を検査する（PR-E3c・DEC-20260815-003 決定 6）。

    - `task_id` は `correction_repair.repair_target_task_id`（plan／checkpoint／flag を
      repair target へ exact 同期・spec 208-211 行）
    - `substep ∈ {evidence-correction, repair}`。`repair` は phase=revalidating に限る
      （spec 412-415 行「falseが残る場合…`repair` substepとしてdispatch」）
    - `blocked_by == ["repair:<discovery artifact raw SHA-256 先頭 16 hex>"]`
    - checkpoint-next／flag の task_id／substep は block と exact 一致、closeout-registration
      mirror（checkpoint marker／flag object）は持たない（S1 ではない）
    - `resolve_closeout_action` が導出可能（phase 由来・revalidating は回復判定）
    """
    issues: list[str] = []
    repair_hold = _load_repair_hold()
    target = repair.get("repair_target_task_id")
    if block.get("task_id") != target:
        issues.append(
            f"{PLAN_REL}: repair-hold 中の task_id は correction_repair.repair_target_task_id"
            f"（{target!r}）と一致する必要があります（現在 {block.get('task_id')!r}）"
        )
    substep = block.get("substep")
    if substep not in repair_hold.HOLD_SUBSTEPS:
        issues.append(
            f"{PLAN_REL}: repair-hold 中の substep は {list(repair_hold.HOLD_SUBSTEPS)} に限ります"
            f"（現在 {substep!r}）"
        )
    elif substep == repair_hold.HOLD_SUBSTEP_REPAIR and repair.get("phase") != "revalidating":
        issues.append(
            f"{PLAN_REL}: substep=repair は correction_repair.phase=revalidating（再評価で false が"
            f"残った repair target の再 closeout）に限ります（現在 phase={repair.get('phase')!r}）"
        )
    manifest_path = repo_root / MANIFEST_REL
    manifest: dict[str, Any] | None = None
    if manifest_path.is_file():
        loaded, _issue = _read_yaml_file(manifest_path)
        manifest = loaded if isinstance(loaded, dict) else None
    expected_blocked_by = repair_hold.expected_blocked_by(repair, manifest)
    if expected_blocked_by is None:
        issues.append(
            f"{PLAN_REL}: repair-hold の blocked_by を導出できません（discovery artifact の"
            " raw SHA-256 が discovery_evidence／registry entry から得られない）"
        )
    elif block.get("blocked_by") != expected_blocked_by:
        issues.append(
            f"{PLAN_REL}: repair-hold 中の blocked_by は {expected_blocked_by!r} でなければ"
            f"なりません（現在 {block.get('blocked_by')!r}）"
        )

    checkpoint_next, checkpoint_next_issue = load_marker_object(
        checkpoint_text, "checkpoint-next:v1"
    )
    if checkpoint_next_issue is not None:
        issues.append(f"{CHECKPOINT_REL}: {checkpoint_next_issue}")
    elif checkpoint_next is None:
        issues.append(f"{CHECKPOINT_REL}: checkpoint-next:v1 block がありません")
    else:
        if checkpoint_next.get("task_id") != block.get("task_id"):
            issues.append(
                f"{CHECKPOINT_REL}: checkpoint-next の task_id が repair-hold の active block と"
                "一致しません"
            )
        if checkpoint_next.get("substep") != block.get("substep"):
            issues.append(
                f"{CHECKPOINT_REL}: checkpoint-next の substep が repair-hold の active block と"
                "一致しません"
            )
    checkpoint_mirror, checkpoint_mirror_issue = load_marker_object(
        checkpoint_text, "closeout-registration:v1"
    )
    if checkpoint_mirror_issue is not None:
        issues.append(f"{CHECKPOINT_REL}: {checkpoint_mirror_issue}")
    elif checkpoint_mirror is not None:
        issues.append(
            f"{CHECKPOINT_REL}: repair-hold（closeout_registration null）中は"
            " closeout-registration:v1 mirror を持ちません"
        )

    flag, flag_issues = load_flag(repo_root)
    issues.extend(flag_issues)
    if flag is not None:
        if flag.get("current_task_id") != block.get("task_id"):
            issues.append(
                f"{FLAG_REL}: current_task_id が repair-hold の active block と一致しません"
            )
        if flag.get("current_substep") != block.get("substep"):
            issues.append(
                f"{FLAG_REL}: current_substep が repair-hold の active block と一致しません"
            )
        if flag.get("closeout_registration") is not None:
            issues.append(
                f"{FLAG_REL}: repair-hold（closeout_registration null）中は closeout_registration"
                " mirror を持ちません"
            )

    action, action_issues = resolve_closeout_action(repo_root=repo_root)
    issues.extend(action_issues)
    if action is not None and action not in CLOSEOUT_ACTIONS:
        issues.append(f"closeout_action が未知です: {action!r}")
    return issues


def _check_closing_block(
    *, repo_root: Path, block: dict[str, Any], checkpoint_text: str
) -> list[str]:
    """`state=closing` の active block と event／checkpoint／flag の mirror を検査する。

    `subject_kind`（`task_closeout`／`control_unit`）に応じて task_id／substep の
    期待値を分岐する（AC-06 汎化・AC-10）。task_closeout は任意の canonical task ID
    を許し、control_unit は spec が固定する `task_id=R-025`／
    `substep=control-evidence-registration` だけを許す。

    resume 先の実行順契約（manifest dependencies）は
    `check_closing_resume_dispatchability` が検査し、判定不能は issue、未充足は
    stderr への「注記」（exit code 非影響）とする（切り分けの理由は同関数 docstring）。
    """
    issues: list[str] = []
    task_id = block.get("task_id")
    if not isinstance(task_id, str) or not CANONICAL_TASK_ID_RE.fullmatch(task_id):
        issues.append(
            f"{PLAN_REL}: state=closing の task_id が canonical task ID 形式ではありません"
            f"（現在 {task_id!r}）"
        )
    if block.get("scheduling_exception") is not None:
        issues.append(f"{PLAN_REL}: state=closing では scheduling_exception=null が必要です")

    registration = block.get("closeout_registration")
    repair = block.get("correction_repair")
    if repair is not None:
        # repair-hold（PR-E3c）: correction_repair の完全検証（構造・再導出・repo 検査）と
        # hold 中の active block 規則。closeout_registration null は hold 中（C／Rcorr' 前・
        # revalidating で false が残る間）に許し、非 null なら新 sequence の S1₂ として
        # 通常の閉包検査へ続ける。
        issues.extend(_check_correction_repair(repo_root=repo_root, block=block))
        if not isinstance(repair, dict):
            return issues
        if registration is None:
            issues.extend(
                _check_repair_hold_block(
                    repo_root=repo_root, block=block, repair=repair, checkpoint_text=checkpoint_text
                )
            )
            return issues
    if not isinstance(registration, dict):
        issues.append(f"{PLAN_REL}: state=closing には closeout_registration が必要です")
        return issues
    missing_fields = [field for field in CLOSEOUT_REGISTRATION_FIELDS if field not in registration]
    if missing_fields:
        issues.append(
            f"{PLAN_REL}: closeout_registration の field が不足しています: "
            + "、".join(missing_fields)
        )
        return issues

    subject_kind = registration.get("subject_kind")
    if subject_kind not in SUBJECT_KINDS:
        issues.append(
            f"{PLAN_REL}: closeout_registration.subject_kind が未知です"
            f"（現在 {subject_kind!r}・許可は {list(SUBJECT_KINDS)}）"
        )
    elif subject_kind == SUBJECT_KIND_CONTROL_UNIT:
        if task_id != CONTROL_UNIT_TASK_ID:
            issues.append(
                f"{PLAN_REL}: subject_kind=control_unit の closing は"
                f" task_id={CONTROL_UNIT_TASK_ID} に限ります（現在 {task_id!r}）"
            )
        if block.get("substep") != CONTROL_CLOSING_SUBSTEP:
            issues.append(
                f"{PLAN_REL}: subject_kind=control_unit の closing の substep は"
                f" {CONTROL_CLOSING_SUBSTEP} でなければなりません（現在 {block.get('substep')!r}）"
            )
    else:
        if block.get("substep") != CLOSING_SUBSTEP:
            issues.append(
                f"{PLAN_REL}: state=closing の substep は {CLOSING_SUBSTEP} でなければなりません"
                f"（現在 {block.get('substep')!r}）"
            )

    event_id = str(registration.get("event_id"))
    if block.get("blocked_by") != [f"registration:{event_id}"]:
        issues.append(
            f"{PLAN_REL}: state=closing の blocked_by は "
            f'["registration:{event_id}"] でなければなりません（現在 {block.get("blocked_by")!r}）'
        )

    resume = registration.get("resume")
    if not isinstance(resume, dict):
        issues.append(f"{PLAN_REL}: closeout_registration.resume が object ではありません")
    else:
        terminal = resume.get("terminal")
        if terminal is True:
            if resume.get("task_id") is not None or resume.get("substep") is not None:
                issues.append(
                    f"{PLAN_REL}: resume.terminal=true では task_id／substep を null にします"
                )
        elif terminal is False:
            resume_task_id = resume.get("task_id")
            if resume_task_id is None or resume.get("substep") is None:
                issues.append(f"{PLAN_REL}: resume.terminal=false では task_id／substep が必須です")
            elif not isinstance(resume_task_id, str) or not CANONICAL_TASK_ID_RE.fullmatch(
                resume_task_id
            ):
                issues.append(
                    f"{PLAN_REL}: closeout_registration.resume.task_id が canonical task ID"
                    f" 形式ではありません（現在 {resume_task_id!r}）"
                )
        else:
            issues.append(f"{PLAN_REL}: resume.terminal は真偽値が必要です")

    # HRAI-REAUDIT-20260815 P0-02（PR-E1）: resume 先の実行順契約（manifest
    # dependencies）を S1 時点で検査する。判定不能は issue、未充足は注記。
    resume_issues, resume_advisories = check_closing_resume_dispatchability(
        repo_root=repo_root, block=block, registration=registration
    )
    issues.extend(resume_issues)
    for advisory in resume_advisories:
        print(f"注記: {advisory}", file=sys.stderr)

    issues.extend(_check_event_mirror(repo_root=repo_root, registration=registration))
    if subject_kind == SUBJECT_KIND_CONTROL_UNIT:
        issues.extend(
            _check_control_unit_ordering(
                repo_root=repo_root, event_path=str(registration.get("event_path"))
            )
        )

    checkpoint_next, checkpoint_next_issue = load_marker_object(
        checkpoint_text, "checkpoint-next:v1"
    )
    if checkpoint_next_issue is not None:
        # delimiter はあるが YAML が破損している場合を「block なし」と混同しない
        # （PR #1330 Round 2 是正）。
        issues.append(f"{CHECKPOINT_REL}: {checkpoint_next_issue}")
    elif checkpoint_next is None:
        issues.append(f"{CHECKPOINT_REL}: checkpoint-next:v1 block がありません")
    else:
        if checkpoint_next.get("task_id") != block.get("task_id"):
            issues.append(
                f"{CHECKPOINT_REL}: checkpoint-next の task_id が active block と一致しません"
            )
        if checkpoint_next.get("substep") != block.get("substep"):
            issues.append(
                f"{CHECKPOINT_REL}: checkpoint-next の substep が active block と一致しません"
            )
    checkpoint_mirror, checkpoint_mirror_issue = load_marker_object(
        checkpoint_text, "closeout-registration:v1"
    )
    if checkpoint_mirror_issue is not None:
        issues.append(f"{CHECKPOINT_REL}: {checkpoint_mirror_issue}")
    elif checkpoint_mirror is None:
        issues.append(
            f"{CHECKPOINT_REL}: closeout-registration:v1 block がありません"
            "（event の 2 集合 hash を checkpoint へ exact mirror してください）"
        )
    else:
        issues.extend(
            _mirror_issues(label=CHECKPOINT_REL, expected=registration, actual=checkpoint_mirror)
        )

    flag, flag_issues = load_flag(repo_root)
    issues.extend(flag_issues)
    if flag is not None:
        if flag.get("current_task_id") != block.get("task_id"):
            issues.append(f"{FLAG_REL}: current_task_id が active block と一致しません")
        if flag.get("current_substep") != block.get("substep"):
            issues.append(f"{FLAG_REL}: current_substep が active block と一致しません")
        flag_mirror = flag.get("closeout_registration")
        if not isinstance(flag_mirror, dict):
            issues.append(f"{FLAG_REL}: closeout_registration mirror がありません")
        else:
            issues.extend(_mirror_issues(label=FLAG_REL, expected=registration, actual=flag_mirror))

    action, action_issues = resolve_closeout_action(repo_root=repo_root)
    issues.extend(action_issues)
    if action is not None and action not in CLOSEOUT_ACTIONS:
        issues.append(f"closeout_action が未知です: {action!r}")
    return issues


def _check_event_mirror(*, repo_root: Path, registration: dict[str, Any]) -> list[str]:
    """closeout event の raw bytes・2 集合・hash が plan と exact 一致するか検査する。

    `subject_kind=task_closeout` は `docs/ai/task-closeout-events/TCE-*.yml`
    （集合が `terminal_verification` 配下）、`subject_kind=control_unit` は
    `docs/ai/control-unit-events/CUE-*.yml`（集合が top-level）を対象とし、
    event 配置・ID prefix・schema 形状の 3 点を subject_kind ごとに分岐する（AC-10）。
    """
    subject_kind = registration.get("subject_kind")
    if subject_kind == SUBJECT_KIND_TASK_CLOSEOUT:
        event_dir_rel = EVENT_DIR_REL
        id_prefix = "TCE-"
    elif subject_kind == SUBJECT_KIND_CONTROL_UNIT:
        event_dir_rel = CONTROL_EVENT_DIR_REL
        id_prefix = "CUE-"
    else:
        return [
            f"{PLAN_REL}: closeout_registration.subject_kind が未知のため event を"
            f" 照合できません（現在 {subject_kind!r}）"
        ]

    issues: list[str] = []
    event_path = str(registration.get("event_path"))
    if not event_path.startswith(f"{event_dir_rel}/") or ".." in event_path.split("/"):
        issues.append(f"{PLAN_REL}: event_path は {event_dir_rel}/ 配下でなければなりません")
        return issues
    event_id = registration.get("event_id")
    if not isinstance(event_id, str) or not event_id.startswith(id_prefix):
        issues.append(
            f"{PLAN_REL}: closeout_registration.event_id は {id_prefix!r} で始まる必要が"
            f" あります（subject_kind={subject_kind!r}・現在 {event_id!r}）"
        )
    event_file = repo_root / event_path
    if not event_file.is_file():
        issues.append(f"{event_path}: closeout event file が存在しません")
        return issues
    try:
        raw = event_file.read_bytes()
    except OSError as exc:
        issues.append(f"{event_path}: 読めません: {exc}")
        return issues
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != registration.get("event_sha256"):
        issues.append(
            f"{event_path}: raw SHA-256 が plan の event_sha256 と一致しません（実測 {actual_sha}）"
        )
    try:
        loaded = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        issues.append(f"{event_path}: YAML として読めません: {exc}")
        return issues
    if not isinstance(loaded, dict):
        issues.append(f"{event_path}: event file が object ではありません")
        return issues
    if loaded.get("event_id") != registration.get("event_id"):
        issues.append(f"{event_path}: event_id が plan と一致しません")
    if event_path != f"{event_dir_rel}/{loaded.get('event_id')}.yml":
        issues.append(f"{event_path}: file 名が event_id と一致しません")

    if subject_kind == SUBJECT_KIND_TASK_CLOSEOUT:
        source: Any = loaded.get("terminal_verification")
        if not isinstance(source, dict):
            issues.append(f"{event_path}: terminal_verification がありません")
            return issues
        source_label = "terminal_verification."
    else:
        source = loaded
        source_label = ""

    pairs = (
        ("evidence_roots", "required_evidence_roots"),
        ("evidence_roots_sha256", "required_evidence_roots_sha256"),
        ("closure_claim_ids", "required_closure_claim_ids"),
        ("closure_claim_ids_sha256", "required_closure_claim_ids_sha256"),
    )
    for event_field, plan_field in pairs:
        if source.get(event_field) != registration.get(plan_field):
            issues.append(
                f"{PLAN_REL}: closeout_registration.{plan_field} が "
                f"{event_path} の {source_label}{event_field} と exact 一致しません"
            )
    return issues


def _extract_checkpoint_completed_task_id(checkpoint_text: str) -> str | None:
    """checkpoint の「## 直前の完了」節から直前に closing した task ID を抽出する。

    `resolve_current_state.py` の `last_completed.task_id` と同じ抽出規則
    （`CHECKPOINT_TASK_RE` + `extract_task_tokens`）を再利用する。
    """
    match = CHECKPOINT_TASK_RE.search(checkpoint_text)
    if match is None:
        return None
    tokens = extract_task_tokens(match.group(1))
    return tokens[0] if tokens else None


def _check_prior_task_closeout_registered(*, repo_root: Path, checkpoint_text: str) -> list[str]:
    """active block が closing でない間、checkpoint が指す直前 closing task の R が
    完全登録済みであることを再検査する（AC-06 汎化）。

    N-602A 限定 bootstrap は「S2 で active task_id が固定の resume 先（旧
    `BOOTSTRAP_RESUME_TASK_ID="N-598C"`）になった直後だけ」検査していたが、汎化後は
    checkpoint の last-completed task token（`CHECKPOINT_TASK_RE`）を「直前に
    closing した task」の一般シグナルとして使い、active task が何であっても常に
    検査する（実データでは N-602A closeout R・PR #1328 の checkpoint 記述から
    `N-602A` が抽出され、同じ predicate で valid であり続ける）。該当 task の
    closeout event が 1 件も無ければ何も検査しない（closeout event 導入前の旧
    task・synthetic fixture を誤検出しない）。

    PR-E3c（DEC-20260815-003 決定 6）: 旧「task ごとに unsuperseded event 1 件」は、post-S2
    回復で同じ task が新しい closeout sequence（T₂→S1₂→R₂→S2₂）を持つと成り立たないため、
    「stable key `(task_id, merged_pr, closeout_role, closeout_sequence)` ごとに unsuperseded
    event 1 件・全 key の root が登録済み」へ緩和した。同一 key に 2 件は引き続き拒否する。
    """
    prior_task_id = _extract_checkpoint_completed_task_id(checkpoint_text)
    if prior_task_id is None:
        return []
    events, issues = _load_task_events(repo_root, prior_task_id)
    if not events:
        return issues
    by_key: dict[tuple[Any, Any, Any, Any], list[dict[str, Any]]] = {}
    for event in events:
        key = (
            event.get("task_id"),
            event.get("merged_pr"),
            event.get("closeout_role"),
            event.get("closeout_sequence"),
        )
        by_key.setdefault(key, []).append(event)
    registered: set[str] | None = None
    for key in sorted(by_key, key=repr):
        members = by_key[key]
        if len(members) != 1:
            issues.append(
                f"{EVENT_DIR_REL}: {prior_task_id} の stable key {key!r} に unsuperseded event が"
                f" {len(members)} 件あります（1 件が必要）"
            )
            continue
        verification = members[0].get("terminal_verification")
        if not isinstance(verification, dict):
            issues.append(f"{EVENT_DIR_REL}: {key!r} の terminal_verification を読めません")
            continue
        roots = verification.get("evidence_roots")
        required = (
            [str(root.get("evidence_id")) for root in roots if isinstance(root, dict)]
            if isinstance(roots, list)
            else []
        )
        if registered is None:
            registered, registry_issues = _load_manifest_registry(repo_root)
            if registered is None:
                return issues + registry_issues
        missing = [evidence_id for evidence_id in required if evidence_id not in registered]
        if missing:
            issues.append(
                f"R 未完（未登録 {len(missing)}/{len(required)} 件・closeout_sequence="
                f"{members[0].get('closeout_sequence')!r}）のまま {prior_task_id} の closing から"
                "先へ進んでいます"
            )
    return issues


def check_task_closeout_sequencing(
    *, repo_root: Path, plan_text: str, checkpoint_text: str
) -> list[str]:
    """closing（closeout_action／3 情報源 mirror）の汎化検査（AC-06・AC-10）。

    `active-queue:v1` block が無い環境（N-598C 以前の旧 plan・fixture）では何も
    検査せず、既存の pass 経路を変えない。ただし delimiter があるのに YAML が
    破損している場合は「block なし」と混同せず fail-close する（PR #1330 Round 2
    是正の踏襲）。N-602A 限定 bootstrap（旧 `check_closeout_bootstrap`）が固定して
    いた task_id 判定は削除し、任意 task（`subject_kind=task_closeout`）と R-025 の
    control unit（`subject_kind=control_unit`）の双方を扱う。
    """
    block, block_issue = load_marker_object(plan_text, "active-queue:v1")
    if block_issue is not None:
        return [f"{PLAN_REL}: {block_issue}"]
    if block is None:
        return []
    if block.get("state") != CLOSING_STATE:
        issues: list[str] = []
        if block.get("closeout_registration") is not None:
            issues.append(
                f"{PLAN_REL}: state={block.get('state')!r} で closeout_registration を持てません"
            )
        if block.get("correction_repair") is not None:
            # repair-hold は state=closing でのみ持てる（repair 途中の通常 dispatch を拒否・
            # spec 415-416 行・PR-E3c）。
            issues.append(
                f"{PLAN_REL}: state={block.get('state')!r} で correction_repair（repair-hold）を"
                "持てません（repair 途中の通常 dispatch を拒否）"
            )
        issues.extend(
            _check_prior_task_closeout_registered(
                repo_root=repo_root, checkpoint_text=checkpoint_text
            )
        )
        return issues
    return _check_closing_block(repo_root=repo_root, block=block, checkpoint_text=checkpoint_text)


def check_checkpoint_freshness(
    *,
    repo_root: Path,
    sha: str,
    pr_number: str | None,
    mode: str,
    staleness_threshold: int,
) -> list[str]:
    """checkpoint の PR/SHA 整合・ancestor・first-parent・merge-freshness だけを検証する。

    N-598C（`scripts/ai/resolve_current_state.py`）の Resolver Output Schema
    `checkpoint_fresh` フィールドが指す「checkpoint が mainline に対してどれだけ
    新鮮か」の意味論をここへ切り出す（PR #1330 Round 1 Copilot 指摘 対応）。
    `validate()` が呼ぶ他の検査（次タスク token 整合・task closeout sequencing・
    execution-ledger の stale heading）は freshness の範囲外として含めない。
    """
    issues: list[str] = []
    if pr_number is not None:
        pr_issue = check_pr_matches_merge_subject(pr_number, sha, repo_root=repo_root)
        if pr_issue is not None:
            issues.append(pr_issue)

    mainline_ref, mainline_issue = resolve_mainline_ref(repo_root=repo_root)
    if mainline_issue is not None:
        issues.append(mainline_issue)
    else:
        assert mainline_ref is not None
        ancestor_issue = check_ancestor(sha, repo_root=repo_root, target_ref=mainline_ref)
        if ancestor_issue is not None:
            issues.append(ancestor_issue)
        else:
            first_parent_issue = check_first_parent_mainline(
                sha, repo_root=repo_root, mainline_ref=mainline_ref
            )
            if first_parent_issue is not None:
                issues.append(first_parent_issue)
            else:
                issues.extend(
                    check_merge_freshness(
                        sha,
                        repo_root=repo_root,
                        mode=mode,
                        staleness_threshold=staleness_threshold,
                        mainline_ref=mainline_ref,
                    )
                )
    return issues


def validate(*, repo_root: Path, staleness_threshold: int, mode: str = DEFAULT_MODE) -> list[str]:
    """全検証を実行し、違反理由の一覧を返す（空リストなら pass）。"""
    issues: list[str] = []

    checkpoint_path = repo_root / CHECKPOINT_REL
    ledger_path = repo_root / LEDGER_REL
    plan_path = repo_root / PLAN_REL

    sha: str | None = None
    pr_number: str | None = None
    checkpoint_text: str | None = None
    if not checkpoint_path.exists():
        issues.append(f"{CHECKPOINT_REL} が存在しません")
    else:
        checkpoint_text = checkpoint_path.read_text(encoding="utf-8")
        sha = extract_checkpoint_sha(checkpoint_text)
        if sha is None:
            issues.append(
                f"{CHECKPOINT_REL} から merge SHA を抽出できません"
                "（`- merge SHA: <40hex>` 行が必要です）"
            )
        pr_number = extract_checkpoint_pr_number(checkpoint_text)
        if pr_number is None:
            issues.append(
                f"{CHECKPOINT_REL} から PR 番号を抽出できません（`- PR: <数字>` 行が必要です）"
            )

    if sha is not None:
        issues.extend(
            check_checkpoint_freshness(
                repo_root=repo_root,
                sha=sha,
                pr_number=pr_number,
                mode=mode,
                staleness_threshold=staleness_threshold,
            )
        )

    if checkpoint_text is not None:
        if not plan_path.exists():
            issues.append(f"{PLAN_REL} が存在しません")
        else:
            plan_text = plan_path.read_text(encoding="utf-8")
            next_task_issue = check_next_task_matches_plan(checkpoint_text, plan_text)
            if next_task_issue is not None:
                issues.append(next_task_issue)
            # AC-06 汎化 task closeout sequencing（T→S1→R→S2）検査。
            issues.extend(
                check_task_closeout_sequencing(
                    repo_root=repo_root,
                    plan_text=plan_text,
                    checkpoint_text=checkpoint_text,
                )
            )

    if not ledger_path.exists():
        issues.append(f"{LEDGER_REL} が存在しません")
    else:
        ledger_text = ledger_path.read_text(encoding="utf-8")
        stale_headings = find_stale_headings(ledger_text)
        if stale_headings:
            heading_list = "、".join(stale_headings)
            issues.append(
                f"{LEDGER_REL} に「最新」を名乗る現在状態見出しが"
                f"{len(stale_headings)} 件残っています: {heading_list}"
            )

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="リポジトリルート（既定はスクリプト位置から算出。fixture テストで override）",
    )
    parser.add_argument(
        "--staleness-threshold",
        type=int,
        default=DEFAULT_STALENESS_THRESHOLD,
        help=(
            f"state-neutral merge（transition mode）または state-changing merge"
            f"（report mode）に許容する件数の上限（既定 {DEFAULT_STALENESS_THRESHOLD}）"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=MODE_CHOICES,
        default=DEFAULT_MODE,
        help=(
            f"鮮度判定モード（既定 {DEFAULT_MODE!r}）。transition は未表現の"
            "state-changing merge を 0 件要求し、report は従来の bounded-staleness"
            "（state-changing merge のみを閾値と比較）を再現する。"
        ),
    )
    parser.add_argument(
        "--print-closeout-action",
        action="store_true",
        help=(
            "state=closing のとき resolve_closeout_action が導出した closeout_action.kind を"
            "stdout へ出す（task_closeout／control_unit のいずれの subject_kind にも対応）"
        ),
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    issues = validate(
        repo_root=repo_root,
        staleness_threshold=args.staleness_threshold,
        mode=args.mode,
    )

    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        print(f"current state NG（{len(issues)} 件）", file=sys.stderr)
        return 1

    if args.print_closeout_action:
        action, action_issues = resolve_closeout_action(repo_root=repo_root)
        if action_issues:
            for issue in action_issues:
                print(issue, file=sys.stderr)
            return 1
        print(f"closeout_action: {action if action is not None else 'null'}")

    print("current state OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
