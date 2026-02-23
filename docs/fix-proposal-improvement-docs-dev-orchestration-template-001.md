# 修正案: 改善要件・改善作業計画ドキュメント

> 作成日: 2026-02-23
> レビュー報告: `docs/review-improvement-docs-dev-orchestration-template-001.md`
> 修正対象:
> - `docs/improvement-plan-dev-orchestration-template-001.md`（5件）
> - `docs/improvement-requirements-dev-orchestration-template-001.md`（1件）

---

## 修正一覧

| # | 優先度 | 対象ファイル | 箇所 | 問題の種別 |
|---|---|---|---|---|
| F-1 | 🔴 重大 | improvement-plan | Session 6 作業2・完了後 | 関数名誤り |
| F-2 | 🔴 重大 | improvement-plan | Session 3 作業2 | セクション名誤り・存在しないファイル参照 |
| F-3 | 🟡 中程度 | improvement-plan | Session 1 作業1 | 誤誘導となるファイル参照 |
| F-4 | 🟢 軽微 | improvement-plan | Session 1 作業3 | 要件定義書との乖離 |
| F-5 | 🟢 軽微 | improvement-plan | §6 リスク表 | 未実装オプションの参照 |
| F-6 | 🟢 軽微 | improvement-requirements | §9 | 行数の不一致 |

---

## F-1: Session 6 — 関数名の誤り（🔴 重大）

**対象ファイル**: `docs/improvement-plan-dev-orchestration-template-001.md`

**問題**: `_get_tracer`（アンダースコア付き）と記載されているが、実際の関数名は `get_tracer`（アンダースコアなし）。完了確認コマンドが `ImportError` となり、正常実装後も検証が失敗する。

**根拠**: `src/observability/tracing.py` 行99の定義:
```python
def get_tracer() -> Any:
```

### 修正箇所 1: 作業2の本文（行594付近）

```diff
-`src/observability/tracing.py` の `_get_tracer()` 関数の戻り値型を修正してください:
+`src/observability/tracing.py` の `get_tracer()` 関数の戻り値型を修正してください:
```

### 修正箇所 2: 完了後の確認コマンド（行610付近）

```diff
-1. `python -c "from src.observability.tracing import _get_tracer"` で import エラーなし確認
+1. `python -c "from src.observability.tracing import get_tracer"` で import エラーなし確認
```

---

## F-2: Session 3 — セクション名誤りと不存在ファイル参照（🔴 重大）

**対象ファイル**: `docs/improvement-plan-dev-orchestration-template-001.md`

**問題**:
1. `project-config.yml` の `[agents]` セクションは**存在しない**。エージェント別設定は `[ai_models]` の `overrides` サブキーで管理される
2. `configs/ai_models.toml` は**存在しないファイル**。`update_agent_models.sh` は `project-config.yml` を直接参照しており、このファイルを読み込まない

**根拠**: `project-config.yml` の実際のセクション構成:
```
[project], [toolchain], [source], [roadmap], [policies], [github], [ai_models]
```
`scripts/update_agent_models.sh` の参照先: `project-config.yml` の `ai_models.default` / `ai_models.overrides`

### 修正箇所 1: 作業2の設定先説明（行315付近）

```diff
-`project-config.yml` の `[agents]` セクションで、各エージェントに推奨モデルを設定してください。
+`project-config.yml` の `[ai_models]` セクション内 `overrides` サブキーで、各エージェントに推奨モデルを設定してください。
```

### 修正箇所 2: 作業2の完了後手順（行330付近）

```diff
-設定後、`scripts/update_agent_models.sh` を実行してエージェント定義に反映してください。
-configs/ai_models.toml が存在しない場合は作成してください。
+設定後、`scripts/update_agent_models.sh` を実行してエージェント定義に反映してください。
+（スクリプトは `project-config.yml` の `[ai_models]` セクションを直接参照します）
```

### 修正箇所 3: 期待される成果物（行359付近）

```diff
-- `project-config.yml` or `configs/ai_models.toml`: モデル割当設定
+- `project-config.yml`（`[ai_models].overrides` セクション）: モデル割当設定
```

---

## F-3: Session 1 — staging.yml のハッシュ参照が誤誘導（🟡 中程度）

**対象ファイル**: `docs/improvement-plan-dev-orchestration-template-001.md`

**問題**: `staging.yml` の `actions/checkout` は `@v4` タグ指定（ハッシュ固定なし）。Copilot が参照しても有効なコミットハッシュを得られず、混乱の原因になる。

