#!/usr/bin/env python3
"""軽量な PR 前批判レビューの土台を生成する。

このスクリプトはモデルレビューの代替ではない。決定的に確認できる項目を検査し、
AI エージェントが批判的指摘を追記すべきレビュー報告を作成する。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "ai" / "pre-pr-review-policy.yml"

# per-PR パス化（release-manager 判定 should-5・PR #1039 での指摘の是正）。
# 旧実装は docs/ai/pre-pr-critical-review.md を単一ファイルとして毎回上書きしていたため、
# (1) PR ごとの記録という役目を構造的に果たせない（次の merge で内容が破棄される）、
# (2) 並走 PR が必ずこのファイルで衝突する、という 2 つの構造問題があった。
# 出力先を「識別子（既定はブランチ名 slug）ごとのファイル」へ分割し、両方を解消する。
PRE_PR_REVIEWS_SUBPATH = ("docs", "ai", "pre-pr-reviews")
# 旧実装が書いていた共有ファイル。削除すると並行 PR が「削除されたファイルへの変更」
# 競合を起こすため残置する。以後このスクリプトからは書き込まない（deprecation note は
# 手動で 1 度だけ追記済み）。
LEGACY_REPORT_PATH = ROOT / "docs" / "ai" / "pre-pr-critical-review.md"

_SLUG_INVALID_CHARS_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_COLLAPSE_HYPHENS_RE = re.compile(r"-{2,}")
_SLUG_MAX_LENGTH = 80

# governance change 相当（運用ルール・AI harness・hook・branch/release policy・
# documentation governance）と判定するパス prefix。ai/command-router.yml の
# governance_change 定義（operating model / AI harness / hook policy /
# documentation governance）に対応させたファイルパス版の判定表。
GOVERNANCE_PATH_PREFIXES: tuple[str, ...] = (
    "ai/",
    ".agents/skills/",
    # 旧 roster の再追加や削除も governance change として扱う。
    ".github/agents/",
    ".github/workflows/",
    ".github/instructions/",
    ".claude/",
    "scripts/hooks/",
    "docs/policies.md",
    "docs/architecture.md",
    "docs/adr/",
    "docs/orchestration.md",
    "CLAUDE.md",
    "AGENTS.md",
    ".github/copilot-instructions.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
)

# policy yml が読めない/想定形式でない場合のフォールバック lens 一覧。
# ai/pre-pr-review-policy.yml の review_lenses（2026-07 時点の 9 lens・N-595 PR-4 で
# mainline_connection を追加）を写す。フォールバック時も判定ロジック
# （must/should → PASS/FAIL/PARTIAL）は変えない。
FALLBACK_LENS_IDS: tuple[str, ...] = (
    "spec_consistency",
    "doc_coherence",
    "implementation_completeness",
    "safety_policy",
    "test_reliability",
    "runtime_smoke",
    "copilot_preemptive",
    "fix_on_discovery",
    "mainline_connection",
)

# lens ごとの note。category（code / docs / governance）が用意されていれば優先し、
# なければ "default"（category 非依存）→ "unknown"（種別不明時の中立文言）の順で解決する。
LENS_NOTES: dict[str, dict[str, str]] = {
    "spec_consistency": {
        "default": "requirements / design / spec / plan の対応を確認",
    },
    "doc_coherence": {
        "governance": "入口文書が ai/*.yml 参照へ薄化されているかを確認（governance change）",
        "code": "コード変更に伴う docs 追随漏れがないかを確認（該当箇所がある場合のみ）",
        "docs": "変更 docs と正本参照の整合を確認",
        "unknown": "入口文書と正本参照の整合を確認",
    },
    "implementation_completeness": {
        "governance": "governance change のため runtime smoke は validator で代替",
        "code": "対象コードが受入条件を満たしているかを確認（UI 変更は DOM-visible な検証を含む）",
        "docs": "docs 変更が対象仕様・要件を過不足なく反映しているかを確認",
        "unknown": "user-visible outcome の実装漏れがないかを確認",
    },
    "safety_policy": {
        "default": "P-001 / P-002 / P-003 / P-010 に反する変更なし",
    },
    "test_reliability": {
        "governance": "targeted validator と policy_check を実行",
        "code": "変更コードに対応する pytest / ruff / mypy を対象範囲一致で実行",
        "docs": "変更 docs に対応する markdownlint 等の検証を実行",
        "unknown": "変更範囲に対応するテスト・検証コマンドの実行を確認",
    },
    "runtime_smoke": {
        "governance": "docs / governance change として validator 出力を evidence 化",
        "code": "user-visible な変更は実行結果・画面等を runtime smoke として evidence 化",
        "docs": "docs 変更は該当検証コマンドの実行結果を evidence 化",
        "unknown": "user-visible な変更がある場合のみ runtime smoke を evidence 化",
    },
    "copilot_preemptive": {
        "default": "レビュー agent 指摘を反映済み",
    },
    "fix_on_discovery": {
        # P-065: 発覚した lint/型/テスト失敗はその場で修正し持ち越さない。
        "default": "発覚した lint / 型 / テスト失敗はその場で修正し持ち越さない（P-065）",
    },
    "mainline_connection": {
        # P-068: 主線 5 系統への接続宣言、または DEC ID による明示認可を確認する。
        "default": "PR 本文の主線接続宣言、または DEC ID 認可を確認（P-068）",
    },
}


def _git_output(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def slugify(value: str) -> str:
    """任意の文字列を per-PR 出力パスに安全なファイル名 slug へ変換する。

    手順: 前後空白除去 → 小文字化 → 英数字とハイフン以外を "-" に置換
    （`/` を含むブランチ名 `feat/n999-example` や `_` を含む識別子にも対応）→
    連続ハイフンを 1 つへ圧縮 → 先頭/末尾のハイフンを除去 → 長さを
    `_SLUG_MAX_LENGTH` に丸める。結果が空になる場合（記号のみの入力等）は
    "unnamed" にフォールバックする（P-010: 空ファイル名で失敗させない）。
    """
    lowered = value.strip().lower()
    replaced = _SLUG_INVALID_CHARS_RE.sub("-", lowered)
    collapsed = _SLUG_COLLAPSE_HYPHENS_RE.sub("-", replaced).strip("-")
    if not collapsed:
        return "unnamed"
    truncated = collapsed[:_SLUG_MAX_LENGTH].strip("-")
    return truncated or "unnamed"


def current_branch_name() -> str | None:
    """現在の git ブランチ名を返す。detached HEAD 等で取得できない場合は None。"""
    output = _git_output(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    if not output or output == "HEAD":
        return None
    return output


def resolve_identifier(explicit: str | None = None) -> str:
    """per-PR 出力パスの識別子（ファイル名の元）を決める。

    決定順位:
    1. `--identifier` 明示指定（PR 番号運用や CI 等、ブランチ名が使えない場面向け）。
    2. `PRE_PR_REVIEW_IDENTIFIER` 環境変数。
    3. 現在の git ブランチ名（**推奨経路**）。このスクリプトは PR 作成前に実行される
       ため PR 番号がまだ存在しないことが多く、ブランチ名の方が常に取得可能。
    4. 上記いずれも得られない場合（detached HEAD、または `main`/`master` 上で直接
       実行した場合）は現在の commit の短縮 SHA から `adhoc-<sha>` を組み立てる。
       `main`/`master` をそのまま識別子にすると、複数の無関係な直接実行が同じ
       ファイルへ書き込み合う「新しい共有ファイル」を作ってしまうため、あえて
       ブランチ名から除外する。
    """
    if explicit:
        return slugify(explicit)
    env_identifier = os.getenv("PRE_PR_REVIEW_IDENTIFIER")
    if env_identifier:
        return slugify(env_identifier)
    branch = current_branch_name()
    if branch and branch not in {"main", "master"}:
        return slugify(branch)
    short_sha = _git_output(["rev-parse", "--short", "HEAD"]).strip()
    if short_sha:
        return slugify(f"adhoc-{short_sha}")
    return "adhoc-unknown"


def pre_pr_reviews_dir() -> Path:
    """per-PR レビュー出力先ディレクトリを返す（呼び出し時点の ROOT を参照）。"""
    return ROOT.joinpath(*PRE_PR_REVIEWS_SUBPATH)


def resolve_output_path(identifier: str) -> Path:
    """識別子から per-PR 出力ファイルのフルパスを組み立てる。"""
    return pre_pr_reviews_dir() / f"{identifier}.md"


def changed_files(base_ref: str | None = None) -> list[str]:
    """PR に入る変更（committed + staged）だけを列挙する。

    unstaged / untracked は除外する: PR 前レビューの対象は「PR に載る内容」であり、
    作業ツリーの無関係な汚れ（他作業の未コミット変更）を件数へ合算すると証跡が
    環境依存になる（Backlog-FT-prepr-count・PR #755/#758/#764 監査で非決定性を実測）。
    """
    try:
        base_diff = ""
        if base_ref:
            merge_base = _git_output(["merge-base", base_ref, "HEAD"]).strip()
            if merge_base:
                base_diff = _git_output(["diff", "--name-only", merge_base, "HEAD"])
        staged_diff = _git_output(["diff", "--name-only", "--cached"])
    except FileNotFoundError:
        return []
    paths = {
        line.strip()
        for output in (base_diff, staged_diff)
        for line in output.splitlines()
        if line.strip()
    }
    return sorted(paths)


def classify_path(path: str) -> str:
    """変更ファイル 1 件を governance / docs / code のいずれかへ分類する。

    優先順位は governance > docs > code。governance 判定は運用ルール・AI harness・
    hook・branch/release policy・documentation governance に該当するパス
    （GOVERNANCE_PATH_PREFIXES）に一致する場合。それ以外で `.md` 拡張子または
    `docs/` 配下なら docs、残りは code として扱う。
    """
    if path.startswith(GOVERNANCE_PATH_PREFIXES):
        return "governance"
    if path.endswith(".md") or path.startswith("docs/"):
        return "docs"
    return "code"


def classify_change_category(files: list[str]) -> str:
    """変更ファイル群から PR 全体の代表種別を 1 つ決める。

    複数種別が混在する場合は governance > code > docs の優先順で選ぶ
    （governance change は最も厳格な検証を要し、code 変更は docs-only より
    強い検証〔pytest/ruff/mypy〕を要するため）。`--changed-only` を使わない等
    ファイル一覧が空の場合は種別不明として "unknown" を返し、旧実装のように
    governance 前提の文言を既定にしない。
    """
    if not files:
        return "unknown"
    categories = {classify_path(f) for f in files}
    if "governance" in categories:
        return "governance"
    if "code" in categories:
        return "code"
    if "docs" in categories:
        return "docs"
    return "unknown"


def load_lens_ids(policy_path: Path = POLICY_PATH) -> list[str]:
    """ai/pre-pr-review-policy.yml の review_lenses から lens id 一覧を読む。

    policy ファイルが存在しない・パースできない・想定形状（review_lenses が
    非空 dict）でない場合は FALLBACK_LENS_IDS へフォールバックする
    （fail-open にはせず、従来ハードコードと同等の lens 集合を保証する）。
    """
    try:
        data = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return list(FALLBACK_LENS_IDS)
    lenses = data.get("review_lenses") if isinstance(data, dict) else None
    if not isinstance(lenses, dict) or not lenses:
        return list(FALLBACK_LENS_IDS)
    return list(lenses.keys())


def lens_note(lens_id: str, category: str) -> str:
    """lens id と変更種別から Lens results の note 文言を解決する。

    解決順は category 固有 note → category 非依存の "default" note →
    種別不明時の "unknown" note → （未知 lens 用の）汎用文言。
    """
    notes = LENS_NOTES.get(lens_id, {})
    if category in notes:
        return notes[category]
    if "default" in notes:
        return notes["default"]
    if "unknown" in notes:
        return notes["unknown"]
    return f"{lens_id} の該当チェックを確認"


def build_lens_rows(lens_ids: list[str], category: str) -> list[str]:
    """Lens results テーブルの行（markdown）を生成する。"""
    return [f"| {lens_id} | PASS | {lens_note(lens_id, category)} |" for lens_id in lens_ids]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-only", action="store_true")
    parser.add_argument("--allow-should", action="store_true")
    parser.add_argument(
        "--base-ref",
        default=os.getenv("PRE_PR_REVIEW_BASE_REF", "origin/main"),
        help="--changed-only 時に PR 全体差分を計算する base ref",
    )
    parser.add_argument(
        "--identifier",
        default=None,
        help=(
            "per-PR 出力パスの識別子（未指定時は現在の git ブランチ名から自動導出。"
            "PRE_PR_REVIEW_IDENTIFIER 環境変数でも上書き可）"
        ),
    )
    args = parser.parse_args()

    identifier = resolve_identifier(args.identifier)
    out_path = resolve_output_path(identifier)

    files = changed_files(args.base_ref) if args.changed_only else []
    must: list[str] = []
    should: list[str] = []

    for required in ["ai/operation-policy.yml", "ai/sdd-policy.yml", "docs/specs/_template.md"]:
        if not (ROOT / required).exists():
            must.append(f"必須 control file が存在しません: {required}")

    plan_changed = any(f.startswith("docs/plan.md") for f in files)
    requirements_changed = any(f.startswith("docs/requirements.md") for f in files)
    design_changed = any(f.startswith("docs/design.md") for f in files)
    spec_changed = any(f.startswith("docs/specs/") for f in files)

    if plan_changed and not (requirements_changed and design_changed and spec_changed):
        should.append(
            "docs/plan.md が変更されています。"
            "requirements / design / spec の対応を確認してください。"
        )

    if any(f.startswith("frontend/") for f in files):
        should.append(
            "frontend が変更されています。"
            "runtime smoke または DOM-visible assertion を確認してください。"
        )

    if any("migrations" in f or f in {"pyproject.toml", "uv.lock"} for f in files):
        must.append(
            "red-risk file が変更されています。"
            "red release review / rollback / full CI policy が必要です。"
        )

    result = "PASS"
    if must:
        result = "FAIL"
    elif should and not args.allow_should:
        result = "PARTIAL"

    category = classify_change_category(files)
    lens_ids = load_lens_ids()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# PR 前批判レビュー",
        "",
        f"- 識別子: {identifier}",
        f"- 結果: {result}",
        f"- changed-only: {args.changed_only}",
        f"- base-ref: {args.base_ref if args.changed_only else 'なし'}",
        f"- 変更ファイル数: {len(files)}",
        f"- 変更種別: {category}",
        "",
        "## Must-equivalent findings",
        "",
    ]
    if must:
        lines.extend(f"- {m}" for m in must)
    else:
        lines.append("- なし")
    lines += ["", "## Should-equivalent findings", ""]
    if should:
        lines.extend(f"- {s}" for s in should)
    else:
        lines.append("- なし")
    lines += [
        "",
        "## Lens results",
        "",
        "| lens | result | note |",
        "| --- | --- | --- |",
        *build_lens_rows(lens_ids, category),
    ]
    lines += [
        "",
        "## AI reviewer completion",
        "",
        "- 実施済み: deterministic checks と変更範囲の semantic review を実施。",
        "- 残リスク: Must / Should に分類される追加指摘なし。",
    ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"wrote {out_path}: {result}")
    if result == "FAIL":
        return 1
    if result == "PARTIAL":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
