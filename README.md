# dev-orchestration-template

Copilot エージェント駆動の開発オーケストレーション テンプレートリポジトリ。

あらゆるプロジェクトに適用可能な、計画→実装→監査→リリースの自動化フレームワークを提供する。

> **初めての方へ**: [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) に、macOS 向けのセットアップ手順・開発概念の解説・VS Code の設定方法をまとめています。

## 特徴

- **SSOT（Single Source of Truth）ドキュメント体系**: 要件・ポリシー・制約・計画・アーキテクチャを `docs/` に集約
- **7エージェント構成**: Orchestrator が司令塔として5つの専門エージェントに委譲する多層監査体制
- **自動化スクリプト**: Labels / Milestones / Epic Issues / GitHub Project を一括作成
- **CI 品質ゲート**: ポリシーチェック（禁止操作・秘密情報検出）を含む CI パイプライン
- **言語非依存**: Python / TypeScript / Rust / Go 等、任意の言語に対応可能

## クイックスタート

### 1. テンプレートからリポジトリを作成

```bash
# GitHub の "Use this template" ボタンから作成するか、以下のコマンドを使用
gh repo create my-project --template githypn/dev-orchestration-template --clone
cd my-project
```

### 2. プロジェクト設定を編集

```bash
# project-config.yml を開き、プロジェクトの情報を入力する
code project-config.yml
```

主な設定項目：

| セクション  | 説明                                             |
| ----------- | ------------------------------------------------ |
| `project`   | プロジェクト名、説明、オーナー                   |
| `toolchain` | 言語、パッケージマネージャ、lint/test コマンド   |
| `source`    | ソースディレクトリ、パッケージ名、モジュール構成 |
| `roadmap`   | フェーズ構成（Phase 0〜4）                       |
| `policies`  | 禁止パターン（CI で自動検査）                    |
| `github`    | Labels、Project 名                               |

### 3. ブートストラップを実行

```bash
# プロジェクト構造の初期化（ディレクトリ作成、設定ファイル生成、テンプレート変数置換）
bash scripts/bootstrap.sh

# GitHub Labels / Milestones / Issues / Project の自動作成
bash scripts/setup_github.sh
```

### 4. docs を編集

`docs/` 配下のテンプレートをプロジェクトに合わせて編集する：

```
docs/
├── plan.md           # ロードマップ、Next タスク、Backlog
├── requirements.md   # 要件定義・受入条件
├── policies.md       # ポリシー（禁止事項等）
├── constraints.md    # 制約仕様（しきい値等）
├── architecture.md   # アーキテクチャ・責務境界
├── runbook.md        # 実行・復旧手順
└── adr/
    └── ADR-TEMPLATE.md  # 重要判断の記録テンプレート
```

### 5. 開発を開始

```bash
# Copilot Chat で Orchestrator エージェントを起動
# 「計画に沿い作業を実施」と指示すると、plan.md の Next タスクを自動的に分解・実行する
```

## ディレクトリ構成

```
.
├── project-config.yml              # プロジェクト設定（ブートストラップ用）
│
├── docs/                           # 正本ドキュメント（SSOT）
│   ├── plan.md                     # 計画・ロードマップ
│   ├── requirements.md             # 要件定義
│   ├── policies.md                 # ポリシー
│   ├── constraints.md              # 制約仕様
│   ├── architecture.md             # アーキテクチャ
│   ├── runbook.md                  # 運用手順
│   └── adr/                        # Architecture Decision Records
│
├── agents/                         # エージェント定義（参考資料）
│   ├── implementer.agent.md
│   ├── test_engineer.agent.md
│   ├── auditor_spec.agent.md
│   ├── auditor_security.agent.md
│   ├── auditor_reliability.agent.md
│   └── release_manager.agent.md
│
├── .github/
│   ├── agents/                     # Copilot Custom Agents（実動作版）
│   │   ├── orchestrator.agent.md
│   │   ├── implementer.agent.md
│   │   ├── test-engineer.agent.md
│   │   ├── auditor-spec.agent.md
│   │   ├── auditor-security.agent.md
│   │   ├── auditor-reliability.agent.md
│   │   └── release-manager.agent.md
│   ├── instructions/               # Copilot 指示ファイル
│   │   ├── docs.instructions.md
│   │   ├── security.instructions.md
│   │   └── tests.instructions.md
│   ├── prompts/                    # 監査・実装プロンプト
│   │   ├── EXECUTE.prompt.md
│   │   ├── AUDIT_SPEC.prompt.md
│   │   ├── AUDIT_SECURITY.prompt.md
│   │   ├── AUDIT_RELIABILITY.prompt.md
│   │   └── FINAL_REVIEW.prompt.md
│   ├── ISSUE_TEMPLATE/             # Issue テンプレート
│   ├── PULL_REQUEST_TEMPLATE.md    # PR テンプレート
│   ├── workflows/ci.yml            # CI ワークフロー
│   ├── copilot-instructions.md     # Copilot 全体ルール
│   └── copilot-code-review-instructions.md
│
├── ci/
│   └── policy_check.py             # ポリシーチェッカー
│
├── scripts/
│   ├── bootstrap.sh                # プロジェクト初期化
│   ├── setup_github.sh             # GitHub Labels/Milestones/Issues/Project 作成
│   └── init_packages.sh            # Python __init__.py 生成
│
├── configs/                        # 実行設定
├── data/                           # データ（git 管理外）
├── outputs/                        # 生成物（git 管理外）
└── notebooks/                      # 実験用ノートブック
```

