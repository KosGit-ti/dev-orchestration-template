# 改善作業計画: dev-orchestration-template

> 作成日: 2026-02-23
> 要件定義書: `docs/improvement-requirements-dev-orchestration-template-001.md`
> 実行環境: GitHub Copilot Pro+ Chat（Claude Opus 4.6）
> コンテキストウィンドウ: 128K tokens

---

## 1. 概要

本計画は `improvement-requirements-dev-orchestration-template-001.md` に記載された25項目の改善要件を、
GitHub Copilot Pro+ の Chat（Claude Opus 4.6）で段階的に実行するための手順書である。

### 前提条件

- 各セッションは独立した Copilot Chat で実行する（文脈継続が必要な場合は明記）
- 本リポジトリの `.github/copilot-instructions.md` がシステムプロンプトに自動付与される
- 各セッションの作業指示はそのままコピペで使用可能な形式で記載する
- 変更後は各セッション内で品質管理手順（CI実行・エラー検証）を行う
- `docs/plan.md` の Next/Backlog への反映は別途行う

### 推奨実行順序

1. **本リポジトリ（dev-orchestration-template）を先に改善する**（テンプレートとしての品質確立）
2. その後 `stock-trading-system` に改善を適用する（テンプレート変更を参考にできる）

---

## 2. トークン予算モデル

### 128K コンテキストウィンドウの内訳

```
┌─────────────────────────────────────────────────────┐
│ 128K tokens (Claude Opus 4.6 on Copilot Pro+)       │
├─────────────────────────────────────────────────────┤
│ システムプロンプト（Copilot 基盤）    :  ~8,000 tok │
│ copilot-instructions.md               :  ~4,000 tok │
│ instructions/*.md（適用対象分）       :  ~2,500 tok │
│ ─────────────────────────────────────────────────── │
│ システムオーバーヘッド合計            : ~15,000 tok │
│                                                     │
│ 実作業予算                            :~113,000 tok │
│   ├─ ファイル読み込み                 : ~30-50K tok │
│   ├─ 会話履歴（蓄積・圧縮）          : ~20-40K tok │
│   ├─ ツール呼び出しオーバーヘッド     : ~10-20K tok │
│   └─ 出力生成                         : ~10-20K tok │
└─────────────────────────────────────────────────────┘
```

### ターン数とコンテキスト圧縮の関係

| ターン数 | 実効利用可能トークン | リスク                 | 推奨               |
| -------- | -------------------- | ---------------------- | ------------------ |
| 1-5      | ~90,000              | なし                   | 最適               |
| 6-10     | ~70,000              | 軽微な圧縮             | 良好               |
| 11-15    | ~50,000              | 初期読み込み情報が圧縮 | 注意               |
| 16-20    | ~30,000              | 文脈喪失リスク         | 危険               |
| 20+      | ~20,000              | 重要情報の喪失         | 新規セッション推奨 |

### セッション設計原則

- 1セッションあたり **10-15ターン以内** に完了させる
- ファイル読み込みは **合計40K tokens 以内** を目安とする
- 文脈継続が不要なタイミングで **新規チャットセッションを開始** する
- 修正→CI失敗→再修正ループは **最大3回** で打ち切り、次セッションに持ち越す

---

## 3. セッション一覧

| Session | Phase | 対象項目                     | 推定トークン消費 | 推定ターン数 | 文脈切れ許容 |
| ------- | ----- | ---------------------------- | ---------------- | ------------ | ------------ |
| 1       | P0    | #1, #2, #3, #4               | ~50K             | 8-12         | ✅ 後に可    |
| 2       | P1    | #5, #6, #10                  | ~55K             | 10-15        | ✅ 後に可    |
| 3       | P1    | #7, #8, #9                   | ~55K             | 10-15        | ✅ 後に可    |
| 4       | P2    | #11, #12, #13                | ~60K             | 12-18        | ✅ 後に可    |
| 5       | P2    | #14, #15, #17                | ~50K             | 8-12         | ✅ 後に可    |
| 6       | P2    | #16, #18, #19                | ~45K             | 8-10         | ✅ 後に可    |
| 7       | P2    | #20, #21, #22, #23, #24, #25 | ~55K             | 10-15        | ✅ 完了      |

