"""Stop hook の同一ブロック理由 N 連続検出（B-367）。

PR 番号 + ブロック理由の fingerprint を履歴ファイルに保存し、
N 連続検出時にエージェントへ「実作業要求」へ強制誘導する。

2026-05-19 incident: Stop hook が同一 reason で繰り返し block し、エージェントが
同一の応答を数十回出力した問題の再発防止。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any


def _default_history_path() -> Path:
    """履歴ファイルの保存先を解決する。

    /memories/ マウントを持つ環境（旧 AC-3 前提）ではそちらを優先し、
    マウントが無い環境（本 devcontainer 等）では repo 内の gitignore 済み
    runtime ディレクトリへ保存する。旧パスは root 直下のため mkdir が
    OSError → save_history の suppress で黙殺され、履歴が一度も永続化されず
    B-367 検出が無効化されていた（2026-07-03 是正）。
    """
    mounted = Path("/memories/session")
    if mounted.is_dir():
        return mounted / "stop-hook-block-history.json"
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / ".claude" / "runtime" / "stop-hook-block-history.json"


# 履歴ファイルの保存先
HISTORY_PATH = _default_history_path()
DEFAULT_MAX_REPEATS = 3


def compute_fingerprint(
    pr_number: str,
    unreplied_thread_ids: list[str] | None = None,
    ci_failure_names: list[str] | None = None,
    other_reasons: list[str] | None = None,
) -> str:
    """同一ブロック理由を識別する time-independent fingerprint を計算する。

    PR 番号 + ソート済 unreplied thread IDs + ソート済 CI failure names +
    その他 reason テキスト の SHA-256 hash を返す。
    リスト要素はソートして順不同で同一 hash を生成する（AC-2 準拠）。
    """
    parts: list[str] = [f"pr={pr_number}"]

    sorted_threads = sorted(unreplied_thread_ids or [])
    parts.append(f"threads={','.join(sorted_threads)}")

    sorted_ci = sorted(ci_failure_names or [])
    parts.append(f"ci={','.join(sorted_ci)}")

    sorted_other = sorted(other_reasons or [])
    parts.append(f"other={','.join(sorted_other)}")

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_history(path: Path | None = None) -> dict[str, Any]:
    """履歴ファイルを読み込む。存在しない・破損時は空 dict を返す。"""
    target = path if path is not None else HISTORY_PATH
    try:
        text = target.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        return dict(data)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_history(history: dict[str, Any], path: Path | None = None) -> None:
    """履歴ファイルを書き込む。親ディレクトリ自動作成。

    書き込み権限がない環境（テスト・read-only FS 等）では黙って無視する。
    書き込み失敗は次回呼び出し時に履歴が空になるだけで、安全側の挙動（block し続ける）になる。
    """
    target = path if path is not None else HISTORY_PATH
    with contextlib.suppress(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_stored_count(value: Any) -> int:
    """履歴 count が不正な場合は空履歴相当の 0 に戻す。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def record_block_and_check_repeat(
    pr_number: str,
    fingerprint: str,
    *,
    path: Path | None = None,
    max_repeats: int = DEFAULT_MAX_REPEATS,
) -> tuple[int, bool]:
    """ブロックを記録し、N 連続検出フラグを返す。

    PR 番号が変わったら履歴をリセット。同 PR + 同 fingerprint なら count をインクリメント。
    PR が同じでも fingerprint が変わったら count を 1 にリセット（別の理由とみなす）。

    Returns:
        (count, exceeded): count は現在の連続回数、exceeded は count >= max_repeats なら True
    """
    history = load_history(path)

    stored_pr: str = str(history.get("pr_number", ""))
    stored_fp: str = str(history.get("fingerprint", ""))
    stored_count = _safe_stored_count(history.get("count", 0))

    if stored_pr != pr_number:
        # PR 番号が変わったら自動リセット（AC-3）
        new_count = 1
    elif stored_fp != fingerprint:
        # fingerprint が変わったら連続カウントをリセット
        new_count = 1
    else:
        # 同 PR + 同 fingerprint → インクリメント
        new_count = stored_count + 1

    new_history: dict[str, Any] = {
        "pr_number": pr_number,
        "fingerprint": fingerprint,
        "count": new_count,
    }
    save_history(new_history, path)

    exceeded = new_count >= max_repeats
    return new_count, exceeded


def reset_history(path: Path | None = None) -> None:
    """履歴を全リセット（テスト用）。"""
    target = path if path is not None else HISTORY_PATH
    with contextlib.suppress(OSError):
        target.unlink(missing_ok=True)


def build_escalation_reason(
    pr_number: str,
    fingerprint: str,
    repeats: int,
    unreplied_thread_ids: list[str] | None = None,
    ci_failure_names: list[str] | None = None,
    other_reasons: list[str] | None = None,
) -> str:
    """N 連続検出時のエスカレーション理由テキストを構築する。

    通常 block reason の代わりに、エージェントへ「実作業要求」へ強制誘導する
    具体的な指示を含む reason を返す（AC-1 準拠）。
    「進捗ゼロの状態での override 要求は禁止」を明記する（AC-11 / B-368 参照）。
    """
    threads = unreplied_thread_ids or []
    ci_failures = ci_failure_names or []
    other = other_reasons or []

    lines: list[str] = [
        f"【同一ブロック {repeats} 回検出 (B-367)】"
        f"PR #{pr_number} で fingerprint={fingerprint} のブロックが "
        f"{repeats} 回連続発生しています。",
        "状態取得を繰り返すだけでは進展しません。"
        "以下のいずれかを「次回ターンで必ず」実施してください:",
        "",
        "■ 残作業（機械可読）:",
    ]

    if threads:
        lines.append(f"- unreplied_thread_ids: {', '.join(threads)}")
    else:
        lines.append("- unreplied_thread_ids: なし")

    if ci_failures:
        lines.append(f"- ci_failure_names: {', '.join(ci_failures)}")
    else:
        lines.append("- ci_failure_names: なし")

    if other:
        lines.append(f"- other_reasons: {'; '.join(other)}")
    else:
        lines.append("- other_reasons: なし")

    lines += [
        "",
        "■ 推奨アクション例:",
    ]

    if threads:
        lines.append(
            "1. 各 unreplied thread に "
            "gh api -X POST repos/{owner}/{repo}/pulls/"
            f"{pr_number}/comments/<id>/replies で返信する"
        )
    if ci_failures:
        lines.append("2. CI failure を gh run view <id> --log-failed で調査し、原因を修正する")
    if not threads and not ci_failures:
        lines.append("1. PR の状態を gh pr view で確認し、残作業を特定して実行する")

    lines += [
        "",
        "進捗ゼロの状態で「ユーザに override を要求する」応答は禁止です（B-368 参照）。",
        "これら以外の作業を行わずに、まずこれを完了させること。",
    ]

    return "\n".join(lines)
