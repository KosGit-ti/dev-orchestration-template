# オーケストレーション

この文書は AI エージェント連携の入口である。複数プラットフォーム（Claude Code / Codex / Copilot / Cursor 等）の routing（入口文書・path 指示・MCP・tier 再現・hooks）は各ハーネスの入口文書（`CLAUDE.md` / `AGENTS.md` / `.github/copilot-instructions.md` 等）と roster frontmatter（`.claude/agents/*.md` / `.github/agents/*.agent.md` / `agents/*.agent.md`）が定義する。日常コマンド解釈、実行方針、仕様駆動開発、文書読込範囲、PR 前批判レビュー、`release_to_main` の詳細は `ai/*.yml` を正本とし、本書では重複定義しない。

## 正本

| 領域 | 正本 |
| --- | --- |
| コマンド解釈 | `ai/command-router.yml` |
| 実行方針・安全停止・release tier | `ai/operation-policy.yml` |
| 仕様駆動開発 | `ai/sdd-policy.yml` |
| ワークフロー手順 | `ai/coherence-workflow.yml` |
| 文書読込範囲 | `ai/context-index.yml` |
| 文書統治 | `ai/document-governance.yml` |
| PR 前批判レビュー | `ai/pre-pr-review-policy.yml` |
| Copilot レビューループ | `.github/instructions/review-loop.instructions.md` |
| full-plan delivery loop | `ai/coherence-workflow.yml` / `ai/operation-policy.yml` |

## 動作コンテキスト

- `daily_development`: 日常開発。ユーザーは不在になり得るため、通常の戦術判断は聞き返さず、リポジトリ文脈で安全に解く。
- `governance_change`: AI ハーネス、運用ルール、文書統治、branch/release policy、hook policy など将来の運用を変える作業。必要な確認・対話を許可する。

両者の分類は `ai/command-router.yml` と `ai/operation-policy.yml` に従う。日常開発の簡易コマンドを governance change に拡張解釈しない。

## 日常 3 コマンド

日常開発は次の 3 コマンドで動く。

1. `◯◯のプランを見直して直近のプランに入れてほしい。詳細設計と要件定義もあわせて見直すこと。`
2. `プランのうちバックログの中身をすべて直近のプランに入れて。`
3. `プランの全実施。`

各コマンドの workflow、必読文書、完了条件は `ai/command-router.yml` と `ai/coherence-workflow.yml` を参照する。

## 仕様駆動開発の鎖

意味のある変更は、`ai/sdd-policy.yml` の coherence chain を維持する。

```text
User Intent
→ Expectation Ledger
→ Requirements
→ Design
→ Spec
→ Implementation
→ Tests
→ Verification
→ Propagation
→ Runtime Smoke
→ Evidence
```

`Plan` だけ、`Design` だけ、テスト成功だけでは完了扱いにしない。ユーザー可視の変更では Runtime Smoke と Evidence を残す。

## エージェント役割

| 役割 | 主な責務 | tier（論理） | 正本 |
| --- | --- | --- | --- |
| orchestrator | ルーティング、文脈読込、委譲、統合 | standard（分解・進行管理の委譲先。最上位はメイン会話） | `.github/agents/orchestrator.agent.md` |
| implementer | 実装と関連 docs 更新 | standard | `.github/agents/implementer.agent.md` |
| implementer-single-file | 局所変更 | light | `.github/agents/implementer-single-file.agent.md` |
| test-engineer | テスト設計・実行 | light | `.github/agents/test-engineer.agent.md` |
| auditor-spec | 仕様整合監査 | standard | `.github/agents/auditor-spec.agent.md` |
| auditor-security | セキュリティ監査 | light | `.github/agents/auditor-security.agent.md` |
| auditor-reliability | 信頼性・テスト品質監査 | standard | `.github/agents/auditor-reliability.agent.md` |
| pre-pr-critical-reviewer | PR 前の批判レビュー | standard | `.github/agents/pre-pr-critical-reviewer.agent.md` |
| release-manager | 最終判定 | high | `.github/agents/release-manager.agent.md` |

監査は独立監査として行う。Must / Should / Nice の分類、根拠、再現手順を添える。

## モデル階層委譲（model_tiering_v1）

正本は `ai/operation-policy.yml` の `subagent_delegation.model_tiering`。要点:

- **メイン会話は常に最上位 Orchestrator**（セッションで役割指定は不要）。primary_orchestrator（その時点で利用可能な最上位の総合推論モデル。解決の正本は [ai/capability-registry.yml](../ai/capability-registry.yml)）が直接行うのは統括・目的関数監査・本質的問題分解・設計判断・計画立案・批判的検証・最終統合・ユーザー報告のみ。
- **単調・機械的・大量作業は下位 tier のサブエージェントへ委譲する**（bulk 編集・定型ドラフト・探索・証跡整形・閉処理 bookkeeping）。1〜2 ステップの軽微な作業は直接実行してよい。
- 論理 tier（Bloom 風 3 段・[docs/ai/shogun-operating-model.md](ai/shogun-operating-model.md) の写像方針）: light=理解/抽出/探索/定型整形/定型テスト（ダミーデータの単体・境界値）、standard=実装/複雑なテスト設計（統合・再現性・リーク検査）/ドラフト、high=設計/リスク/監査/リリース判定、top=統括のみ。役割表の tier と本定義は一致させる（test-engineer は定型テスト担当＝light。複雑なテスト設計は implementer〔standard〕が担う）。
- tier→モデルの写像正本は各ハーネスの roster frontmatter `model:`（`.claude/agents/*.md`・`.github/agents/*.agent.md`〔Copilot〕・`agents/*.agent.md`〔Codex〕）。3 ハーネスで同一の論理 tier を維持する。capability role（primary_orchestrator / independent_peer_auditor 等）と実行時解決の正本は [ai/capability-registry.yml](../ai/capability-registry.yml)。

