# Copilot Repository Instructions

## Language（最優先）

- すべての成果物（PR タイトル/本文、Issue 本文、レビューコメント、ADR、docs 更新要約）は日本語で書く。
- コードの識別子は英語でよいが、コメント・docstring・説明文は日本語で書く。
- PR 本文は必ず `.github/PULL_REQUEST_TEMPLATE.md` の構成に合わせる。

## Scope & Safety（最優先）

- 禁止操作（P-001）を実装しない。
- API キー/トークン/認証情報/個人情報/実データをコミットしない（P-002）。`.env` はローカルのみ。
- 判断不能な場合は安全側に倒す（P-010: フェイルクローズ）。
- 制約は常に優先する（P-003）。制約回避のコードを書かない。

## Single Source of Truth（正本）

| 正本 | ファイル |
|---|---|
| 要件 | `docs/requirements.md` |
| ポリシー | `docs/policies.md` |
| 制約仕様 | `docs/constraints.md` |
| アーキテクチャ | `docs/architecture.md` |
| 運用手順 | `docs/runbook.md` |
| 重要判断 | `docs/adr/` |
| 計画 | `docs/plan.md` |

### 作業方針

- 会話ログではなく、必要な前提・決定は正本 docs へ反映する。
- `docs/plan.md` の「Next」以外に勝手に着手しない（人間が指示した場合を除く）。
- 正本に矛盾がある場合は修正を提案し、暗黙に無視しない。

## Development Workflow

- 変更は 1PR で理解できる粒度に分割する（P-031）。
- 変更を加えたら必ずローカルまたは CI でテストを通す。
- CI が失敗する PR は提出しない。
- PR には検証手順と結果を必ず記載する（AC-040）。

## Autonomous Execution（自動実行モード）

以下のトリガーフレーズでユーザーが指示した場合、Orchestrator エージェントは **承認確認なしに自動実行パイプラインを開始** し、最後まで自律的に実行する：

- 「計画に従い作業を実施して」「Nextを実行して」「plan.md に従って進めて」「作業を開始して」「タスクを実行して」

### 自動実行パイプラインの概要

1. `docs/plan.md` の Next 先頭タスクを選択する
2. フィーチャーブランチを作成する
3. implementer にコード実装を委譲する
4. test-engineer にテスト作成を委譲する
5. ローカル CI を実行する（失敗時は修正ループ、最大3回）
6. 3つの監査エージェントに監査を委譲する
7. Must 指摘が残れば修正ループ（最大3回）
8. コミット・プッシュし、PR を作成する（`gh pr create`）
   - PR 本文に `Closes #XX` を必ず記載する（plan.md の Issue 対応表を参照）
9. PR の CI を監視する（失敗時は修正→再プッシュ、最大3回）
10. release-manager に最終判定を委譲する
11. **人間の最終承認を得てからマージする**（自動マージは禁止）
12. マージ後の Issue/Project 検証（独立監査）
    - `issue-lifecycle` ワークフローが対象 Issue を自動 Close する
    - GitHub Projects のステータスが「Done」に自動更新される
    - plan.md の Done セクションとの整合性を確認する

### 停止条件

- ポリシー違反（P-001〜P-003）の検出
- 修正ループが3回を超えた場合
- plan.md の Next が空の場合

## Issue Lifecycle（Issue / Project 連動）

- PR 本文には必ず `Closes #XX` を記載し、完了する Issue を明示する。
- `issue-lifecycle` ワークフロー（`.github/workflows/issue-lifecycle.yml`）が PR マージ時に：
  - PR 本文から `Closes/Resolves/Fixes #XX` を抽出する
  - `plan.md` の Done セクションとの整合性を独立監査する
  - 対象 Issue を自動 Close する
- GitHub Projects の built-in ワークフローにより、Issue Close 時にステータスが「Done」に自動更新される。
- 手動で Issue を Close する場合は `gh issue close #XX --comment "理由"` を使用する。

## Review & Audit Attitude

- 監査（review）は相互合意ではなく独立監査として行う。
- 指摘は「Must / Should / Nice」に分類し、根拠（ファイル/行/再現手順）を添える。
- 不確実な場合は仮説として述べ、確認手段（テスト追加、ログ追加、感度分析）を提案する。