## エージェント構成

```
ユーザー
  │
  ▼
Orchestrator（司令塔）
  │  自らコードは書かない。分解・委譲・統合に専念する。
  │
  ├──→ Implementer（実装担当）
  ├──→ Test Engineer（テスト担当）
  ├──→ Auditor Spec（仕様監査）
  ├──→ Auditor Security（セキュリティ監査）
  ├──→ Auditor Reliability（信頼性監査）
  │
  └──→ Release Manager（リリース判定）
```

### ワークフロー

1. **計画確認**: `docs/plan.md` の Next から対象タスクを特定
2. **実装委譲**: Implementer にコード実装を指示
3. **テスト委譲**: Test Engineer にテスト作成を指示
4. **三重監査**: Spec / Security / Reliability の独立監査
5. **修正ループ**: Must 指摘がゼロになるまで繰り返し
6. **リリース判定**: Release Manager が AC チェックしてマージ可否を判定
7. **計画更新**: 完了した Next を削除、必要なら Backlog を昇格

## 受入条件（全PR共通）

| ID     | 条件                                            |
| ------ | ----------------------------------------------- |
| AC-001 | 変更は要件・ポリシー・計画のいずれかに整合する  |
| AC-010 | 必要なテストが追加または更新されている          |
| AC-020 | CI（lint/type/test/policy_check）が成功している |
| AC-030 | 変更に応じて正本（docs）が更新されている        |
| AC-040 | 検証手順と結果が PR 本文に記載されている        |
| AC-050 | プロジェクト固有の制約に反する変更がない        |

## カスタマイズガイド

### ポリシーチェックの拡張

`ci/policy_check.py` の定数を編集して、プロジェクト固有のルールを追加する：

```python
# 例: HTTP ライブラリの import を禁止
FORBIDDEN_IMPORT_PATTERNS = [
    r"^\s*import\s+requests",
    r"^\s*from\s+requests\s+import",
]

# 例: 本番 URL の直書きを禁止
FORBIDDEN_PATTERNS = [
    r"https://api\.production\.example\.com",
]
```

### エージェントのカスタマイズ

`.github/agents/` 配下のエージェント定義を編集して、プロジェクト固有の指示を追加する。

### 制約の追加

`docs/constraints.md` に制約を定義し、テストで境界値をカバーする。

## プロトコル標準

### MCP（Model Context Protocol）

AI モデルと外部ツール・データソース間の通信に使用するオープン標準プロトコル。
各エージェントがデータベース、ファイルシステム、外部 API 等のリソースにアクセスする際のインターフェースを標準化する。
詳細: [docs/a2a-design/README.md（§4 MCP vs A2A の使い分けガイド）](docs/a2a-design/README.md#4-mcp-vs-a2a-の使い分けガイド)

### A2A（Agent2Agent Protocol）

複数のエージェントフレームワークやベンダー間でエージェントが協調する際に使用するオープン標準プロトコル。
現時点では設計テンプレートのみ提供（`docs/a2a-design/`）。
実装への移行は各プロジェクトの要件に応じて判断すること。
詳細: [docs/a2a-design/README.md](docs/a2a-design/README.md)

## ライセンス

MIT License