## 4. サブエージェント応答スキーマ

サブエージェントは Orchestrator へ全文ログや調査ダンプを返さず、次の構造化要約だけを返す。
Orchestrator はこの要約をさらに圧縮し、メイン会話には「実施内容 / 結果 / 次アクション」を中心に報告する。

必須フィールド:

- `role`: サブエージェント名（例: `implementer`, `auditor-spec`）
- `task_id`: 対象タスク ID（例: `TASK-001`）
- `status`: `done` / `blocked` / `needs_input`
- `summary`: 結論を 3〜5 行で記載する
- `changed_files[]`: 変更・確認した主要ファイル
- `evidence[]`: `file:line`、実行コマンド、結果など検証可能な根拠
- `next_actions[]`: Orchestrator が次に行うべき操作
- `risks[]`: 残リスク、未確認事項、ブロッカー

最小例:

```json
{
  "role": "implementer",
  "task_id": "TASK-001",
  "status": "done",
  "summary": ["端的報告契約を追加", "roster 検証を実装"],
  "changed_files": ["scripts/ai/validate_harness_ready.py"],
  "evidence": [{"command": "uv run python scripts/ai/validate_harness_ready.py", "result": "pass"}],
  "next_actions": ["auditor-spec に仕様監査を委譲"],
  "risks": []
}
```

## レビューループ上限

push 後の AI レビューループは `.github/instructions/review-loop.instructions.md` を正本とし、最大 3 ラウンドで扱う。Round 3 後の非ブロッキング Must / Should は Backlog 化し、即時ブロッカーだけ fail-close とする。オーケストレーション文書側で Round 4 以降の自動 push / review request を許可する別ルールを定義しない。

## full-plan delivery loop

`プランの全実施` / `execute_current_queue` は、実装・ローカル検証・証跡更新だけでは完了ではない。`ai/coherence-workflow.yml` の step 順に、commit / push / PR 作成 / push 後 AI レビュー / CI 全チェック確認（`gh pr checks`）/ release-manager / 全プラン実行モードの merge / main pull / plan 更新 / 次タスク遷移まで進める。詳細な必須ライフサイクルは `ai/operation-policy.yml` の `full_plan_delivery_pipeline` を正本とする。

## PR 前批判レビュー

PR 作成前に以下を実行し、`docs/ai/pre-pr-critical-review.md` を更新する。

```bash
uv run python scripts/ai/run_pre_pr_critical_review.py --changed-only --allow-should
```

このレビューは Copilot レビューループの代替ではない。Copilot レビューは push 後に `.github/instructions/review-loop.instructions.md` に従って完了まで追跡する。

## release_to_main

main 一本化運用では feature/fix → main の PR がそのままリリースであり、別個の develop→main 昇格工程は存在しない。`release_to_main` の risk tier（green/yellow/red）は「変更リスクに応じた main 直 PR のレビュー軽量化」として適用する（green=低リスク変更の軽量レビュー、yellow/red=高リスク変更のフルレビュー）。閾値・red_if_touches パスは `ai/operation-policy.yml` を正本として現状維持とする。

- green: 軽量確認。policy check、差分確認、変更箇所 smoke を中心にする。
- yellow: 追加の AI review、targeted test、release summary を求める。
- red: full CI、rollback、release manifest を必須にする。

red 判定対象は workflow、依存・lockfile、DB migration、設定、秘密情報関連など `ai/operation-policy.yml` に従う（プロジェクト固有の重要操作は `project-config.yml` で追加定義する）。

## 文書統治

- 入口文書は短く保ち、長文方針を `ai/*.yml` や正本 docs へ寄せる。
- 通常セッションで全文読みにしない。読込範囲は `ai/context-index.yml` に従う。
- 初回棚卸しでは文書削除をしない。分類は `docs/ai/document-inventory.md` に記録する。
- 一回限りプロンプトは日常コンテキストに入れない。必要に応じて `ARCHIVE` として分類する。

## 検証コマンド

AI Operating Model 関連の基本検証は次を使う。

```bash
uv run python scripts/ai/audit_document_inventory.py
uv run python scripts/ai/validate_context_index.py
uv run python scripts/ai/validate_harness_ready.py
uv run python scripts/ai/run_pre_pr_critical_review.py --changed-only --allow-should
python ci/policy_check.py
```
