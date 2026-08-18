#!/usr/bin/env python3
"""PreCompact hook: コンテキスト圧縮前にレビューループルールと全プラン実施モードを注入する。

会話が長くなりコンテキスト圧縮が発生すると、レビューループの手順や
「全プラン実施」モードが失われることがある。この Hook は圧縮前に
クリティカルなルールを systemMessage として注入し、圧縮後もエージェントが
ルールとモードを保持するようにする。

これは Copilot の pre_compact_context.py の Claude Code 移植版。
出力形式は {"systemMessage": "..."} で両者共通のため変更なし。
"""

import contextlib
import json
import subprocess
import sys
from pathlib import Path

# 同一ディレクトリの _github_api を import するため sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _github_api import gh_subprocess_env, is_claude_code_remote  # noqa: E402


def run(cmd: list[str], timeout: int = 5) -> str | None:
    """コマンドを実行して stdout を返す。失敗時は None。

    gh 未認証環境でも gh 呼び出しが認証されるよう gh_subprocess_env() の env を渡す。
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=gh_subprocess_env() if cmd[:1] == ["gh"] else None,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def load_full_plan_flag() -> "dict[str, object] | None":
    """全プラン実施フラグファイルを読み取る。存在しない場合は None。"""
    flag_path = Path(__file__).parent.parent.parent / ".github" / "full-plan-execution.flag"
    if not flag_path.exists():
        return None
    try:
        data = json.loads(flag_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def main() -> None:
    with contextlib.suppress(json.JSONDecodeError):
        json.load(sys.stdin)  # 入力を消費（使用しないが読む必要がある）

    # 全プラン実施フラグを読み取る
    flag_data = load_full_plan_flag()

    # PR ブランチでなければレビューループ注入はスキップ（全プランモードのみ注入）
    branch = run(["git", "branch", "--show-current"])
    pr_number = None
    remote_env = is_claude_code_remote()
    if branch and branch not in ("main", "master") and not remote_env:
        # ローカル CLI: gh で PR 番号を解決
        pr_json = run(["gh", "pr", "view", branch, "--json", "number,state"], timeout=10)
        if pr_json:
            try:
                pr_data = json.loads(pr_json)
                if pr_data.get("state") == "OPEN":
                    pr_number = pr_data["number"]
            except json.JSONDecodeError:
                pass

    # 注入するコンテキストを構築
    context_parts: list[str] = []

    if remote_env and branch and branch not in ("main", "master"):
        # Claude Code Remote: PR 番号は不明なまま、webhook ベース運用を案内
        context_parts.append(
            "【重要・コンテキスト圧縮前の自動注入 / Claude Code Remote 環境】"
            f"ブランチ {branch} で作業中。\n"
            "■ この環境ではシェルポーリング前提のレビューループは適用不可:\n"
            "  - gh CLI / GITHUB_TOKEN は無い\n"
            "  - 60秒 sleep を伴う Bash ループは禁止（生産性低下と中断要因）\n"
            "■ 代替フロー（必須）:\n"
            "1. PR 確認: mcp__github__list_pull_requests または "
            f"mcp__github__search_pull_requests (head={branch}) で OPEN PR を取得\n"
            "2. レビュワー: mcp__github__pull_request_read で requested_reviewers を確認、"
            "未設定なら mcp__github__request_copilot_review を呼ぶ\n"
            "3. イベント受信: mcp__github__subscribe_pr_activity を一度呼べば、"
            "CI 結果・レビューコメントが <github-webhook-activity> として届く\n"
            "4. コメント対応: 受信した指摘を Must/Should/Nice 分類、修正後は "
            "mcp__github__add_reply_to_pull_request_comment で各スレッドに返信\n"
            "5. 完了判定: active AI thread がすべて返信済みかつ resolved と確認\n"
            "6. release-judgement skill 実行前に未解決または未返信の thread 0件を確認\n"
            "■ 参照: .github/instructions/review-loop.instructions.md "
            "（シェル前提部分は MCP/webhook に読み替え）"
        )
    elif pr_number is not None:
        context_parts.append(
            f"【重要・コンテキスト圧縮前の自動注入】"
            f"現在 PR #{pr_number} (ブランチ: {branch}) で作業中です。\n"
            "■ レビューループ必須ルール（省略禁止）:\n"
            "1. push 後は必ず CI 通過を確認する（同期 sleep ループは禁止。"
            "短い状態確認を最大20回相当）\n"
            "2. Round 1 は自動発火しない。Copilot レビューを明示リクエストする\n"
            "   ※ Draft PR は発火しない。Ready for review へ変更後にリクエスト\n"
            "3. レビューコメント到着後:\n"
            "  (a) コメントを取得・分類（Must/Should/Nice）\n"
            "  (b) plan.md の AC と照合し、AC と矛盾する指摘は AC 優先で対応不要と判定\n"
            "  (c) Must/Should を修正する\n"
            "  (d) 【必須】各コメントに GitHub 上で返信する\n"
            "      Bash ツールで gh api を使い全スレッドに修正内容・見解を返信\n"
            "      返信なしのまま release-judgement / セッション終了に進む場合は "
            "Hook がリマインドする\n"
            "  (e) CI 実行・検証 → コミット → push\n"
            "  (f) 次のラウンドを明示リクエストする\n"
            "4. レビューは Round 3 を既定の収束点とする。\n"
            "   非ブロッキング指摘だけでは延長しない。適格理由、実質修正、延長証跡が"
            "ある場合だけ terminal anchor 前に限って Round 4 以降へ進む\n"
            "   停止条件: 延長不適格・進捗ゼロ・同一指摘3回繰り返し・"
            "再トリガー3回超過・ポリシー違反・認証不能\n"
            "5. commit_id フィルタリングは禁止。2段階検出を使う\n"
            "6. レビュー対応完了後も、push → CI確認 → 新レビュー待機のサイクルを省略しない\n"
            "■ 完了判定の正確な定義: active AI thread がすべて返信済みかつ resolved\n"
            "■ release-judgement skill 実行前に未解決または未返信の thread 0件を確認\n"
            "■ 参照: .github/instructions/review-loop.instructions.md\n"
            "■ 参照: /memories/repo/review-loop-rules.md"
        )

    if flag_data is not None:
        # 現行 flag スキーマ（current_task / current_task_description / current_pr /
        # last_merged_pr / status。正: scripts/hooks/full_plan_completion.py）を第一に読み、
        # 旧スキーマ（2026-05-08: next_task / current_state / instructions /
        # resume_after_401_error）へ fallback する（PR-T6 監査で乖離を実測・是正）
        current_task = str(flag_data.get("current_task", flag_data.get("next_task", "不明")))
        current_desc = str(
            flag_data.get("current_task_description", flag_data.get("next_task_description", ""))
        )
        remaining_raw = flag_data.get("remaining_tasks", [])
        remaining: list[str] = (
            [str(t) for t in remaining_raw] if isinstance(remaining_raw, list) else []
        )
        status = str(flag_data.get("status", "") or "")

        def _pr_label(value: object) -> str:
            """PR 番号を表示用に正規化する（null / 空は「なし」。#None 注入を防ぐ）。"""
            if value is None or str(value).strip() in ("", "None", "null"):
                return "なし"
            return f"#{value}"

        current_pr = _pr_label(flag_data.get("current_pr"))
        current_state_raw = flag_data.get("current_state", {})
        current_state: dict[str, object] = (
            current_state_raw if isinstance(current_state_raw, dict) else {}
        )
        last_merged_pr = _pr_label(
            flag_data.get("last_merged_pr", current_state.get("last_merged_pr"))
        )
        instructions_raw = flag_data.get("instructions", [])
        instructions: list[str] = (
            [str(i) for i in instructions_raw] if isinstance(instructions_raw, list) else []
        )
        resume_raw = flag_data.get("resume_after_401_error", [])
        resume_steps: list[str] = (
            [str(r) for r in resume_raw] if isinstance(resume_raw, list) else []
        )
        extra_lines: list[str] = []
        if status:
            extra_lines.append(f"status: {status}")
        if instructions:
            extra_lines.extend(f"  {i}" for i in instructions)
        if resume_steps:
            extra_lines.append("■ エラー後の再開手順:")
            extra_lines.extend(f"  {r}" for r in resume_steps)
        extra_text = ("\n".join(extra_lines) + "\n") if extra_lines else ""
        if status == "gated_idle":
            stop_rule = (
                "■ 現在は gated_idle（残タスクは全て user/infra-gated）。"
                "gated タスクを自動で進めず、偽装せず明示して待機する（P-003）。\n"
            )
        else:
            stop_rule = (
                "■ 「1タスク完了したので停止」は絶対禁止。次タスクへ必ず進め。\n"
                "■ 停止条件: remaining_tasks が空になった時のみ\n"
            )
        context_parts.append(
            "\n\n【全プラン実施モード — 継続中】\n"
            "ユーザーは「プランをすべて実施して」と指示している。\n"
            "コンテキスト圧縮が発生しても、このモードは継続中である。\n"
            f"現在のタスク: {current_task} — {current_desc}\n"
            f"残タスク一覧: {', '.join(str(t) for t in remaining)}\n"
            f"現在の PR: {current_pr} / 最後に merge した PR: {last_merged_pr}\n"
            f"{extra_text}"
            f"{stop_rule}"
            "■ フラグファイル: .github/full-plan-execution.flag"
            "（delivery object と ledger が状態の正本）\n"
        )

    if not context_parts:
        json.dump({}, sys.stdout)
        return

    context = "".join(context_parts)
    json.dump({"systemMessage": context}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
