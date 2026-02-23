# レビュー報告: 改善要件・改善作業計画ドキュメント

> 作成日: 2026-02-23
> レビュー対象:
> - `docs/improvement-requirements-dev-orchestration-template-001.md`
> - `docs/improvement-plan-dev-orchestration-template-001.md`
> レビュー手法: 実コードとの照合（ci/policy_check.py, ワークフロー YAML, エージェント定義, スクリプト類など主要18ファイルを調査）

---

## 1. 総合評価

| ドキュメント | 評価 | 概要 |
|---|---|---|
| improvement-requirements | ✅ **概ね問題なし** | 軽微な記述の不正確さが1点 |
| improvement-plan | ⚠️ **要修正** | 重大な誤りが2点、中程度1点、軽微2点 |

---

## 2. improvement-requirements-dev-orchestration-template-001.md

### 正確な記述（実コードで確認済み）

| 記述箇所 | 確認内容 |
|---|---|
| §4.3「ci.yml では commit hash で固定済み」 | ✅ `actions/checkout@34e11...` でピン留め確認 |
| §4.3「production.yml / staging.yml は要改善」 | ✅ production.yml は `@v4` タグ指定のまま |
| §3.1「implementer → test-engineer の逐次フロー」 | ✅ orchestrator.agent.md の構成と一致 |
| §4.4「CI 品質チェックはコメントアウト済み」 | ✅ ci.yml / production.yml 両方で確認 |
| §4.2「SECRET_PATTERNS に Anthropic キー等が未登録」 | ✅ `sk-ant-api03-` / `sk-proj-` 等は未登録を確認 |
| §4.3「SCAN_DIRS に .github/ 未含有」 | ✅ `src/`, `tests/`, `scripts/` のみ確認 |

### 要注意点

**§9「旧レポート（421行）」の行数記述**

実際の `docs/review-report.md` は **422行**。誤差の範囲だが参照ドキュメントとして正確さを期すなら「約422行」または「Git 履歴参照」とした方が確実。

### §4.2 の記述について補足

「並列監査の確保には明示的な並列実行指示が必要」という主旨は正確だが、実際の `.github/agents/orchestrator.agent.md` Step 5 には「3つの監査サブエージェントに並行して委譲する」と明記されている。現時点では**指示は存在する**が、フレームワーク（Copilot Chat）レベルでの実行保証がないという点が本来の課題。文書化上の表現は問題ないが、背景として記録する。

### 結論

要件定義書の**内容・論理・優先順位付けはすべて妥当**。軽微な行数差異のみで、実装判断に影響する誤りはない。

---

## 3. improvement-plan-dev-orchestration-template-001.md

### 問題1（重大）: Session 6 — 関数名の誤り

**箇所**: Session 6「作業2: tracing.py 戻り値型の修正」および「完了後」セクション

**記述**:
```
`_get_tracer()` 関数の戻り値型を修正
python -c "from src.observability.tracing import _get_tracer"
```

**実際の関数定義** (`src/observability/tracing.py` 行99):
```python
def get_tracer() -> Any:   # アンダースコアなし
```

**影響**: 完了確認コマンドが `ImportError` になり、正常実装後も検証が失敗する。また Copilot がアンダースコア付きの関数を新規作成してしまうリスクもある。

**修正案**: `_get_tracer` を `get_tracer` に統一。

---

### 問題2（重大）: Session 3 — project-config.yml のセクション名が誤り

**箇所**: Session 3「作業2: エージェント別モデル割当の最適化」

**記述**:
```
`project-config.yml` の `[agents]` セクションで各エージェントに推奨モデルを設定してください
configs/ai_models.toml が存在しない場合は作成してください
```

**実際の `project-config.yml` 構造**:
- `[agents]` セクションは**存在しない**
- エージェント設定は `[ai_models]` セクション内の `overrides` サブキーで管理
- `scripts/update_agent_models.sh` は `project-config.yml` の `ai_models.default` / `ai_models.overrides` を `yq` で直接読み込む設計

また `configs/ai_models.toml` は**現時点で存在しない**ファイルであり、`update_agent_models.sh` もこのファイルを参照しない。「存在しない場合は作成」という指示は `update_agent_models.sh` の実際の動作と整合しない。

