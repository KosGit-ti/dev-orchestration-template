#!/usr/bin/env python3
"""requirements / design / spec / plan / ADR 間の coherence drift 検出（N-439・FR-159 AC-11）。

`scripts/validate_doc_linkage.py`（B-373・advisory + baseline 方式）と同方式の
local advisory チェッカー。初期セットとして次の 4 種の drift を検出する。

1. fr-not-in-design: requirements の FR/NFR 見出しが design から参照されない
2. design-task-unmapped: design のタスク節（N-XXX / B-XXX）が plan に言及されず spec も無い
3. superseded-adr-ref: supersede 済 ADR への現役文書（requirements / design / specs）からの参照
4. spec-missing-path: spec の Verification Commands が参照するリポジトリパスの不存在

設計判断（spec AC-N439-02）: 既定は advisory（警告を stderr に報告して exit 0）。
baseline（既知 gap の許容リスト）に無い新規 drift と陳腐化 baseline は --strict での
opt-in でのみ exit 1 にする。CI / hook には接続しない（warning-only）。
検知→自動修復（Coherence Engine 相当）は実装しない（N-440 で Backlog 採番のみ）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = "docs/ai/coherence-drift-baseline.txt"

FR_HEADING_RE = re.compile(r"^#{1,6}\s+((?:FR|NFR)-\d+)\b", re.MULTILINE)
TASK_ID_RE = re.compile(r"\b([NB]-\d{3})\b")
# タスク節は ## / ### だけでなく #### 以深のサブ節見出しにも現れる
# （例: design.md の「#### N-561 設計：…」）ため見出しレベル 2〜6 を対象にする
# （Backlog-N439-drift-detection-coverage ④）
DESIGN_HEADING_RE = re.compile(r"^#{2,6}\s+(.+)$", re.MULTILINE)
SUPERSEDED_ROW_RE = re.compile(r"\[(ADR-\d{4})\][^|]*\|[^|]*\|[^|]*Superseded", re.IGNORECASE)
BASH_BLOCK_RE = re.compile(r"^[ \t]*```bash[ \t]*\r?\n(.*?)^[ \t]*```", re.DOTALL | re.MULTILINE)
# glob は drift 対象外のため文字クラスに * を含めない（含めて後段除外する曖昧さを排除）
PATH_TOKEN_RE = re.compile(r"(?<![\w$])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+)")
# ルート直下のファイル参照（python missing_tool.py / cat pyproject.toml 等・拡張子つき）。
# 拡張子なしの慣用ファイル名（Dockerfile / Makefile）は別枝で明示 allowlist にする
# （任意の単語をパス扱いすると誤検知が爆発するため・Backlog-N439-drift-detection-coverage ①）
BARE_FILE_RE = re.compile(
    r"(?<![\w$/.-])("
    r"[A-Za-z0-9_-]+\.(?:py|md|yml|yaml|toml|json|txt|sh|db|sql|cfg|ini)"
    # 派生名（Dockerfile.prod 等）の部分マッチで存在チェックがすり替わるのを防ぐため
    # 後続にファイル名構成文字（. 含む）が来る場合はマッチさせない（Copilot R1 指摘）
    r"|(?:Dockerfile|Makefile)(?![A-Za-z0-9_.-])"
    r")\b"
)


def detect_fr_not_in_design(root: Path) -> list[str]:
    """drift 1: FR/NFR が design.md から参照されないものを返す。"""
    requirements = root / "docs" / "requirements.md"
    design = root / "docs" / "design.md"
    if not requirements.exists() or not design.exists():
        return []
    design_text = design.read_text(encoding="utf-8").lower()
    keys: list[str] = []
    for fr_id in FR_HEADING_RE.findall(requirements.read_text(encoding="utf-8")):
        token = fr_id.lower()
        # 本文トークン（fr-159）と GitHub アンカー（#fr-159-…）の双方を参照とみなす
        if not re.search(rf"\b{re.escape(token)}\b", design_text):
            keys.append(f"fr-not-in-design:{fr_id}")
    return keys


def detect_design_task_unmapped(root: Path) -> list[str]:
    """drift 2: design のタスク節が plan（archive 移設分含む）に言及されず spec も無いものを返す。

    plan.md 本体に加え、archive 移設分（ADR-0003）も検索対象に含める。
    """
    design = root / "docs" / "design.md"
    plan = root / "docs" / "plan.md"
    if not design.exists() or not plan.exists():
        return []
    plan_text = plan.read_text(encoding="utf-8")
    # ADR-0003: plan.md の履歴節（Done／Issue 対応表／変更履歴等）は削除ではなく
    # docs/archive/ へ移設・保全される（N-595 履歴移設）。移設済みタスク ID を
    # 「plan 未言及」と誤検知しないよう、archive 本文も検索対象に含める
    # （N-595 PR-2 で発覚・fix-on-discovery P-065）。
    archive_dir = root / "docs" / "archive"
    if archive_dir.exists():
        for archive_file in sorted(archive_dir.glob("*.md")):
            plan_text += "\n" + archive_file.read_text(encoding="utf-8")
    specs_dir = root / "docs" / "specs"
    task_ids: set[str] = set()
    for heading in DESIGN_HEADING_RE.findall(design.read_text(encoding="utf-8")):
        task_ids.update(TASK_ID_RE.findall(heading))
    keys: list[str] = []
    for task_id in sorted(task_ids):
        in_plan = re.search(rf"\b{re.escape(task_id)}\b", plan_text) is not None
        has_spec = specs_dir.exists() and any(specs_dir.glob(f"{task_id}-*.md"))
        if not in_plan and not has_spec:
            keys.append(f"design-task-unmapped:{task_id}")
    return keys


def detect_superseded_adr_refs(root: Path) -> list[str]:
    """drift 3: supersede 済 ADR への現役文書からの参照を返す。"""
    adr_index = root / "docs" / "adr" / "README.md"
    if not adr_index.exists():
        return []
    superseded = set(SUPERSEDED_ROW_RE.findall(adr_index.read_text(encoding="utf-8")))
    if not superseded:
        return []
    # 現役の正本のみ対象（plan は履歴 closeout を多く含むため対象外＝既知の許容）
    active_docs = [root / "docs" / "requirements.md", root / "docs" / "design.md"]
    specs_dir = root / "docs" / "specs"
    if specs_dir.exists():
        active_docs.extend(sorted(specs_dir.glob("*.md")))
    keys: list[str] = []
    for doc in active_docs:
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8")
        for adr_id in sorted(superseded):
            if adr_id in text:
                keys.append(f"superseded-adr-ref:{doc.relative_to(root).as_posix()}:{adr_id}")
    return keys


def _extract_spec_paths(bash_text: str) -> set[str]:
    paths: set[str] = set()
    heredoc_end: str | None = None
    for line in bash_text.splitlines():
        stripped = line.strip()
        # heredoc 本文（Python 断片等）はシェルのファイル引数ではないためスキップ
        if heredoc_end is not None:
            if stripped == heredoc_end:
                heredoc_end = None
            continue
        heredoc = re.search(r"<<-?\s*'?(\w+)'?", stripped)
        if heredoc:
            heredoc_end = heredoc.group(1)
        if stripped.startswith("#"):
            continue
        # gh の owner/repo 引数・GitHub API パスはコマンド文脈で除去（小文字 owner も対象）
        stripped = re.sub(r"(?:--repo|-R)\s+\S+", " ", stripped)
        stripped = re.sub(r"\brepos/\S+", " ", stripped)
        # grep -E 等の正規表現内でエスケープされた拡張子（scripts/predict\.py 等）を
        # 正規化する。バックスラッシュは文字クラス外のためトークンが拡張子欠落のまま
        # 打ち切られ、存在するファイルが誤って drift 扱いされていた
        # （Backlog-N439-drift-detection-coverage ③・baseline の scripts/predict /
        #  scripts/upload_db 2 行は本件が原因の既知 false-drift）
        stripped = stripped.replace("\\.", ".")
        for token in PATH_TOKEN_RE.findall(stripped) + BARE_FILE_RE.findall(stripped):
            token = token.rstrip(".,:;)）」")
            token = token.split("::", 1)[0].split("#", 1)[0]
            if not token:
                continue
            # リポジトリ相対パス以外を除外: 数字開始（URL ポート断片）/ git rev-range
            # （origin/main...HEAD 等）/ 慣用トークン（dev/null）/ remote 参照
            if "..." in token:
                continue
            if token.startswith(("/", "http", "tmp/", "origin/", "upstream/")):
                continue
            segments = token.split("/")
            # ポート断片（8000/api/… 等）＝先頭セグメントが数字のみの「/ 入り」トークンのみ除外
            # （2026-05-18.json のような数字開始ファイル名は drift 対象に残す）
            if "/" in token and segments[0].isdigit():
                continue
            if token in ("dev/null",) or token.endswith("/dev/null"):
                continue
            # owner/repo 形式（gh 引数。例: KosGit-Dev/english-learning-app）＝大文字を含む
            # 先頭セグメント + ドット無し 2 セグメントはリポジトリパスではない
            segments = token.split("/")
            if len(segments) == 2 and "." not in token and segments[0] != segments[0].lower():
                continue
            paths.add(token)
    return paths


def _tracked_files(root: Path) -> set[str] | None:
    """git 追跡ファイル一覧（クリーン checkout で再現可能な存在判定の基準）。

    git が使えない場合（テスト fixture 等）は None を返し、呼び出し側は
    ファイルシステムの存在判定にフォールバックする。
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {line for line in out.splitlines() if line}


