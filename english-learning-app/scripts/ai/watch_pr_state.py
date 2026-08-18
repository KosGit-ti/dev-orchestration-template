#!/usr/bin/env python3
"""PR の CI / レビュー待ちを終端検知で自動再開させる watcher。

「待機か停止か区別できず時間ロスが発生する」問題（2026-08-10 ユーザー指示）への
恒久対策。エージェントは push・レビュー依頼の直後に本スクリプトを background
実行（Claude Code では Monitor / Bash run_in_background）してからターンを終える。
終端（全 check 終了・またはレビュー到着）で終端行を出力して exit するため、
background 完了通知がエージェントを自動再開する。

沈黙を成功と誤認しない設計（silence is not success）:
- CI 監視は pending 消滅時に「全 pass」か「FAIL あり」を必ず出力する。
- レビュー監視は新着 review の件数と head を出力する。
- gh の一時失敗では終了せず、poll を継続する（連続失敗は上限で fail-close）。

使い方:
    watch_pr_state.py --pr 1284 --mode checks   # CI 終端まで監視
    watch_pr_state.py --pr 1284 --mode review --baseline-reviews 2
                                                # レビュー件数が 2 を超えたら終了
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

_MAX_CONSECUTIVE_FAILURES = 10


def _gh_json(args: list[str]) -> object:
    """gh を実行して JSON を返す。失敗は例外で伝える。"""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(proc.stdout)


def classify_checks(rows: list[dict[str, object]]) -> tuple[bool, list[str]]:
    """check 行から (終端か, 終端行リスト) を返す。

    bucket: pass / fail / pending / skipping / cancel。pending が 1 件でも
    残っていれば非終端。終端時は fail / cancel を先頭に列挙する。
    """
    pending = [r for r in rows if r.get("bucket") == "pending"]
    if pending:
        return False, []
    bad = [
        f"{r.get('name')}: {r.get('bucket')}" for r in rows if r.get("bucket") in ("fail", "cancel")
    ]
    good = [
        f"{r.get('name')}: {r.get('bucket')}"
        for r in rows
        if r.get("bucket") not in ("fail", "cancel")
    ]
    return True, bad + good


def watch_checks(pr: int, interval: int) -> int:
    """CI の全 check 終端まで poll し、終端サマリを出力して exit する。"""
    failures = 0
    while True:
        try:
            rows = _gh_json(["pr", "checks", str(pr), "--json", "name,bucket"])
            failures = 0
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
            failures += 1
            if failures >= _MAX_CONSECUTIVE_FAILURES:
                print(f"TERMINAL: watcher 自体が失敗（gh 連続 {failures} 回失敗: {exc}）")
                return 2
            time.sleep(interval)
            continue
        if not isinstance(rows, list):
            print("TERMINAL: gh pr checks の出力が list ではない（fail-close）")
            return 2
        done, lines = classify_checks(rows)
        if done:
            has_fail = any(": fail" in line or ": cancel" in line for line in lines)
            for line in lines:
                print(line)
            print(
                "TERMINAL: FAIL あり — 対応が必要" if has_fail else "TERMINAL: 全 pass — 次工程へ"
            )
            return 1 if has_fail else 0
        time.sleep(interval)


def watch_reviews(pr: int, interval: int, baseline: int) -> int:
    """レビュー件数が baseline を超えたら、新着の要約を出力して exit する。"""
    failures = 0
    while True:
        try:
            pages = _gh_json(
                [
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{{owner}}/{{repo}}/pulls/{pr}/reviews?per_page=100",
                ]
            )
            # --slurp はページの配列を返す。フラット化して全 review を数える
            # （既定 30 件/頁のままだと開始時点で 30 件超の PR は新着を永遠に検知できない）。
            reviews = [r for page in pages for r in page] if isinstance(pages, list) else None
            failures = 0
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
            failures += 1
            if failures >= _MAX_CONSECUTIVE_FAILURES:
                print(f"TERMINAL: watcher 自体が失敗（gh 連続 {failures} 回失敗: {exc}）")
                return 2
            time.sleep(interval)
            continue
        if isinstance(reviews, list) and len(reviews) > baseline:
            for r in reviews[baseline:]:
                commit = str(r.get("commit_id", ""))[:8]
                print(f"review 到着: id={r.get('id')} commit={commit} state={r.get('state')}")
            print(
                "TERMINAL: 新着レビューあり — 本文全文照合へ（スレッド 0 でも本文指摘はすり抜ける）"
            )
            return 0
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True, help="PR 番号")
    parser.add_argument("--mode", choices=("checks", "review"), required=True)
    parser.add_argument("--interval", type=int, default=45, help="poll 間隔秒（既定 45）")
    parser.add_argument(
        "--baseline-reviews",
        type=int,
        default=0,
        help="review mode: この件数を超えたら新着とみなす",
    )
    args = parser.parse_args()
    if args.pr <= 0 or args.interval <= 0:
        print("TERMINAL: --pr と --interval は正の整数が必要（fail-close）", file=sys.stderr)
        return 2
    if args.baseline_reviews < 0:
        print("TERMINAL: --baseline-reviews は 0 以上が必要（fail-close）", file=sys.stderr)
        return 2
    if args.mode == "checks":
        return watch_checks(args.pr, args.interval)
    return watch_reviews(args.pr, args.interval, args.baseline_reviews)


if __name__ == "__main__":
    sys.exit(main())
