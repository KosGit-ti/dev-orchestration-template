# テンプレートアップデートガイド

> **対象読者**: `dev-orchestration-template` を GitHub の「Use this template」ボタンでコピーし、すでに自分のプロジェクトで開発を進めている方。
>
> **前提**: GitHub Copilot（VS Code 拡張）を使用していること。Git の知識は不要です。Copilot に指示を出すだけでほとんどの操作を完了できます。

---

## 目次

1. [このガイドについて](#1-このガイドについて)
2. [アップデート前の準備](#2-アップデート前の準備)
3. [アップデート方法（推奨: スマートアップデート方式）](#3-アップデート方法推奨-スマートアップデート方式)
4. [マニフェストの仕組み](#4-マニフェストの仕組み)
5. [アップデート後の検証](#5-アップデート後の検証)
6. [変更内容の概要](#6-変更内容の概要)
7. [従来方式（Git マージ方式）](#7-従来方式git-マージ方式)
8. [手動アップデート方式（Copilot なしの場合）](#8-手動アップデート方式copilot-なしの場合)
9. [トラブルシューティング](#9-トラブルシューティング)
10. [よくある質問（FAQ）](#10-よくある質問faq)
11. [全体の流れ（チートシート）](#全体の流れチートシート)

---

## 1. このガイドについて

`dev-orchestration-template` は初回リリース以降、大幅な改善を行いました。主な変更点は以下のとおりです。

- **処理フロー全体の刷新**: 自動実行パイプライン、計画修正モード、汎用リクエストモードの追加
- **エージェント定義の強化**: 全7エージェントの指示内容を大幅に拡充
- **CI/CD パイプラインの充実**: セキュリティスキャン、Issue ライフサイクル自動化、ステージング/本番ワークフロー追加
- **開発環境の整備**: Dev Container 対応、VS Code 推奨設定、Serena MCP 統合
- **ドキュメント拡充**: GETTING_STARTED.md、オーケストレーション設計書、品質ガイド等を追加
- **コード品質テンプレート**: サンプルコード、テスト、可観測性（tracing）の雛形を追加
- **スマートアップデート機構**: マニフェスト + スクリプトによるプロジェクト安全なアップデート方式を追加

このガイドでは、すでに開発を進めている **あなたのリポジトリ** にこれらの改善を取り込む方法を、ステップごとに解説します。

### 重要な注意点

「Use this template」でコピーしたリポジトリは、テンプレートとの Git 履歴のつながりがありません。従来は `git merge --allow-unrelated-histories` で取り込んでいましたが、この方式では **プロジェクト固有のファイルが上書きされるリスク** がありました。

**v2 以降は、`.template-update.yml` マニフェストと `scripts/template_update.py` による「スマートアップデート方式」を推奨します。** この方式では、ファイルごとに「上書き」「スキップ」「新規のみ」を自動判定し、安全に更新を適用できます。

### このガイドの読み方

各セクションでは **Copilot に伝える指示**（📎 マーク）と、参考用の **対応するコマンド** の両方を記載しています。基本的には Copilot への指示だけで進められます。

---

## 2. アップデート前の準備

### 2.1 現在の変更を保存する

**必ずすべての作業をコミットしてから** アップデートを始めてください。

VS Code の **ソース管理**（左サイドバーの分岐アイコン）を開き、未保存の変更がないか確認します。変更が残っている場合は、メッセージ欄に「アップデート前の作業を保存」と入力して **コミット** ボタンを押してください。

> **📎 Copilot に伝える場合**: Copilot Chat で以下のように聞くこともできます。
>
> ```text
> 未コミットの変更があるか確認して、あればコミットして
> ```

### 2.2 デフォルトブランチ名を確認する

リポジトリのデフォルトブランチは `main` または `master` の場合があります。以降の手順で使うので、最初に確認しておきましょう。

> **📎 Copilot への指示**:
>
> ```text
> このリポジトリのデフォルトブランチ名（main / master など）を確認して教えて
> ```

> **💡 以降の手順について**: このガイドでは「デフォルトブランチ」と書かれている箇所を、あなたのリポジトリのデフォルトブランチ名（`main` や `master`）に置き換えて読んでください。Copilot に指示する場合はそのまま伝えれば自動的に判断してくれます。

### 2.3 リモートリポジトリにもプッシュしておく（推奨）

VS Code のソース管理で **同期** ボタンを押すか、Copilot に「デフォルトブランチをプッシュして」と伝えてください。

---

## 3. アップデート方法（推奨: スマートアップデート方式）

> **この方式が推奨される理由**:
>
> - **プロジェクト固有ファイルを保護**: `plan.md`、`requirements.md`、`architecture.md` 等は自動的にスキップ
> - **サンプルファイルを注入しない**: `src/sample/`、`src/my_package/` 等のテンプレート専用ファイルを除外
> - **プロジェクト固有 instructions を保護**: `.github/instructions/` 配下のカスタマイズを維持
> - **dry-run で事前確認**: 実際に変更する前に何が起きるか確認可能
> - **バックアップ自動作成**: 万が一の復元も簡単

### ステップ 1: テンプレートの最新マニフェストを取得

まず、テンプレートの最新版から `.template-update.yml`（マニフェスト）と `scripts/template_update.py`（アップデートスクリプト）を取得します。

> **📎 Copilot への指示**:
>
> ```text
> https://github.com/KosGit-ti/dev-orchestration-template.git を
> 一時ディレクトリにクローンして、以下の2ファイルだけ自分のリポジトリにコピーして:
> - .template-update.yml
> - scripts/template_update.py
> コピーしたらコミットして。
> ```

<details>
<summary>📝 対応するコマンド（参考）</summary>

```bash
# 一時ディレクトリにクローン
git clone --depth=1 https://github.com/KosGit-ti/dev-orchestration-template.git /tmp/template-latest

# マニフェストとスクリプトをコピー
cp /tmp/template-latest/.template-update.yml .
cp /tmp/template-latest/scripts/template_update.py scripts/

# コミット
git add .template-update.yml scripts/template_update.py
git commit -m "chore: テンプレートアップデート用マニフェストとスクリプトを追加"

# 一時ディレクトリを削除
rm -rf /tmp/template-latest
```

</details>

> **💡 2回目以降**: マニフェストとスクリプトがすでにリポジトリにある場合、このステップはスキップできます（スクリプトが自動的にテンプレートの最新版を取得します）。

### ステップ 2: dry-run で変更内容を確認

実際の変更を行う前に、何が起きるか確認します。

> **📎 Copilot への指示**:
>
> ```text
> scripts/template_update.py を --dry-run オプション付きで実行して、
> 結果を教えて
> ```

<details>
<summary>📝 対応するコマンド（参考）</summary>

```bash
python scripts/template_update.py --dry-run
```

</details>

**出力例**:

```text
============================================================
テンプレートアップデート — 操作計画レポート
============================================================

✅ 更新対象 (always_update): 25 ファイル
   📝 .github/copilot-instructions.md [上書き]
   📝 .github/agents/implementer.agent.md [上書き]
   ➕ .github/workflows/staging.yml [新規追加]
   ...

➕ 新規追加 (add_only): 3 ファイル
   ➕ .vscode/extensions.json
   ...

🔒 スキップ — プロジェクト固有 (never_update): 12 ファイル
   🔒 docs/plan.md
   🔒 docs/requirements.md
   🔒 README.md
   ...

📦 スキップ — サンプル (sample_only): 8 ファイル
   📦 scripts/run_pipeline.py
   📦 src/sample/example_module.py
   ...

──────────────────────────────────────────────────────────────
合計: 変更 28 / スキップ 20 / 未分類 0
──────────────────────────────────────────────────────────────

📎 これは dry-run です。実際の変更は行われていません。
```

**確認すべきポイント**:

| 確認項目 | 問題がある場合 |
|---|---|
| `🔒 スキップ` にプロジェクト固有ファイルが含まれているか | 含まれていなければマニフェストを修正 |
| `✅ 更新対象` に上書きされたくないファイルがないか | あればマニフェストの `never_update` に追加 |
| `⚠️ 未分類` のファイルがないか | あればマニフェストにカテゴリを追加 |

### ステップ 3: マニフェストをカスタマイズ（必要な場合のみ）

プロジェクト固有の `.github/instructions/` ファイルや独自スクリプトがある場合、`.template-update.yml` に追記します。

> **📎 Copilot への指示**:
>
> ```text
> .template-update.yml を開いて、以下のファイルを never_update カテゴリに追加して:
> - .github/instructions/backtest.instructions.md
> - .github/instructions/monitoring.instructions.md
> （自分のプロジェクト固有の instructions ファイル名に置き換えてください）
> ```

> **💡 ポイント**: `.github/instructions/` ディレクトリ全体がすでに `never_update` に含まれている場合、個別ファイルの追加は不要です。

### ステップ 4: アップデートを実行

dry-run の結果に問題がなければ、実行します。

> **📎 Copilot への指示**:
>
> ```text
> scripts/template_update.py を実行して（--dry-run なしで）。
> 結果を確認して、問題がなければコミットして。
> ```

<details>
<summary>📝 対応するコマンド（参考）</summary>

```bash
python scripts/template_update.py
```

</details>

スクリプトは以下の処理を自動的に行います:

1. **バックアップブランチの作成**: `backup-before-template-update-YYYYMMDD-HHMMSS`
2. **テンプレートのクローン**: 最新版を一時ディレクトリに取得
3. **ファイルの選択的適用**: マニフェストに従い、カテゴリごとに処理
4. **ポストチェック**: ruff check / ruff format / policy_check を実行

### ステップ 5: 変更を確認してコミット・プッシュ

> **📎 Copilot への指示**:
>
> ```text
> アップデートで変更されたファイルの一覧を教えて。
> 問題なければ「chore: テンプレートアップデート適用」でコミットしてプッシュして。
> ```

### ステップ 6: ポストチェックのエラーを修正（必要な場合のみ）

スクリプト実行時にポストチェックで失敗が報告された場合:

> **📎 Copilot への指示**:
>
> ```text
> テンプレートアップデート後にリンター/フォーマッターのエラーが出ています。
> エラーを確認して修正して。
> ```

よくあるエラー:

| エラー | 原因 | 対処 |
|---|---|---|
| `ruff check` の失敗 | テンプレートのコードスタイルとプロジェクトの設定差異 | Copilot に修正を依頼 |
| `ruff format` の差異 | フォーマット設定の違い | `uv run ruff format .` で自動修正 |
| `policy_check` の警告 | 新しいポリシールールの追加 | 警告内容に応じて修正 |

---

## 4. マニフェストの仕組み

### `.template-update.yml` の構造

```yaml
version: 1

categories:
  # テンプレートインフラ: 常に最新版で上書きする
  always_update:
    - .github/copilot-instructions.md
    - .github/agents/            # ディレクトリ末尾の / で配下全体を指定
    - ci/policy_check.py
    # ...

  # プロジェクト固有コンテンツ: 絶対に上書きしない
  never_update:
    - docs/plan.md
    - docs/requirements.md
    - docs/architecture.md
    - README.md
    - project-config.yml
    - src/                       # プロジェクトのソースコード全体
    - tests/                     # プロジェクトのテスト全体
    - .github/instructions/      # プロジェクト固有の instructions
    # ...

  # 新規追加のみ: 既存ファイルは上書きしない
  add_only:
    - .vscode/
    - configs/
    # ...

  # テンプレート専用サンプル: 子プロジェクトには適用しない
  sample_only:
    - scripts/run_pipeline.py
    - src/sample/
    - src/my_package/
    # ...
```

### 4つのカテゴリの意味

| カテゴリ | 動作 | 例 |
|---|---|---|
| `always_update` | テンプレートの最新版で **常に上書き** | エージェント定義、CI ワークフロー、ポリシーチェック |
| `never_update` | **絶対にスキップ**（プロジェクト固有） | plan.md、requirements.md、src/、README.md |
| `add_only` | **新規ファイルのみ追加**、既存は上書きしない | .vscode/ 設定、configs/ テンプレート |
| `sample_only` | **完全にスキップ**（テンプレート内部のサンプル） | run_pipeline.py、src/sample/、src/my_package/ |

### カテゴリの優先度

同じファイルが複数のカテゴリにマッチする場合（例: ディレクトリパターンと個別パス）、以下の優先度で判定します:

```
sample_only（最高）> never_update > always_update > add_only（最低）
```

### マニフェストのカスタマイズ

プロジェクト固有のファイルを追加した場合は、`.template-update.yml` に追記してください。

> **📎 Copilot への指示**:
>
> ```text
> .template-update.yml を確認して、自分のプロジェクト固有のファイル（instructions、
> 独自スクリプト等）が never_update に含まれているか確認して。
> 不足があれば追加して。
> ```

---

## 5. アップデート後の検証

### 5.1 プロジェクトの動作確認

> **📎 Copilot への指示**:
>
> ```text
> プロジェクトの CI チェック（リンター、フォーマッター、型チェック、テスト）を
> すべて実行して、結果を教えて
> ```

Copilot が `project-config.yml` の `toolchain` セクションを参照して適切なコマンドを実行してくれます。

<details>
<summary>📝 手動で実行する場合（Python プロジェクト）</summary>

```bash
uv run ruff check .         # リンター
uv run ruff format --check . # フォーマッター
uv run mypy src/             # 型チェック
uv run pytest -q --tb=short  # テスト
```

</details>

### 5.2 CI ポリシーチェック

> **📎 Copilot への指示**:
>
> ```text
> ci/policy_check.py を実行して、ポリシー違反がないか確認して
> ```

### 5.3 プロジェクト固有ファイルの確認

アップデート後に、プロジェクト固有のファイルが意図せず変更されていないか確認します。

> **📎 Copilot への指示**:
>
> ```text
> 以下のファイルが変更されていないか git diff で確認して:
> docs/plan.md, docs/requirements.md, docs/architecture.md,
> README.md, project-config.yml
> ```

### 5.4 project-config.yml の更新

テンプレートの `project-config.yml` に新しい設定項目が追加されている場合があります。`never_update` のためスクリプトでは更新されないので、手動で確認します。

> **📎 Copilot への指示**:
>
> ```text
> テンプレートの project-config.yml と自分の project-config.yml を比較して、
> 自分側に不足している設定セクション（特に ai_models セクション）があれば追加して
> ```

---

## 6. 変更内容の概要

### 新規追加ファイル（41 ファイル）

テンプレートに新しく追加されたファイルです。スマートアップデート方式では、`always_update` または `add_only` に分類されたファイルのみ自動的に追加されます。

#### 開発環境

| ファイル | 内容 |
|---|---|
| `.devcontainer/devcontainer.json` | GitHub Codespaces / Dev Container の設定 |
| `.devcontainer/setup.sh` | Dev Container の初期化スクリプト |
| `.vscode/extensions.json` | VS Code 推奨拡張機能 |
| `.vscode/mcp.json` | Serena MCP の接続設定 |
| `.vscode/settings.json` | VS Code のプロジェクト設定 |
| `.gitattributes` | 改行コードの LF 統一設定 |
| `.serena/project.yml` | Serena のプロジェクト定義 |
| `.serena/.gitignore` | Serena キャッシュの除外設定 |

#### CI/CD

| ファイル | 内容 |
| --- | --- |
| `.github/workflows/issue-lifecycle.yml` | PR マージ時の Issue 自動 Close |
| `.github/workflows/production.yml` | 本番デプロイワークフロー |
| `.github/workflows/staging.yml` | ステージングワークフロー |

#### ドキュメント

| ファイル | 内容 |
| --- | --- |
| `docs/GETTING_STARTED.md` | 初心者向けセットアップガイド（18 セクション構成） |
| `docs/orchestration.md` | オーケストレーション設計書（エージェント連携の詳細） |
| `docs/quality-guide.md` | コード品質ガイド |
| `docs/observability-guide.md` | 可観測性（ログ・トレーシング）ガイド |
| `docs/security-policy-template.md` | セキュリティポリシーテンプレート |
| `docs/mobile-workflow.md` | モバイル環境のワークフロー |
| `docs/a2a-design/*` | A2A プロトコル設計ドキュメント群（11 ファイル） |

#### コード

| ファイル | 内容 |
| --- | --- |
| `pyproject.toml.template` | Python プロジェクトの設定テンプレート |
| `scripts/update_agent_models.sh` | AI モデル設定の一括更新スクリプト |
| `src/observability/tracing.py` | OpenTelemetry トレーシングの雛形 |
| `src/observability/__init__.py` | observability パッケージ初期化 |
| `src/sample/example_module.py` | サンプルコード（プロパティベーステスト付き） |
| `src/sample/__init__.py` | sample パッケージ初期化 |
| `tests/__init__.py` | テストパッケージ初期化 |
| `tests/test_sample_properties.py` | プロパティベーステストのサンプル |

> **注**: `src/sample/`、`src/my_package/` 等はテンプレート内部のサンプル（`sample_only`）のため、スマートアップデート方式では **コピーされません**。手動で取り込みたい場合は[セクション 8](#8-手動アップデート方式copilot-なしの場合)を参照してください。

### 変更されたファイル（25 ファイル）

既存のファイルで、機能改善・バグ修正が行われたファイルです。

#### 重要な変更

| ファイル | 変更内容 |
|---|---|
| `.github/copilot-instructions.md` | 自動実行パイプライン・計画修正モード・汎用リクエストモード・Serena MCP 統合を追加 |
| `.github/agents/*.agent.md`（7 ファイル） | 全エージェントの指示内容を大幅拡充 |
| `.github/workflows/ci.yml` | セキュリティスキャン・SBOM 生成・concurrency 設定を追加 |
| `.github/prompts/EXECUTE.prompt.md` | 実行プロンプトの改善 |
| `ci/policy_check.py` | 秘密情報パターンを拡充、ruff 準拠にリファクタリング |
| `agents/*.agent.md`（3 ファイル） | orchestrator / implementer / auditor-reliability の指示拡充 |

#### マイナーな変更

| ファイル | 変更内容 |
|---|---|
| `.gitignore` | `uv.lock`、`.hypothesis/`、Serena キャッシュを追加 |
| `README.md` | クイックスタート手順を更新 |
| `project-config.yml` | AI モデル設定セクションを追加 |
| `docs/requirements.md` | 要件番号の付与、可観測性・品質要件を追加 |
| `docs/constraints.md` | OWASP ASI セキュリティ制約を追加 |
| `docs/architecture.md` | 可観測性のアーキテクチャ追記 |
| `docs/runbook.md` | 運用手順の拡充 |
| `docs/plan.md` | ロードマップの更新 |
| `docs/policies.md` | ポリシーの微修正 |
| `scripts/bootstrap.sh` | テンプレート変数置換の改善 |
| `scripts/init_packages.sh` | パッケージ初期化の修正 |

> **⚠️ 重要**: `README.md`、`project-config.yml`、`docs/plan.md`、`docs/requirements.md`、`docs/architecture.md` はスマートアップデート方式では `never_update` のため **上書きされません**。テンプレートの改善を取り込みたい場合は、差分を手動で確認してください。

---

## 7. 従来方式（Git マージ方式）

> **⚠️ この方式は非推奨です。** プロジェクト固有ファイルの上書き、サンプルファイルの注入、エージェント定義のカスタマイズ消失などの問題が発生する可能性があります。新しい「スマートアップデート方式」（[セクション 3](#3-アップデート方法推奨-スマートアップデート方式)）を使用してください。
>
> **この方式が有効なケース**:
> - テンプレートのすべての変更を Git 履歴で追跡したい場合
> - プロジェクト固有のカスタマイズがほとんどない初期段階の場合

<details>
<summary>📝 従来の Git マージ方式の手順（クリックで展開）</summary>

### ステップ 1: テンプレートリポジトリをリモートとして追加

> **📎 Copilot への指示**:
>
> ```text
> テンプレートリポジトリ https://github.com/KosGit-ti/dev-orchestration-template.git を
> template-upstream という名前でリモートに追加して
> ```

### ステップ 2: テンプレートの最新コードを取得

> **📎 Copilot への指示**:
>
> ```text
> template-upstream リモートから最新のコードを fetch して
> ```

### ステップ 3: アップデート用ブランチを作成

> **📎 Copilot への指示**:
>
> ```text
> update-from-template という新しいブランチを作成して切り替えて
> ```

### ステップ 4: テンプレートの変更をマージ

> **📎 Copilot への指示**:
>
> ```text
> template-upstream/master を現在のブランチにマージして。
> 履歴が異なるので --allow-unrelated-histories オプションを付けて。
> ```

### ステップ 5: コンフリクトの解消

マージ後にコンフリクトが発生した場合は、以下のファイル種類ごとの判断基準に従って解消してください。

| ファイルの種類 | おすすめの対応 |
|---|---|
| `.github/copilot-instructions.md` | **テンプレートを採用** して、自分のプロジェクト固有の部分だけ書き戻す |
| `.github/agents/*.agent.md` | **テンプレートを採用**（エージェント定義はテンプレートの最新版が最適） |
| `.github/workflows/ci.yml` | **テンプレートを採用** して、自分で追加した CI ステップがあれば手動で追加 |
| `docs/requirements.md` | **あなたの変更を採用**（プロジェクト固有の内容を維持） |
| `docs/plan.md` | **あなたの変更を採用**（プロジェクト固有の計画を維持） |
| `docs/architecture.md` | **あなたの変更を採用**（プロジェクト固有の設計を維持） |
| `docs/policies.md` | **両方を比較** して手動で統合 |
| `project-config.yml` | **あなたの変更を採用**（新しい設定項目があれば手動で追加） |
| `README.md` | **あなたの変更を採用**（プロジェクト固有の説明を維持） |
| `ci/policy_check.py` | **テンプレートを採用**（セキュリティチェックの改善を取り込む） |
| `scripts/*.sh` | **テンプレートを採用** |
| `.github/instructions/*` | **あなたの変更を採用**（プロジェクト固有の instructions を維持） |

### ステップ 6: デフォルトブランチにマージしてプッシュ

> **📎 Copilot への指示**:
>
> ```text
> デフォルトブランチに切り替えて、update-from-template ブランチをマージして、
> リモートにプッシュして
> ```

</details>

---

## 8. 手動アップデート方式（Copilot なしの場合）

### 8.1 Copilot に一括で任せる方法（最も簡単）

> **📎 Copilot への指示**:
>
> ```text
> https://github.com/KosGit-ti/dev-orchestration-template.git を /tmp/template-latest にクローンして、
> 以下の手順でファイルを取り込んで:
>
> 1. .template-update.yml を参照して、always_update に含まれるファイルを上書きコピー
> 2. add_only に含まれるファイルは、自分のリポジトリに存在しないものだけコピー
> 3. never_update と sample_only に含まれるファイルはスキップ
> 4. 完了したら変更をコミットして
> ```

### 8.2 手動でコマンドを使う場合

Copilot を使わず自分でコマンドを実行する場合の手順です。

#### テンプレートの最新版をダウンロード

```bash
git clone --depth=1 https://github.com/KosGit-ti/dev-orchestration-template.git /tmp/template-latest
```

#### そのまま上書きしてよいファイル（always_update）

あなたがカスタマイズしていない可能性が高いファイルです。

```bash
# エージェント定義（テンプレートの最新版を素直に採用）
cp /tmp/template-latest/.github/agents/*.agent.md .github/agents/
cp /tmp/template-latest/agents/*.agent.md agents/ 2>/dev/null

# プロンプト
cp /tmp/template-latest/.github/prompts/EXECUTE.prompt.md .github/prompts/

# ポリシーチェック
cp /tmp/template-latest/ci/policy_check.py ci/

# ブートストラップ等のスクリプト
cp /tmp/template-latest/scripts/bootstrap.sh scripts/
cp /tmp/template-latest/scripts/init_packages.sh scripts/
```

#### 新規ファイルをコピー（add_only）

あなたのリポジトリに **存在しないファイル** をコピーします。

```bash
cd ~/my-project  # あなたのプロジェクトに移動

# .vscode（まだない場合のみ）
[ ! -f .vscode/extensions.json ] && mkdir -p .vscode && cp /tmp/template-latest/.vscode/extensions.json .vscode/
[ ! -f .vscode/mcp.json ] && cp /tmp/template-latest/.vscode/mcp.json .vscode/
[ ! -f .vscode/settings.json ] && cp /tmp/template-latest/.vscode/settings.json .vscode/

# .devcontainer（まだない場合のみ）
[ ! -d .devcontainer ] && cp -r /tmp/template-latest/.devcontainer .
```

#### 差分を確認して手動で統合するファイル

> **📎 Copilot への指示**:
>
> ```text
> /tmp/template-latest/.github/copilot-instructions.md と
> .github/copilot-instructions.md の差分を教えて。
> テンプレート側の改善を取り込みつつ、
> 自分のプロジェクト固有の設定は維持する方法を提案して。
> ```

#### コピーしてはいけないファイル（never_update + sample_only）

以下のファイルは **絶対にコピーしない** でください:

- `docs/plan.md` — プロジェクト固有の計画
- `docs/requirements.md` — プロジェクト固有の要件
- `docs/architecture.md` — プロジェクト固有の設計
- `README.md` — プロジェクト固有の説明
- `project-config.yml` — プロジェクト固有の設定
- `src/` 配下全体 — プロジェクトのソースコード
- `tests/` 配下全体 — プロジェクトのテスト
- `.github/instructions/` — プロジェクト固有の instructions
- `scripts/run_pipeline.py` — テンプレートのサンプルスクリプト
- `src/sample/` — テンプレートのサンプルコード
- `src/my_package/` — テンプレートのパッケージひな形

#### コミット

> **📎 Copilot への指示**:
>
> ```text
> 変更をすべてステージングして「chore: テンプレートの最新版を手動で取り込み」
> というメッセージでコミットしてプッシュして
> ```

#### 一時ディレクトリを削除

```bash
rm -rf /tmp/template-latest
```

---

## 9. トラブルシューティング

### `template_update.py` を実行すると「マニフェストが見つかりません」

`.template-update.yml` がプロジェクトルートにない場合に発生します。テンプレートの最新版から取得してください。

> **📎 Copilot への指示**:
>
> ```text
> https://github.com/KosGit-ti/dev-orchestration-template.git から
> .template-update.yml を取得して、プロジェクトルートにコピーして
> ```

### `template_update.py` で「未分類ファイル」の警告が出る

テンプレートに新しいファイルが追加され、マニフェストがまだ更新されていない場合に発生します。

> **📎 Copilot への指示**:
>
> ```text
> .template-update.yml を確認して、未分類と表示されたファイルを適切なカテゴリに追加して
> ```

### バックアップから復元したい

`template_update.py` が自動作成したバックアップブランチから復元できます。

> **📎 Copilot への指示**:
>
> ```text
> backup-before-template-update で始まるブランチの一覧を表示して。
> 最新のバックアップブランチの状態にデフォルトブランチを戻して。
> ```

### 従来方式で `--allow-unrelated-histories` エラーが出る

> **📎 Copilot への指示**:
>
> ```text
> template-upstream/master を --allow-unrelated-histories 付きでマージして
> ```

### CI が失敗する

アップデート後に CI が失敗する場合:

> **📎 Copilot への指示**:
>
> ```text
> CI が失敗しています。以下を確認して修正して:
> 1. .github/workflows/ci.yml のパスが自分のプロジェクトの構成と合っているか
> 2. ci/policy_check.py の禁止パターンが自分のプロジェクトに適しているか
> 3. 必要な依存パッケージがインストールされているか
> ```

---

## 10. よくある質問（FAQ）

### Q: テンプレートが今後もアップデートされた場合、またこの手順が必要ですか？

**A**: はい。ただし、2回目以降は `scripts/template_update.py` がリポジトリにあるので、以下の指示だけでアップデートできます。

> **📎 Copilot への指示**:
>
> ```text
> scripts/template_update.py を --dry-run で実行して結果を確認して。
> 問題なければ --dry-run なしで実行して、コミットして。
> ```

### Q: テンプレートの変更のうち、一部だけを取り込むことはできますか？

**A**: できます。スマートアップデート方式では、`.template-update.yml` を編集して特定のファイルを `never_update` に追加すれば、そのファイルをスキップできます。

### Q: `docs/plan.md` や `docs/requirements.md` はテンプレートの内容で上書きされますか？

**A**: **いいえ。** スマートアップデート方式では `never_update` に分類されているため、**絶対に上書きされません**。テンプレートの改善を取り込みたい場合は、手動で差分を確認してください。

### Q: プロジェクト固有の `.github/instructions/` ファイルは安全ですか？

**A**: **はい。** `.github/instructions/` ディレクトリは `never_update` に分類されています。`backtest.instructions.md` や `monitoring.instructions.md` 等のプロジェクト固有ファイルは保護されます。

### Q: `scripts/run_pipeline.py` がリポジトリに追加されてしまいました。

**A**: 従来の Git マージ方式を使った場合に発生します。このファイルはテンプレートの内部サンプル（`sample_only`）なので、削除して問題ありません。スマートアップデート方式では自動的にスキップされます。

### Q:「Use this template」ではなく `git clone` でコピーした場合も同じ手順ですか？

**A**: スマートアップデート方式は Git 履歴に依存しないため、どちらの場合も同じ手順です。

### Q: `.serena/` や `.vscode/` のような環境固有のファイルも取り込む必要がありますか？

**A**: これらは開発環境の設定ファイルです。

| ファイル | 取り込み推奨度 | 理由 |
|---|---|---|
| `.devcontainer/` | ⭐⭐⭐ 強く推奨 | GitHub Codespaces を使う場合は必須 |
| `.vscode/extensions.json` | ⭐⭐ 推奨 | チームで VS Code 拡張を統一できる |
| `.vscode/settings.json` | ⭐ 任意 | 既存の設定と競合する可能性がある |
| `.vscode/mcp.json` | ⭐ 任意 | Serena MCP を使う場合のみ必要 |
| `.serena/` | ⭐ 任意 | Serena MCP を使う場合のみ必要 |
| `.gitattributes` | ⭐⭐⭐ 強く推奨 | 改行コードの統一はチーム開発で重要 |

---

## 全体の流れ（チートシート）

### スマートアップデート方式（推奨）

以下を Copilot Chat にそのまま貼り付けてください:

```text
以下の手順でテンプレートの最新版をアップデートしたい:

1. https://github.com/KosGit-ti/dev-orchestration-template.git から
   .template-update.yml と scripts/template_update.py を取得して
   自分のリポジトリにコピーして
2. scripts/template_update.py を --dry-run で実行して結果を教えて
3. 問題なければ --dry-run なしで実行して
4. 結果を確認してコミットしてプッシュして
```

### 従来の Git マージ方式

> ⚠️ 非推奨。プロジェクト固有ファイルの保護が必要な場合はスマートアップデート方式を使用してください。

<details>
<summary>📝 従来方式のチートシート（クリックで展開）</summary>

```text
以下の手順でテンプレートリポジトリの最新版をマージしたい:

1. このリポジトリのデフォルトブランチ名を確認して
2. 未コミットの変更があればコミットして
3. backup-before-template-update ブランチでバックアップを作って
4. https://github.com/KosGit-ti/dev-orchestration-template.git を
   template-upstream という名前でリモートに追加して
5. template-upstream から fetch して
6. update-from-template ブランチを作って
7. template-upstream/master を --allow-unrelated-histories でマージして
8. コンフリクトがあれば教えて
```

コンフリクト解消後:

```text
デフォルトブランチに切り替えて、update-from-template をマージして、
リモートにプッシュして。update-from-template ブランチは削除して。
```

</details>

### Git コマンドで手動実行する場合（スマートアップデート方式）

```text
1. git clone --depth=1 <TEMPLATE_URL> /tmp/template-latest
2. cp /tmp/template-latest/.template-update.yml .
3. cp /tmp/template-latest/scripts/template_update.py scripts/
4. python scripts/template_update.py --dry-run              ← 確認
5. python scripts/template_update.py                        ← 実行
6. git add -A && git commit -m "chore: テンプレートアップデート適用"
7. git push origin <DEFAULT_BRANCH>                         ← 完了！
```