> **全セッション間で文脈切れ許容**：各セッションは独立設計されており、任意のタイミングで新規チャットを開始可能。

---

## 4. 各セッション詳細

---

### Session 1: P0 セキュリティ基盤の強化（要件 #1, #2, #3, #4）

#### 目的

本番ワークフローの Action ピン留め、CI 品質チェックの復活、`policy_check.py` のスキャン範囲拡張とシークレット検出パターンの最新化。

#### 読み込みファイル一覧

| ファイル                                                          | 推定トークン | 用途                    |
| ----------------------------------------------------------------- | ------------ | ----------------------- |
| `docs/improvement-requirements-dev-orchestration-template-001.md` | ~10,000      | 要件全体の把握          |
| `ci/policy_check.py`                                              | ~1,600       | #3, #4 の修正対象       |
| `.github/workflows/production.yml`                                | ~800         | #1 の修正対象           |
| `.github/workflows/ci.yml`                                        | ~800         | #2 の修正対象           |
| `.github/workflows/staging.yml`                                   | ~900         | #1 参考                 |
| `scripts/bootstrap.sh`                                            | ~2,300       | #2 自動有効化の実装場所 |
| `pyproject.toml.template`                                         | ~600         | CI ツール設定確認       |
| **合計**                                                          | **~17,000**  |                         |

#### 推定総トークン消費

- システムオーバーヘッド: ~15,000
- ファイル読み込み: ~17,000
- 会話・出力: ~18,000
- **合計: ~50,000 / 128,000**（余裕あり）

#### コンテキスト切れ許容タイミング

- **セッション開始前**: OK（独立セッション）
- **セッション完了後**: OK（次セッション着手可能）
- **セッション途中**: #1+#2 完了後に切れても可（#3+#4 は独立して実行可能）

#### 作業指示文（コピペ用）

```
以下の4つの改善を順番に実施してください。

## 作業1: production.yml の Action ピン留め（要件 #1）
`.github/workflows/production.yml` で `actions/checkout@v4` のようにタグ指定されている
Action を、コミットハッシュ指定に変更してください。
参考: `.github/workflows/ci.yml` や `staging.yml` で使用されているハッシュを確認し、
同一のハッシュを使用してください。

## 作業2: CI 品質チェックの復活（要件 #2）
`.github/workflows/ci.yml` のコメントアウトされている品質チェックステップ（Lint, Format check,
Type check, Test）を有効化してください。
`pyproject.toml.template` にツール設定があるため、`pyproject.toml` が存在する前提で動作するよう
条件分岐（`if: hashFiles('pyproject.toml') != ''`）を追加するか、
`scripts/bootstrap.sh` の最後に CI ステップをアンコメントする処理を追加してください。
`bootstrap.sh` に自動化する方法を推奨します。

## 作業3: policy_check.py の SCAN_DIRS 拡張（要件 #3）
`ci/policy_check.py` の `SCAN_DIRS` に以下のディレクトリを追加してください:
- `.github/`（エージェント定義・instructions・workflows）
- `configs/`（設定ファイル）
- `ci/`（CI スクリプト自身）
- `docs/`（ドキュメント）

## 作業4: シークレット検出パターンの最新化（要件 #4）
`ci/policy_check.py` の `SECRET_PATTERNS` に以下のパターンを追加してください:
- `sk-ant-api03-` パターン（Anthropic API キー新形式）
- `sk-proj-` パターン（OpenAI API キー新形式）
- `Bearer [A-Za-z0-9\-._~+/]+=*` パターン
- `password\s*=\s*["'][^"']+["']` パターン
既存の `sk-[A-Za-z0-9]{32,}` パターンはそのまま残してください。

## 完了後
全変更完了後に以下を実行してください:
1. `python ci/policy_check.py` で誤検知がないことを確認
2. get_errors ツールで全体エラーゼロを確認
3. 変更内容のセルフレビュー（P-001〜P-003 違反なし）
```

#### 期待される成果物

