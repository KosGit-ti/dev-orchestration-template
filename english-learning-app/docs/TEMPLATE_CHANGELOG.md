# テンプレート変更履歴（TEMPLATE_CHANGELOG）

このファイルは **テンプレート自身（ai-dev-template、旧: dev-orchestration-template）** の版と変更を記録する正本である。
子リポジトリの内容ログではなく、テンプレート基盤の版管理に用いる。
`scripts/template_update.py check` は本ファイルを参照し、適用済み版より新しいエントリを抜粋表示する。

書式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準じ、
版番号は [セマンティックバージョニング](https://semver.org/lang/ja/) に従う。
分類見出し: 追加 / 変更 / 削除 / 非採用（意図的に取り込まなかった決定）/ 移行手順。

---

## [3.1.0] - 2026-07-07

### 削除

- Spec Kit 一式（`.specify/`・`.claude/skills/speckit-*` 14 件・`.github/agents/speckit.git.*` 5 件・
  `.github/prompts/speckit.git.*` 5 件・`docs/spec-kit-bridge.md`）: 導入元プロジェクトが
  「使用痕跡がなく agent/skill 二重管理のコストが上回る」としてユーザー承認のうえ全削除した判断
  （2026-07-07）に追随。必要なプロジェクトは公式配布から個別導入する。

### 変更

- `template-catalog.yml`: spec-kit feature を除去し `template.version` を 3.1.0 へ更新。
- `scripts/template_update.py` の `export`: 分類元をテンプレート側マニフェスト優先に変更（子リポジトリが apply 保護のためにローカルマニフェストを保守的にしても、逆反映の範囲が狭まらない）。
- `ai/context-index.yml` / `.template-update.yml` / `scripts/ai/audit_document_inventory.py` /
  `docs/agent-skills-integration.md` / `README.md` から Spec Kit 参照を除去。

## [3.0.0] - 2026-07-07

AI 駆動開発環境（AI Operating Model・整合性駆動開発・orchestration・hooks・3ハーネス対称構成）を
汎用テンプレートとして刷新した大規模更新。名称を **ai-dev-template** へ改称する決定を含む。

### 追加

- **AI Operating Model**（`ai/*.yml` 8ファイル）: コマンド解釈・運用方針・能力レジストリ・
  仕様駆動開発・整合性ワークフロー・文書統治・PR 前批判レビューの正本を導入。
- **3ハーネス対称構成**: Claude Code（`.claude/`）/ Copilot（`.github/`）/ Codex（`AGENTS.md`）の
  入口と roster を対称に整備。共通正本は `.github/agents/*.agent.md`。
- **完了事故防止ガード（hooks）**: `scripts/hooks/`（指示元判別・push 後リマインド・
  完了ゲート検査等）と Claude Code 用 `.claude/hooks/`、`.github/hooks/` を導入。
- **エージェントスキル**: `agmsg`（エージェント間メッセージング）/ `stop-ai-slop-jp`（日本語文体）を
  `.agents/skills/` に同梱し、`.claude/skills/` からリンク。
- **Spec Kit 連携**（`.specify/` と speckit スキル群）: 仕様駆動開発の橋渡し。
- **PR 前批判レビュー**（`scripts/ai/`・`scripts/run_ai_review.py`）と品質ガイド類。
- **アップデート機構 v2**: `template-catalog.yml`（機能カタログ・版の正本）、
  再設計した `.template-update.yml`（4カテゴリ分類・最具体一致）、
  `scripts/template_update.py`（`check` / `apply` / `export` サブコマンド）、
  子リポジトリ側の状態ファイル `.template-version.yml`、
  モデル非依存の手順書 `.github/instructions/template-sync.instructions.md` を導入。
- **運用台帳の雛形**（`docs/ai/`）: 意思決定・実行・期待値・人手要件等の ledger テンプレート。

### 変更

- 入口3ファイル（`CLAUDE.md` / `AGENTS.md` / `.github/copilot-instructions.md`）の
  「第一目的」を汎用プレースホルダ（`{{PROJECT_PURPOSE}}`）へ置換し、テンプレート同期節を追加。
- `docs/UPGRADE_GUIDE.md` を v2 機構前提に全面改訂。「Copilot 専用」前提を撤廃し
  「使用中の AI アシスタント（Claude Code / Copilot / Codex 等）」表記へ統一。
- CI（`.github/workflows/ci.yml`）を `main` / `master` 両対応、
  policy-check → lint → format → type-check → test → secret-scan の構成へ整理。
- 組織名表記を **KosGit-Dev** に統一（旧: KosGit-ti / githypn を全置換）。

### 削除

- `.devcontainer/`（Dev Container / Codespaces 一式）: 個人開発では過剰なため廃止。
- `.github/workflows/staging.yml` / `production.yml`: プレースホルダ工程。main 一本化運用に不整合。
- トップレベル `agents/`: `.github/agents/` の古い重複コピー。正本を一本化。
- `docs/a2a-design/`（構想資料）と過去の改善ラウンド内部生成物、`docs/mobile-workflow.md`。
- 各文書内の devcontainer / Codespaces / staging / production ブランチ参照。

### 非採用（意図的に取り込まなかった決定）

- **多プロバイダレビュー証跡ゲート**（review_report_gate / ci_final_gate / acceptance_audit /
  run_ci_fallback_review と対応 workflow）: 個人開発の既定には過剰な承認ルートのため既定では持たない。
  必要な場合は導入元プロジェクトから取り込み可能。既定は「Copilot 1ラウンド＋任意の代替AIレビュー」。
- **CODEOWNERS**: 承認ルートを増やすだけのため導入しない。
- `mutmut`（変異テスト）/ `pip-audit`: 個人開発の既定では過剰なため CI から除外。

### 移行手順（既存の子リポジトリ向け）

1. `python scripts/template_update.py check` で更新有無を確認する。
2. `python scripts/template_update.py apply --dry-run` で影響範囲（未分類 0 件）を確認する。
3. `python scripts/template_update.py apply` を実行し、lint / test を通してコミットする。
4. 入口3ファイルと `project-config.yml` は `add_only` / `never_update` のため自動更新されない。
   `docs/UPGRADE_GUIDE.md` の手順に従い、手動 diff で改善点を取り込む。
5. GitHub 上のリポジトリ名変更（ai-dev-template への改称）はユーザー操作。旧 URL は
   GitHub の自動リダイレクトで動作するため既存参照は壊れない。

---

## [2.0.0] - 2025 年（スマートアップデート導入）

### 追加

- **スマートアップデート機構 v1**: `.template-update.yml` マニフェストと
  `scripts/template_update.py` により、ファイルごとに
  「上書き / スキップ / 新規のみ」を自動判定する選択的アップデート方式を導入。
- 4カテゴリ分類（`always_update` / `never_update` / `add_only` / `sample_only`）と
  dry-run・バックアップブランチ・ポストチェックを追加。

### 変更

- 従来の `git merge --allow-unrelated-histories` 方式を非推奨化
  （プロジェクト固有ファイル上書きのリスクを回避）。

---

## [1.0.0] - 初期テンプレート

### 追加

- 開発オーケストレーションテンプレートの初版。エージェント roster、CI/CD の雛形、
  ポリシー・要件・設計・計画の文書骨格、サンプルコードとテストを提供。
