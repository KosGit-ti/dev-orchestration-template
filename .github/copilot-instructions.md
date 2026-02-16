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
   - **重要**: 型チェックのスコープにテストディレクトリを含めること
5.5. 全体エラー検証（ゲートチェック）
   - get_errors ツールでワークスペース全体のコンパイルエラー・型エラーを取得する
   - **エラーがゼロになるまで監査ステップに進まない**
6. 3つの監査エージェントに監査を委譲する
7. Must 指摘が残れば修正ループ（最大3回）
8. コミット・プッシュし、PR を作成する（`gh pr create`）
   - PR 本文は必ず `--body-file` で一時ファイル経由で渡す（`--body` は使用禁止。`\n` がリテラル文字として送信され、Markdown が崩壊する）
   - PR 本文に `Closes #XX` を必ず記載する（plan.md の Issue 対応表を参照）
9. PR の CI を監視する（失敗時は修正→再プッシュ、最大3回）
10. Copilot コードレビュー対応ループ（最大3回）
    - `gh api` でレビューコメントを取得し、Must/Should/Nice に分類する
    - Must/Should 指摘があれば implementer に修正を委譲し、再プッシュする
    - 各レビューコメントに対応結果を `gh api .../comments/{id}/replies` で返信する
    - **CI の完了とレビューの完了は独立事象** — CI 通過をレビュー完了と混同しない
    - **レビュー検出はカウント比較で行う**（state だけで判定しない。古いレビューを誤認するため）
    - **コメント安定化フェーズ**: レビュー検出後、コメント数が安定するまで追加待機する（指摘が時間差で到着するため）
    - **再レビュー依頼はレビュアー Remove → Re-add で行う**（add だけではトリガーされないことがある）
    - 再レビュー待機: 30秒間隔 × 20回（最大10分）ポーリング＋安定化フェーズ最大2分
    - **タイムアウト時は「指摘なし」と自動判定せず、ユーザーに報告して停止する**
    - 指摘がゼロまたは approve 済みならループ終了
11. release-manager に最終判定を委譲する
12. **人間の最終承認を得てからマージする**（自動マージは禁止）
13. マージ後の Issue/Project 検証（独立監査）
    - `issue-lifecycle` ワークフローが対象 Issue を自動 Close する
    - GitHub Projects のステータスが「Done」に自動更新される
    - plan.md の Done セクションとの整合性を確認する

### 停止条件

- ポリシー違反（P-001〜P-003）の検出
- 修正ループが3回を超えた場合
- plan.md の Next が空の場合

## Plan Revision（計画修正モード）

以下のトリガーフレーズでユーザーが指示した場合、計画修正パイプラインを実行する：

- 「計画を修正して」「計画を見直して」「新しい要件を追加して」「Issue を追加して」「Backlog に追加して」「計画を更新して」

### 計画修正パイプラインの概要

1. ユーザーから新要件・変更内容をヒアリングする
2. 影響範囲を評価する（既存 Phase への追加 or 新 Phase 作成）
3. `docs/plan.md` を更新する（Backlog/Next 追加、ロードマップ調整、変更履歴記録）
4. GitHub Issues を作成する（`gh issue create`）
5. Issues を GitHub Project に追加する（`gh project item-add`）
6. Project フィールドを設定する（Status / Type / Phase）
7. `plan.md` の Issue 対応表を更新する
8. Next の調整（空きがあれば昇格、なければ Backlog に留置）
9. 変更をコミット・プッシュする（main に直接、計画文書のため PR 不要）

### 注意事項

- 自動実行パイプライン実行中に計画修正は行わない（完了後に修正する）
- Issue 対応表と GitHub Issues / Project の整合性を常に維持する
- 既存タスクの変更・削除は Issue の更新・Close も合わせて行う

## General Request（汎用リクエストモード）

自動実行モード・計画修正モードのいずれのトリガーにも該当しないリクエスト（改善提案、調査依頼、設定変更、リファクタリング指示など）に対しても、品質管理を省略しない。

### 適用条件

以下のいずれかに該当する場合、汎用リクエストモードとして品質管理手順を適用する：
- コードファイル（`src/`, `tests/`, `ci/`, `scripts/`）を変更する場合
- エージェント定義ファイル（`.github/agents/`, `agents/`）を変更する場合
- 設定ファイル（`configs/`, `pyproject.toml`）を変更する場合
- ドキュメント（`docs/`）以外のファイルを変更する場合

### 品質管理手順

1. **ローカル CI の実行**: 変更後に必ず CI ツールを実行する
2. **全体エラー検証**: get_errors ツール（filePaths 省略）でワークスペース全体のエラーがゼロであることを確認する
3. **変更ファイルの個別検証**: 変更したファイルに対して get_errors ツール（filePaths 指定）で個別検証する
4. **セルフレビュー**: 変更内容がポリシー（P-001〜P-003）に違反していないか自己確認する

### ドキュメントのみの変更の場合

`docs/` 配下のドキュメントのみの変更で、コードへの影響がない場合は上記手順を省略可能。
ただし正本間の整合性（requirements ⇔ architecture ⇔ plan）は確認する。

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