- `production.yml`: Action がコミットハッシュで固定
- `ci.yml` または `bootstrap.sh`: 品質チェックの有効化機構
- `ci/policy_check.py`: SCAN_DIRS 拡張 + SECRET_PATTERNS 最新化

#### 完了確認

- [ ] `python ci/policy_check.py` がエラーなしで完了
- [ ] production.yml に `@v4` 等のタグ指定が残っていない
- [ ] `.github/` がスキャン対象に含まれている
- [ ] `sk-ant-api03-` テスト文字列が検出される

---

### Session 2: P1 CI/セキュリティ強化（要件 #5, #6, #10）

#### 目的

依存関係脆弱性スキャン（pip-audit / gitleaks）の CI 統合、SBOM 生成、concurrency/キャッシュ設定の追加。

#### 読み込みファイル一覧

| ファイル                                                                 | 推定トークン | 用途                   |
| ------------------------------------------------------------------------ | ------------ | ---------------------- |
| `docs/improvement-requirements-dev-orchestration-template-001.md` §3, §4 | ~5,000       | 要件（該当セクション） |
| `.github/workflows/ci.yml`                                               | ~800         | 修正対象               |
| `.github/workflows/staging.yml`                                          | ~900         | #10 修正対象           |
| `.github/workflows/production.yml`                                       | ~800         | #10 修正対象           |
| `.github/workflows/issue-lifecycle.yml`                                  | ~1,000       | #10 修正対象           |
| `ci/policy_check.py`                                                     | ~1,600       | 参考                   |
| `docs/policies.md`                                                       | ~1,200       | P-040 確認             |
| `pyproject.toml.template`                                                | ~600         | 依存追加先             |
| **合計**                                                                 | **~11,900**  |                        |

#### 推定総トークン消費: ~55K / 128K

#### コンテキスト切れ許容タイミング

- **セッション開始前**: OK
- **#5 + #6 完了後**: OK（#10 は独立）
- **セッション完了後**: OK

#### 作業指示文（コピペ用）

````
以下の3つの改善を実施してください。

## 作業1: pip-audit / gitleaks の CI 統合（要件 #5）
`.github/workflows/ci.yml` に以下の2つのセキュリティスキャンステップを追加してください:

1. **pip-audit**: Python 依存パッケージの既知脆弱性スキャン
   - `pip install pip-audit` → `pip-audit --strict` を実行
   - pyproject.toml が存在する場合のみ実行する条件分岐付き

2. **gitleaks**: Git 履歴内のシークレット漏洩スキャン
   - `gitleaks/gitleaks-action` をコミットハッシュ固定で使用
   - または `trufflehog` の GitHub Action を代替として使用
   - セキュリティルール: サードパーティ Action はコミットハッシュ固定必須

docs/policies.md の P-040（依存関係監査）を参照し、整合性を確認してください。

## 作業2: SBOM 生成・署名の CI 統合（要件 #6）
`.github/workflows/ci.yml` または `production.yml` に SBOM 生成ステップを追加してください:
- `anchore/syft` で SBOM を CycloneDX 形式で生成
- 成果物として GitHub Actions artifacts にアップロード
- 注意: `cosign` による署名は将来課題とし、SBOM 生成のみを実装する
- サードパーティ Action はコミットハッシュ固定必須

## 作業3: concurrency / キャッシュ設定の追加（要件 #10）
全ワークフローファイル（ci.yml, staging.yml, production.yml, issue-lifecycle.yml）に
以下を追加してください:

1. **concurrency 設定**:
   ```yaml
   concurrency:
     group: ${{ github.workflow }}-${{ github.ref }}
     cancel-in-progress: true
````

2. **uv キャッシュ設定**（ci.yml のみ）:
   Python セットアップ後に uv のキャッシュを有効化してください。

## 完了後

1. 全ワークフローファイルの YAML 構文チェック
2. get_errors ツールで全体エラーゼロを確認
3. concurrency 設定が全ワークフローに存在することを確認

```

#### 期待される成果物

- `ci.yml`: pip-audit + gitleaks ステップ追加、uv キャッシュ追加、concurrency 追加
- `staging.yml`, `production.yml`, `issue-lifecycle.yml`: concurrency 追加
- （オプション）`production.yml`: SBOM 生成ステップ追加

