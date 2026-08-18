# Copilot Repository Instructions

このファイルは Copilot 向けの薄い入口である。日常コマンド解釈、実行方針、仕様駆動開発、文書読込範囲、PR 前批判レビュー、`release_to_main` の詳細は `ai/*.yml` を正本とする。

Copilot の本 repo における通常 role は PR review である。主実装と orchestration は Claude Code / Codex が担う。明示依頼がない限り Copilot を routine implementer や司令塔として扱わない。

## 第一目的（最優先）

本 repo の第一目的は、学習者が英語を母語へ訳さずに理解できる状態へ到達させることである（FR-001）。学習コンテンツへ日本語訳を混ぜないことは第一目的に直結する制約であり（P-101）、UI 文言の日本語化とは区別する。最上位レベルは TOEIC 990 / TOEFL 120（いずれも満点）を上端に置く。

## Language（最優先）

- すべての成果物（PR タイトル/本文、Issue 本文、レビューコメント、ADR、docs 更新要約）は日本語で書く。
- コードの識別子は英語でよいが、コメント・docstring・説明文は日本語で書く。
- PR 本文は必ず `.github/PULL_REQUEST_TEMPLATE.md` の構成に合わせる。

## Default Skills（最優先）

- 日本語のチャット回答、PR 本文、Issue、レビューコメント、要件、仕様、設計、ADR、docs、コメント、docstring を作成・編集するときは、既定で `stop-ai-slop-jp` の基準を適用する。
- コード本体、識別子、機械生成 JSON/YAML、コマンド出力の引用、外部仕様の逐語引用には `stop-ai-slop-jp` を適用しない。
- `stop-ai-slop-jp` 適用時は、意味を変えずに false agency、命題型 H2、全方位肯定、過剰な抽象語、全角ダッシュ、引用符の乱用を確認する。
- `agmsg` は Claude Code / Codex 間で重大な曖昧性や方式比較が必要な場合の助言経路であり、Copilot review の必須工程ではない。直接 DB や team registry を編集しない。

## Communication Style

- 端的報告と委譲方針は `ai/operation-policy.yml` の `reporter_communication` / `subagent_delegation` を正本とする。
- メイン会話は「実施内容 / 結果 / 次アクション」の短い報告を基本とし、重い読込・実装・テスト・監査・探索はサブエージェントへ委譲する。
- サブエージェントの返答は `docs/orchestration.md`「応答契約」に従う。
- `execute_current_queue` は `ai/operation-policy.yml` の `full_plan_delivery_pipeline` と `ai/coherence-workflow.yml` を正本とする。最終判定は同一 head の release judgement と merge 前契約検査まで進める。
- model は版付き ID を固定せず、`ai/capability-registry.yml` の provider role と routing contract に従う。accepted review provider と auto-trigger provider を混同しない。
- Orchestrator の行動仕様（進捗の裏取り・正直な測定・スコープ規律・turn 終了規律等の 12 項目）は `ai/operation-policy.yml` の `orchestrator_behavior` を正本とし、本ハーネスでも同仕様に従う。
- 設計比較や未知障害の深掘りでは、必要な根拠を省略しない。

## AI Operating Model

作業開始時は、`ai/command-router.yml` でリクエストを分類し、`ai/context-index.yml` に従って必要な文書だけを読む。

日常開発でユーザーが使うコマンドは次の 3 つである。意味は `ai/command-router.yml` と `ai/coherence-workflow.yml` を正本とする。

1. `◯◯のプランを見直して直近のプランに入れてほしい。詳細設計と要件定義もあわせて見直すこと。`
2. `プランのうちバックログの中身をすべて直近のプランに入れて。`
3. `プランの全実施。`

- `daily_development` では通常の戦術判断をユーザーへ聞き返さず、リポジトリ文脈で安全に解く。
- `governance_change` では将来の運用に影響するため、必要な確認・対話を許可する。
- 仕様駆動開発の鎖は `ai/sdd-policy.yml` を正本とする。
- 文書肥大化対策は `ai/context-index.yml` と `ai/document-governance.yml` を正本とする。

## Context Maintenance（最優先）

作業開始時に必ず以下を確認する。

1. `.github/instructions/review-loop.instructions.md`
2. `.github/PULL_REQUEST_TEMPLATE.md`
3. `ai/context-index.yml` が指定する対象モードの必読文書

`/memories/` マウントは本リポジトリの標準環境に存在しない（2026-07-03 是正）。マウントを持つ環境でのみ配下を確認する。

長時間セッションでは compact（会話圧縮）で判断構造が失われる前提で動く。重い作業に入る前に PR 番号・レビュー状態・次アクションを `docs/ai/execution-ledger.md` へ先に書く（ledger 先行更新）。復旧は `.github/full-plan-execution.flag` と ledger の読み直しに一本化する（正本: `ai/operation-policy.yml` の `compact_survival_contract`）。

