# はじめてのセットアップガイド（macOS 向け）

このガイドは、GitHub・VS Code・Copilot を初めて使う方が、このテンプレートリポジトリを使って開発を始めるための手順書です。

> **所要時間**: 約 30〜60 分（アカウント作成含む）

---

## 目次

1. [必要なもの](#1-必要なもの)
2. [GitHub アカウントの準備](#2-github-アカウントの準備)
3. [GitHub Copilot のプラン選択と登録](#3-github-copilot-のプラン選択と登録)
4. [VS Code のインストールと初期設定](#4-vs-code-のインストールと初期設定)
5. [Git のセットアップ](#5-git-のセットアップ)
6. [リポジトリをクローンして開く](#6-リポジトリをクローンして開く)
7. [推奨拡張機能のインストール](#7-推奨拡張機能のインストール)
8. [Copilot の使い方](#8-copilot-の使い方)
9. [開発の概念ガイド（Issue・PR・Project）](#9-開発の概念ガイドissueprproject)
10. [プルリクエストの確認とマージ](#10-プルリクエストの確認とマージ)
11. [このテンプレートの仕組み](#11-このテンプレートの仕組み)
12. [トラブルシューティング](#12-トラブルシューティング)

---

## 1. 必要なもの

| 項目 | 説明 |
|------|------|
| **MacBook Pro** | macOS が動作していれば OK |
| **インターネット接続** | GitHub・VS Code のダウンロードに必要 |
| **GitHub アカウント** | 無料で作成可能 |
| **GitHub Copilot の契約** | このテンプレートの機能をフル活用するには有料プランが必要（後述） |

---

## 2. GitHub アカウントの準備

すでにアカウントがある場合はスキップしてください。

1. https://github.com にアクセス
2. **Sign up** をクリック
3. メールアドレス、パスワード、ユーザー名を入力
4. メール認証を完了

---

## 3. GitHub Copilot のプラン選択と登録

このテンプレートでは **GitHub Copilot のエージェント機能** を利用します。  
どのプランに入るかで使える機能が変わります。

### プラン比較

| 機能 | **Copilot Free** | **Copilot Pro** | **Copilot Pro+** |
|------|:-:|:-:|:-:|
| 月額料金 | 無料 | $10/月 | $39/月 |
| コード補完 | ◯（制限あり） | ◯（無制限） | ◯（無制限） |
| Copilot Chat | ◯（制限あり） | ◯（無制限） | ◯（無制限） |
| エージェントモード | ◯（制限あり） | ◯ | ◯ |
| PR レビュー（Copilot Review） | ✕ | ✕ | ◯ |
| 高性能モデル（Claude Opus 等） | ✕ | △（制限あり） | ◯ |

### おすすめ

- **まず試したい** → **Copilot Free**（無料で基本機能を体験）
- **本格的に使いたい** → **Copilot Pro**（$10/月、ほとんどの機能が使える）
- **エージェントをフル活用したい** → **Copilot Pro+**（$39/月、全機能利用可能）

> **このテンプレートを最大限活用するには Copilot Pro 以上を推奨します。**  
> エージェントによる自動実装・監査パイプラインは Pro 以上で安定して動作します。

### 登録方法

1. https://github.com/settings/copilot にアクセス
2. お好みのプランを選択
3. 支払い情報を入力して登録完了

### 参考リンク

- [GitHub Copilot 公式ページ](https://github.com/features/copilot) — 機能概要・料金
- [GitHub Copilot プラン比較](https://docs.github.com/ja/copilot/about-github-copilot/subscription-plans-for-github-copilot) — 各プランの詳細な違い
- [GitHub Copilot ドキュメント](https://docs.github.com/ja/copilot) — 公式ドキュメント（日本語）

---

## 4. VS Code のインストールと初期設定

### インストール

1. https://code.visualstudio.com にアクセス
2. **macOS 用をダウンロード**（Apple Silicon / Intel を自動判別）
3. ダウンロードした `.zip` を展開
4. `Visual Studio Code.app` を **アプリケーション** フォルダにドラッグ
5. Launchpad または Finder から VS Code を起動

### 日本語化（任意）

1. VS Code を起動
2. 左サイドバーの **拡張機能アイコン**（四角が4つ並んだアイコン）をクリック
3. 検索バーに「Japanese」と入力
4. **Japanese Language Pack for Visual Studio Code** をインストール
5. VS Code を再起動

### コマンドラインから VS Code を起動できるようにする

1. VS Code を開く
2. `Cmd + Shift + P` でコマンドパレットを開く
3. 「shell command」と入力
4. **Shell Command: Install 'code' command in PATH** を選択

これで、ターミナルから `code .` でプロジェクトを開けるようになります。

---

## 5. Git のセットアップ

macOS には Git がプリインストールされています。

### 確認

ターミナル（`Terminal.app` または VS Code のターミナル）を開いて：

```bash
git --version
```

バージョンが表示されれば OK です。  
「Xcode Command Line Tools をインストールしますか？」と聞かれたら **インストール** をクリック。

### 初期設定

```bash
# あなたの名前（GitHub のユーザー名でも OK）
git config --global user.name "あなたの名前"

# GitHub に登録したメールアドレス
git config --global user.email "your-email@example.com"
```

### GitHub CLI のインストール（推奨）

GitHub の操作をコマンドラインから行えるツールです。

```bash
# Homebrew がインストールされていない場合、先にインストール
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# GitHub CLI をインストール
brew install gh

# GitHub にログイン
gh auth login
```

`gh auth login` では以下を選択：
- **GitHub.com** を選択
- **HTTPS** を選択
- **Login with a web browser** を選択
- ブラウザが開くので、GitHub にログイン

---

## 6. リポジトリをクローンして開く

### 方法 A: ターミナルから（推奨）

```bash
# 作業用フォルダに移動（例: ホームの GitHub フォルダ）
mkdir -p ~/GitHub
cd ~/GitHub

# リポジトリをクローン
git clone https://github.com/githypn/dev-orchestration-template.git

# VS Code で開く
code dev-orchestration-template
```

### 方法 B: VS Code から

1. VS Code を起動
2. `Cmd + Shift + P` →「Git: Clone」と入力
3. リポジトリ URL を入力: `https://github.com/githypn/dev-orchestration-template.git`
4. 保存先フォルダを選択
5. 「開く」をクリック

---

## 7. 推奨拡張機能のインストール

リポジトリを VS Code で開くと、右下に **「推奨拡張機能をインストールしますか？」** という通知が表示されます。

**「すべてインストール」** をクリックすれば、必要な拡張機能が一括でインストールされます。

### 主な拡張機能の説明

| 拡張機能 | 何をするもの？ |
|----------|---------------|
| **GitHub Copilot Chat** | AI と対話しながらコードを書ける。このテンプレートの中核機能 |
| **GitHub Pull Requests** | VS Code の中でプルリクエスト（後述）を確認・マージできる |
| **GitHub Actions** | CI（自動テスト）の結果を VS Code から確認できる |
| **Python** | Python の開発に必要な基本機能 |
| **Pylance** | Python のコード補完・型チェック |
| **GitLens** | Git の履歴を見やすく表示する |
| **Jupyter** | ノートブック（実験的なコード実行）を VS Code で使える |
| **Markdown All in One** | ドキュメント（.md ファイル）の編集を便利にする |

通知が出なかった場合：
1. `Cmd + Shift + P` → 「Extensions: Show Recommended Extensions」と入力
2. 推奨拡張機能の一覧が表示されるので、個別にインストール

---

## 8. Copilot の使い方

### 基本操作

| 操作 | キー・手順 |
|------|-----------|
| **Copilot Chat を開く** | `Cmd + Shift + I`（またはサイドバーの Copilot アイコン） |
| **エージェントモードをオンにする** | Chat パネル上部のドロップダウンで「Agent」を選択 |
| **コード補完を受け入れる** | `Tab` キー |
| **コード補完を拒否する** | `Esc` キー |

### エージェントモードでの使い方

このテンプレートでは、Copilot Chat の「エージェントモード」を使って開発を自動化します。

1. `Cmd + Shift + I` で Copilot Chat を開く
2. 上部のドロップダウンで **Agent** モードを選択
3. チャット欄に指示を入力する

#### よく使う指示の例

```
計画に従い作業を実施して
```
→ `docs/plan.md` の Next タスクを自動的に実装・テスト・監査・PR 作成まで実行します。

```
計画を修正して。○○の機能を追加したい。
```
→ 新しいタスクを計画に追加し、GitHub Issue も自動作成します。

```
このコードの意味を教えて
```
→ 選択中のコードを AI が解説してくれます。

---

## 9. 開発の概念ガイド（Issue・PR・Project）

ソフトウェア開発では、作業を整理・追跡するための仕組みがあります。  
ここでは、このテンプレートで使う主要な概念を説明します。

### リポジトリ（Repository）

**コードや書類を保管する「プロジェクトフォルダ」** のようなもの。  
GitHub 上にあり、変更の履歴もすべて記録されます。

### ブランチ（Branch）

**「作業用のコピー」** のようなもの。

```
main（本番） ─────────────────────────→
              \                    /
               feat/新機能 ───────  ← 作業用ブランチ
```

- **main ブランチ**: 常に正しい状態を保つ「本番」
- **作業ブランチ**: 新しい機能を作るときに main から分岐させる。完成したら main に合流させる（マージ）

### Issue（イシュー）

**「やることリスト」の1項目**。  
「○○の機能を追加する」「△△のバグを直す」のように、作業内容を記録します。

- 番号が振られる（例: #1, #15, #25）
- 完了したらクローズ（閉じる）する
- ラベル（タグ）で分類できる（例: `enhancement`, `bug`）

### プルリクエスト（Pull Request / PR）

**「この変更を本番に入れてもいいですか？」** という提案のこと。

1. 作業ブランチでコードを変更する
2. PR を作成して「こんな変更をしました」と説明を書く
3. 他の人（や AI）がレビューする
4. 問題なければ **マージ**（main に合流）する

PR を通すことで、おかしな変更が本番に混入するのを防ぎます。

### CI（継続的インテグレーション）

**コードを変更するたびに自動でテストを実行する仕組み**。

PR を出すと、GitHub Actions が自動で以下をチェックします：
- コードの書き方ルール（lint）
- 型チェック（mypy）
- テストの実行（pytest）
- ポリシーチェック（禁止操作がないか）

全部パスしないとマージできません。

### GitHub Projects

**「プロジェクトの進捗管理ボード」**。  
Issue や PR をカンバンボード（Todo → In Progress → Done）で管理します。

```
Todo          In Progress     Done
┌───────┐    ┌───────┐    ┌───────┐
│ #15   │    │ #20   │    │ #10   │
│ #16   │    │       │    │ #11   │
│ #17   │    │       │    │ #12   │
└───────┘    └───────┘    └───────┘
```

### このテンプレートでの使われ方

```
docs/plan.md   ←→   GitHub Issues   ←→   GitHub Projects
（計画書）          （個別タスク）        （進捗ボード）
```

1. `docs/plan.md` に計画を書く（Next = 今やるタスク、Backlog = 将来やるタスク）
2. 各タスクに対応する GitHub Issue を作成する
3. GitHub Projects で進捗を可視化する
4. タスク完了 → PR マージ → Issue が自動でクローズ → Projects が自動で Done に移動

---

## 10. プルリクエストの確認とマージ

PR の確認とマージは **VS Code 上で** 行えます（推奨拡張機能をインストール済みの場合）。

### VS Code で PR を確認・マージする方法

**GitHub Pull Requests** 拡張機能を使います。

1. **左サイドバー** に GitHub のアイコン（猫のアイコン付き）が表示される
2. クリックすると、現在のリポジトリの PR 一覧が見える
3. PR をクリックすると：
   - **変更されたファイルの差分**（ビフォー・アフター）が見える
   - **レビューコメント** が見える
   - **CI の結果** が見える
4. 問題なければ、PR の詳細画面で **「Merge Pull Request」** ボタンをクリック

#### 手順の詳細

```
① サイドバーの GitHub アイコンをクリック
② 「Pull Requests」セクションを展開
③ 対象の PR をクリック
④ 「Description」タブで PR の説明を確認
⑤ 「Files Changed」で変更内容を確認
⑥ 「Checks」で CI の結果を確認（全部 ✓ なら OK）
⑦ 問題なければ「Merge Pull Request」をクリック
⑧ マージ方法を選択（通常は「Create a merge commit」で OK）
⑨ 「Confirm Merge」をクリック
```

### GitHub Web で PR を確認・マージする方法（代替手段）

VS Code が使えない場面では、ブラウザからも操作できます。

1. https://github.com にアクセスしてログイン
2. 対象のリポジトリページを開く
3. **「Pull requests」** タブをクリック
4. 対象の PR をクリック
5. **「Files changed」** タブで変更内容を確認
6. **「Checks」** で CI の結果を確認
7. ページ下部の **「Merge pull request」** → **「Confirm merge」** をクリック

---

## 11. このテンプレートの仕組み

### フォルダ構成

```
.
├── docs/               ← 📝 計画・設計書（ここが「正本」）
│   ├── plan.md         ← ロードマップ・タスク管理
│   ├── requirements.md ← 要件定義
│   ├── policies.md     ← やってはいけないルール
│   ├── constraints.md  ← 制約条件（しきい値など）
│   ├── architecture.md ← 設計図
│   └── runbook.md      ← 実行手順
│
├── .github/agents/     ← 🤖 AI エージェントの設定
│   ├── orchestrator    ← 司令塔（他のエージェントに指示を出す）
│   ├── implementer     ← 実装担当
│   ├── test-engineer   ← テスト担当
│   ├── auditor-*       ← 監査担当（3種類）
│   └── release-manager ← リリース判定担当
│
├── .github/workflows/  ← ⚙️ CI（自動テスト）の設定
├── scripts/            ← 🔧 セットアップスクリプト
├── configs/            ← ⚙️ 設定ファイル
└── project-config.yml  ← 📋 プロジェクトの基本設定
```

### AI エージェントの仕組み

```
あなた（ユーザー）
  │  「計画に従い作業を実施して」
  ▼
🤖 Orchestrator（司令塔）
  │  タスクを分解して各担当に指示
  │
  ├──→ 🛠️ Implementer  → コードを書く
  ├──→ 🧪 Test Engineer → テストを書く
  ├──→ 📋 Auditor Spec  → 仕様に合ってるか確認
  ├──→ 🔒 Auditor Security → セキュリティの確認
  ├──→ ⚡ Auditor Reliability → 品質の確認
  │
  └──→ ✅ Release Manager → マージしていいか判定
```

「計画に従い作業を実施して」と一言伝えるだけで、AI が自動的に：
1. 計画書を読んで次のタスクを把握
2. コードを実装
3. テストを作成
4. 3 方向から監査
5. PR（変更提案）を作成
6. あなたの承認を待ってマージ

---

## 12. トラブルシューティング

### 「Copilot が反応しない」

- Copilot のプランが有効か確認: https://github.com/settings/copilot
- VS Code で GitHub にサインインしているか確認:
  - 左下のアカウントアイコン → 「Sign in to GitHub」

### 「推奨拡張機能の通知が出ない」

- `Cmd + Shift + P` → 「Extensions: Show Recommended Extensions」

### 「git push でエラーが出る」

- `gh auth login` で GitHub にログインし直す
- HTTPS で認証されているか確認: `gh auth status`

### 「Python が見つからない」

```bash
# Homebrew で Python をインストール
brew install python@3.11

# 確認
python3 --version
```

### その他

- [VS Code 公式ドキュメント（日本語）](https://code.visualstudio.com/docs)
- [GitHub 公式ドキュメント（日本語）](https://docs.github.com/ja)
- [GitHub Copilot ドキュメント（日本語）](https://docs.github.com/ja/copilot)

---

> **困ったら**: Copilot Chat（`Cmd + Shift + I`）で「○○のやり方を教えて」と聞いてみてください。AI が日本語で丁寧に教えてくれます。