---

### Session 3: P1 運用基盤・モデル最適化（要件 #7, #8, #9）

#### 目的

本番昇格記録の永続化、エージェント別モデル割当最適化、ロールバック手順の追加。

#### 読み込みファイル一覧

| ファイル                                      | 推定トークン | 用途                           |
| --------------------------------------------- | ------------ | ------------------------------ |
| `docs/improvement-requirements-dev-orchestration-template-001.md` §2, §4, §7 | ~6,000 | 要件（該当セクション） |
| `docs/runbook.md`                             | ~1,300       | #9 修正対象                    |
| `docs/orchestration.md` §7（モデル管理）      | ~3,000       | #8 参考                        |
| `.github/workflows/production.yml`            | ~800         | #7 修正対象                    |
| `scripts/update_agent_models.sh`              | ~800         | #8 手順確認                    |
| `project-config.yml`                          | ~1,000       | #8 モデル設定                  |
| agent定義 2-3ファイル（確認用）               | ~5,000       | #8 現状確認                    |
| **合計**                                      | **~17,900**  |                                |

#### 推定総トークン消費: ~55K / 128K

#### コンテキスト切れ許容タイミング

- **セッション開始前**: OK
- **#7 完了後**: OK（#8, #9 はそれぞれ独立）
- **#8 完了後**: OK
- **セッション完了後**: OK

#### 作業指示文（コピペ用）

```

以下の3つの改善を実施してください。

## 作業1: 本番昇格記録の永続化（要件 #7）

`.github/workflows/production.yml` の「Record production promotion」ステップを改善してください。
現状は `echo` でログに出力するだけで永続化されていません。

以下のいずれかの方法で永続化してください:

1. **GitHub Releases への記録**（推奨）:
   - `gh release create` で本番昇格ごとにリリースを作成
   - リリースノートにコミット SHA、タイムスタンプ、承認者を記録
2. **リポジトリ内ファイルへの追記**:
   - `outputs/production_log.md` 等に追記してコミット
   - ただしワークフロー内でのコミットは無限ループに注意

`docs/policies.md` の P-020（監査ログ）との整合性を確認してください。

## 作業2: エージェント別モデル割当の最適化（要件 #8）

`project-config.yml` の `[agents]` セクションで、各エージェントに推奨モデルを設定してください。
`docs/orchestration.md` §7 のコスト分析を参考にします。

推奨割当:
| エージェント | 推奨モデル | 理由 |
|--------------------|------------------|-------------------------------|
| orchestrator | Claude Opus 4.6 | 高度な判断・調整が必要 |
| implementer | Claude Sonnet 4.6| SWE-bench 最強、コスト 1x |
| test-engineer | Claude Sonnet 4.6| 反復的テスト生成に適する |
| auditor-spec | Claude Sonnet 4.6| 論理照合が主タスク |
| auditor-security | Claude Sonnet 4.6| パターンマッチ主体 |
| auditor-reliability| Claude Sonnet 4.6| 信頼性分析 |
| release-manager | Claude Opus 4.6 | 最終判定、最高品質が必要 |

設定後、`scripts/update_agent_models.sh` を実行してエージェント定義に反映してください。
configs/ai_models.toml が存在しない場合は作成してください。

## 作業3: ロールバック手順の追加（要件 #9）

`docs/runbook.md` に以下のロールバック手順セクションを追加してください:

1. **フィーチャーブランチの巻き戻し**
   - `git revert` による安全な取り消し手順
   - 強制プッシュの禁止（main/master ブランチ保護）

2. **本番リリースのロールバック**
   - GitHub Releases から前バージョンの特定手順
   - 本番環境への再デプロイ手順

3. **ポリシー違反発覚時の復旧フロー**
   - 即時停止条件との連携（copilot-instructions.md 参照）
   - エスカレーション手順

## 完了後

1. get_errors ツールで全体エラーゼロを確認
2. runbook.md のロールバック手順が停止条件と対称的であることを確認
3. セルフレビュー（P-001〜P-003 違反なし）

```

#### 期待される成果物

