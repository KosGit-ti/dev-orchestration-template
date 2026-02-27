# オブザーバビリティ導入ガイド

> OpenTelemetry GenAI Semantic Conventions に準拠した計装テンプレートの導入・使用方法

## 目的

このテンプレートは、マルチエージェントワークフローにおける以下の操作を
OpenTelemetry でトレースするための標準化された計装モジュールを提供する。

- **エージェント操作**: タスク実行、計画立案、委譲等
- **ツール実行**: 外部ツール呼び出し、コマンド実行等
- **LLM 呼び出し**: モデルへのプロンプト送信・レスポンス受信

すべてのデコレータは OTel SDK がインストールされていない場合にパススルー（no-op）
として動作し、既存コードに副作用を与えない。

---

## 前提条件

### 必要な依存ライブラリ

| パッケージ | 最小バージョン | 用途 |
|---|---|---|
| `opentelemetry-sdk` | 1.20.0 | コア SDK（TracerProvider 等） |
| `opentelemetry-exporter-otlp` | 1.20.0 | OTLP エクスポータ（本番環境向け） |

### Python バージョン

- Python 3.11 以上

---

## セットアップ手順

### 1. 依存ライブラリの追加

`pyproject.toml`（`pyproject.toml.template` から生成）に以下が含まれていることを確認する:

```toml
[project.optional-dependencies]
observability = [
    "opentelemetry-sdk>=1.20.0",
    "opentelemetry-exporter-otlp>=1.20.0",
]
```

インストール:

```bash
# uv の場合
uv sync --extra observability

# pip の場合
pip install -e ".[observability]"
```

### 2. プレースホルダーの置換

`src/observability/tracing.py` 内の以下のプレースホルダーをプロジェクトに合わせて置換する
（`bootstrap.sh` 実行時に自動置換される）:

| プレースホルダー | 説明 | 例 |
|---|---|---|
| `{{PROJECT_NAME}}` | プロジェクト名（`project-config.yml` の `project.name`） | `my-project` |
| `{{TRACER_NAME}}` | トレーサー識別子。通常は `パッケージ名.observability` | `my_package.observability` |

### 3. TracerProvider の初期化

アプリケーションのエントリーポイントで `init_tracer()` を呼び出す:

```python
from src.observability.tracing import init_tracer

# 基本的な初期化
init_tracer()

# コンソール出力を有効にする場合（開発・デバッグ用）
init_tracer(enable_console_export=True)

# カスタムサービス名を指定する場合
init_tracer(service_name="my-service", enable_console_export=True)
```

### 4. デコレータの適用

対象の関数にデコレータを付与する（詳細は次章を参照）。

---

## デコレータの使用方法と記録属性

### `@trace_agent_operation`

エージェントの操作（タスク計画、実行委譲等）をトレースする。

```python
from src.observability.tracing import trace_agent_operation

@trace_agent_operation("orchestrator.plan_task")
def plan_task(task: str) -> str:
    """タスクを分解し計画を立案する。"""
    ...
```

**記録属性**:

| 属性名 | 型 | 説明 |
|---|---|---|
| `gen_ai.agent.operation` | string | 操作名（引数 or `__qualname__`） |
| `gen_ai.system` | string | システム名（`SERVICE_NAME`） |
| `agent.status` | string | `"success"` / `"error"` |

### `@trace_tool_execution`

ツールの実行（シェルコマンド、ファイル操作等）をトレースする。

```python
from src.observability.tracing import trace_tool_execution

@trace_tool_execution("shell.run_command")
def run_shell_command(cmd: str) -> str:
    """シェルコマンドを実行する。"""
    ...
```

**記録属性**:

| 属性名 | 型 | 説明 |
|---|---|---|
| `tool.name` | string | ツール名（引数 or `__qualname__`） |
| `gen_ai.system` | string | システム名（`SERVICE_NAME`） |
| `tool.status` | string | `"success"` / `"error"` |

### `@trace_llm_call`

LLM 呼び出し（モデルへのプロンプト送信）をトレースする。

