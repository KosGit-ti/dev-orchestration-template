# EXECUTE — 実装プロンプト

## 目的

`docs/plan.md` の Next にある指定タスクを実装する。

## 手順

1. `docs/plan.md` の Next から対象タスクを確認する
2. 対象タスクの受入条件（AC）を確認する
3. 関連する正本を読む：
   - `docs/requirements.md`（要件）
   - `docs/policies.md`（ポリシー）
   - `docs/constraints.md`（制約仕様）
   - `docs/architecture.md`（モジュール責務・依存ルール）
4. 実装する
5. テストを書く・通す
6. CI を通す
7. 必要なら docs を更新する
8. PR を `.github/PULL_REQUEST_TEMPLATE.md` に従って作成する

## チェックリスト

- [ ] 実装がアーキテクチャの依存ルールに従っている
- [ ] 禁止操作を含まない（P-001）
- [ ] 秘密情報を含まない（P-002）
- [ ] 制約を回避していない（P-003）
- [ ] テストが追加・更新されている（AC-010）
- [ ] CI が成功する（AC-020）
- [ ] 必要な docs が更新されている（AC-030）
- [ ] PR に検証手順と結果が記載されている（AC-040）