- `production.yml`: 昇格記録の永続化ステップ
- `project-config.yml` or `configs/ai_models.toml`: モデル割当設定
- agent定義ファイル: モデル設定の反映
- `docs/runbook.md`: ロールバック手順セクション追加

---

### Session 4: P2 エージェント通信の型安全化（要件 #11, #12, #13）

#### 目的

エージェント応答の JSON Schema 定義、並列監査の実行保証、パイプライン状態の永続化。

#### 読み込みファイル一覧

| ファイル                                      | 推定トークン | 用途                           |
| --------------------------------------------- | ------------ | ------------------------------ |
| `docs/improvement-requirements-dev-orchestration-template-001.md` §2, §8 | ~5,000 | 要件 |
| `docs/orchestration.md`                       | ~9,400       | パイプライン設計の全体像       |
| `agents/orchestrator.agent.md` or `.github/agents/orchestrator.agent.md` | ~10,000 | 修正対象 |
| `.github/copilot-instructions.md`             | ~4,000       | パイプライン定義（自動付与済み）|
| **合計**                                      | **~28,400**  |                                |

#### 推定総トークン消費: ~60K / 128K

#### 注意: このセッションは設計判断を含む

#11（JSON Schema）と #25（型安全オーケストレーション）は関連するため、
Session 7 の #25 を先に検討してから #11 に着手する選択もある。
ただし #11 は現行アーキテクチャ内での改善であり、#25 はアーキテクチャ変更のため、独立実施可能。

#### コンテキスト切れ許容タイミング

- **セッション開始前**: OK
- **#11 完了後**: OK（#12, #13 は独立）
- **セッション完了後**: OK

#### 作業指示文（コピペ用）

```

以下の3つの改善を実施してください。
事前に `docs/orchestration.md` を読んでパイプラインの全体設計を把握してください。

## 作業1: エージェント応答のスキーマ定義（要件 #11）

エージェントの報告フォーマットを明確化するため、以下を実施してください:

1. `docs/orchestration.md` に「エージェント応答スキーマ」セクションを追加し、
   各エージェントの報告に含めるべきフィールドを定義する:
   - `status`: "success" | "failure" | "partial"
   - `summary`: 自然言語の要約
   - `findings`: 具体的な指摘事項のリスト（severity, file, line, message）
   - `metrics`: 定量的な指標（テスト数、カバレッジ等）

2. `copilot-instructions.md` の各エージェント委譲ステップに、
   「報告は上記スキーマに従うこと」と明記する

注意: 現時点では Copilot Chat の制約上、JSON スキーマの強制的なバリデーションは
実装できない。ドキュメントベースの規約として定義し、将来のフレームワーク移行時に
型安全化する設計とする。

## 作業2: 並列監査の実行指示明確化（要件 #12）

`copilot-instructions.md` および `docs/orchestration.md` の監査ステップ（Step 6）で、
3つの監査エージェントの「並列実行」について明確な指示を追加してください:

- orchestrator.agent.md に「3つの監査エージェントを **可能な限り並列に** 呼び出すこと。
  フレームワークが逐次実行しかサポートしない場合は、その旨を報告すること」と記載
- 並列実行の可否はプラットフォーム依存であることを注記する

## 作業3: パイプライン状態の永続化設計（要件 #13）

パイプライン中断時の復旧のため、状態永続化の仕組みを設計・実装してください:

1. `.github/agents/orchestrator.agent.md` に以下の指示を追加:
   - 各ステップ完了時に `outputs/pipeline_state.json` に状態を書き出す
   - 状態ファイルのフォーマット: { step, loop_count, branch, audit_results, timestamp }
   - パイプライン開始時に状態ファイルが存在すれば復旧を試みる

2. `docs/orchestration.md` に状態永続化の設計セクションを追加

## 完了後

1. orchestration.md の応答スキーマセクションが整合的であることを確認
2. get_errors ツールで全体エラーゼロを確認

```

---

### Session 5: P2 可観測性・コスト管理（要件 #14, #15, #17）

#### 目的

エージェントパイプライン層の OpenTelemetry スパン設計、予算上限の定義、AI Gateway レイヤーの設計。

