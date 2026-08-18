#!/usr/bin/env python3
"""リポジトリ文書の軽量な棚卸しを生成する。"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "ai" / "document-inventory.md"

PATTERNS = [
    "AGENTS.md",
    "CLAUDE.md",
    ".github/**/*.md",
    ".claude/**/*.md",
    ".agents/**/*.md",
    "docs/**/*.md",
    "docs/audits/*.json",
    "docs/audits/*.yml",
    "ai/**/*.yml",
    ".shogun/**/*.md",
]


def classify(path: Path) -> tuple[str, str, str, bool]:
    s = path.as_posix()
    if s.startswith("ai/"):
        return "CORE_CONTROL", "keep", "AI 制御面", True
    # Shogun 運用モデル（N-435・FR-159/ADR-0018）。正本 2 本は ai/context-index.yml の
    # shogun_dispatch モードで required 指定（第 4 要素の bool は「毎セッション既定読込
    # （read_by_default）」の意であり、モード required とは別概念のため False）。
    if s == "docs/ai/shogun-operating-model.md":
        return "DOMAIN_SSOT", "keep", "Shogun 運用モデル正本（shogun_dispatch モードで読込）", False
    if s == "docs/ai/shogun-safety-boundary.md":
        return (
            "DOMAIN_SSOT",
            "keep",
            "Shogun 安全境界正本（既存安全床への参照のみ・shogun_dispatch モードで読込）",
            False,
        )
    if s == "docs/ai/shogun-multi-session-protocol.md":
        return (
            "DOMAIN_SSOT",
            "keep",
            "Shogun 複数セッション層プロトコル正本（任意層・単一セッションでは不使用）",
            False,
        )
    if s == "docs/ai/shogun-dispatch-dryrun-evidence.md":
        return "REFERENCE", "keep", "N-435a 受入証跡（FR-159 AC-05）", False
    if s.startswith("docs/ai/fable-transition-"):
        return (
            "REFERENCE",
            "keep",
            "N-613 以前の Fable 移行履歴（active policy ではない）",
            False,
        )
    if s.startswith(".claude/output-styles/"):
        return "AGENT_SPECIFIC", "keep", "行動仕様の Claude Code 写像（output style）", False
    # N-613: provider-neutral な共通 skill と Claude Code の薄い adapter を正本化する。
    if s.startswith(".agents/skills/"):
        return "AGENT_SPECIFIC", "keep", "provider-neutral skill（共通正本）", False
    if s.startswith(".claude/skills/"):
        return "AGENT_SPECIFIC", "keep", "Claude Code skill adapter", False
    if s.startswith(".shogun/"):
        return "REFERENCE", "keep", "Shogun skeleton（README・雛形のみコミット）", False
    if s in {
        "AGENTS.md",
        "CLAUDE.md",
        ".github/copilot-instructions.md",
        ".github/instructions/review-loop.instructions.md",
    }:
        return "AGENT_SPECIFIC", "revise", "ツール別入口。薄く保つ", True
    if s.startswith("docs/audits/audit-materialization-report-"):
        return "REFERENCE", "keep", "監査項目の割当・状態照合証跡", False
    if s.startswith("docs/audits/audit-materialization-manifest-"):
        return "REFERENCE", "keep", "監査claimと要件・AC・証跡の機械可読追跡正本", False
    if s.startswith("docs/audits/oae-phase-unit-matrix-"):
        return "REFERENCE", "keep", "OAE 監査項目の機械可読な Phase-unit 行列", False
    if s.startswith("docs/specs/"):
        return "TASK_SPEC", "keep", "タスク単位の仕様", False
    if s.startswith("docs/archive/"):
        return "ARCHIVE", "keep", "履歴アーカイブ", False
    if s.startswith("docs/research/") or s.startswith("docs/adr/"):
        return "REFERENCE", "keep", "参照資料", False
    if s in {
        "docs/requirements.md",
        "docs/design.md",
        "docs/policies.md",
        "docs/constraints.md",
        "docs/architecture.md",
        "docs/runbook.md",
        "docs/plan.md",
    }:
        return "DOMAIN_SSOT", "keep", "ドメイン正本", False
    if "prompt" in s.lower() or s.startswith("prompts/"):
        return "ARCHIVE", "move_or_keep_out_of_default_context", "一回限りプロンプト素材", False
    return "REFERENCE", "review", "手動または AI による分類精査が必要", False


def render_inventory() -> tuple[str, int]:
    """現在のファイル集合から deterministic な棚卸し本文を作る。"""
    paths: set[Path] = set()
    for pattern in PATTERNS:
        # `.claude/worktrees/` はサブエージェント用の untracked な nested worktree。
        # 走査すると環境依存の行が混入して出力が再現不能になるため除外する
        paths.update(
            p
            for p in ROOT.glob(pattern)
            if p.is_file() and ".claude/worktrees/" not in p.relative_to(ROOT).as_posix()
        )
    # 初回生成時にも生成物自身を行へ含め、2 回目の実行で行数が変わらないようにする。
    paths.add(OUT)
    rows = []
    for p in sorted(paths):
        rel = p.relative_to(ROOT)
        classification, action, reason, read = classify(rel)
        rows.append((rel.as_posix(), classification, action, reason, "yes" if read else "no"))

    lines = [
        "# 文書棚卸し",
        "",
        "`scripts/ai/audit_document_inventory.py` により生成。",
        "",
        "| path | classification | action | reason | read_by_default |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows)
    return "\n".join(lines) + "\n", len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="リポジトリ文書の棚卸しを生成する")
    parser.add_argument(
        "--check",
        action="store_true",
        help="現在の棚卸しと生成結果を比較し、書き換えずに drift を検出する",
    )
    args = parser.parse_args(argv)
    content, row_count = render_inventory()
    if args.check:
        try:
            current = OUT.read_text(encoding="utf-8")
        except OSError:
            print(f"document inventory is missing: {OUT}")
            return 1
        if current != content:
            print(f"document inventory is stale: {OUT}")
            return 1
        print(f"document inventory is current: {OUT} ({row_count} rows)")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8")
    print(f"wrote {OUT} ({row_count} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
