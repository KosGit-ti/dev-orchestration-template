---
name: test-change
description: コード、設定、文書、CI の変更に対するテスト計画、実行、証跡整理を行う。変更後の回帰確認、境界条件の検証、失敗原因の切り分け、PR 前の検証結果作成が必要なときに使う。
---

# 変更テスト

変更のリスクに合う実行条件を選び、観測できる振る舞いを検証する。テスト数ではなく、受入条件と失敗時の影響を基準にする。

## 入力

1. 対象差分、受入条件、関連する仕様と既存テストを確認する。
2. リポジトリに risk tier の正本があれば、その判定を使う。未判定なら変更範囲、不可逆性、秘密情報、外部状態、データ互換性から保守的に判定する。
3. 作業ツリーにある対象外の変更を区別し、テスト準備でも上書きしない。

## 実行条件

モデル名を設定しない。実行環境が利用できるモデルから、次の能力エイリアスと effort を満たすものを選ぶ。

| risk tier | capability floor | effort | independence | budget strategy |
| --- | --- | --- | --- | --- |
| green | `light` | `low` または `medium` | `same-context` 可 | `cheap-first` |
| yellow | `standard` | `medium` 以上 | 実装と別の実行コンテキストを推奨 | `cheap-first` |
| red | `high` | `high` | 実装と別の実行コンテキストを必須 | `single-strong` |

次のいずれかが起きたら、能力エイリアスか effort を一段上げる。必要なら別コンテキストまたは別プロバイダーへ切り替える。

- 受入条件と実装の対応が一意に決まらない
- 同じ失敗に二度対処しても原因が確定しない
- 非決定的な失敗、データ破損、権限逸脱、秘密情報の露出を疑う
- 変更中に risk tier が上がる事実を見つける
- 必要な証跡を現在の権限や実行環境では取得できない

最高位の能力は、上記の昇格条件が成立した場合だけ使う。実行環境が実モデル名を公開した場合は `resolved_runtime` の観測値として残し、次回の固定設定には転用しない。

## テスト

1. 受入条件を、成功経路、失敗経路、境界値、既存挙動の回帰に分解する。
2. 変更箇所に近い小さいテストから始め、必要な範囲だけ統合テストや全体テストへ広げる。
3. 外部サービス、時刻、乱数、並行処理、再実行に依存する箇所では、再現条件と決定性を確認する。
4. 実データ、認証情報、実資金を使わない。fixture、dummy、dry-run を優先する。
5. 失敗を隠す再試行や期待値の緩和をしない。失敗コマンド、終了コード、主要なエラーを残す。
6. テストできない項目は pass と扱わず、理由、残リスク、次に必要な権限または環境を示す。

## 報告

次の順で簡潔に返す。

```yaml
risk_tier: green | yellow | red
runtime_profile:
  capability_floor: light | standard | high | top
  effort: low | medium | high | inherit
  independence: same-context | separate-context | separate-model-family | separate-provider
  budget_strategy: cheap-first | single-strong | dual-check
resolved_runtime: 実行環境が公開した範囲。非公開なら unavailable
coverage:
  - 受入条件またはリスク: 対応したテスト
commands:
  - command: 実行したコマンド
    result: pass | fail | blocked
findings: 修正が必要な事実。なければ []
gaps: 未検証項目と残リスク。なければ []
```