#### 読み込みファイル一覧

| ファイル                                      | 推定トークン | 用途                     |
| --------------------------------------------- | ------------ | ------------------------ |
| `docs/improvement-requirements-dev-orchestration-template-001.md` §7 | ~3,000 | 要件 |
| `src/observability/tracing.py`                | ~2,200       | 現行 OTel 実装           |
| `docs/orchestration.md` §6, §7               | ~5,000       | パイプライン設計         |
| `docs/observability-guide.md`                 | ~3,400       | 既存の可観測性ガイド     |
| **合計**                                      | **~13,600**  |                          |

#### 推定総トークン消費: ~50K / 128K（余裕あり）

#### コンテキスト切れ許容タイミング

- 全タイミングで OK（独立した設計・文書作業が中心）

#### 作業指示文（コピペ用）

```

以下の3つの改善を実施してください。
事前に `src/observability/tracing.py` と `docs/observability-guide.md` を確認してください。

## 作業1: パイプライン層 OTel スパン設計（要件 #14）

`docs/observability-guide.md` に「エージェントパイプラインの計装」セクションを追加してください:

1. 計測対象の定義:
   - 各ステップ（Step 1-13）の実行時間
   - サブエージェントへの委譲回数・成功率
   - 修正ループ回数の分布
   - CI 実行時間・失敗率

2. スパン階層の設計:

   ```
   pipeline (root span)
   ├── plan_selection
   ├── branch_creation
   ├── implementation
   │   └── implementer_call
   ├── testing
   │   └── test_engineer_call
   ├── local_ci
   │   ├── ruff_check
   │   ├── mypy
   │   └── pytest
   ├── audit
   │   ├── audit_spec
   │   ├── audit_security
   │   └── audit_reliability
   ├── pr_creation
   └── review_loop
   ```

3. 属性（attributes）の定義:
   - `pipeline.task_id`, `pipeline.branch`, `pipeline.loop_count`
   - `agent.name`, `agent.model`, `agent.token_usage`

注意: 実装は将来課題とし、本セッションでは設計ドキュメントの整備のみ行う。

## 作業2: 予算上限（Budget cap）の設計（要件 #15）

`docs/orchestration.md` に「コスト管理」セクションを追加してください:

1. 1パイプライン実行あたりの最大コスト見積もり:
   - 各ステップの最大トークン使用量を推算
   - Bounded Recursion（最大3回）× 各エージェントのコスト
   - Opus 4.6: $15/MTok input, $75/MTok output (参考値)
   - Sonnet 4.6: $3/MTok input, $15/MTok output (参考値)

2. 予算超過時の自動停止条件を定義

3. `copilot-instructions.md` の停止条件に「予算超過」を追加

## 作業3: AI Gateway 検討メモ（要件 #17）

`docs/orchestration.md` に「AI Gateway 検討」セクションを追加してください:

- LiteLLM / PortKey / Helicone の比較表
- 現時点では Copilot Chat 経由のため直接適用不可であることを注記
- 将来的にプログラマティック API 呼び出しに移行した際の導入指針

## 完了後

1. observability-guide.md と orchestration.md の新セクション間に矛盾がないことを確認
2. get_errors ツールで全体エラーゼロを確認

```

---

### Session 6: P2 コード品質改善（要件 #16, #18, #19）

#### 目的

ミューテーションテストの導入、`tracing.py` の型修正、`__init__.py` の `__all__` 定義追加。

#### 読み込みファイル一覧

| ファイル                                  | 推定トークン | 用途                 |
| ----------------------------------------- | ------------ | -------------------- |
| `docs/improvement-requirements-dev-orchestration-template-001.md` §5 | ~3,000 | 要件 |
| `src/observability/tracing.py`            | ~2,200       | #18 修正対象         |
| `src/sample/example_module.py`            | ~750         | #19 参考             |
| `tests/test_sample_properties.py`         | ~1,200       | #16 対象             |
| `scripts/init_packages.sh`               | ~230         | #19 修正対象         |
| `pyproject.toml.template`                 | ~600         | #16 ツール追加       |
| **合計**                                  | **~7,980**   |                      |