# basename 一致での存在許容は明示 allowlist に限定（任意ディレクトリの同名ファイルで
# ルート直下想定の参照を見逃さないため）。慣用的に名前だけで参照されるファイルのみ登録する
BARE_BASENAME_ALLOWLIST = {
    # workflow は名前だけで言及される慣用がある（実在は追跡 basename 一致で判定）
    "ci.yml",
    "data-ingest-incremental.yml",
    "lighthouse-ci.yml",
    "scheduled-ingest-health-check.yml",
    "scheduled-predict-daily.yml",
    # 複数セッションプロトコルの lock メタ・著名スクリプトの慣用名
    "meta.yml",
    "live_gate.py",
    "predict.py",
    "upload_db.py",
}


def _path_exists(root: Path, tracked: set[str] | None, token: str) -> bool:
    """token の存在判定。git 追跡基準（untracked / ignored 生成物は不存在扱い）。"""
    if tracked is None:
        if "/" in token:
            return (root / token).exists()
        if (root / token).exists():
            return True
        if token in BARE_BASENAME_ALLOWLIST:
            return next(root.rglob(token), None) is not None
        return False
    if "/" in token:
        prefix = token + "/"
        return token in tracked or any(f.startswith(prefix) for f in tracked)
    # 基本はルート直下の追跡ファイル。basename 許容は allowlist のみ
    if token in tracked:
        return True
    if token in BARE_BASENAME_ALLOWLIST:
        return any(f.rsplit("/", 1)[-1] == token for f in tracked)
    return False


