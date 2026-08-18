# はじめてのセットアップガイド

このガイドは、このテンプレートリポジトリを使って開発を始めるための手順書です。GitHub・Git の基本操作ができれば、使用する AI アシスタント（Claude Code / GitHub Copilot / Codex・Cursor 等）は問いません。

> **所要時間**: 約 20〜30 分（アカウント作成含む）

---

## 目次

1. [必要なもの](#1-必要なもの)
2. [GitHub アカウントの準備](#2-github-アカウントの準備)
3. [使用する AI アシスタントの選択](#3-使用する-ai-アシスタントの選択)
4. [ローカル環境の準備](#4-ローカル環境の準備)
5. [リポジトリを作成する](#5-リポジトリを作成する)
6. [ブートストラップを実行する](#6-ブートストラップを実行する)
7. [docs を編集する](#7-docs-を編集する)
8. [開発を始める](#8-開発を始める)
9. [開発の概念（Issue・PR・Project）](#9-開発の概念issueprproject)
10. [プルリクエストの確認とマージ](#10-プルリクエストの確認とマージ)
11. [このテンプレートの仕組み](#11-このテンプレートの仕組み)
12. [トラブルシューティング](#12-トラブルシューティング)

---

## 1. 必要なもの

| 項目 | 説明 |
|------|------|
| **PC**（macOS / Linux / Windows） | ターミナルと Git が使える環境 |
| **インターネット接続** | GitHub・依存パッケージの取得に必要 |
| **GitHub アカウント** | 無料で作成可能 |
| **AI アシスタントの契約**（いずれか1つ以上） | Claude Code / GitHub Copilot / Codex・Cursor 等。詳細は次節 |

---

## 2. GitHub アカウントの準備

すでにアカウントがある場合はスキップしてください。

1. <https://github.com> にアクセス
2. **Sign up** をクリック
3. メールアドレス、パスワード、ユーザー名を入力
4. メール認証を完了

---

## 3. 使用する AI アシスタントの選択

このテンプレートは特定の AI アシスタントに依存しない。以下のいずれか（または複数）を用意する。

| ハーネス | 主な利用形態 | 参考 |
|---------|-------------|------|
| **Claude Code** | ターミナル上の CLI エージェント。`.claude/` の設定・hooks を利用 | <https://docs.claude.com/ja/docs/claude-code> |
| **GitHub Copilot**（VS Code） | Copilot Chat の Agent モード。`.github/agents/`・`.github/copilot-instructions.md` を参照 | <https://docs.github.com/ja/copilot> |
| **Codex / Cursor** | `.cursor/` の設定・rules を参照するエージェント | 各製品の公式ドキュメント |

どのハーネスでも `ai/*.yml`（AI Operating Model）を正本として同じ運用ルールに従う。複数を併用してもよい（例: 実装は Claude Code、セカンドオピニオンは Codex）。

VS Code を使う場合は [公式サイト](https://code.visualstudio.com) からインストールし、リポジトリを開くと `.vscode/extensions.json` の推奨拡張機能が提案される。

---

## 4. ローカル環境の準備

### Git の確認

```bash
git --version
```

未インストールの場合は OS 標準の手順（macOS: Xcode Command Line Tools、Linux: パッケージマネージャ、Windows: [Git for Windows](https://gitforwindows.org)）でインストールする。

### 初期設定

```bash
git config --global user.name "あなたの名前"
git config --global user.email "your-email@example.com"
```

### GitHub CLI（推奨）

GitHub の操作をコマンドラインから行える。Issue/PR/Project の自動作成スクリプトが利用する。

```bash
# インストール方法は OS ごとに異なる。詳細は https://cli.github.com
gh auth login
```

`gh auth login` では **GitHub.com** → **HTTPS** → **Login with a web browser** を選択する。

### 言語ランタイム（Python の場合）

このテンプレートは既定で Python + [uv](https://docs.astral.sh/uv/) を前提にしている（`toolchain.language` で変更可能）。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

---

## 5. リポジトリを作成する

GitHub の **Use this template** ボタン、または GitHub CLI でテンプレートから自分のリポジトリを作成する。

```bash
gh repo create my-project --template KosGit-Dev/dev-orchestration-template --clone
cd my-project
```

作成されたリポジトリは元のテンプレートと独立しているため、自由に変更できる。接続先の確認：

```bash
git remote -v
# origin  https://github.com/あなたのユーザー名/my-project.git (fetch/push)
```

---

## 6. ブートストラップを実行する

### 6.1 project-config.yml を編集する

```bash
code project-config.yml   # または任意のエディタ
```

主な設定項目は `project`（名前・目的・説明）、`toolchain`（言語・ツール）、`source`（ディレクトリ構成）、`roadmap`、`policies`、`github`、`ai_models` である。

### 6.2 bootstrap.sh を実行する

```bash
bash scripts/bootstrap.sh
```

ディレクトリ構造の作成、`pyproject.toml` 等の生成、`{{PLACEHOLDER}}` の置換、CI ワークフローの言語別セットアップを自動で行う。

### 6.3 GitHub Labels / Milestones / Issues / Project の作成（任意）

```bash
bash scripts/setup_github.sh
```

---

## 7. docs を編集する

`docs/` 配下は正本（SSOT）である。プロジェクトに合わせて編集する。

```text
docs/
├── plan.md           # ロードマップ、Next タスク、Backlog
├── requirements.md   # 要件定義・受入条件
├── policies.md       # ポリシー（禁止事項等）
├── constraints.md    # 制約仕様（しきい値等）
├── architecture.md   # アーキテクチャ・責務境界
├── runbook.md        # 実行・復旧手順
└── adr/              # 重要判断の記録
```

各ファイル末尾の「テンプレート適用時のチェックリスト」に従うとよい。

---

## 8. 開発を始める

使用する AI アシスタントを開き、日常コマンドを伝える。

```text
プランの全実施。
```

`docs/plan.md` の Next タスクを Orchestrator が自動的に分解・実行し、実装 → テスト → 監査 → PR 作成まで進める。

日常コマンドは3種類（プラン見直し／バックログ昇格／プラン全実施）あり、それ以外の依頼や運用ルール変更の扱いは [docs/MODE_GUIDE.md](MODE_GUIDE.md) を参照する。

> **あなたがやること**: PR の確認とマージ承認。AI エージェントが自律的にファイル変更・コマンド実行を行う場合でも、マージは必ず人間が判断する。

---

## 9. 開発の概念（Issue・PR・Project）

### リポジトリ / ブランチ

コードや文書を保管する場所が **リポジトリ**。`main` ブランチが常に正しい状態を保つ本流であり、作業は `main` から分岐したフィーチャーブランチで行い、完了したら PR で `main` へ合流させる（個人開発向けに `main` 一本化運用とし、長命の派生ブランチは作らない）。

### Issue

「やることリスト」の1項目。番号が振られ、完了したらクローズする。

### プルリクエスト（PR）

「この変更を `main` に入れてもいいですか」という提案。差分・レビュー・CI 結果を確認し、問題なければマージする。

### CI（継続的インテグレーション）

PR を出すと GitHub Actions が自動でポリシーチェック・lint・型チェック・テスト・シークレットスキャンを実行する。全部パスしないとマージできない。

### GitHub Projects

Issue や PR をカンバンボード（Todo → In Progress → Done）で管理する進捗ボード。

```text
docs/plan.md   ←→   GitHub Issues   ←→   GitHub Projects
（計画書）          （個別タスク）        （進捗ボード）
```

---

## 10. プルリクエストの確認とマージ

### VS Code から

**GitHub Pull Requests** 拡張機能を使うと、サイドバーから PR の差分・レビュー・CI 結果を確認し、そのままマージできる。

### GitHub Web から

1. リポジトリページの **Pull requests** タブを開く
2. 対象の PR をクリックし、**Files changed** で差分、**Checks** で CI 結果を確認する
3. 問題なければ **Merge pull request** → **Confirm merge**

---

## 11. このテンプレートの仕組み

### フォルダ構成（抜粋）

```text
.
├── ai/                 # AI Operating Model 正本（コマンド解釈・運用方針 等）
├── docs/               # 正本ドキュメント（plan / requirements / policies / constraints / architecture / runbook）
├── .github/agents/     # AI エージェントの共通定義（roster）
├── .claude/            # Claude Code ハーネス（agents / hooks 等）
├── .github/workflows/  # CI（自動テスト）の設定
├── scripts/            # bootstrap・hooks・レビュー支援スクリプト
└── project-config.yml  # プロジェクトの基本設定
```

### AI エージェントの仕組み

```text
あなた（ユーザー）
  │  「プランの全実施。」
  ▼
Orchestrator（司令塔）
  │  タスクを分解して各担当に指示
  │
  ├──→ Implementer  → コードを書く
  ├──→ Test Engineer → テストを書く
  ├──→ Auditor Spec / Security / Reliability → 監査する
  │
  └──→ Release Manager → マージしていいか判定
```

詳細は [README.md](../README.md) と [docs/orchestration.md](orchestration.md) を参照する。

---

## 12. トラブルシューティング

### 「AI アシスタントが反応しない」

- 使用しているハーネスの認証状態を確認する（Claude Code: `claude auth status` 相当、Copilot: VS Code 左下のサインイン状態、Codex/Cursor: 各製品の設定画面）

### 「git push でエラーが出る」

- `gh auth login` で GitHub にログインし直す
- HTTPS で認証されているか確認: `gh auth status`

### 「Python / uv が見つからない」

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.11
```

### 「CI が失敗する」

- ローカルで同じコマンドを実行して再現する（`docs/runbook.md` の「代表コマンド」を参照）
- AI アシスタントに「CI が失敗しているので直して」と伝えると、多くの場合そのまま調査・修正してくれる

### その他

- [GitHub 公式ドキュメント（日本語）](https://docs.github.com/ja)
- [uv 公式ドキュメント](https://docs.astral.sh/uv/)

---

> **最後に**: 迷ったら、使用中の AI アシスタントに日本語でそのまま質問してください。「○○のやり方を教えて」だけで、たいていのことは解決します。
