#!/usr/bin/env python3
"""タスク完了ごとに docs/ai/task-checkpoint.md を上書き生成する CLI。

N-578 B13（セッション文脈継続機構 v1）の成果物。全ての文脈喪失イベント
（compact／セッション跨ぎ／モデル交代）の直後に自動再注入される、小さく
決定的なディスク正本を生成する。同一入力からは常にバイト同一の出力を
生成する（--timestamp を必須引数として受け取り、内部で現在時刻を
取得しない）。

N-598C AC-04（`docs/specs/N-598C-exact-current-state-resolver.md`）で
`checkpoint-next:v1`／`research-resume:v1`／（closing 時のみ）
`closeout-registration:v1` marker の決定的生成に対応した。生成値は
呼び出し側が明示的に渡す引数だけを正本とし（`docs/plan.md` や
`.github/full-plan-execution.flag` を本 script が自分で読みには行かない）、
`scripts/ai/resolve_current_state.py` の値をそのまま渡すことで
resolver と同じ正本を使う契約になる。

参照: docs/specs/N-578-backlog-batch-2026-07.md §B13+B14 /
docs/ai/decision-ledger.md DEC-20260707-004 /
ai/operation-policy.yml compact_survival_contract（v2）/
docs/specs/N-598C-exact-current-state-resolver.md AC-04。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import resolve_current_state as resolver  # noqa: E402
from scripts.ai import validate_current_state as current_state  # noqa: E402

DEFAULT_OUTPUT = ROOT / "docs" / "ai" / "task-checkpoint.md"
GENERATOR = "scripts/ai/write_task_checkpoint.py"
TERMINAL_NEXT_TASK_SENTINEL = "[TERMINAL] 自動実行対象なし"


def _render_marker_block(marker: str, obj: dict[str, Any]) -> list[str]:
    """`<!-- marker -->` ... `<!-- /marker -->` を決定的な YAML code fence として描画する。

    ``yaml.safe_dump`` は同一入力から常に同一 byte 列を返す（``sort_keys=False`` で
    dict の挿入順を保つため、呼び出し側の field 順が出力へそのまま反映される）。
    """
    body = yaml.safe_dump(obj, sort_keys=False, allow_unicode=True, default_flow_style=False)
    lines = [f"<!-- {marker} -->", "```yaml"]
    lines.extend(body.rstrip("\n").splitlines())
    lines.append("```")
    lines.append(f"<!-- /{marker} -->")
    return lines


def render_checkpoint(
    *,
    task: str,
    pr: str,
    merge_sha: str,
    next_task: str | None = None,
    next_task_id: str | None = None,
    next_substep: str | None = None,
    terminal: bool = False,
    research_resume: dict[str, Any] | None = None,
    closeout_registration: dict[str, Any] | None = None,
    learnings: list[str],
    timestamp: str,
) -> str:
    """チェックポイント本文（Markdown）を決定的に組み立てる。

    見出し・区切りは machine-readable かつ markdownlint clean な形式
    （見出し前後に空行）を保つ。marker は「## 正本ポインタ」節の後（ファイル末尾）へ
    置く。N-598C spec 67 行は「## 次タスク」直下への配置を指示するが、そのまま従うと
    N-598B の ``_terminal_checkpoint_section_is_exact``（terminal 時「## 次タスク」節が
    sentinel 1 行だけであることを要求する既存検査）が「## 次タスク」直下の marker
    block を余分な行として検出し fail してしまう（既存検査を弱めない制約と矛盾する）。
    現行 ``docs/ai/task-checkpoint.md`` も同じ理由でファイル末尾配置になっており、
    本実装はその実運用パターンに合わせる（spec とのズレは実装報告に明記する）。
    """
    if terminal and (next_task is not None or next_task_id is not None or next_substep is not None):
        raise ValueError(
            "terminal checkpoint に next_task/next_task_id/next_substep は指定できません"
        )
    if not terminal:
        if next_task is None or not next_task.strip():
            raise ValueError("非 terminal checkpoint には next_task が必要です")
        if next_task_id is None or not next_task_id.strip():
            raise ValueError(
                "非 terminal checkpoint には next_task_id が必要です（checkpoint-next:v1・AC-04）"
            )
        if next_substep is None or not next_substep.strip():
            raise ValueError(
                "非 terminal checkpoint には next_substep が必要です（checkpoint-next:v1・AC-04）"
            )

    checkpoint_next_obj: dict[str, Any]
    if terminal:
        checkpoint_next_obj = {
            "schema_version": 1,
            "task_id": None,
            "substep": None,
            "terminal": True,
        }
    else:
        checkpoint_next_obj = {
            "schema_version": 1,
            "task_id": next_task_id,
            "substep": next_substep,
        }
    next_obj_issues = resolver.validate_checkpoint_next_object(checkpoint_next_obj)
    if next_obj_issues:
        raise ValueError(
            f"checkpoint-next:v1 の生成結果が schema を満たしません: {next_obj_issues}"
        )

    research_resume_obj: dict[str, Any] | None = None
    if research_resume is not None:
        schema_issues = current_state.validate_research_resume_object(research_resume)
        if schema_issues:
            raise ValueError(f"research_resume が schema を満たしません: {schema_issues}")
        research_resume_obj = {"schema_version": 1, **research_resume}

    closeout_registration_obj: dict[str, Any] | None = None
    if closeout_registration is not None:
        if not isinstance(closeout_registration, dict):
            raise ValueError("closeout_registration は object である必要があります")
        missing = [
            field
            for field in current_state.MIRROR_REQUIRED_FIELDS
            if field not in closeout_registration
        ]
        if missing:
            raise ValueError(
                f"closeout_registration mirror に必須 field が不足しています: {missing}"
            )
        closeout_registration_obj = dict(closeout_registration)

    rendered_next_task = TERMINAL_NEXT_TASK_SENTINEL if terminal else next_task
    assert rendered_next_task is not None
    learning_lines = [f"- {item}" for item in learnings] if learnings else ["- （なし）"]

    lines: list[str] = [
        "# タスクチェックポイント（自動生成・上書き型）",
        "",
        f"> 生成: {timestamp} / 生成器: {GENERATOR}",
        "",
        "## 直前の完了",
        "",
        f"- タスク: {task}",
        f"- PR: {pr}",
        f"- merge SHA: {merge_sha}",
        "",
        "## 主要知見",
        "",
        *learning_lines,
        "",
        "## 次タスク",
        "",
        f"- {rendered_next_task}",
        "",
        "## 正本ポインタ（毎回ここから再接地する）",
        "",
        "- 実行状態: .github/full-plan-execution.flag（current_task/status/delivery）",
        "- 実行順: docs/plan.md「## Next」冒頭の最新優先順注記",
        "- 台帳: docs/ai/execution-ledger.md / docs/ai/decision-ledger.md",
        "- 役割: CLAUDE.md（Orchestrator として振る舞う）"
        "+ .claude/output-styles/orchestrator-behavior.md + ai/capability-registry.yml",
        "",
        *_render_marker_block("checkpoint-next:v1", checkpoint_next_obj),
        "",
    ]
    if research_resume_obj is not None:
        lines.extend(_render_marker_block("research-resume:v1", research_resume_obj))
        lines.append("")
    if closeout_registration_obj is not None:
        lines.extend(_render_marker_block("closeout-registration:v1", closeout_registration_obj))
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="タスク完了ごとに docs/ai/task-checkpoint.md を上書き生成する"
    )
    parser.add_argument("--task", required=True, help="完了タスク ID")
    parser.add_argument("--pr", required=True, help="PR 番号")
    parser.add_argument("--merge-sha", required=True, help="merge commit の SHA")
    next_mode = parser.add_mutually_exclusive_group(required=True)
    next_mode.add_argument("--next", dest="next_task", help="次タスク（表示用 bullet）")
    next_mode.add_argument(
        "--terminal",
        action="store_true",
        help="自動実行対象がなく、terminal sentinel を生成する",
    )
    parser.add_argument(
        "--next-task-id",
        default=None,
        help="checkpoint-next:v1.task_id（--terminal 以外で必須。plan の active-queue:v1 と同値）",
    )
    parser.add_argument(
        "--next-substep",
        default=None,
        help="checkpoint-next:v1.substep（--terminal 以外で必須。plan の active-queue:v1 と同値）",
    )
    parser.add_argument(
        "--research-resume-json",
        default=None,
        help=(
            "research-resume:v1 の 7 field（schema_version を除く）を持つ JSON object。"
            "省略時は marker を生成しない"
        ),
    )
    parser.add_argument(
        "--closeout-registration-json",
        default=None,
        help=(
            "closeout-registration:v1 mirror（少なくとも event_id/event_path/event_sha256/"
            "required_evidence_roots_sha256/required_closure_claim_ids_sha256）を持つ"
            " JSON object。closing checkpoint のときだけ指定する"
        ),
    )
    parser.add_argument(
        "--learnings",
        action="append",
        default=[],
        help="主要知見（複数指定可・指定順に列挙）",
    )
    parser.add_argument(
        "--timestamp",
        required=True,
        help="ISO 8601 形式のタイムスタンプ（決定性のため内部で now() を呼ばない）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="出力先パス（既定: docs/ai/task-checkpoint.md）",
    )
    args = parser.parse_args()

    research_resume: dict[str, Any] | None = None
    if args.research_resume_json is not None:
        try:
            research_resume = json.loads(args.research_resume_json)
        except json.JSONDecodeError as exc:
            print(f"--research-resume-json を解析できません: {exc}", file=sys.stderr)
            return 1

    closeout_registration: dict[str, Any] | None = None
    if args.closeout_registration_json is not None:
        try:
            closeout_registration = json.loads(args.closeout_registration_json)
        except json.JSONDecodeError as exc:
            print(f"--closeout-registration-json を解析できません: {exc}", file=sys.stderr)
            return 1

    try:
        content = render_checkpoint(
            task=args.task,
            pr=args.pr,
            merge_sha=args.merge_sha,
            next_task=args.next_task,
            next_task_id=args.next_task_id,
            next_substep=args.next_substep,
            terminal=args.terminal,
            research_resume=research_resume,
            closeout_registration=closeout_registration,
            learnings=list(args.learnings),
            timestamp=args.timestamp,
        )
    except ValueError as exc:
        print(f"checkpoint 生成に失敗しました: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
