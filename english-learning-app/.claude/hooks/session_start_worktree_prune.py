#!/usr/bin/env python3
"""SessionStart hook: stale worktree を検出したら背景で自動掃除を起動する。

WSL/Docker vhdx 肥大化再発防止（`.claude/worktrees/agent-*` の残置が主因、
docs/runbook.md 参照）の高頻度トリガー。**サービス非依存の正トリガーは
devcontainer の `postStartCommand`（コンテナ起動ごと）であり、本 hook は
Claude Code セッション開始ごとに追加で検出頻度を上げる補助トリガーに
過ぎない**。

設計方針:
    - **高速動作必須**。git を一切呼ばず、`.claude/worktrees/` 直下の
      ディレクトリの mtime だけを見て stale 候補数を数える（実際の
      stale 判定・削除は `scripts/ops/prune_stale_worktrees.py` 側で
      git を使って厳密に行う。本 hook は「呼ぶかどうか」の高速判定のみ）。
    - stale 候補が 0 件なら何も出力せず即 exit 0（無音・無害）。
    - 1 件以上あれば prune script を `subprocess.Popen` で detach 起動し
      （`start_new_session=True`）、セッション開始をブロックしない。
    - **fail-open**: stdin 読み取り・JSON 解析・ファイル走査・起動の
      いずれで例外が起きても握りつぶし、セッション開始を絶対に
      ブロックしない（exit 0 固定）。
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

# stale 候補と見なす cutoff（日数）。scripts/ops/prune_stale_worktrees.py の
# 既定 --max-age-days と同じ値を使う（本 hook 側は候補数の概算に過ぎない）。
_CANDIDATE_MAX_AGE_DAYS = 7
_SECONDS_PER_DAY = 86400


def _repo_root() -> Path:
    """repo root を環境変数 CLAUDE_PROJECT_DIR、無ければ cwd から求める。"""
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root)
    return Path.cwd()


def count_stale_candidates(repo_root: Path, *, max_age_days: int = _CANDIDATE_MAX_AGE_DAYS) -> int:
    """`.claude/worktrees/` 直下でトップ mtime が cutoff より古いディレクトリ数を数える。

    git は一切呼ばない高速パス。ディレクトリが存在しない場合は 0 件を返す。
    """
    worktrees_dir = repo_root / ".claude" / "worktrees"
    if not worktrees_dir.is_dir():
        return 0

    cutoff = time.time() - max_age_days * _SECONDS_PER_DAY
    count = 0
    try:
        for child in worktrees_dir.iterdir():
            if not child.is_dir():
                continue
            try:
                if child.stat().st_mtime < cutoff:
                    count += 1
            except OSError:
                continue
    except OSError:
        return 0
    return count


def _launch_prune_background(repo_root: Path) -> None:
    """prune script を detach したバックグラウンドプロセスとして起動する。"""
    log_path = repo_root / "outputs" / "worktree-prune.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    script_path = repo_root / "scripts" / "ops" / "prune_stale_worktrees.py"

    with log_path.open("a", encoding="utf-8") as log_file:
        subprocess.Popen(  # noqa: S603
            [sys.executable or "python3", str(script_path), "--log", str(log_path)],
            cwd=str(repo_root),
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


def main() -> None:
    with contextlib.suppress(Exception):
        sys.stdin.read()  # 入力は使わないが読み捨てる（fail-open）

    try:
        repo_root = _repo_root()
        stale_count = count_stale_candidates(repo_root)
        if stale_count <= 0:
            return

        _launch_prune_background(repo_root)
        print(
            f"【worktree 自動掃除】stale {stale_count} 件を検出し背景削除を起動した"
            "（dirty は outputs/worktree-prune.log 参照）"
        )
    except Exception:
        # fail-open: 予期しない失敗があってもセッション開始を絶対にブロックしない。
        pass


if __name__ == "__main__":
    main()