```python
from src.observability.tracing import trace_llm_call

@trace_llm_call("claude-3-opus")
def call_claude(prompt: str) -> str:
    """Claude にプロンプトを送信する。"""
    ...
```

**記録属性**:

| 属性名 | 型 | 説明 |
|---|---|---|
| `gen_ai.operation.name` | string | 操作種別（`"chat"`） |
| `gen_ai.request.model` | string | リクエスト時のモデル名 |
| `gen_ai.system` | string | システム名（`SERVICE_NAME`） |
| `gen_ai.response.finish_reason` | string | `"stop"` / `"error"` |

> **トークン数の記録**: `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` は
> LLM API のレスポンスから取得する必要があるため、デコレータ内では自動設定されない。
> 必要に応じてスパンに手動で属性を追加すること。

---

## OpenTelemetry GenAI Semantic Conventions のステータスに関する注記

本ドキュメント記載時点（2026年2月）で、OpenTelemetry GenAI Semantic Conventions の
ステータスは **"Development"** である。"Stable" には未達のため、属性名が将来変更される
可能性がある。

最新仕様は以下を定期的に確認し、属性名の変更に追従すること:

- <https://opentelemetry.io/docs/specs/semconv/gen-ai/>

---

## ローカル開発環境での確認方法

### stdout exporter を用いたトレース確認

```bash
# 1. OTel SDK をインストール
uv sync --extra observability

# 2. コンソール出力モードで初期化し、トレースを確認
python -c "
from src.observability.tracing import init_tracer, get_tracer

# コンソール出力を有効化
init_tracer(enable_console_export=True)

# テストスパンを生成
tracer = get_tracer()
if tracer is not None:
    with tracer.start_as_current_span('test-span') as span:
        span.set_attribute('test.key', 'test-value')
    print('トレース出力を確認してください（上記の JSON 出力）')
else:
    print('OTel SDK が見つかりません')
"
```

正常に動作すると、コンソールに JSON 形式のスパン情報が出力される。

---

## エージェントパイプラインの計装

> 本セクションはパイプライン層（Orchestrator が管理する Step 1〜11、`orchestration.md` §3 準拠）の OTel スパン設計を定義する。
> 現時点では **設計のみ** であり、実装は将来課題とする。

### 計測対象

エージェントパイプラインで計測すべき項目は以下の通り:

| 分類 | 計測対象 | 目的 |
|---|---|---|
| パフォーマンス | 各ステップ（Step 1〜11）の実行時間 | ボトルネック特定、SLA 設定 |
| 信頼性 | サブエージェントへの委譲回数・成功率 | エージェントの安定性評価 |
| 効率性 | 修正ループ回数の分布 | プロセス改善指標 |
| CI 品質 | CI 実行時間・失敗率 | CI パイプラインの健全性監視 |

### スパン階層設計

`orchestration.md` §3 のパイプラインフローに対応するスパン階層を以下のように設計する。
各スパンは `trace_agent_operation` デコレータ（[tracing.py](../src/observability/tracing.py)）と
同一のセマンティクスで生成される。

```text
pipeline (root span)
├── plan_selection           ... Step 1: 計画読込
├── branch_creation          ... Step 2: ブランチ作成
├── implementation           ... Step 3: 実装委譲
│   └── implementer_call     ... implementer サブエージェント呼び出し
├── testing                  ... Step 3 (cont.): テスト委譲
│   └── test_engineer_call   ... test-engineer サブエージェント呼び出し
├── semantic_analysis        ... Step 3.5: セマンティック影響分析（条件付き）
├── local_ci                 ... Step 4: ローカル CI
│   ├── ruff_check           ... ruff check 実行
│   ├── mypy                 ... mypy 型チェック実行
│   └── pytest               ... pytest 実行
├── ide_error_gate           ... Step 4.5: IDE エラーゲート
├── audit                    ... Step 5: 監査委譲
│   ├── audit_spec           ... auditor-spec サブエージェント呼び出し
│   ├── audit_security       ... auditor-security サブエージェント呼び出し
│   └── audit_reliability    ... auditor-reliability サブエージェント呼び出し
├── fix_loop                 ... Step 6: 修正ループ（0〜3回）
│   └── fix_iteration        ... 各修正イテレーション
├── pr_creation              ... Step 7: コミット・PR 作成
├── pr_ci                    ... Step 8: PR CI 検証
├── review_loop              ... Step 9: Copilot レビュー対応
│   └── review_iteration     ... 各レビューイテレーション
├── release_judgment         ... Step 10: リリース判定
└── human_approval           ... Step 11: 人間承認待ち
```