**修正案**:
- `[agents]` → `[ai_models]` の `overrides` サブセクション
- `configs/ai_models.toml` の記述は削除し、`project-config.yml` の `[ai_models]` に設定することを明記

---

### 問題3（中程度）: Session 1 — staging.yml のハッシュ参照が誤誘導

**箇所**: Session 1「作業1: production.yml の Action ピン留め」

**記述**:
```
参考: `.github/workflows/ci.yml` や `staging.yml` で使用されているハッシュを確認し、
同一のハッシュを使用してください。
```

**実際**:
- `ci.yml`: `actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5`（ハッシュ固定済み）✅
- `staging.yml`: `actions/checkout@v4`（タグ指定、ハッシュ未固定）❌

staging.yml を参照してもハッシュは得られず、Copilot が混乱する原因になる。

**修正案**: 「`ci.yml` で使用されているハッシュを確認し」に絞る。

---

### 問題4（軽微）: Session 1 — SCAN_DIRS 拡張が要件定義を超える

**箇所**: Session 1「作業3: SCAN_DIRS 拡張」

**記述**: `.github/`, `configs/`, `ci/`, `docs/` を追加
**要件定義書 §4.3**: `.github/` のみ明示

`ci/` への追加は合理的（`policy_check.py` 自身のセルフチェック）だが、`docs/` は API キーの説明例やダミー文字列が含まれる場合に誤検知を増やすリスクがある。意図的な拡張であれば要件定義書に根拠を記載することを推奨。

---

### 問題5（軽微）: §6 リスク表 — bootstrap.sh の `--dry-run` オプション存在不明

**箇所**: §6「リスクと緩和策」の「bootstrap.sh 修正による互換性破壊」行

**記述**: 「修正前後で `bootstrap.sh --dry-run` テスト」
**実際**: `bootstrap.sh`（340行）が `--dry-run` オプションを実装しているか未確認。未実装の場合、緩和策として機能しない。

---

### 正確な記述（実コードで確認済み）

| 箇所 | 確認内容 |
|---|---|
| 各セッションの読み込みファイル一覧 | 全ファイルの存在を確認（ `configs/ai_models.toml` のみ未存在） |
| トークン見積もり（~50K〜60K / 128K） | ファイルサイズと整合的、余裕は十分 |
| Session 4: orchestrator のパス | `.github/agents/orchestrator.agent.md` で正確 |
| Session 6: `init_packages.sh` が `touch` で空生成 | ✅ 行26: `touch "$init_file"` 確認 |
| Session 7: requirements.md の FR-001/FR-010 がプレースホルダー | ✅ 確認 |
| Session 7: constraints.md の閾値・条件がプレースホルダー | ✅ 確認 |
| Session 3: runbook.md にロールバック手順がない | ✅ 確認（失敗時対応のみで専用セクションなし） |
| セッション間の独立設計（文脈切れ許容） | ✅ 各セッションが独立して実行可能な構成 |

---

## 4. 修正優先度サマリ

| 優先度 | ファイル | 箇所 | 問題 |
|---|---|---|---|
| 🔴 重大 | improvement-plan | Session 6 作業2・完了後 | `_get_tracer` → `get_tracer` に修正 |
| 🔴 重大 | improvement-plan | Session 3 作業2 | `[agents]` → `[ai_models].overrides`、`configs/ai_models.toml` の記述を整理 |
| 🟡 中程度 | improvement-plan | Session 1 作業1 | staging.yml のハッシュ参照を削除、ci.yml のみ参照 |
| 🟢 軽微 | improvement-plan | Session 1 作業3 | `docs/` 追加の根拠を要件定義書に記載 |
| 🟢 軽微 | improvement-plan | §6 リスク表 | `bootstrap.sh --dry-run` の実装有無を確認 |
| 🟢 軽微 | improvement-requirements | §9 | 「421行」→「422行」（または「Git 履歴参照」） |

---

_本レビューは実コード照合（18ファイル調査）に基づく。ドキュメント変更は加えておらず、上記の指摘を反映した修正は別途実施のこと。_