#### 推定総トークン消費: ~45K / 128K（余裕大）

#### コンテキスト切れ許容タイミング

- 全タイミングで OK（すべて独立したコード修正）

#### 作業指示文（コピペ用）

```

以下の3つの改善を実施してください。

## 作業1: ミューテーションテスト導入（要件 #16）

テストの有効性を検証するためにミューテーションテストツールを導入してください:

1. `pyproject.toml.template` に `mutmut` を dev 依存に追加
2. `pyproject.toml.template` に mutmut の設定セクションを追加:
   ```toml
   [tool.mutmut]
   paths_to_mutate = "src/"
   tests_dir = "tests/"
   ```
3. `.github/workflows/ci.yml` にミューテーションテストステップを追加
   （ただし `continue-on-error: true` で非ブロッキングとする）
4. `docs/quality-guide.md` にミューテーションテストの説明セクションを追加

## 作業2: tracing.py 戻り値型の修正（要件 #18）

`src/observability/tracing.py` の `_get_tracer()` 関数の戻り値型を修正してください:

- 現状: 戻り値型が曖昧（`Any` または型未指定）
- 修正: `Optional[Tracer]` を明示し、OpenTelemetry の `tracer` モジュールからの型を使用
- None ケース（OTel 未インストール時）のハンドリングを型安全にする
- strict mypy でエラーが出ないことを確認

## 作業3: init_packages.sh の **all** 生成対応（要件 #19）

`scripts/init_packages.sh` を修正して、生成する `__init__.py` に `__all__` を含めるようにしてください:

- 空の `__init__.py` ではなく、`__all__: list[str] = []` を含むファイルを生成
- 既存の `__init__.py` がある場合は上書きしない（現行動作を維持）

## 完了後

1. `python -c "from src.observability.tracing import _get_tracer"` で import エラーなし確認
2. get_errors ツールで全体エラーゼロを確認

```

---

### Session 7: P2 ドキュメント・プロセス改善（要件 #20, #21, #22, #23, #24, #25）

#### 目的

requirements.md / constraints.md の実値記入ガイド、モデル一覧の移管、
TDD 並列フロー検討、Eval フレームワーク検討、型安全オーケストレーション検討。

#### 読み込みファイル一覧

| ファイル                                      | 推定トークン | 用途                     |
| --------------------------------------------- | ------------ | ------------------------ |
| `docs/improvement-requirements-dev-orchestration-template-001.md` §1, §6, §8 | ~5,000 | 要件 |
| `docs/requirements.md`                        | ~1,300       | #20 修正対象             |
| `docs/constraints.md`                         | ~1,000       | #21 修正対象             |
| `docs/orchestration.md` §7                    | ~3,000       | #22 修正対象             |
| `docs/architecture.md`                        | ~900         | 参考                     |
| `project-config.yml`                          | ~1,000       | #22 移管先               |
| **合計**                                      | **~12,200**  |                          |

#### 推定総トークン消費: ~55K / 128K

#### コンテキスト切れ許容タイミング

- 全タイミングで OK（すべて独立したドキュメント修正）
- ただし #23, #24, #25 は設計検討のため、一貫した議論が望ましい

#### 作業指示文（コピペ用）

