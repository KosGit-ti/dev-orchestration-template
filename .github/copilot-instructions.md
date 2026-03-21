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
   - **PR 本文は必ず一時ファイルに書き出してから `--body-file` で渡す**（`--body` は禁止。`\n` がリテラル文字として送信され Markdown が崩壊するため）
   - MCP API で PR を作成・更新する場合も、本文は必ずファイル経由で渡す
   - PR 本文に `Closes #XX` を必ず記載する（plan.md の Issue 対応表を参照）
9. **PR 後の必須フロー** を実行する（下記「PR 後の必須フロー」セクションに従う。省略禁止）
10. release-manager に最終判定を委譲する
11. 承認待ち状態を報告して作業を終了する（マージ判断は人間が行う）
12. マージ後の Issue/Project 検証（独立監査）
    - `issue-lifecycle` ワークフローが対象 Issue を自動 Close する
    - GitHub Projects のステータスが「Done」に自動更新される
    - plan.md の Done セクションとの整合性を確認する

### 停止条件

- ポリシー違反（P-001〜P-003）の検出
- CI 修正ループが3回を超えた場合
- 予算超過（Budget cap）の検出（詳細は `docs/orchestration.md` §10 を参照）
- plan.md の Next が空の場合

## PR 後の必須フロー（最優先・省略禁止）

PR 作成後、または既存 PR へのコミット push 後は、以下のフローを **必ず最後まで実行する**。
このフローの省略は禁止する。push 後に即座に「マージしていいですか？」と聞くことも禁止する。
自動実行パイプラインだけでなく、あらゆる PR 関連の作業に適用する。

### A. CI 待機・対応

1. `gh pr checks <PR番号> --watch` で CI 完了を待機する（タイムアウト: 60秒×20回 = 最大20分）
2. CI が `pending` のまま20分経過した場合 → `gh pr view <PR番号> --json mergeable,mergeStateStatus` で状態を診断し、問題を報告する
3. CI 失敗時 → 修正して再 push し、A.1 に戻る（最大3回。超過時は停止・報告）
4. CI 成功 → B へ

### B. Copilot レビュー待機・対応

1. Copilot レビューの到着を 60秒間隔×最大20回（20分）ポーリングする
   ```bash
   gh api repos/{owner}/{repo}/pulls/{pr}/reviews \
     --jq '[.[] | select(.user.login == "copilot-pull-request-reviewer[bot]")] | length'
   ```
2. レビュー未到着（20分経過）→ 「Copilot レビューが未到着です。レビュー設定を確認してください。」と報告し、C へ
3. レビュー到着 → コメントを取得し Must / Should / Nice に分類
4. Must/Should 指摘あり →
   - implementer に修正を委譲
   - 各コメントに返信（修正内容とコミットハッシュを記載）
   - 修正を push → **A.1 に戻る**（CI 再待機から）
   - CI 通過後、再度 B.1 から Copilot レビュー到着をポーリングする
5. 指摘なし or approve 済み → C へ

**回数制限**: Copilot レビュー対応に回数制限はない（安全上限: 100回）。人間が再レビューを依頼する限り対応を続ける。

**重要**: Copilot 再レビューは API から自動依頼できない（GitHub の仕様制限）。push 後にレビューが来なかった場合は「Copilot 再レビューは人間が GitHub GUI の Re-request review ボタンから依頼してください」と報告する。

### C. 承認待ち報告（終了条件）

- 「CI 通過・レビュー対応完了。人間のレビュー・承認を待っています。」と報告する
- **禁止**: 「マージしていいですか？」「マージしてよいでしょうか？」等のマージ確認質問
- マージするかどうかは人間自身が判断する。エージェントはマージ実行もマージ確認もしない
- 人間から追加の指示（「Copilot 再レビューが来た」「レビュー対応して」等）があれば、B.1 に戻る

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
