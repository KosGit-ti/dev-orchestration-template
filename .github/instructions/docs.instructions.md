# Documentation Instructions

## 適用範囲

`docs/` 配下の正本ドキュメント、および PR/Issue の記述に適用する。

## 正本運用ルール（P-030）

- 仕様変更は `docs/requirements.md` または `docs/policies.md` に反映する。
- 計画変更は `docs/plan.md` を上書き更新する（過去ログを増やさない）。
- 重要判断は `docs/adr/` に残す。plan に長文履歴を残さない。
- 会話ログを仕様の根拠にしない。決定事項は必ず正本に反映する。

## ドキュメント一覧と役割

| ファイル | 役割 | 更新タイミング |
|---|---|---|
| `docs/requirements.md` | 要件定義・受入条件 | 要件追加・変更時 |
| `docs/policies.md` | ポリシー（禁止事項等） | ポリシー追加・変更時 |
| `docs/constraints.md` | 制約仕様 | しきい値変更時 |
| `docs/plan.md` | 現在の計画 | Next/Backlog 変更時 |
| `docs/architecture.md` | アーキテクチャ・責務境界 | モジュール構成変更時 |
| `docs/runbook.md` | 実行・復旧手順 | 手順変更時 |
| `docs/adr/` | 重要判断の記録 | 例外・設計判断時 |

## ADR の書き方

- `docs/adr/ADR-TEMPLATE.md` に従う。
- 番号は連番（ADR-0001, ADR-0002, ...）。
- 状態は Proposed → Accepted → Deprecated の遷移。
- 見直し条件と期限を必ず明記する。

## PR 本文

- `.github/PULL_REQUEST_TEMPLATE.md` の構成に合わせる。
- すべて日本語で記述する（P-032）。
- 受入条件（AC）のチェックボックスをすべて確認する。
- 検証手順（実行コマンド）と結果を記載する。

## docs 更新の判断基準

コード変更時に以下に該当する場合は docs 更新が必要（AC-030）：

- 新しいモジュールやパッケージを追加した → `architecture.md`
- 制約のしきい値を変更した → `constraints.md` + ADR
- 実行手順が変わった → `runbook.md`
- 受入条件を追加・変更した → `requirements.md`
- ポリシー違反の例外を設けた → `policies.md` + ADR
- 計画の Next/Backlog を変更した → `plan.md`