### 属性（Attributes）定義

OpenTelemetry GenAI Semantic Conventions に準拠しつつ、パイプライン固有の属性を追加定義する。

#### パイプライン共通属性（root span に設定）

| 属性名 | 型 | 説明 | 例 |
|---|---|---|---|
| `pipeline.task_id` | string | 対象タスクの ID | `"TASK-001"` |
| `pipeline.branch` | string | フィーチャーブランチ名 | `"feat/TASK-001-add-feature"` |
| `pipeline.mode` | string | 動作モード | `"auto"` / `"general"` / `"plan_revision"` |

#### ループ関連属性（fix_loop / review_loop に設定）

| 属性名 | 型 | 説明 | 例 |
|---|---|---|---|
| `pipeline.loop_count` | number | 現在のループ回数 | `2` |
| `pipeline.loop_max` | number | 最大ループ回数 | `3` |
| `pipeline.loop_type` | string | ループ種別 | `"ci_fix"` / `"audit_fix"` / `"review_fix"` |

#### エージェント呼び出し属性（各サブエージェントスパンに設定）

| 属性名 | 型 | 説明 | 例 |
|---|---|---|---|
| `agent.name` | string | エージェント名 | `"implementer"` |
| `agent.model` | string | 使用モデル名 | `"claude-sonnet-4.6"` |
| `agent.token_usage.input` | number | 入力トークン数 | `15000` |
| `agent.token_usage.output` | number | 出力トークン数 | `3000` |
| `agent.status` | string | 実行結果 | `"success"` / `"failure"` / `"partial"` |

#### CI 実行属性（local_ci / pr_ci に設定）

| 属性名 | 型 | 説明 | 例 |
|---|---|---|---|
| `ci.tool` | string | CI ツール名 | `"ruff"` / `"mypy"` / `"pytest"` |
| `ci.exit_code` | number | 終了コード | `0` |
| `ci.error_count` | number | エラー件数 | `3` |

### tracing.py との関係

パイプライン層のスパンは、既存の `tracing.py` デコレータを **そのまま適用** する方針とする:

| デコレータ | パイプラインでの用途 |
|---|---|
| `@trace_agent_operation` | `pipeline`, `implementation`, `audit` 等の高レベルスパン |
| `@trace_tool_execution` | `ruff_check`, `mypy`, `pytest` 等のツール実行スパン |
| `@trace_llm_call` | サブエージェント内の LLM 呼び出し（将来の API 直接呼び出し時） |

パイプラインのプログラマティックな計装は、Copilot Chat ベースの現行アーキテクチャでは
直接適用できない。将来的にエージェントフレームワーク（A2A 等）に移行した際に、
上記設計に基づいて実装する。

### 実装ロードマップ

| Phase | 状態 | 内容 |
|---|---|---|
| Phase 1（現在） | ✅ 完了 | 設計ドキュメントの整備（本セクション） |
| Phase 2 | 未着手 | エージェントフレームワーク移行時に計装コードを実装 |
| Phase 3 | 未着手 | ダッシュボード構築（Jaeger / Grafana Tempo 等で可視化） |

### CI での確認

`.github/workflows/ci.yml` の「OpenTelemetry 計装確認」ステップのコメントを
解除することで、CI パイプラインでも計装の初期化を検証できる。

---

## ファイル構成

```
src/
└── observability/
    ├── __init__.py       # パッケージ初期化（空ファイル）
    └── tracing.py        # 計装デコレータ（3種）+ TracerProvider 初期化
```