セッション開始時・compact 相当の後は `docs/ai/task-checkpoint.md` → `.github/full-plan-execution.flag` → `docs/ai/execution-ledger.md` / `docs/ai/decision-ledger.md` の順で読み再接地する（正本: `ai/operation-policy.yml` の `compact_survival_contract`〔v2〕）。

作業開始時に `uv run python scripts/ai/resolve_current_state.py --context transition` を実行し、mainline・last-settled checkpoint（`docs/ai/task-checkpoint.md`）・`docs/plan.md`の`active-queue:v1`・`.github/full-plan-execution.flag`を合成したcurrent stateを取得する（N-598C・SessionStart／AGENTS／Copilot／CI共通entrypoint。`docs/specs/N-598C-exact-current-state-resolver.md`）。exit 0かつissuesが空の場合だけ`active_queue`のtask/substepを着手対象とする。fail（非0終了またはissues非空）時はcheckpointを信用せず、`scripts/ai/write_task_checkpoint.py`による再生成が先（N-598B・`docs/specs/N-598B-semantic-freshness-validator.md`）。R-025の`research_resume`はGit settled roundとlocal resume roundを別fieldのままresolver出力で照合し、単一roundへ丸めない。CIは同じresolverを`--context report`（ignored local flagを要求しないreport mode）で実行する。

## Scope & Safety（最優先）

- 禁止操作（P-001）を実装しない。
- API キー/トークン/認証情報/個人情報/実データをコミットしない（P-002）。`.env` はローカルのみ。
- 判断不能な場合は安全側に倒す（P-010: フェイルクローズ）。
- 制約は常に優先する（P-003）。制約回避のコードを書かない。
- 既存の未コミット変更はユーザーの作業として扱い、明示指示なしに revert・上書き・stage しない。

## Single Source of Truth（正本）

| 種別 | ファイル |
| --- | --- |
| AI コマンド解釈 | `ai/command-router.yml` |
| AI 運用方針 | `ai/operation-policy.yml` |
| モデル選択（capability role） | `ai/capability-registry.yml` |
| 仕様駆動開発 | `ai/sdd-policy.yml` / `ai/coherence-workflow.yml` |
| 文書読込範囲 | `ai/context-index.yml` |
| 文書統治 | `ai/document-governance.yml` |
| PR 前批判レビュー | `ai/pre-pr-review-policy.yml` |
| 要件 | `docs/requirements.md` |
| 詳細設計 | `docs/design.md` |
| ポリシー | `docs/policies.md` |
| 制約仕様 | `docs/constraints.md` |
| アーキテクチャ | `docs/architecture.md` |
| 運用手順 | `docs/runbook.md` |
| 重要判断 | `docs/adr/` |
| 計画 | `docs/plan.md` |

## Development Workflow

- 変更は 1PR で理解できる粒度に分割する（P-031）。
- 変更を加えたら必ずローカルまたは CI でテストを通す。
- **発覚した lint 警告・バグ・構文/型エラー・テスト失敗は、その場（同一変更内）で修正し後続へ持ち越さない（P-065 fix-on-discovery）**。CI が止めない種別（markdown lint〔`.markdownlint.json`〕・エディタ警告・docs リンク切れ）も dismiss せず拾う。繰延は P-031 上妥当な大規模問題に限り Backlog ID + 残リスク + 最小封じ込めを伴う場合のみ。技術文書（cron 式・`__dunder__`・パス）への blanket `markdownlint --fix` は `*`/`__` を強調記法と誤認して破壊するため code span 保護で手動修正する。
- 個人開発のコスト削減のため main 一本化運用。長命ブランチは `main` のみ。
- feature/fix ブランチは `main` から作成し `main` へ直接 PR する（`develop` は廃止）。
- `main` への直接コミットは禁止する。全プラン実行モードのタスク移動は、前タスク
  merge 後に作る次 task／terminal state-sync branch で checkpoint・execution ledger
  と同時に更新し、その PR に含める。
- PR 前に `uv run python scripts/ai/run_pre_pr_critical_review.py --changed-only --allow-should` を実行し、結果を `docs/ai/pre-pr-reviews/<識別子>.md`（既定はブランチ名 slug。per-PR パス化）に残す。旧共有ファイル `docs/ai/pre-pr-critical-review.md` は移行済みで以後更新しない。
- `release_to_main` は通常リリースに統合（`develop` 昇格工程なし）。`ai/operation-policy.yml` の green / yellow / red risk tier に従う。

## 完了ゲート（task_complete 前に必ず確認）

- [ ] G-1: `git push` を実行した場合、CI が全 pass しているか（`gh pr checks`）
- [ ] G-2: `git push` を実行した場合、Copilot レビュー対応が完了しているか（未解決または未返信の active thread 0 件）
- [ ] G-3: オープンな PR に未レビューのコミットがないか
- [ ] G-4: `scripts/hooks/` 変更時は Hook が機能することをテスト済みか

