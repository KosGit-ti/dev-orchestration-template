# work packet 机上分解の実証例（架空の一般例）

本書は work packet の確定テンプレート（`.shogun/` 導入時に作成する `inbox/README.md` を参照）を使い、依頼を分解する手順を確認するための**架空の一般例 2 件**である。実在のプロジェクト・PR・Issue とは対応しない。テンプレートの全フィールドが大型実装・docs-only governance の両極で無理なく埋まることを示すのが目的である。

## 例 1: 大規模実装（キャッシュ層追加・段階分割あり・red tier）

> 想定シナリオ: ユーザーが「検索 API のレスポンスが遅い。キャッシュ層を追加してレイテンシを改善してほしい。ただし既存の検索結果の一貫性は崩さないこと」と依頼した。

```markdown
# 依頼: 検索 API のキャッシュ層追加（レイテンシ改善）

## 依頼(ユーザー記入)

- intent: 検索 API にキャッシュ層を追加し、レイテンシを改善する。既存の検索結果の一貫性（キャッシュ無効化のタイミング）は崩さない。
- constraints: 破壊的な DB スキーマ変更は事前レビュー必須（migration は red tier）。キャッシュ導入前後で検索結果が異なってはならない。

## 分解（Shogun 記入・shogun_dispatch steps 準拠）

- reclassify: 非該当（実装タスク。運用ルール・policy・hook・workflow の変更を含まない）
- risk_tier: red（キャッシュ用テーブル追加の migration が `red_if_touches` の `src/**/migrations/**` に該当）
- capability_lease: ユーザー明示指示（依頼文自体が実装と PR 作成の認可）。merge は通常どおり release-manager 承認後
- required_locks: 不要（単一セッション）
- work_packets:
  - id: WP-1
    目的: キャッシュ抽象層（get/set/invalidate）を実装し、既存の検索呼び出し経路へ差し込む
    委譲先パターン: Ashigaru → implementer
    モデル割当: 標準
    worktree: 不要
    完了条件: キャッシュ層を経由しても既存の検索結果が変わらないことを確認する単体テスト pass
  - id: WP-2
    目的: キャッシュ無効化条件（元データ更新時）を実装する
    委譲先パターン: Ashigaru → implementer + test-engineer
    モデル割当: 標準
    worktree: 不要
    完了条件: 元データ更新後にキャッシュが陳腐化しないことを確認する境界値テスト pass
  - id: WP-3
    目的: キャッシュメタデータ用テーブルの migration を追加する
    委譲先パターン: Ashigaru → implementer（migration は red・Gunshi 監査必須）
    モデル割当: 標準
    worktree: 不要
    完了条件: migration 適用後の schema で必要なクエリが動作する runtime smoke pass
  - id: WP-4
    目的: 設計・リスク整合（既存経路への影響・red tier 充足）の独立監査と release 判定
    委譲先パターン: Gunshi → auditor-spec / auditor-security / auditor-reliability / release-manager
    モデル割当: 上位
    worktree: 不要
    完了条件: 監査 Must=0・release-manager 承認
- promotion_gate: pre-PR critical review → tier 分類（red）→ release-manager
- safety_floor: migration（`red_if_touches`）接触＝red ゲートで処理。既存の検索結果の一貫性（non-goal に反する副作用が無いこと）を `verify_safety_floor` で確認
- status: done（架空の例のため実 PR 番号はなし）
- 報告: shogun_report_contract_v1（実作業あり）
```

## 例 2: docs-only governance（CI 実行コストの調査と正本化）

> 想定シナリオ: ユーザーが「CI の実行時間が長くなってきた。コスト削減策を調査して、次の計画に正本化してほしい。実装はまだしなくてよい」と依頼した。

```markdown
# 依頼: CI 実行コスト削減の調査と正本化

## 依頼(ユーザー記入)

- intent: CI 実行コストの削減策を調査し、次の計画へ正本化する。実装は行わず、調査と文書化に限定する。
- constraints: 秘密情報（runner の登録トークン等）を文書やコミットに含めない（P-002）。

## 分解（Shogun 記入・shogun_dispatch steps 準拠）

- reclassify: 一部 governance_change 相当（CI 体制・workflow 運用の長期変更につながる調査）→ governance_change workflow で方針確定 → 確定後の正本化作業を dispatch として実施
- risk_tier: green（docs-only）
- capability_lease: ユーザー明示指示（依頼文自体が調査・文書化の認可）
- required_locks: 不要（単一セッション）
- work_packets:
  - id: WP-1
    目的: CI 実行時間・頻度・構造要因を調査し、根拠付きで記録する
    委譲先パターン: Karo → 読込・集計 + implementer-single-file（記録）
    モデル割当: 軽量
    worktree: 不要
    完了条件: 調査結果が出典付きで `docs/` 配下に記録される
  - id: WP-2
    目的: 削減方針の要件・設計判断を起草する
    委譲先パターン: Gunshi → 設計・リスク判断（起草は implementer-single-file へ委譲可）
    モデル割当: 上位
    worktree: 不要
    完了条件: 要件・ADR が SDD 必須項目（受入条件・安全床・ロールバック）を満たす
  - id: WP-3
    目的: `docs/plan.md` へタスクとして登録する
    委譲先パターン: Ashigaru → implementer-single-file（docs 編集）
    モデル割当: 標準
    worktree: 不要
    完了条件: 実行順が計画注記と Next 記載順の両方で一致する
  - id: WP-4
    目的: 整合性監査（要件↔設計↔計画の相互参照）と release 判定
    委譲先パターン: Gunshi → auditor-spec / release-manager
    モデル割当: 上位
    worktree: 不要
    完了条件: 監査 Must=0・release-manager 承認
- promotion_gate: pre-PR critical review → tier 分類（green）→ release-manager
- safety_floor: workflow / repo var / secret 非接触（正本化のみ）。hard_block / tiers 非変更
- status: done（架空の例のため実 PR 番号はなし）
- 報告: shogun_report_contract_v1（実作業あり）
```

## 観察（テンプレートの妥当性）

1. 大型実装（red・migration 含む）と docs-only governance の両極で、全フィールドが空欄・不適合なく埋まる。
2. `reclassify` は governance 判断が混在する依頼で分類の二段構造（governance_change での方針確定 → 確定後の dispatch）を明示でき、`shogun_dispatch` の fail-close 再分類 step と整合する。
3. モデル割当の論理 3 段（軽量/標準/上位）は、調査・起草・実装・監査の実分担に一致する。
4. 8 packet 上限は両例とも 4 packet 以下で十分だった。上限超過が必要な依頼は依頼自体の分割を促す設計が妥当。
