# オーケストレーション構成ドキュメント

> 本ドキュメントは、AI エージェントパイプラインの全体設計・構成・運用を一元的に管理するテンプレートである。
> プロジェクト固有の内容は `<!-- PROJECT: ... -->` コメントで示す。
> 実際のプロジェクトに適用する際は、これらのプレースホルダーを置き換えること。

## 目次

1. [全体アーキテクチャ](#1-全体アーキテクチャ)
2. [エージェント一覧](#2-エージェント一覧)
3. [パイプラインフロー](#3-パイプラインフロー)
4. [エージェント応答スキーマ](#4-エージェント応答スキーマ)
5. [Serena MCP 統合](#5-serena-mcp-統合)
6. [Copilot コードレビュー統合](#6-copilot-コードレビュー統合)
7. [CI/CD パイプライン](#7-cicd-パイプライン)
8. [AI モデル構成](#8-ai-モデル構成)
9. [パイプライン状態永続化](#9-パイプライン状態永続化)
10. [構成ファイル一覧](#10-構成ファイル一覧)
11. [運用手順](#11-運用手順)
12. [変更履歴](#12-変更履歴)

---

## 1. 全体アーキテクチャ

### 1.1 設計思想

本システムは **エージェント指向のソフトウェア開発パイプライン** を採用している。
人間の開発者が計画を承認し、AI エージェント群が自律的に実装・テスト・監査・リリース判定を行う。

主要な設計原則：

- **関心の分離**: 各エージェントは単一の責務を持つ
- **独立監査**: 監査エージェントは実装エージェントとは独立して判断する
- **フェイルクローズ**: 不確実な場合は安全側に倒す（P-010）
- **Human-in-the-loop**: 最終マージ判断は人間が行う
- **Bounded Recursion**: 修正ループには回数上限（最大3回）を設ける

### 1.2 システム構成図

```mermaid
graph TB
    Human[👤 人間]

    subgraph "オーケストレーション層"
        Orch[Orchestrator<br/>司令塔]
    end

    subgraph "実装層"
        Impl[implementer<br/>コード実装]
        Test[test-engineer<br/>テスト作成・実行]
    end

    subgraph "監査層"
        AuditSpec[auditor-spec<br/>仕様監査]
        AuditSec[auditor-security<br/>セキュリティ監査]
        AuditRel[auditor-reliability<br/>信頼性監査]
    end

    subgraph "リリース判定層"
        RM[release-manager<br/>リリース判定]
    end

    subgraph "外部ツール"
        Serena[Serena MCP<br/>セマンティック分析]
        CI[GitHub Actions CI<br/>静的解析・テスト]
        CopilotReview[Copilot Code Review<br/>AI コードレビュー]
        GH[GitHub API<br/>PR・Issue 管理]
    end

    Human -->|トリガー| Orch
    Orch -->|実装委譲| Impl
    Orch -->|テスト委譲| Test
    Orch -->|監査委譲| AuditSpec
    Orch -->|監査委譲| AuditSec
    Orch -->|監査委譲| AuditRel
    Orch -->|ハンドオフ| RM
    RM -->|判定結果| Human

    Impl -.->|セマンティック分析| Serena
    Orch -.->|フォールバック分析| Serena
    AuditRel -.->|検証分析| Serena

    Orch -->|CI 実行| CI
    Orch -->|PR 作成| GH
    CopilotReview -.->|自動レビュー| GH
```

### 1.3 動作モード

| モード | トリガーフレーズ | 内容 |
|---|---|---|
| 自動実行 | 「計画に従い作業を実施して」「Next を実行して」等 | plan.md の Next タスクを自律実行 |
| 計画修正 | 「計画を修正して」「Issue を追加して」等 | plan.md / GitHub Issues / Project を更新 |
| 汎用リクエスト | 上記以外 | 改善提案・調査・リファクタリング等（PR を伴う場合は自動実行 Step 6-12 相当の PR フローを追加適用。copilot-instructions.md General Request セクション参照） |
| モデル最適化 | 「モデルを最適化して」「モデルを見直して」等 | AI モデル割当の提案・変更 |

---

## 2. エージェント一覧

### 2.1 エージェント定義サマリ

| エージェント | ファイル | 責務 | 呼出可能 | MCP |
|---|---|---|---|---|
| Orchestrator | `orchestrator.agent.md` | パイプライン全体の指揮・統合 | ✅ | ✅ |
| implementer | `implementer.agent.md` | コード実装・docs 更新 | ❌ | ✅ |
| test-engineer | `test-engineer.agent.md` | テスト作成・実行 | ❌ | ❌ |
| auditor-spec | `auditor-spec.agent.md` | 仕様整合性の監査 | ❌ | ❌ |
| auditor-security | `auditor-security.agent.md` | セキュリティ監査 | ❌ | ❌ |
| auditor-reliability | `auditor-reliability.agent.md` | 信頼性・テスト品質監査 | ❌ | ✅ |
| release-manager | `release-manager.agent.md` | リリース可否の最終判定 | ✅ | ❌ |

### 2.2 ツールマトリクス

| ツール | Orch | Impl | Test | A-Spec | A-Sec | A-Rel | RM |
|---|---|---|---|---|---|---|---|
| agent | ✅ | - | - | - | - | - | - |
| read | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| editFiles | ✅ | ✅ | ✅ | - | - | - | - |
| runInTerminal | ✅ | ✅ | ✅ | - | - | - | ✅ |
| search | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| search/usages | - | - | - | ✅ | ✅ | ✅ | - |
| web/fetch | ✅ | - | - | ✅ | - | - | ✅ |
| web/githubRepo | ✅ | - | - | - | - | - | ✅ |
| mcp (Serena) | ✅ | ✅ | - | - | - | ✅ | - |

### 2.3 エージェント連携（委譲関係）

```mermaid
graph LR
    Orch[Orchestrator]
    Impl[implementer]
    Test[test-engineer]
    AS[auditor-spec]
    ASec[auditor-security]
    AR[auditor-reliability]
    RM[release-manager]

    Orch -->|sub-agent| Impl
    Orch -->|sub-agent| Test
    Orch -->|sub-agent| AS
    Orch -->|sub-agent| ASec
    Orch -->|sub-agent| AR
    Orch ==>|handoff| RM
```

- **sub-agent**: Orchestrator が呼び出し、結果を受け取る（制御は Orchestrator に戻る）
- **handoff**: Orchestrator が制御ごと引き渡す（release-manager が最終判定を行う）

### 2.4 各エージェントの詳細

#### Orchestrator

- **責務**: パイプライン全体の指揮。タスク分解、エージェント委譲、結果統合。**自らコードは書かない。**
- **サブエージェント**: implementer, test-engineer, auditor-spec, auditor-security, auditor-reliability
- **ハンドオフ先**: release-manager
- **制約**: Next 以外のタスクに着手しない / Backlog を勝手に開始しない / ポリシー違反検出時は即停止

#### implementer

- **責務**: ソースコードの実装と `docs/` の更新
- **制約**: P-001（禁止操作）/ P-002（秘密情報禁止）を厳守
- **Serena 利用**: Shift-Left で実装前・中・後にセマンティック分析を実施（第1層）

#### test-engineer

- **責務**: 単体テスト / 境界値テスト / 統合テスト / 再現性テストの作成・実行
- **制約**: ダミーデータのみ使用 / 実データ禁止

#### auditor-spec

- **責務**: `requirements.md` / `policies.md` / `constraints.md` との整合性を独立監査
- **出力**: Must / Should / Nice に分類された指摘リスト

#### auditor-security

- **責務**: P-001（禁止操作）/ P-002（秘密情報禁止）/ P-040（依存関係）の監査
- **特徴**: 最も制限されたツールセット（editFiles / runInTerminal なし）

#### auditor-reliability

- **責務**: 再現性（NFR-001）/ テスト品質（NFR-020）/ エラーハンドリング（P-010）の監査
- **Serena 利用**: Stage 3 で条件付きセマンティック検証（第3層）

#### release-manager

- **責務**: 全監査結果を統合し、受入条件（AC）をチェックして PR のマージ可否を判定
- **制約**: コードを変更しない / 最終マージは人間が行う

---

## 3. パイプラインフロー

### 3.1 自動実行パイプライン全体像

```mermaid
flowchart TD
    Start([トリガー]) --> S1[Step 1: 計画読込]
    S1 --> S2[Step 2: ブランチ作成]
    S2 --> S3[Step 3: 実装委譲]
    S3 --> S3_5{Step 3.5: セマンティック<br/>影響分析?}
    S3_5 -->|必要| S3_5a[Serena 分析実行]
    S3_5 -->|不要| S4
    S3_5a --> S4[Step 4: ローカル CI]
    S4 --> S4_pass{CI 通過?}
    S4_pass -->|失敗| S4_fix[修正ループ<br/>最大3回]
    S4_fix --> S4
    S4_pass -->|成功| S4_5[Step 4.5: IDE エラーゲート]
    S4_5 --> S4_5_pass{エラーゼロ?}
    S4_5_pass -->|残存| S4_5_fix[修正ループ<br/>最大3回]
    S4_5_fix --> S4
    S4_5_pass -->|ゼロ| S5[Step 5: 監査委譲]
    S5 --> S6{Step 6: Must 指摘?}
    S6 -->|あり| S6_fix[修正ループ<br/>最大3回]
    S6_fix --> S4
    S6 -->|なし| S7[Step 7: コミット・PR 作成]
    S7 --> S8{Step 8: PR CI 通過?}
    S8 -->|失敗| S8_fix[修正→再プッシュ<br/>最大3回]
    S8_fix --> S8
    S8 -->|成功| S9[Step 9: Copilot レビュー対応]
    S9 --> S10[Step 10: リリース判定]
    S10 --> S11[Step 11: 人間承認待ち]
    S11 --> End([完了])
```

### 3.2 各ステップの詳細

<!-- PROJECT: CI コマンドはプロジェクトのツールチェインに合わせて変更すること -->

#### Step 1: 計画読込

- `docs/plan.md` の Next セクション先頭タスクを選択
- 起動時読込ファイル: `docs/plan.md`, `docs/requirements.md`, `docs/policies.md`, `docs/constraints.md`, `docs/architecture.md`

#### Step 2: ブランチ作成

- `feat/<タスクID>-<説明>` 形式のフィーチャーブランチを作成

#### Step 3: 実装委譲

- **implementer** にコード実装を委譲
- **test-engineer** にテスト作成を委譲
- implementer は Serena MCP を活用して Shift-Left 分析を実施（§4 参照）

#### Step 3.5: セマンティック影響分析（条件付き）

- **条件**: `src/` の公開 API / シグネチャが変更された場合
- **実行**: Orchestrator が Serena MCP で影響分析を実行
- **目的**: implementer が未実施の場合のフォールバック

#### Step 4: ローカル CI

<!-- PROJECT: CI コマンドはプロジェクトに合わせて変更 -->

失敗時は implementer に修正を指示し、最大3回ループ。

#### Step 4.5: IDE エラーゲート

- `get_errors` ツール（filePaths 省略）でワークスペース全体のエラーを取得
- **エラーがゼロになるまで Step 5 に進まない**

#### Step 5: 監査委譲（並列実行）

3つの監査エージェントを **可能な限り並列に** 呼び出す。
Orchestrator はサブエージェント呼び出しを1つずつ待機するのではなく、独立性のある3つの監査を同時に発行することが望ましい。
ただし、フレームワークの制約により逐次実行しかサポートされない場合は、その旨をパイプラインログに記録する。

> **注記**: 並列実行の可否は利用するプラットフォーム（Copilot Chat / Agent Framework 等）に依存する。
> 逐次実行でも機能的な問題は発生しないが、パイプライン実行時間が増大する。

#### Step 6: 修正ループ

- Must 指摘が 1 件以上 → implementer に修正指示 → Step 4 から再実行
- 最大3回のループで解決しない場合は停止

#### Step 7: コミット・プッシュ・PR 作成

- Conventional Commit フォーマットでコミット
- PR 本文は `--body-file` で一時ファイル経由（`--body` は禁止）
- PR 本文に `Closes #XX` を記載

#### Step 8: PR CI 検証

- `gh pr checks <PR番号> --watch` で CI 結果を監視
- 失敗時は修正 → 再プッシュ（最大3回）

#### Step 9: Copilot コードレビュー対応（初回レビューのみ）

§6 で詳述。初回レビューのみ取得し、最大3回のイテレーションで指摩対応。

#### Step 10: リリース判定

- **release-manager** にハンドオフ
- 承認された場合、「マージ可能」と人間に報告

#### Step 11: 人間承認待ち

- **人間の最終承認なしにマージは実行しない**（自動マージ禁止）

### 3.3 停止条件

- ポリシー違反（P-001〜P-003）の検出
- 修正ループが3回を超えた
- サブエージェントから解決不能なエラーが報告された
- `docs/plan.md` の Next が空

---

## 4. エージェント応答スキーマ

### 4.1 概要

各サブエージェントが Orchestrator に報告する際のフォーマットを標準化する。
これにより、Orchestrator はエージェント応答を一貫した形式で解析・統合できる。

> **注意**: 現時点では Copilot Chat の制約上、JSON スキーマの強制的なバリデーションは実装できない。
> 本スキーマはドキュメントベースの規約として定義し、将来のフレームワーク移行時に型安全化する設計とする。

### 4.2 共通応答スキーマ

すべてのサブエージェントは、報告に以下のフィールドを含めること。

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `status` | `"success" \| "failure" \| "partial"` | ✅ | タスクの完了状態 |
| `summary` | `string` | ✅ | 結果の自然言語要約（1〜3文） |
| `findings` | `Finding[]` | ❌ | 具体的な指摘事項のリスト（監査エージェント必須） |
| `metrics` | `Metrics` | ❌ | 定量的な指標（該当する場合） |

#### `Finding` オブジェクト

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `severity` | `"Must" \| "Should" \| "Nice"` | ✅ | 指摘の深刻度 |
| `file` | `string` | ✅ | 対象ファイルの相対パス |
| `line` | `number \| null` | ❌ | 対象行番号（特定できない場合は null） |
| `message` | `string` | ✅ | 指摘内容の説明 |

#### `Metrics` オブジェクト

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `tests_added` | `number` | ❌ | 追加されたテスト数 |
| `tests_passed` | `number` | ❌ | 通過したテスト数 |
| `tests_failed` | `number` | ❌ | 失敗したテスト数 |
| `coverage` | `number \| null` | ❌ | テストカバレッジ（%） |
| `files_changed` | `number` | ❌ | 変更されたファイル数 |
| `lines_added` | `number` | ❌ | 追加行数 |
| `lines_removed` | `number` | ❌ | 削除行数 |

### 4.3 エージェント別の応答要件

| エージェント | `status` | `summary` | `findings` | `metrics` |
|---|---|---|---|---|
| implementer | ✅ | ✅ | ❌（任意） | ✅（files_changed, lines_added/removed） |
| test-engineer | ✅ | ✅ | ❌（任意） | ✅（tests_added, tests_passed/failed, coverage） |
| auditor-spec | ✅ | ✅ | ✅（必須） | ❌ |
| auditor-security | ✅ | ✅ | ✅（必須） | ❌ |
| auditor-reliability | ✅ | ✅ | ✅（必須） | ❌ |
| release-manager | ✅ | ✅ | ✅（必須） | ❌ |

### 4.4 応答例

#### 監査エージェントの応答例

```json
{
  "status": "partial",
  "summary": "仕様監査完了。Must 指摘1件、Should 指摘2件を検出。",
  "findings": [
    {
      "severity": "Must",
      "file": "src/sample/calculator.py",
      "line": 42,
      "message": "requirements.md の AC-003 で要求されている入力バリデーションが未実装。"
    },
    {
      "severity": "Should",
      "file": "src/sample/calculator.py",
      "line": 15,
      "message": "docstring が Google Style に準拠していない。"
    }
  ],
  "metrics": null
}
```

#### implementer の応答例

```json
{
  "status": "success",
  "summary": "AC-003 の入力バリデーションを実装し、対応するユニットテストを追加。",
  "findings": [],
  "metrics": {
    "files_changed": 2,
    "lines_added": 45,
    "lines_removed": 3
  }
}
```

### 4.5 将来の型安全化ロードマップ

1. **Phase 1（現在）**: ドキュメントベースの規約として運用。Orchestrator が応答の形式を目視確認。
2. **Phase 2**: JSON Schema ファイル（`schemas/agent_response.json`）を作成し、CI でバリデーション。
3. **Phase 3**: A2A（Agent-to-Agent）プロトコル移行時に、型安全なメッセージングフレームワークで強制。

---

## 5. Serena MCP 統合

### 5.1 概要

Serena MCP はセマンティックなコード理解（シンボル検索、参照元追跡、構造把握）を提供するツールサーバーである。
**Shift-Left + ターゲット分析** アプローチを採用し、3層で統合する。

### 5.2 接続構成

```jsonc
// .vscode/mcp.json
{
  "servers": {
    "serena": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/oraios/serena",
        "serena", "start-mcp-server",
        "--context", "ide-assistant",
        "--project", "${workspaceFolder}"
      ],
      "type": "stdio"
    }
  }
}
```

### 5.3 3層統合モデル

| 層 | エージェント | タイミング | 目的 |
|---|---|---|---|
| 第1層（主要） | implementer | 実装前・中・後 | コード構造把握、参照元チェック、変更整合性確認 |
| 第2層（補完） | Orchestrator | Step 3.5 | implementer 未実施時のフォールバック影響分析 |
| 第3層（検証） | auditor-reliability | Stage 3 | 前段分析結果のスポットチェック or フォールバックフル分析 |

### 5.4 条件付き実行ルール

| 変更種別 | Serena 実行 |
|---|---|
| `src/` の公開 API / シグネチャ変更 | **必須** |
| `src/` の内部ロジック変更 | 推奨 |
| テスト / docs / config のみ | スキップ |
| MCP 利用不可 | スキップ（従来の方法で代替） |

---

## 6. Copilot コードレビュー統合

### 6.1 概要

GitHub Copilot Code Review は **PR 作成時に自動トリガーされる初回レビューのみ** を対象とする。
修正 push 後の再レビュー依頼は API 制限により自動化不可能であるため、
**静的解析（CI + get_errors）の通過をもって品質ゲート** とする。

### 6.2 技術的背景（API 制限）

API 経由での Copilot 再レビュー依頼は以下のすべてが失敗する（2025-07 検証済み）：

| 方法 | 結果 |
|---|---|
| REST API `POST /requested_reviewers` | Bot に対しては `requested_reviewers: []`（無視） |
| GraphQL `requestReviews` | Bot ノード ID を User として解決できない |
| dismiss → 再リクエスト | COMMENTED レビューは dismiss 不可（422 エラー） |

唯一の再レビュー手段は GitHub GUI の「Re-request review」ボタンのみ。

### 6.3 設計原則

| # | 原則 | 説明 |
|---|---|---|
| 1 | 初回レビューのみ取得 | PR 作成時の自動トリガーによる初回レビューのみを対象（再レビュー依頼は API 制限により不可能） |
| 2 | 静的解析が品質ゲート | 修正 push 後は CI + get_errors の通過をもって品質を担保する（再レビューの代替） |
| 3 | Bounded Recursion | 初回レビュー指摘への対応は最大3回のイテレーションで制限 |
| 4 | 静的解析ファースト | AI レビューの前に CI + get_errors を必ず通過させ、再レビューの代替としても静的解析を用いる |

### 6.4 レビュー対応フロー

```mermaid
flowchart TD
    Start([PR 作成]) --> WaitCI[CI 通過を確認]
    WaitCI --> WaitReview[初回 Copilot レビュー到着待機]
    WaitReview --> Stable[コメント安定化フェーズ]
    Stable --> GetComments[レビューコメント取得]
    GetComments --> Classify{Must/Should 指摘?}
    Classify -->|あり| Fix[implementer に修正委譲]
    Fix --> LocalCI[ローカル CI 再実行]
    LocalCI --> IDECheck[get_errors ゲート]
    IDECheck --> Reply[コメント返信]
    Reply --> Push[コミット・プッシュ<br/>再レビュー依頼なし]
    Push --> Recheck[初回レビューの<br/>未対応指摘を確認]
    Recheck --> Inc{iteration < 3?}
    Inc -->|Yes & 未解決あり| Fix
    Inc -->|No| Escalate[人間にエスカレーション]
    Classify -->|なし / approve| Done([ループ終了])
    Recheck -->|全解決| Done
```

### 6.5 指摘の分類

| 分類 | 対応 |
|---|---|
| **Must** | マージ前に修正必須 |
| **Should** | 強く推奨（時間が許せば修正） |
| **Nice** | 今回はスキップ可 |

---

## 7. CI/CD パイプライン

<!-- PROJECT: CI ワークフローとコマンドはプロジェクト固有の設定に合わせる -->

### 7.1 GitHub Actions ワークフロー

ファイル: `.github/workflows/ci.yml`

### 7.2 セキュリティ制約

- `permissions` は必要最小限を明示的に指定
- `permissions: write-all` は禁止
- サードパーティ Action はコミットハッシュで固定（タグ指定は禁止）

---

## 8. AI モデル構成

### 8.1 現在の設定

設定ファイル: `configs/ai_models.toml`

<!-- PROJECT: 現在のモデル設定を記載 -->

### 8.2 利用可能なモデル一覧（2025年7月時点）

#### 無料（0x）

| モデル | 特徴 |
|---|---|
| GPT-4.1 | OpenAI の汎用モデル |
| GPT-5 mini | 軽量版 GPT-5 |
| Raptor mini | 軽量推論モデル |

#### 低コスト（0.25x〜0.33x）

| モデル | 倍率 | 特徴 |
|---|---|---|
| Grok Code Fast 1 | 0.25x | xAI のコード特化高速モデル |
| Claude Haiku 4.5 | 0.33x | Anthropic の最軽量モデル |
| Gemini 3 Flash | 0.33x | Google の高速モデル |
| GPT-5.1-Codex-Mini | 0.33x | OpenAI のコード特化軽量モデル |

#### スタンダード（1x）

| モデル | 特徴 | 主要ベンチマーク |
|---|---|---|
| **Claude Sonnet 4.6** | **エージェントタスク最強** | SWE-bench 79.6%, TerminalBench 53% (#1) |
| Claude Sonnet 4.5 | 安定した前世代モデル | SWE-bench 70.3% |
| GPT-5.2 | 総合力トップクラス | SWE-bench 80.0%, GPQA Diamond 92.4% |

#### プレミアム（3x）

| モデル | 特徴 | 主要ベンチマーク |
|---|---|---|
| **Claude Opus 4.6** | **深い推論・最高品質** | SWE-bench ~80.8%, GPQA Diamond 91.3% |

### 8.3 モデル変更手順

1. `configs/ai_models.toml` の `[ai_models.overrides]` セクションを編集
2. `bash scripts/update_agent_models.sh` を実行
3. 変更をコミット・プッシュ

---

## 9. パイプライン状態永続化

### 9.1 概要

パイプラインが中断（コンテキスト切れ、タイムアウト、ユーザー中断等）した場合に、
途中から再開できるよう、各ステップ完了時に状態を永続化する。

### 9.2 状態ファイル

- **パス**: `outputs/pipeline_state.json`
- **ライフサイクル**: パイプライン開始時に作成、各ステップ完了時に更新、パイプライン正常完了時に削除
- **gitignore**: `outputs/` は `.gitignore` 対象のため、リポジトリにコミットされない

### 9.3 状態ファイルフォーマット

```json
{
  "step": 5,
  "step_name": "監査委譲",
  "loop_count": {
    "ci_fix": 1,
    "audit_fix": 0,
    "pr_ci_fix": 0,
    "review_fix": 0
  },
  "branch": "feat/TASK-001-implement-feature",
  "task_id": "TASK-001",
  "pr_number": null,
  "audit_results": {
    "spec": null,
    "security": null,
    "reliability": null
  },
  "serena_analysis": false,
  "timestamp": "2026-02-27T10:30:00Z",
  "version": "1.0"
}
```

### 9.4 フィールド定義

| フィールド | 型 | 説明 |
|---|---|---|
| `step` | `number` | 最後に完了したステップ番号（1〜11） |
| `step_name` | `string` | ステップ名（人間向け表示用） |
| `loop_count` | `object` | 各修正ループの現在回数 |
| `branch` | `string` | フィーチャーブランチ名 |
| `task_id` | `string` | 対象タスクの ID |
| `pr_number` | `number \| null` | 作成済み PR 番号（Step 7 以降） |
| `audit_results` | `object` | 各監査エージェントの結果サマリ |
| `serena_analysis` | `boolean` | Serena 影響分析を実施済みか |
| `timestamp` | `string` | 最終更新日時（ISO 8601） |
| `version` | `string` | 状態ファイルフォーマットのバージョン |

### 9.5 復旧フロー

```mermaid
flowchart TD
    Start([パイプライン開始]) --> Check{outputs/pipeline_state.json\nが存在する?}
    Check -->|なし| Fresh[Step 1 から新規開始]
    Check -->|あり| Read[状態ファイルを読み込む]
    Read --> Validate{ブランチが\n存在する?}
    Validate -->|なし| Discard[状態ファイルを破棄\nStep 1 から新規開始]
    Validate -->|あり| Checkout[ブランチをチェックアウト]
    Checkout --> Resume[記録された Step の\n次のステップから再開]
    Resume --> Pipeline[パイプライン続行]
    Fresh --> Pipeline
    Discard --> Pipeline
```

### 9.6 復旧時の注意事項

- **ブランチ不在**: 状態ファイルに記録されたブランチがリモートに存在しない場合、状態ファイルを破棄して新規開始する
- **ループカウント**: 復旧時は状態ファイルのループカウントを引き継ぐ。復旧前後を合算して最大3回の制限を適用する
- **監査結果**: 復旧時に監査結果が存在する場合、再監査をスキップし Step 7 以降から再開できる
- **タイムアウト**: 状態ファイルの `timestamp` が24時間以上経過している場合、古い状態として警告を表示する（破棄はしない）

---

## 10. 構成ファイル一覧

### 10.1 エージェント定義

| ファイル | 内容 |
|---|---|
| `.github/agents/orchestrator.agent.md` | Orchestrator 定義 |
| `.github/agents/implementer.agent.md` | implementer 定義 |
| `.github/agents/test-engineer.agent.md` | test-engineer 定義 |
| `.github/agents/auditor-spec.agent.md` | 仕様監査定義 |
| `.github/agents/auditor-security.agent.md` | セキュリティ監査定義 |
| `.github/agents/auditor-reliability.agent.md` | 信頼性監査定義 |
| `.github/agents/release-manager.agent.md` | リリース判定定義 |

### 10.2 設定ファイル

| ファイル | 内容 |
|---|---|
| `configs/ai_models.toml` | モデル割当の正本 |
| `scripts/update_agent_models.sh` | toml → agent ファイルへの一括反映 |
| `.github/copilot-instructions.md` | リポジトリ全体の Copilot 指示 |
| `.github/copilot-code-review-instructions.md` | Copilot Code Review 設定 |
| `.vscode/mcp.json` | MCP サーバー接続設定 |
| `.serena/project.yml` | Serena プロジェクト設定 |

### 10.3 正本ドキュメント

| ファイル | 内容 |
|---|---|
| `docs/plan.md` | 計画（ロードマップ、Next、Backlog、Done） |
| `docs/requirements.md` | 要件定義 |
| `docs/policies.md` | ポリシー一覧 |
| `docs/constraints.md` | 制約仕様 |
| `docs/architecture.md` | アーキテクチャ |
| `docs/runbook.md` | 運用手順 |
| `docs/adr/` | アーキテクチャ決定記録 |

---

## 11. 運用手順

### 11.1 新規エージェントの追加

1. `.github/agents/<name>.agent.md` を作成
2. `configs/ai_models.toml` にエントリを追加（任意）
3. Orchestrator の `agents:` リストに追加
4. `copilot-instructions.md` を更新
5. 本ドキュメントを更新

### 11.2 モデル変更

1. `configs/ai_models.toml` を編集
2. `bash scripts/update_agent_models.sh` を実行
3. 変更をコミット・プッシュ
4. 本ドキュメントの §8.1 を更新

---

## 12. 変更履歴

| 日付 | 内容 |
|---|---|
| 2026-02-27 | §4 エージェント応答スキーマ追加（要件 #11）。Step 5 並列監査の明確化（要件 #12）。§9 パイプライン状態永続化追加（要件 #13）。 |
| 2025-07-17 | テンプレート初版作成。 |
