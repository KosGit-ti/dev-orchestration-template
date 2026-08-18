"""AI レビュー証跡の共通ユーティリティ。

scripts/run_ai_review.py がレビュー証跡 JSON（docs/ai/reviews/ 配下）を
生成する際に使う最小限のヘルパーを提供する。差分 fingerprint は
レビュー証跡自身の変更を除外して計算するため、証跡の追記だけでは
fingerprint が変わらない（証跡の stale 判定に使える）。
"""

from __future__ import annotations

import hashlib
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA_VERSION = "1.0"

REVIEW_REPORT_PREFIX = "docs/ai/reviews/"


def _run_git(args: list[str], cwd: Path) -> str:
    """git コマンドを実行して標準出力を返す。失敗時は RuntimeError。"""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        msg = f"git {' '.join(args)} failed: {stderr}"
        raise RuntimeError(msg)
    return result.stdout


def _safe_ref(ref: str) -> str:
    """git ref としてオプション注入や空白を含む値を拒否する。"""
    if ref.startswith("-") or any(char.isspace() for char in ref):
        msg = f"unsafe git ref: {ref!r}"
        raise ValueError(msg)
    return ref


def _strip_git_diff_path(path: str) -> str:
    normalized = path.strip().strip('"').replace("\\", "/")
    if normalized.startswith(("a/", "b/")):
        return normalized[2:]
    return normalized


def _diff_header_paths(line: str) -> list[str]:
    parts = line.strip().split()
    if len(parts) < 4:
        return []
    return [_strip_git_diff_path(parts[2]), _strip_git_diff_path(parts[3])]


def _is_review_report_path(path: str) -> bool:
    return _strip_git_diff_path(path).startswith(REVIEW_REPORT_PREFIX)


def merge_base(root: Path, base_ref: str, head_ref: str) -> str:
    """base と head の merge-base コミット SHA を返す。"""
    return _run_git(["merge-base", _safe_ref(base_ref), _safe_ref(head_ref)], root).strip()


def diff_fingerprint(root: Path, base_ref: str, head_ref: str) -> str:
    """レビュー証跡自身を除外した差分 fingerprint を返す。"""
    base = merge_base(root, base_ref, head_ref)
    diff = _run_git(["diff", "--binary", base, _safe_ref(head_ref)], root)
    chunks: list[str] = []
    keep = True
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if keep and current:
                chunks.extend(current)
            current = [line]
            paths = _diff_header_paths(line)
            keep = not paths or not all(_is_review_report_path(path) for path in paths)
            continue
        current.append(line)
    if keep and current:
        chunks.extend(current)
    return hashlib.sha256("".join(chunks).encode("utf-8")).hexdigest()
