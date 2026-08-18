# Hooks ガイド

## 概要

Hook は完了事故を防ぐガードであり、日常コマンド解釈や仕様駆動開発の正本ではない。コマンド解釈は `ai/command-router.yml`、運用方針は `ai/operation-policy.yml`、レビューループは `.github/instructions/review-loop.instructions.md` を正本とする。

## Hook 一覧

有効化状況の正本は `.github/hooks/review-loop-guard.json` とする。

| Hook | 主な役割 |
| --- | --- |
| UserPromptSubmit | 指示元（人間 vs 非人間 = 自動/エージェント）を毎ターン判別し、指示元権限ルール（P-066）を非ブロッキングで `additionalContext` に注入する（`scripts/hooks/instruction_source.py` 正本・`.claude/hooks/instruction_source_guard.py` 結線・常に exit 0・fail-open で継続作業を中断しない） |
| PreToolUse | `task_complete` / `release-manager` 呼び出し、`git push`、Copilot review request の安全床とレビュー儀式状態を検査する |
| PostToolUse | `git push` 後に CI とレビューループの継続をリマインドする |
| Stop | セッション終了時に CI、レビュー儀式、全プラン完了状態を検査する |
| PreCompact | コンテキスト圧縮前に重要ルールを再注入する |

## ブロック条件

完了ガード系 hook の block は安全床に限定する。レビュー儀式はリマインドとして検査する。

- OPEN PR に失敗・キャンセル・エラー・タイムアウトの CI がある
- `.github/full-plan-execution.flag` が全プラン実行モードを示し、`docs/plan.md` の完了認証に失敗する
- `scripts/hooks/` 変更後の hook smoke test が未実施である

以下は block せず、`additionalContext` / `hookSpecificOutput` のリマインドとして返す。

- OPEN PR の Copilot / Codex / Claude レビューが未到着、未返信、または状態取得不能
- Copilot AI がレビュワー未設定で、レビューが必要な PR がある
- Copilot レビューが Round 4 以降へ進もうとしている

詳細な round budget と停止条件は `.github/instructions/review-loop.instructions.md` と `ai/operation-policy.yml` に従う。
AI レビューループは最大 3 ラウンドであり、Round 3 後の非ブロッキング Must/Should は Backlog 化、即時ブロッカーだけ fail-close とする。

## fail-close 設計

- PR lookup、レビュー状態、Copilot レビュー数、未返信スレッド数の一時的な取得失敗は fail-open とし、リマインドに降格する。
- CI API の取得失敗は hook 滞留を避けるため fail-open する場合がある。ただしエージェントの CI 確認義務は免除しない。
- 全プラン完了認証では `docs/plan.md` を読めない、またはフラグが壊れている場合は fail-close とする。
- Claude Code Remote など hook が完全検査できない環境では、hook は案内を出し、エージェント側が MCP / GitHub ツールで完了ゲートを確認する。

## レビューループ

Copilot レビューループは `.github/instructions/review-loop.instructions.md` を正本とする。Hook は、レビュー待ち・未返信・Round 予算超過を機械的に検出できる範囲でリマインドするだけであり、レビュー依頼・返信・再確認を代行しない。

## AI Operating Model 検証

運用ルール変更時は、Hook だけでなく AI Operating Model の validator も実行する。

```bash
uv run python scripts/ai/audit_document_inventory.py
uv run python scripts/ai/validate_context_index.py
uv run python scripts/ai/validate_harness_ready.py
uv run python scripts/ai/run_pre_pr_critical_review.py --changed-only --allow-should
python ci/policy_check.py
```

## 一回限りプロンプト

`COPY_PASTE_TO_CODEX_ONCE.md` のような一回限りプロンプトは Hook の入力にも日常コンテキストにも含めない。監査目的で残す場合は `docs/ai/document-inventory.md` で `ARCHIVE` として分類する。