```

以下の6つの改善を実施してください。#20-#22 は具体的な修正、#23-#25 は設計検討です。

## 作業1: requirements.md のテンプレート適用ガイド追加（要件 #20）

`docs/requirements.md` の FR-001, FR-010 等がプレースホルダーのままです。
テンプレートとしての性質を維持しつつ、以下を改善してください:

- 各 FR/NFR にコメントで「記入例」を追加（`<!-- 例: ユーザーが ... -->` 形式）
- 「テンプレート適用時のチェックリスト」セクションを末尾に追加
- `GETTING_STARTED.md` に requirements.md 記入手順のリンクを確認

## 作業2: constraints.md の閾値テンプレート改善（要件 #21）

`docs/constraints.md` に具体的な閾値定義のテンプレートを追加してください:

- 各制約に `閾値`, `トリガー条件`, `アクション` の3列を明示するテーブル形式
- 記入例を1つ含める

## 作業3: モデル一覧の project-config.yml 移管（要件 #22）

`docs/orchestration.md` §7.2 のモデル一覧（利用可能モデル表）を整理してください:

- 静的な一覧を削除し、「最新のモデル情報は各プロバイダーの公式ドキュメントを参照」に変更
- モデル選定の **ガイドライン**（判断基準・コスト考慮事項）は残す
- 具体的なモデル名は `project-config.yml` の `[models]` セクションのみで管理

## 作業4: TDD 並列フロー検討メモ（要件 #23）

`docs/orchestration.md` に「TDD 並列フローの検討」補足セクションを追加してください:

- 現行: implementer → test-engineer（逐次）
- 提案: テスト仕様書を先に作成 → implementer と test-engineer が並行で実装
- メリット: テスト品質の向上、TDD 本来の設計駆動効果
- デメリット: 調整コスト増、LLM コンテキストの分断
- 結論: 「将来的に検討」として Issue 化の推奨

## 作業5: Eval フレームワーク検討メモ（要件 #24）

`docs/orchestration.md` に「パイプライン品質評価」セクションを追加してください:

- 目的: パイプラインが「正しい実装」を生成できた割合を計測
- 候補ツール: Promptfoo, Braintrust, LangSmith
- 評価軸: CI 通過率、監査指摘数の推移、レビュー修正回数
- 結論: 「データ蓄積後に導入検討」として Issue 化の推奨

## 作業6: 型安全オーケストレーション検討メモ（要件 #25）

`docs/orchestration.md` に「型安全オーケストレーション」セクションを追加してください:

- LangGraph: 状態機械ベース、グラフの視覚化、再現可能な実行トレース
- Pydantic AI: 型安全なエージェント呼び出し、バリデーション内蔵
- 現行 Copilot Chat ベースとの互換性課題
- 結論: 「プログラマティック API 移行時に再評価」として本テンプレートの将来方針を記載

## 完了後

1. docs/ 内の各ファイル間に矛盾がないことを確認
2. get_errors ツールで全体エラーゼロを確認
3. orchestration.md の新セクションが既存構成と整合していることを確認

```

---

## 5. クロスリポジトリ同期ガイド

### 同期方針

本リポジトリ（dev-orchestration-template）での改善完了後、以下のファイルの変更を
`stock-trading-system` に適用する:

| 変更対象                     | 同期方法                                             |
| ---------------------------- | ---------------------------------------------------- |
| `ci/policy_check.py`         | 差分を手動適用（stock-trading-system は拡張版あり）   |
| `.github/workflows/*.yml`    | 差分を手動適用（stock-trading-system のワークフロー構成が異なる） |
| `docs/runbook.md`            | ロールバック手順セクションをコピー（適宜調整）       |
| `docs/orchestration.md`      | 新セクションをコピー（適宜調整）                     |
| agent定義ファイル            | モデル設定を同期                                     |
| `scripts/init_packages.sh`   | `__all__` 生成ロジックをコピー                       |

### 同期不要（stock-trading-system 固有）

- `src/trading_system/` 配下のコード修正（#18 の具体的修正箇所が異なる）
- `docs/risk-limits.md`（stock-trading-system 固有の制約）
- ドメイン固有のテスト設定

---

## 6. リスクと緩和策

| リスク                             | 影響        | 緩和策                                        |
| ---------------------------------- | ----------- | --------------------------------------------- |
| Session 途中でコンテキスト圧縮     | 作業品質低下 | 15ターン到達前に新セッション開始               |
| CI 追加後の既存テスト失敗          | ブロック     | `continue-on-error: true` で段階的導入        |
| Action ハッシュの不一致            | CI 停止     | ci.yml の既存ハッシュを基準として統一          |
| bootstrap.sh 修正による互換性破壊  | テンプレ破損 | 修正前後で `bootstrap.sh --dry-run` テスト     |
| P2 設計検討がスコープ拡大          | 時間超過     | 検討メモとして記録し、Issue 化で区切る         |

---

_本計画は `docs/improvement-requirements-dev-orchestration-template-001.md` の25項目を実行するための手順書である。_
```
