# Expectation Ledger

ユーザーの意図を、requirements、design、spec、implementation へ変換する前に記録する。

## テンプレート

### EXP-YYYYMMDD-001

- 元のユーザー意図:
- AI の解釈:
- ユーザー可視の成果:
- 非目標:
- 制約:
- 影響する requirements:
- 影響する design:
- 作成・更新した spec:
- plan queue entry:
- 受入条件:
- Runtime smoke:
- 結果:

## 記録

以下は記入例（架空の一般例）である。運用開始後はこの例を残したまま、実際の意図変換エントリを追記する。

### EXP-20260101-001

- 元のユーザー意図: AI Operating Model を導入し、入口文書を `ai/*.yml` を参照する薄い入口へ改定する
- AI の解釈: 日常 3 コマンド、仕様駆動開発、文書読込範囲、PR 前批判レビュー、`release_to_main` risk tier を安定制御ファイルへ集約する
- ユーザー可視の成果: 日常開発で 3 コマンドだけを使い、governance change は別文脈として扱える
- 非目標: 初回棚卸しでの文書削除、AI レビューループの廃止、実装機能の変更
- 制約: P-001 / P-002 / P-003 / P-010、既存 PR 完了ゲート、main 直接コミット禁止
- 影響する requirements: 該当する NFR/FR（`docs/requirements.md` を参照）
- 影響する design: `docs/architecture.md` の該当節
- 作成・更新した spec: なし（本例は導入初期のため spec 未作成）
- plan queue entry: `docs/plan.md` の該当タスク
- 受入条件: 入口文書の薄化、文書棚卸し生成、PR 前批判レビュー生成、risk tier 反映、一回限りプロンプト非コミット
- Runtime smoke: `validate_context_index.py`、`validate_harness_ready.py`、`audit_document_inventory.py`、`run_pre_pr_critical_review.py`
- 結果: 実施済み
