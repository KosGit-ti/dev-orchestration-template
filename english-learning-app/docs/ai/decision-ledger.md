# Decision Ledger

日常開発で AI が曖昧さを内部解決した場合に使う。会話ログではなく、このファイルに判断を残す。

## テンプレート

### DEC-YYYYMMDD-001

- 論点:
- 判断:
- 仮定:
- 理由:
- 影響ファイル:
- リスク:
- ロールバック:

Capability Lease（ユーザーが明示的に一時的な自律実行範囲を認可した場合）を記録する際は、以下のフィールドも追加する。

### DEC-YYYYMMDD-NNN（capability lease）

- 論点:
- granted_by: ユーザー明示指示（引用または要約）
- scope: 認可された変更範囲・対象ファイル
- granted: 認可された操作（実装・PR 化・特定ゲート通過後の merge 等）
- excluded: `safety.hard_block` 全項目など、lease の対象外
- expiry_condition: 失効条件（完了時点・ユーザーの撤回時 等）
- 記録者注: 本 lease は AI の自己記録ではなくユーザー指示の引用に基づく

## 記録

以下は記入例（架空の一般例）である。運用開始後はこの例を残したまま、実際の判断エントリを追記する。

### DEC-20260101-001

- 論点: 日常開発の依頼と運用ルール変更の依頼をどう区別して質問方針を分けるか
- 判断: `daily_development` は原則聞き返さず、`governance_change` は将来運用に影響する確認・対話を許可する
- 仮定: 日常タスクではユーザーが不在になり得るが、運用ルール変更は将来の使い勝手へ直接影響する
- 理由: `ai/operation-policy.yml` の `ask_user_policy` とユーザー指示に整合するため
- 影響ファイル: `ai/operation-policy.yml`、`AGENTS.md`、`CLAUDE.md`、`.github/copilot-instructions.md`
- リスク: governance change を広く解釈しすぎると日常開発で聞き返しが増える
- ロールバック: `ai/command-router.yml` の分類と入口文書の該当記述を revert する