**根拠**: 実ファイルの状態:
- `ci.yml`: `actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5`（ハッシュ固定済み）
- `staging.yml`: `actions/checkout@v4`（タグ指定、ハッシュなし）
- `production.yml`: `actions/checkout@v4`（タグ指定、ハッシュなし）← 修正対象

### 修正箇所: 作業1の参考ファイル説明（行131付近）

```diff
-参考: `.github/workflows/ci.yml` や `staging.yml` で使用されているハッシュを確認し、
-同一のハッシュを使用してください。
+参考: `.github/workflows/ci.yml` で使用されているコミットハッシュ（`34e114876b0b11c390a56381ad16ebd13914f8d5`）を
+`production.yml` および `staging.yml` に適用してください。
+（`staging.yml` もハッシュ固定が必要な状態です）
```

---

## F-4: Session 1 — SCAN_DIRS 拡張の根拠が要件書にない（🟢 軽微）

**対象ファイル**: `docs/improvement-plan-dev-orchestration-template-001.md`

**問題**: `docs/` への SCAN_DIRS 追加が要件定義書（§4.3）に明示されていない計画独自の拡張。特に `docs/` はAPIキー説明例等のダミー文字列を含む場合に誤検知を増やすリスクがある。

**要件定義書 §4.3 の記載**: `.github/` のみ明示

### 修正案 A（推奨）: `docs/` を除外し、意図を明記する

```diff
 ## 作業3: policy_check.py の SCAN_DIRS 拡張（要件 #3）
 `ci/policy_check.py` の `SCAN_DIRS` に以下のディレクトリを追加してください:
 - `.github/`（エージェント定義・instructions・workflows）
 - `configs/`（設定ファイル）
 - `ci/`（CI スクリプト自身）
-- `docs/`（ドキュメント）
+（注: `docs/` は説明用のダミー文字列による誤検知リスクがあるため除外。必要であれば要件定義書を更新してから追加する）
```

### 修正案 B（代替）: 要件定義書 §4.3 に `docs/` 追加の根拠を追記してから計画に反映する

---

## F-5: §6 — bootstrap.sh の `--dry-run` オプション未実装（🟢 軽微）

**対象ファイル**: `docs/improvement-plan-dev-orchestration-template-001.md`

**問題**: `bootstrap.sh`（340行）に `--dry-run` オプションの実装がなく、緩和策として機能しない。`scripts/bootstrap.sh` の引数パース処理（`getopts`/`case` 等）は存在しない。

### 修正箇所: §6 リスク表（行743付近）

```diff
-| bootstrap.sh 修正による互換性破壊  | テンプレ破損 | 修正前後で `bootstrap.sh --dry-run` テスト     |
+| bootstrap.sh 修正による互換性破壊  | テンプレ破損 | 修正後に `bash -n scripts/bootstrap.sh`（構文チェック）および `shellcheck` を実行する |
```

**補足**: `bash -n` は構文エラーのみを検出する。完全な動作確認はテスト用の一時ディレクトリに対して `bootstrap.sh` を実際に実行して確認する。

---

## F-6: §9 — 行数記述の不一致（🟢 軽微）

**対象ファイル**: `docs/improvement-requirements-dev-orchestration-template-001.md`

**問題**: 「旧レポート（421行）」と記載されているが、Git 履歴上どのバージョンでも review-report.md は 421行にも 422行にもなっていない（最大252行）。行数は概算値であり不正確。

### 修正箇所: §9（行286付近）

```diff
-本ドキュメントは旧「リポジトリ総合レビューレポート（421行）」の**簡潔な改訂版（サマリ）**である。
+本ドキュメントは旧「リポジトリ総合レビューレポート」の**簡潔な改訂版（サマリ）**である。
```

> **検証メモ**: fix-proposal 原案では「422行」への修正を提案していたが、Git 履歴を確認した結果、
> どのコミットでも 421行・422行のバージョンは存在しなかった。
> 不正確な概算値を別の概算値に置き換えるのではなく、行数記載自体を削除する方針で修正した。

---

## 適用状況

| # | ステータス | 修正方針 |
|---|---|---|
| F-1 | ✅ 適用済み | 提案通り |
| F-2 | ✅ 適用済み | 提案通り |
| F-3 | ✅ 適用済み | 提案通り |
| F-4 | ✅ 適用済み | 修正案A（除外 + 注記）を採用 |
| F-5 | ✅ 適用済み | 提案通り |
| F-6 | ✅ 適用済み | 行数を削除する方針に変更（提案の422行も不正確なため） |

---

_本ドキュメントは `docs/review-improvement-docs-dev-orchestration-template-001.md` の指摘に基づく修正案を diff 形式で示したものである。実際の修正は元ドキュメントに対して行うこと。_