PreToolUse Hook が `task_complete` を自動ブロックする環境でも、Hook に依存せず自分で確認する。

## コードレビュー観点（Copilot code review 向け・最優先）

PR review では次を最優先に見る。順序は重要度順である。

1. **学習コンテンツの言語（P-101）**: `web/content/**` にひらがな・カタカナ・漢字が入っていないか。訳語を格納できるフィールドが型へ追加されていないか。混入は Must で指摘する。
2. **fail-close**: 検査器・ゲート・スクリプトは前提違反時に黙って通らず、明示メッセージ + 非 0 exit で落ちること。`validate:content` に「警告だけ出して通す」段が追加されていないか（P-010）。
3. **認証境界（P-102 / C-002〜C-004）**: パスワードを素で保存する経路、PBKDF2 の反復回数を下げる変更、`requireAdmin` を迂回する管理操作、管理者の自己削除を許す変更は Must で指摘する。認証がクライアント完結である旨の UI 表示を消す変更も同様。
4. **進捗スキーマの移行（P-104 / C-005）**: `Progress.version` を上げる変更に `migrate()` の変換とテストが同伴しているか。未知の版を推測で読む実装になっていないか。
5. **出題の決定性（NFR-001 / C-007）**: `Math.random()` や `Date.now()` がロジック層（`lib/srs`・`lib/content`）へ入り込んでいないか。テストが実行のたびに結果を変えないか。
6. **設問の妥当性**: 誤答選択肢がレベルをまたいでいないか（消去法で解けてしまう）。`answerIndex` が範囲内か。`rationale` が本文から辿れる根拠になっているか。
7. **静的エクスポートとの整合**: サーバー前提の機能（動的ルート、Server Actions、実行時 fetch）が入っていないか。`basePath` を前提にした絶対パスが `next.config.ts` の外へ散っていないか。
8. **アクセシビリティ**: 選択肢ボタンの状態が色だけで伝えられていないか。`aria-*` とラベルが付いているか。`prefers-reduced-motion` を無視する演出が入っていないか。

指摘は具体的なファイル・行と、再現または反証可能な根拠を添え、修正案があれば併記する。inline comment を投稿できない環境でも、review body 本文に書かれた指摘はレビューループで全件対応されるため、ファイル/行番号を明記すること。

## PR フロー

- PR 本文は必ず `--body-file` で渡す。
- plan.md に Issue 対応があるタスクは `Closes #XX` を記載する。
- push 後の CI / Copilot レビューループは `.github/instructions/review-loop.instructions.md` を正本として完了まで追跡する。
- Copilot レビューは Round 3 を既定の収束点とする。Round 3 後の非ブロッキング
  Must/Should は Backlog 化し、適格理由、実質修正、延長証跡がある場合だけ terminal
  anchor 前に限って Round 4 以降へ延長する。
- PR 前批判レビューは Copilot レビューの前段であり、Copilot レビューループの代替ではない。

## GitHub トークン管理

長時間の自動化セッションでは、PR 操作前に `docs/runbook.md` の GitHub トークン管理手順を確認する。トークン値はログ、履歴、PR、Issue、コメントに残さない。

## Serena MCP 統合

Serena MCP は `.vscode/mcp.json` を参照する。`src/` の公開 API / シグネチャ変更では参照元追跡を必須、`src/` の内部ロジック変更では推奨、テスト / docs / config のみではスキップ可とする。

## テンプレート同期

本 repo は ai-dev-template（旧: dev-orchestration-template）の導入元・参照実装である。同期のトリガーフレーズと手順の正本は `.github/instructions/template-sync.instructions.md`、実行体は `scripts/template_update.py`（`check` / `apply` / `export` サブコマンド）である。

- 「アップデートを確認」: テンプレートの新バージョン有無を確認する（`check`）。
- 「アップデートを適用」: テンプレート更新を取り込む（`apply`）。本 repo の環境正本（`ai/`・`.claude/`・`.github/`・`scripts/` 等）は `.template-update.yml` で never_update 保護されており自動上書きされない（例外は同期機構自身の 2 ファイル: `scripts/template_update.py`・`.github/instructions/template-sync.instructions.md` のみ always_update）。取り込みが必要な差分は check の結果を見て手動 diff で反映する。
- 「テンプレートに変更を反映」: 本 repo の環境改善をテンプレートへ逆反映する（`export`。分類はテンプレート側マニフェストを使用し、反映後にテンプレート側で汎用化する）。

## 一回限りプロンプト

`COPY_PASTE_TO_CODEX_ONCE.md` のような一回限りプロンプトは日常コンテキストに含めない。監査目的で残す場合も `ai/context-index.yml` から除外し、`docs/ai/document-inventory.md` で `ARCHIVE` として分類する。
