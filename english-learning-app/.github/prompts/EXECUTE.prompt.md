# EXECUTE — 自動実行プロンプト

## 目的

`docs/plan.md` の Next にある先頭タスクを、ブランチ作成から PR 作成、push 後レビュー、CI 全チェック確認、release-manager、全プラン実行モードの merge / main pull / 次タスク遷移まで一貫して自動実行する。

## 前提

このプロンプトは Orchestrator エージェントの自動実行パイプラインと連動する。
ユーザーの承認を待たず、以下の手順を最後まで実行する。

## 手順

### 1. 計画確認
1. `docs/plan.md` の Next から先頭タスクを選択する
2. 受入条件（AC）を確認する
3. 関連する正本を読む：
   - `docs/requirements.md`（要件）
   - `docs/policies.md`（ポリシー）
   - `docs/constraints.md`（制約仕様）
   - `docs/architecture.md`（モジュール責務・依存ルール）

### 2. ブランチ作成
4. `git checkout main && git pull origin main`
5. `git checkout -b feat/<タスクID>-<説明>`

### 3. 実装・テスト
6. コードを実装する
7. テストを書く・通す

### 4. ローカル CI
8. CI コマンドをすべて実行し、全て成功するまで修正する（最大3回）
   - 具体的コマンドは `docs/runbook.md` を参照

### 5. 監査（Orchestrator 経由）
9. 3つの監査エージェントに監査を委譲
10. Must 指摘がゼロになるまで修正ループ（最大3回）

### 6. PR 作成
11. `git add -A && git commit -m "<conventional commit>"`
12. `git push -u origin HEAD`
13. PR 本文を一時ファイルに書き出し、`--body-file` で渡す（**`--body` は使わない**）：
    ```bash
    cat > /tmp/pr_body.md << 'PRBODY'
    <.github/PULL_REQUEST_TEMPLATE.md に従った本文をここに記載>
    PRBODY
    gh pr create --title "<タスクID>: <説明>" \
      --body-file /tmp/pr_body.md \
      --base main
    rm -f /tmp/pr_body.md
    ```
    **重要**: `--body` でインライン文字列を渡すと `\n` がリテラル文字として送信され、Markdown のレイアウトが崩壊する。
14. PR 本文に検証手順と結果を記載する（AC-040）
15. 関連 Issue を `Closes #XX` で紐付ける

### 7. PR 検証
16. `gh pr checks <PR番号>` を単発実行して CI 結果を確認（watchモード等のブロッキング待機禁止）
    - pending の場合はエージェント進行管理で次のポーリングへ進む（同期 sleep ループ禁止）
17. 失敗時は修正→再プッシュ→再確認（最大3回）

### 8. PR 後レビューループ / CI 全チェック確認
18. `.github/instructions/review-loop.instructions.md` に従い、push 後 AI レビュー（Copilot または Codex / Claude repo-aware fallback）を最大 3 ラウンドまで完了させる
19. `gh pr checks` で全チェックを確認し、実 failure は修正して再 push する

### 9. リリース判定 / マージ
20. release-manager に最終判定を委譲
21. 単発実行モードでは、release-manager 承認済み PR ready を報告して停止する
22. 全プラン実行モードでは、release-manager 承認後に PR を merge し、`main` を pull し、plan / execution ledger を更新して次タスクへ進む

## チェックリスト

- [ ] 実装がアーキテクチャの依存ルールに従っている
- [ ] 禁止操作を含まない（P-001）
- [ ] 秘密情報を含まない（P-002）
- [ ] 制約を回避していない（P-003）
- [ ] テストが追加・更新されている（AC-010）
- [ ] ローカル CI が成功する（AC-020）
- [ ] 必要な docs が更新されている（AC-030）
- [ ] PR に検証手順と結果が記載されている（AC-040）
- [ ] PR の CI が成功する
- [ ] push 後 AI レビューループが完了している
- [ ] 監査の Must 指摘がゼロである
- [ ] 全プラン実行モードでは merge / main pull / 次タスク遷移まで完了している
