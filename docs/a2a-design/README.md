# A2A プロトコル設計ガイド

> 本ドキュメントは、A2A（Agent2Agent）プロトコルに基づくエージェント間通信の設計ガイドである。
> Agent Card の各フィールドの説明、スキル設計の指針、MCP との使い分けを記載する。

---

## 目次

1. [A2A プロトコルの概要](#1-a2a-プロトコルの概要)
2. [Agent Card の各フィールド説明](#2-agent-card-の各フィールド説明)
3. [skills フィールドの設計指針](#3-skills-フィールドの設計指針)
4. [MCP vs A2A の使い分けガイド](#4-mcp-vs-a2a-の使い分けガイド)
5. [本ディレクトリのファイル構成](#5-本ディレクトリのファイル構成)
6. [参考情報](#6-参考情報)

---

## 1. A2A プロトコルの概要

### A2A（Agent2Agent Protocol）とは

Google が主導し 2025 年 4 月に発表、同年 6 月に Linux Foundation 債下でオープンソース化されたエージェント間通信プロトコル。

> **バージョン情報**: 最新の安定版・リリース日は [A2A 公式リポジトリ](https://github.com/google/A2A) で確認すること。

通信基盤に **HTTP、SSE（Server-Sent Events）、JSON-RPC、gRPC** を使用する。

> **注意**: IBM BeeAI の ACP（Agent Communication Protocol）は A2A へ統合済みであり、A2A と ACP は現在は同一プロトコルを指す。「A2A または ACP」という記述は誤りであるため使用しないこと。

### Agent Card とは

A2A プロトコルにおいて各エージェントが `/.well-known/agent.json` で公開する JSON 形式のメタデータ。エージェントの名前・エンドポイント URL・スキル・認証方式等を宣言する。クライアントエージェントは Agent Card を参照することでリモートエージェントの能力を動的に発見できる。

---

## 2. Agent Card の各フィールド説明

汎用テンプレートは [agent-card-template.json](agent-card-template.json) を参照のこと。

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `name` | string | ✅ | エージェントの識別名。ケバブケースを推奨（例: `code-implementer`） |
| `description` | string | ✅ | エージェントの責務の説明。1〜3 文で簡潔に記述する |
| `url` | string | ✅ | エンドポイント URL。Agent Card の公開先（`/.well-known/agent.json`）の基底 URL |
| `version` | string | ✅ | セマンティックバージョニング（例: `1.0.0`） |
| `capabilities` | object | ✅ | エージェントが対応する通信機能 |
| `capabilities.streaming` | boolean | ✅ | SSE / gRPC ストリーミング対応の有無 |
| `capabilities.pushNotifications` | boolean | ✅ | プッシュ通知対応の有無 |
| `authentication` | object | ✅ | 認証方式の定義 |
| `authentication.schemes` | string[] | ✅ | 認証スキームのリスト（`bearer` / `apiKey` / `none`） |
| `defaultInputModes` | string[] | − | デフォルトの入力形式（例: `["text"]`, `["text", "image"]`） |
| `defaultOutputModes` | string[] | − | デフォルトの出力形式 |
| `provider` | object | − | エージェントの提供元情報 |
| `provider.organization` | string | − | 組織名またはプロジェクト名 |
| `skills` | array | ✅ | エージェントが提供するスキルのリスト（後述） |

### skills 内の各フィールド

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string | ✅ | スキルの一意識別子。ケバブケースで記述（例: `generate-code`） |
| `name` | string | ✅ | スキルの表示名 |
| `description` | string | ✅ | このスキルで実行できる具体的なタスクの説明 |
| `inputModes` | string[] | − | 入力形式（省略時は `defaultInputModes` を使用） |
| `outputModes` | string[] | − | 出力形式（省略時は `defaultOutputModes` を使用） |

---

## 3. skills フィールドの設計指針

### 原則: 1 スキル ＝ 1 つの明確なタスク

スキルは「このエージェントに何を依頼できるか」をクライアントに伝えるインターフェースである。以下の指針に従って設計すること。

**良い設計例:**

```json
{
  "id": "generate-code",
  "name": "コード生成",
  "description": "指定されたモジュールの実装コードを生成する。受入条件と参照正本を入力として受け取る。"
}
```

**避けるべき設計例:**

```json
{
  "id": "do-everything",
  "name": "全般処理",
  "description": "何でもやります"
}
```

### 設計チェックリスト

- [ ] 各スキルの `id` がケバブケースで一意であること
- [ ] 各スキルの `description` が具体的なタスクを説明していること（「何を入力し、何を出力するか」が読み取れること）
- [ ] 1 つのスキルに複数の異質な責務を詰め込んでいないこと
- [ ] `inputModes` / `outputModes` が実際の入出力に合致していること

### スキル粒度の判断基準

| 判断基準 | 分割する | 統合してよい |
|---|---|---|
| 入出力の形式が異なる | ✅ | − |
| 独立して呼び出される | ✅ | − |
| 常にセットで呼び出される | − | ✅ |
| 異なる権限が必要 | ✅ | − |

---

## 4. MCP vs A2A の使い分けガイド

MCP（Model Context Protocol）と A2A（Agent2Agent Protocol）は用途が異なる補完的なプロトコルである。

### 比較表

| 観点 | MCP | A2A |
|---|---|---|
| **用途** | AI モデルが外部ツール・データソースにアクセスする | 異なるフレームワーク・ベンダー間でエージェントが協調する |
| **通信の方向** | クライアント（AI モデル）→ サーバー（ツール提供者） | エージェント ↔ エージェント（双方向） |
| **公開元** | Anthropic（2024 年 11 月） | Google → Linux Foundation（2025 年 4〜6 月） |
| **通信基盤** | JSON-RPC | HTTP / SSE / JSON-RPC / gRPC |
| **メタデータ** | ツール定義（Tool / Resource） | Agent Card（`/.well-known/agent.json`） |
| **発見方式** | MCP サーバーへの接続時に取得 | Agent Card エンドポイントへの HTTP GET |

### MCP を使うケース

- AI モデルがデータベース、ファイルシステム、外部 API 等のリソースにアクセスする場合
- ツール呼び出し（関数実行）のインターフェースを標準化したい場合
- 例: Serena MCP によるコードのセマンティック分析、GitHub MCP によるリポジトリ操作

### A2A を使うケース

- 異なるフレームワーク（LangGraph、CrewAI、AutoGen 等）で構築されたエージェント同士が連携する場合
- エージェントを外部チームや外部システムに公開する場合
- エージェントの能力を動的に発見・選択する仕組みが必要な場合
- 例: 社内の分析エージェントと外部パートナーの実行エージェントの連携

### 併用パターン

典型的なシステムでは、MCP と A2A を併用する:

```
エージェント A ──[A2A]──→ エージェント B
    │                          │
    │[MCP]                     │[MCP]
    ▼                          ▼
  DB / ファイル            外部 API / ツール
```

- エージェント間の通信・タスク委譲には **A2A** を使用する
- 各エージェントが自身のツール・データソースにアクセスする際には **MCP** を使用する

---

## 5. 本ディレクトリのファイル構成

```
docs/a2a-design/
├── README.md                          # 本ファイル（設計ガイド）
├── agent-card-template.json           # 汎用 Agent Card テンプレート
├── migration-roadmap-template.md      # 汎用移行ロードマップテンプレート
├── migration-roadmap.md               # プロジェクト固有の移行ロードマップ（参考）
├── implementer.agent.json             # 各エージェントの Agent Card 定義
├── orchestrator.agent.json
├── test-engineer.agent.json
├── auditor-spec.agent.json
├── auditor-security.agent.json
├── auditor-reliability.agent.json
└── release-manager.agent.json
```

---

## 6. 参考情報

- **A2A 公式リポジトリ**: https://github.com/google/A2A
- **A2A v0.3 仕様**（最新安定版、2025 年 7 月 31 日リリース）
- **MCP 公式サイト**: https://modelcontextprotocol.io/
- **OWASP ASI07 - Insecure Inter-Agent Communication**: エージェント間通信のセキュリティリスクと対策