def detect_spec_missing_paths(root: Path) -> list[str]:
    """drift 4: spec の Verification Commands が参照するパスの不存在を返す。

    存在判定は git 追跡ファイル基準（クリーン checkout で同一結果）。ignored /
    untracked の生成物（operator-local DB・出力 JSON 等）は「リポジトリに無い」
    として drift になり、恒久的なものは baseline で許容する。
    """
    specs_dir = root / "docs" / "specs"
    if not specs_dir.exists():
        return []
    tracked = _tracked_files(root)
    keys: list[str] = []
    for spec in sorted(specs_dir.glob("*.md")):
        text = spec.read_text(encoding="utf-8")
        marker = text.find("## Verification Commands")
        if marker < 0:
            continue
        # 次の level-2 見出し（## Runtime Smoke 等）で探索を打ち切る。打ち切らないと
        # 後続節の bash 例（実コマンドではない説明用スニペット等）まで対象になり
        # 誤検知（false-drift）方向になる（Backlog-N439-drift-detection-coverage ②）
        next_marker = text.find("\n## ", marker + len("## Verification Commands"))
        section = text[marker:next_marker] if next_marker >= 0 else text[marker:]
        for block in BASH_BLOCK_RE.findall(section):
            for path in sorted(_extract_spec_paths(block)):
                if not _path_exists(root, tracked, path):
                    keys.append(f"spec-missing-path:{spec.name}:{path}")
    return keys


def load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    entries: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            entries.add(stripped)
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=f"既知 gap の許容リスト（--root 基準の相対可。既定 {DEFAULT_BASELINE}）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="baseline 外の新規 drift または陳腐化 baseline があれば exit 1（既定は advisory）",
    )
    args = parser.parse_args()
    root: Path = args.root
    baseline_path = args.baseline if args.baseline is not None else Path(DEFAULT_BASELINE)
    if not baseline_path.is_absolute():
        baseline_path = root / baseline_path

    # 同一 key の重複（同じ欠落パスが複数フェンスに出る等）は早期に除去し、
    # total / by_type / strict 判定をすべて同じ重複なし集合に揃える
    found: list[str] = sorted(
        set(detect_fr_not_in_design(root))
        | set(detect_design_task_unmapped(root))
        | set(detect_superseded_adr_refs(root))
        | set(detect_spec_missing_paths(root))
    )

    baseline = load_baseline(baseline_path)
    found_set = set(found)
    new_keys = sorted(found_set - baseline)
    stale_baseline = sorted(baseline - found_set)

    for key in new_keys:
        print(f"WARNING: new drift: {key}", file=sys.stderr)
    for key in stale_baseline:
        print(
            f"WARNING: stale baseline（解消済み・baseline から削除すべき）: {key}",
            file=sys.stderr,
        )

    by_type: dict[str, int] = {}
    for key in found:
        by_type[key.split(":", 1)[0]] = by_type.get(key.split(":", 1)[0], 0) + 1
    summary = " / ".join(f"{k}: {v}" for k, v in sorted(by_type.items())) or "drift なし"
    print(
        f"coherence drift: total {len(found)}（{summary}）/ "
        f"baseline 許容 {len(found_set & baseline)} / 新規 {len(new_keys)} / "
        f"陳腐化 baseline {len(stale_baseline)}"
    )
    if (new_keys or stale_baseline) and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
