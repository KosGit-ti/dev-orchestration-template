#!/usr/bin/env python3
"""N-58x ↔ FR-171-x ↔ spec 実在 ↔ plan 在圏の機械照合（N-595・FR-171-11 AC-07）。

`docs/audits/audit-materialization-report-2026-07-24.md` の「Assigned repository
IDs」表・`docs/requirements.md` の FR-171 傘要件・`docs/plan.md` を突き合わせ、
次の 4 種を照合する（spec `docs/specs/N-595-scope-doc-governance.md` §10 AC-07）。

  (a) N-585〜N-596 各行に対応する ``docs/specs/N-<id>-*.md`` の実在
  (b) 各 spec が宣言する ``FR-171-x`` の ``docs/requirements.md`` 在圏
  (c) 各 N-58x の ``docs/plan.md`` 在圏
  (d) ``docs/plan.md`` の「最上位正本」出現が 1 以下

加えて spec §10 AC-04(b) の非退行ガード（ratchet 方式）を同一 exit code へ配線する
（release-manager should-2 是正・2026-07-25）: ``docs/plan.md`` の実バイト数が
``docs/ai/plan-md-size-baseline.json`` の baseline を超えたら fail とする。

整合時は exit 0、不一致（照合不能を含む）は差分列挙 + exit 1（P-010 fail-close。
「見つからない＝pass」にしない）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MATERIALIZATION_REPORT = "docs/audits/audit-materialization-report-2026-07-24.md"
REQUIREMENTS = "docs/requirements.md"
PLAN = "docs/plan.md"
SPECS_DIR = "docs/specs"
PLAN_SIZE_BASELINE = "docs/ai/plan-md-size-baseline.json"

# spec §10 AC-07(a) が明示する対象範囲（N-584 は umbrella のため対象外）。
# 番号は materialization report の「Assigned repository IDs」表と 1:1 対応する。
TARGET_IDS = [f"N-58{i}" for i in range(5, 10)] + [f"N-59{i}" for i in range(0, 7)]

TOP_AUTHORITY_MARKER = "最上位正本"
FR171_TOKEN_RE = re.compile(r"FR-171-\d+")


def _read(root: Path, rel: str) -> str | None:
    """root 相対パスのテキストを返す。存在しない場合は None（fail-close の入口）。"""
    path = root / rel
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _parse_assigned_id_table(text: str) -> list[dict[str, str]]:
    """materialization report「Assigned repository IDs」節の markdown 表を行辞書へ変換する。"""
    marker = text.find("## Assigned repository IDs")
    if marker < 0:
        return []
    next_marker = text.find("\n## ", marker + len("## Assigned repository IDs"))
    section = text[marker:next_marker] if next_marker >= 0 else text[marker:]
    rows: list[dict[str, str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4:
            continue
        if cells[0] == "Audit work package" or set(cells[0]) <= {"-"}:
            continue  # ヘッダ行・区切り行をスキップ
        rows.append(
            {
                "work_package": cells[0],
                "finding": cells[1],
                "task": cells[2],
                "spec": cells[3],
            }
        )
    return rows


def check_spec_files_exist(root: Path) -> list[str]:
    """AC-07(a): N-585〜N-596 各行に対応する spec ファイルの実在照合。"""
    report_text = _read(root, MATERIALIZATION_REPORT)
    if report_text is None:
        return [f"missing {MATERIALIZATION_REPORT}"]
    rows = _parse_assigned_id_table(report_text)
    if not rows:
        return [f"{MATERIALIZATION_REPORT} に Assigned repository IDs 表が見つかりません"]

    issues: list[str] = []
    seen_ids: set[str] = set()
    for row in rows:
        task = row["task"]
        if task not in TARGET_IDS:
            continue
        seen_ids.add(task)
        spec_match = re.search(r"`([^`]+\.md)`", row["spec"])
        if not spec_match:
            issues.append(
                f"trace(a):{task}: Spec 列がファイルパス形式ではありません: {row['spec']!r}"
            )
            continue
        spec_rel = spec_match.group(1)
        if not (root / spec_rel).exists():
            issues.append(f"trace(a):{task}: spec ファイルが存在しません: {spec_rel}")

    missing_rows = sorted(set(TARGET_IDS) - seen_ids)
    if missing_rows:
        issues.append(
            "trace(a): Assigned repository IDs 表に行がありません: " + ", ".join(missing_rows)
        )
    return issues


def check_fr171_declared(root: Path) -> list[str]:
    """AC-07(b): 各 spec が宣言する FR-171-x の docs/requirements.md 在圏照合。

    spec の「正本:」冒頭行（`## 2.` 節より前）に FR-171-x を記載する運用が
    N-585〜N-596 全 spec で定着しているため、`## 2.` 節に限定せず spec 全文から
    FR-171-x トークンを収集する（見落としを避ける fail-close 側の設計）。
    """
    requirements_text = _read(root, REQUIREMENTS)
    if requirements_text is None:
        return [f"missing {REQUIREMENTS}"]
    specs_dir = root / SPECS_DIR

    issues: list[str] = []
    for task_id in TARGET_IDS:
        matches = sorted(specs_dir.glob(f"{task_id}-*.md")) if specs_dir.exists() else []
        if not matches:
            continue  # spec 不在は check_spec_files_exist（trace(a)）が報告する
        spec_path = matches[0]
        tokens = sorted(set(FR171_TOKEN_RE.findall(spec_path.read_text(encoding="utf-8"))))
        if not tokens:
            issues.append(
                f"trace(b):{task_id}: spec が FR-171-x を宣言していません（{spec_path.name}）"
            )
            continue
        for token in tokens:
            # 単純な部分文字列一致（`token not in requirements_text`）だと、例えば
            # `FR-171-1` が欠落していても `FR-171-10`/`FR-171-11`/`FR-171-12` が
            # 在圏していれば部分文字列として誤って一致し fail-close が破れる
            # （Copilot レビュー指摘・実測: requirements.md は FR-171-1〜12 を
            # 保有し `FR-171-1` は `FR-171-10` の接頭辞として実際に衝突する）。
            # 単語境界付き正規表現で厳密一致にする（check_plan_in_scope と同型の対策）。
            if not re.search(rf"\b{re.escape(token)}\b", requirements_text):
                issues.append(
                    f"trace(b):{task_id}: {token} が {REQUIREMENTS} に在圏しません"
                    f"（{spec_path.name}）"
                )
    return issues


def check_plan_in_scope(root: Path) -> list[str]:
    """AC-07(c): 各 N-58x の docs/plan.md 在圏照合。"""
    plan_text = _read(root, PLAN)
    if plan_text is None:
        return [f"missing {PLAN}"]

    issues: list[str] = []
    for task_id in TARGET_IDS:
        if not re.search(rf"\b{re.escape(task_id)}\b", plan_text):
            issues.append(f"trace(c):{task_id}: {PLAN} に在圏しません")
    return issues


def check_top_authority_singleton(root: Path) -> list[str]:
    """AC-07(d): docs/plan.md の「最上位正本」出現が 1 以下であることの照合。"""
    plan_text = _read(root, PLAN)
    if plan_text is None:
        return [f"missing {PLAN}"]

    count = plan_text.count(TOP_AUTHORITY_MARKER)
    if count > 1:
        return [
            f"trace(d): {PLAN} に「{TOP_AUTHORITY_MARKER}」が {count} 箇所あります"
            "（1 以下が期待値）"
        ]
    return []


def check_plan_size_nonregression(root: Path) -> list[str]:
    """spec §10 AC-04(b) の非退行ガード（ratchet 方式・release-manager should-2 是正）。

    baseline は ``docs/ai/plan-md-size-baseline.json`` にコミットされた値を正本とする。
    plan.md の実バイト数が baseline を超えたら fail とし、エラーメッセージで次の
    2 択を明示する: (1) 完了済み履歴を docs/archive/ へ移設して baseline 以下へ戻す、
    (2) 同一 PR 内で baseline_bytes を意図的に引き上げ、reason へ理由を記載する。
    baseline の引き上げは diff に必ず現れるため、成長が「意識的で、レビュー可能な
    行為」になる（Backlog-N595-nonregression-scope への回答）。

    絶対閾値ではなく相対制約にすることで、plan.md へ新規タスクを登記する正当な
    PR を一律で落とさない（spec §10 AC-04(b) の設計意図）。
    """
    baseline_path = root / PLAN_SIZE_BASELINE
    if not baseline_path.exists():
        return [f"missing {PLAN_SIZE_BASELINE}"]
    plan_path = root / PLAN
    if not plan_path.exists():
        return [f"missing {PLAN}"]

    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{PLAN_SIZE_BASELINE} が有効な JSON ではありません: {exc}"]
    if not isinstance(baseline, dict) or "baseline_bytes" not in baseline:
        return [f"{PLAN_SIZE_BASELINE} に baseline_bytes がありません"]
    try:
        baseline_bytes = int(baseline["baseline_bytes"])
    except (TypeError, ValueError):
        return [f"{PLAN_SIZE_BASELINE} の baseline_bytes が整数ではありません"]

    # PR 本文・docstring は「baseline 引き上げ時は reason フィールドへの理由記載を
    # 必須にする」と宣言しているが、従来は reason の存在/非空を検証しておらず
    # 宣言が実効性を持っていなかった（Copilot レビュー指摘）。reason は常に
    # 非空文字列であることを fail-close で強制する。
    reason = baseline.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return [f"{PLAN_SIZE_BASELINE} の reason が空です（baseline 変更理由の記載が必須です）"]

    current_bytes = len(plan_path.read_bytes())
    if current_bytes > baseline_bytes:
        return [
            "trace(nonregression): docs/plan.md が baseline を超えています "
            f"（現在 {current_bytes} bytes > baseline {baseline_bytes} bytes）。"
            "次のいずれかで対処してください: "
            "(1) 完了済み履歴を docs/archive/ へ移設して baseline 以下へ戻す、"
            f"(2) 同一 PR 内で {PLAN_SIZE_BASELINE} の baseline_bytes を意図的に"
            "引き上げ、reason フィールドへ引き上げ理由を記載する。"
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="リポジトリルート（既定はスクリプト位置から算出。fixture テストで override）",
    )
    args = parser.parse_args()
    root: Path = args.root

    issues: list[str] = []
    issues.extend(check_spec_files_exist(root))
    issues.extend(check_fr171_declared(root))
    issues.extend(check_plan_in_scope(root))
    issues.extend(check_top_authority_singleton(root))
    issues.extend(check_plan_size_nonregression(root))

    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        print(f"traceability NG（{len(issues)} 件）", file=sys.stderr)
        return 1

    print("traceability ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
