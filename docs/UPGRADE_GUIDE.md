# テンプレートアップデートガイド

> **対象読者**: `dev-orchestration-template` を GitHub の「Use this template」ボタンでコピーし、すでに自分のプロジェクトで開発を進めている方。
>
> **前提**: GitHub Copilot（VS Code 拡張）を使用していること。Git の知識は不要です。Copilot に指示を出すだけでほとんどの操作を完了できます。

---

## 目次

1. [このガイドについて](#1-このガイドについて)
2. [アップデート前の準備](#2-アップデート前の準備)
3. [アップデート方法（推奨: Copilot にお任せ方式）](#3-アップデート方法推奨-copilot-にお任せ方式)
4. [コンフリクト（競合）の解消方法](#4-コンフリクト競合の解消方法)
5. [アップデート後の検証](#5-アップデート後の検証)
6. [変更内容の概要](#6-変更内容の概要)
7. [手動アップデート方式（Copilot なしの場合）](#7-手動アップデート方式copilot-なしの場合)
8. [トラブルシューティング](#8-トラブルシューティング)
9. [よくある質問（FAQ）](#9-よくある質問faq)
10. [全体の流れ（チートシート）](#全体の流れチートシート)

---

## 1. このガイドについて

`dev-orchestration-template` は初回リリース以降、大幅な改善を行いました。主な変更点は以下のとおりです。

- **処理フロー全体の刷新**: 自動実行パイプライン、計画修正モード、汎用リクエストモードの追加
- **エージェント定義の強化**: 全7エージェントの指示内容を大幅に拡充
- **CI/CD パイプラインの充実**: セキュリティスキャン、Issue ライフサイクル自動化、ステージング/本番ワークフロー追加
- **開発環境の整備**: Dev Container 対応、VS Code 推奨設定、Serena MCP 統合
- **ドキュメント拡充**: GETTING_STARTED.md、オーケストレーション設計書、品質ガイド等を追加
- **コード品質テンプレート**: サンプルコード、テスト、可観測性（tracing）の雛形を追加

このガイドでは、すでに開発を進めている **あなたのリポジトリ** にこれらの改善を取り込む方法を、ステップごとに解説します。

### 重要な注意点

「Use this template」でコピーしたリポジトリは、テンプレートとの Git 履歴のつながりがありません。そのため、通常の `git pull` では更新を取り込めません。このガイドで説明する **「リモート追加 → マージ」** の手順が必要です。

### このガイドの読み方

各セクションでは **Copilot に伝える指示**（📎 マーク）と、参考用の **対応する Git コマンド** の両方を記載しています。基本的には Copilot への指示だけで進められます。

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

### 2.2 バックアップブランチを作成する

万が一問題が起きても元に戻せるように、バックアップを作っておきます。

> **📎 Copilot への指示**:
>
> ```text
> 現在の main ブランチの状態を backup-before-template-update という名前のブランチとしてバックアップして
> ```

> **💡 何か問題が起きたら**: Copilot に「`backup-before-template-update` ブランチの状態に main を戻して」と伝えれば復元できます。

### 2.3 リモートリポジトリにもプッシュしておく（推奨）

VS Code のソース管理で **同期** ボタンを押すか、Copilot に「main ブランチをプッシュして」と伝えてください。

---

## 3. アップデート方法（推奨: Copilot にお任せ方式）

以下の手順を **Copilot Chat に指示をコピー＆ペースト** するだけで進められます。各ステップの背景も解説しているので、何が起きているか理解しながら進めてください。

### ステップ 1: テンプレートリポジトリをリモートとして追加

> **📎 Copilot への指示**:
>
> ```text
> テンプレートリポジトリ https://github.com/KosGit-ti/dev-orchestration-template.git を
> template-upstream という名前でリモートに追加して
> ```

**何が起きるか**: テンプレートの最新版を取得する場所が Git に登録されます。あなたのコードは何も変わりません。

<details>
<summary>📝 対応する Git コマンド（参考）</summary>

```bash
git remote add template-upstream https://github.com/KosGit-ti/dev-orchestration-template.git
```

確認:

```bash
git remote -v
```

</details>

### ステップ 2: テンプレートの最新コードを取得

> **📎 Copilot への指示**:
>
> ```text
> template-upstream リモートから最新のコードを fetch して
> ```

**何が起きるか**: テンプレートのコードがダウンロードされますが、あなたのブランチにはまだ反映されません。安全な操作です。

<details>
<summary>📝 対応する Git コマンド（参考）</summary>

```bash
git fetch template-upstream
```

</details>

### ステップ 3: アップデート用ブランチを作成

> **📎 Copilot への指示**:
>
> ```text
> update-from-template という新しいブランチを作成して切り替えて
> ```

**何が起きるか**: main ブランチを直接変更せず、安全な作業場所を作ります。

<details>
<summary>📝 対応する Git コマンド（参考）</summary>

```bash
git checkout -b update-from-template
```

</details>

### ステップ 4: テンプレートの変更をマージ

ここがアップデートの本番です。

> **📎 Copilot への指示**:
>
> ```text
> template-upstream/master を現在のブランチにマージして。
> 履歴が異なるので --allow-unrelated-histories オプションを付けて。
> ```

> **💡 「`--allow-unrelated-histories`」とは？**
>
> 「Use this template」でコピーしたリポジトリは、テンプレートと履歴のつながりがないため、通常の `git merge` では「関係のないリポジトリ同士はマージできない」とエラーになります。このオプションを付けることで、異なる履歴同士でもマージを許可します。

<details>
<summary>📝 対応する Git コマンド（参考）</summary>

```bash
git merge template-upstream/master --allow-unrelated-histories
```

</details>

#### マージの結果は 3 パターンあります

| 結果 | 意味 | 次のアクション |
| --- | --- | --- |
| 自動マージ成功（コンフリクトなし） | Git が自動で統合できました | → ステップ 5 へ |
| `CONFLICT` が表示される | 手動で解消が必要な箇所があります | → [4. コンフリクトの解消方法](#4-コンフリクト競合の解消方法) へ |
| `Already up to date.` | すでに最新です | 何もしなくて OK |

### ステップ 5: 変更を確認して main にマージ

マージ（またはコンフリクト解消）が完了したら、main ブランチに反映します。

> **📎 Copilot への指示**:
>
> ```text
> main ブランチに切り替えて、update-from-template ブランチをマージして、
> リモートにプッシュして
> ```

<details>
<summary>📝 対応する Git コマンド（参考）</summary>

```bash
git checkout main
git merge update-from-template
git push origin main
```

</details>

### ステップ 6: 後片付け

> **📎 Copilot への指示**:
>
> ```text
> update-from-template ブランチを削除して。
> template-upstream リモートは今後のアップデートに備えて残しておいて。
> ```

> **💡 おすすめ**: `template-upstream` リモートは残しておくと、将来テンプレートが更新された際にも同じ手順でアップデートできます。

---

## 4. コンフリクト（競合）の解消方法

マージ時に以下のようなメッセージが出た場合、コンフリクトが発生しています。

```text
Auto-merging .github/copilot-instructions.md
CONFLICT (content): Merge conflict in .github/copilot-instructions.md
Automatic merge failed; fix conflicts and then commit the result.
```

### 4.1 コンフリクトが起きているファイルを確認

VS Code の **ソース管理** ビューを開くと、コンフリクトしているファイルが「マージの変更」セクションに一覧表示されます。

> **📎 Copilot への指示**:
>
> ```text
> コンフリクトが発生しているファイルの一覧を教えて
> ```

### 4.2 コンフリクトの見方

コンフリクトしたファイルを VS Code で開くと、以下のような表示になります。

```text
<<<<<<< HEAD
ここにあなたが変更した内容が表示される
=======
ここにテンプレートの新しい内容が表示される
>>>>>>> template-upstream/master
```

| マーカー | 意味 |
| --- | --- |
| `<<<<<<< HEAD` | あなたの変更の開始 |
| `=======` | あなたの変更とテンプレートの変更の境界 |
| `>>>>>>> template-upstream/master` | テンプレートの変更の終了 |

### 4.3 VS Code で解消する（推奨）

VS Code でコンフリクトが発生したファイルを開くと、コンフリクト箇所の上に以下のボタンが表示されます。

- **Accept Current Change**: あなたの変更を採用する
- **Accept Incoming Change**: テンプレートの変更を採用する
- **Accept Both Changes**: 両方を残す
- **Compare Changes**: 変更を並べて比較する

#### Copilot にコンフリクト解消を手伝ってもらう

どちらを採用すべきか迷ったときは、コンフリクトが発生しているファイルを開いた状態で Copilot に相談できます。

> **📎 Copilot への指示例**:
>
> ```text
> このファイルのコンフリクトを解消して。テンプレートの変更を基本にしつつ、
> 自分のプロジェクト固有の設定は維持して。
> ```
>
> ```text
> このコンフリクトはどちらを採用すべきか判断して。ファイルの種類に合わせて
> テンプレートの最新を優先するか、自分の変更を優先するか教えて。
> ```

### 4.4 ファイルの種類ごとの判断基準

| ファイルの種類 | おすすめの対応 |
|---|---|
| `.github/copilot-instructions.md` | **テンプレートを採用** して、自分のプロジェクト固有の部分だけ書き戻す |
| `.github/agents/*.agent.md` | **テンプレートを採用**（エージェント定義はテンプレートの最新版が最適） |
| `.github/workflows/ci.yml` | **テンプレートを採用** して、自分で追加した CI ステップがあれば手動で追加 |
| `docs/requirements.md` | **あなたの変更を採用**（プロジェクト固有の内容を維持） |
| `docs/plan.md` | **あなたの変更を採用**（プロジェクト固有の計画を維持） |
| `docs/architecture.md` | **あなたの変更を採用**（プロジェクト固有の設計を維持） |
| `docs/policies.md` | **両方を比較** して手動で統合（テンプレートの改善を取り込みつつ、プロジェクト固有のポリシーを維持） |
| `project-config.yml` | **あなたの変更を採用**（プロジェクト固有の設定を維持。ただし新しい設定項目があれば手動で追加） |
| `README.md` | **あなたの変更を採用**（プロジェクト固有の説明を維持） |
| `ci/policy_check.py` | **テンプレートを採用**（セキュリティチェックの改善を取り込む） |
| `scripts/*.sh` | **テンプレートを採用**（改善されたスクリプトを使う。ただし自分で追加したスクリプトは維持） |

### 4.5 解消後のコミット

すべてのコンフリクトを解消したら、コミットします。

> **📎 Copilot への指示**:
>
> ```text
> コンフリクトマーカーが残っていないか確認して、問題なければ
> 「chore: テンプレートアップデートのコンフリクト解消」というメッセージでコミットして
> ```

VS Code の場合は、ソース管理ビューでメッセージを入力してコミットボタンを押すだけでも OK です。

> **⚠️ 注意**: `<<<<<<< HEAD`、`=======`、`>>>>>>> template-upstream/master` のマーカーが残っていないか必ず確認してください。マーカーが残っているとコードが動きません。

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

### 5.3 新規追加ファイルの確認

> **📎 Copilot への指示**:
>
> ```text
> 以下のファイルがリポジトリに存在するか確認して:
> .devcontainer/devcontainer.json, .github/workflows/issue-lifecycle.yml,
> docs/GETTING_STARTED.md, docs/orchestration.md, docs/quality-guide.md,
> scripts/update_agent_models.sh
> ```

### 5.4 project-config.yml の更新

テンプレートの `project-config.yml` に新しい設定項目が追加されている場合があります。

> **📎 Copilot への指示**:
>
> ```text
> テンプレートの project-config.yml と自分の project-config.yml を比較して、
> 自分側に不足している設定セクション（特に ai_models セクション）があれば追加して
> ```

特に確認すべき新しいセクション:

```yaml
# AI モデル設定（新規追加）
ai_models:
  default: "Claude Opus 4.6 (copilot)"
  overrides:
    orchestrator: "Claude Opus 4.6 (copilot)"
    implementer: "Claude Sonnet 4.6 (copilot)"
    # ... 他のエージェント
```

---

## 6. 変更内容の概要

### 新規追加ファイル（41 ファイル）

テンプレートに新しく追加されたファイルです。マージによって自動的にあなたのリポジトリにも追加されます。

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

> **注**: 上記に加え、テンプレートリポジトリ固有の内部ドキュメント（改善計画・レビューレポート等、計 5 ファイル）が含まれます。これらはプロジェクト固有のため、通常は取り込み不要です。

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

---

## 7. 手動アップデート方式（Copilot なしの場合）

Git マージでコンフリクトが多すぎて解消が難しい場合や、Git 操作に不安がある場合は、Copilot にファイルのコピーを任せることもできます。

### 7.1 Copilot に一括で任せる方法（最も簡単）

> **📎 Copilot への指示**:
>
> ```text
> https://github.com/KosGit-ti/dev-orchestration-template.git を /tmp/template-latest にクローンして、
> 以下の手順でファイルを取り込んで:
>
> 1. 新規ファイル（自分のリポジトリに存在しないもの）をすべてコピー
> 2. エージェント定義（.github/agents/）は最新版で上書き
> 3. ci/policy_check.py は最新版で上書き
> 4. scripts/ のスクリプトは最新版で上書き
> 5. .github/copilot-instructions.md は最新版で上書きして、
>    自分のプロジェクト名やスコープ部分は元の内容を維持して
> 6. docs/requirements.md, docs/plan.md, docs/architecture.md, README.md,
>    project-config.yml は上書きしないで
> 7. 完了したら変更をコミットして
> ```

### 7.2 手動でコマンドを使う場合

Copilot を使わず自分でコマンドを実行する場合の手順です。

#### テンプレートの最新版をダウンロード

```bash
git clone https://github.com/KosGit-ti/dev-orchestration-template.git /tmp/template-latest
```

#### 新規ファイルをコピー

あなたのリポジトリに **存在しないファイル** をコピーします。これは安全な操作です。

```bash
cd ~/my-project  # あなたのプロジェクトに移動

# --- 開発環境 ---
cp -r /tmp/template-latest/.devcontainer .
cp /tmp/template-latest/.gitattributes .

# .vscode（まだない場合のみ）
mkdir -p .vscode
cp /tmp/template-latest/.vscode/extensions.json .vscode/
cp /tmp/template-latest/.vscode/mcp.json .vscode/
cp /tmp/template-latest/.vscode/settings.json .vscode/

# Serena 設定（まだない場合のみ）
mkdir -p .serena
cp /tmp/template-latest/.serena/project.yml .serena/
cp /tmp/template-latest/.serena/.gitignore .serena/

# --- CI/CD ---
cp /tmp/template-latest/.github/workflows/issue-lifecycle.yml .github/workflows/
cp /tmp/template-latest/.github/workflows/production.yml .github/workflows/
cp /tmp/template-latest/.github/workflows/staging.yml .github/workflows/

# --- ドキュメント ---
cp /tmp/template-latest/docs/GETTING_STARTED.md docs/
cp /tmp/template-latest/docs/orchestration.md docs/
cp /tmp/template-latest/docs/quality-guide.md docs/
cp /tmp/template-latest/docs/observability-guide.md docs/
cp /tmp/template-latest/docs/security-policy-template.md docs/
cp /tmp/template-latest/docs/mobile-workflow.md docs/
mkdir -p docs/a2a-design
cp -r /tmp/template-latest/docs/a2a-design/* docs/a2a-design/

# --- コード ---
cp /tmp/template-latest/pyproject.toml.template .
cp /tmp/template-latest/scripts/update_agent_models.sh scripts/
mkdir -p src/observability
cp /tmp/template-latest/src/observability/__init__.py src/observability/
cp /tmp/template-latest/src/observability/tracing.py src/observability/

# --- サンプルコード ---
mkdir -p src/sample
cp /tmp/template-latest/src/sample/__init__.py src/sample/
cp /tmp/template-latest/src/sample/example_module.py src/sample/
cp /tmp/template-latest/tests/__init__.py tests/ 2>/dev/null
cp /tmp/template-latest/tests/test_sample_properties.py tests/
```

#### 変更ファイルを更新（慎重に）

既存ファイルの更新は、あなたがカスタマイズした部分を上書きしてしまう可能性があります。以下のファイルは **差分を確認してから** 更新してください。

##### そのまま上書きしてよいファイル

あなたがカスタマイズしていない可能性が高いファイルは、そのまま上書きできます。

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

##### 差分を確認して手動で統合するファイル

> **📎 Copilot への指示**:
>
> ```text
> /tmp/template-latest/.github/copilot-instructions.md と
> .github/copilot-instructions.md の差分を教えて。
> テンプレート側の改善を取り込みつつ、
> 自分のプロジェクト固有の設定は維持する方法を提案して。
> ```

同様に、以下のファイルについても Copilot に差分を確認・統合してもらえます:

- `.github/workflows/ci.yml`
- `.gitignore`
- `project-config.yml`（新しい設定項目のみ追加）

<details>
<summary>📝 コマンドで差分を確認する場合</summary>

```bash
diff ~/my-project/.github/copilot-instructions.md /tmp/template-latest/.github/copilot-instructions.md
diff ~/my-project/.github/workflows/ci.yml /tmp/template-latest/.github/workflows/ci.yml
diff ~/my-project/.gitignore /tmp/template-latest/.gitignore
diff ~/my-project/project-config.yml /tmp/template-latest/project-config.yml
```

VS Code で 2 ファイルを並べて比較:

```bash
code --diff ~/my-project/.github/copilot-instructions.md /tmp/template-latest/.github/copilot-instructions.md
```

</details>

#### コミット

> **📎 Copilot への指示**:
>
> ```text
> 変更をすべてステージングして「chore: テンプレートの最新版を手動で取り込み」
> というメッセージでコミットしてプッシュして
> ```

#### 一時ディレクトリを削除

> **📎 Copilot への指示**:
>
> ```text
> /tmp/template-latest ディレクトリを削除して
> ```

---

## 8. トラブルシューティング

### 「`fatal: refusing to merge unrelated histories`」と表示される

`--allow-unrelated-histories` オプションが必要です。

> **📎 Copilot への指示**:
>
> ```text
> template-upstream/master を --allow-unrelated-histories 付きでマージして
> ```

### 「`fatal: 'template-upstream' does not appear to be a git repository`」と表示される

リモートの追加がうまくいっていません。

> **📎 Copilot への指示**:
>
> ```text
> template-upstream というリモートが登録されているか確認して。
> なければ https://github.com/KosGit-ti/dev-orchestration-template.git を追加して。
> ```

### マージを中止したい（やり直したい）

> **📎 Copilot への指示**:
>
> ```text
> マージを中止して、マージ前の状態に戻して
> ```

<details>
<summary>📝 対応する Git コマンド（参考）</summary>

```bash
git merge --abort
```

</details>

### すべてを元に戻したい

バックアップブランチを作成していた場合:

> **📎 Copilot への指示**:
>
> ```text
> main ブランチを backup-before-template-update の状態に完全に戻して
> ```

> **⚠️ 注意**: この操作はアップデート後のすべての変更を破棄します。

### コンフリクトが多すぎて対処できない

[7. 手動アップデート方式](#7-手動アップデート方式copilot-なしの場合) の「Copilot に一括で任せる方法」を使ってください。まずマージを中止します。

> **📎 Copilot への指示**:
>
> ```text
> マージを中止して、マージ前の状態に戻して
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

## 9. よくある質問（FAQ）

### Q: テンプレートが今後もアップデートされた場合、またこの手順が必要ですか？

**A**: はい。ただし、`template-upstream` リモートを残しておけば、2 回目以降は Copilot に以下のように伝えるだけです。

> **📎 Copilot への指示**:
>
> ```text
> template-upstream リモートから最新を fetch して、
> update-from-template-v2 ブランチを作って、
> template-upstream/master を --allow-unrelated-histories でマージして
> ```

### Q: テンプレートの変更のうち、一部だけを取り込むことはできますか？

**A**: できます。

> **📎 Copilot への指示**:
>
> ```text
> template-upstream/master のコミット一覧を表示して。
> その中から〇〇に関するコミットだけを cherry-pick して取り込んで。
> ```

### Q: `docs/plan.md` や `docs/requirements.md` はテンプレートの内容で上書きされますか？

**A**: マージ時にコンフリクトとして検出されるので、あなたの内容が勝手に上書きされることはありません。コンフリクト解消時に「あなたの変更を採用」を選べば安全です。

### Q:「Use this template」ではなく `git clone` でコピーした場合も同じ手順ですか？

**A**: 基本的に同じです。`git clone` で直接コピーした場合は、`origin` がテンプレートのリポジトリを指している可能性があります。

> **📎 Copilot への指示**:
>
> ```text
> origin リモートの URL を確認して。もしテンプレートリポジトリを指していたら、
> origin を自分のリポジトリ（https://github.com/あなた/my-project.git）に変更して、
> テンプレートは template-upstream として追加して。
> ```

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

### Copilot に一括指示する場合

以下を Copilot Chat にそのまま貼り付けてください:

```text
以下の手順でテンプレートリポジトリの最新版をマージしたい:

1. 未コミットの変更があればコミットして
2. backup-before-template-update ブランチでバックアップを作って
3. https://github.com/KosGit-ti/dev-orchestration-template.git を
   template-upstream という名前でリモートに追加して
4. template-upstream から fetch して
5. update-from-template ブランチを作って
6. template-upstream/master を --allow-unrelated-histories でマージして
7. コンフリクトがあれば教えて
```

コンフリクト解消後:

```text
main ブランチに切り替えて、update-from-template をマージして、
リモートにプッシュして。update-from-template ブランチは削除して。
```

### Git コマンドで手動実行する場合

```text
1. git status                                         ← 未コミットの変更がないことを確認
2. git branch backup-before-template-update           ← バックアップ作成
3. git remote add template-upstream <URL>             ← テンプレートを登録
4. git fetch template-upstream                        ← 最新を取得
5. git checkout -b update-from-template               ← 作業ブランチ作成
6. git merge template-upstream/master --allow-unrelated-histories  ← マージ
7. コンフリクト解消（あれば）
8. git checkout main && git merge update-from-template ← main に反映
9. テスト・CI で確認
10. git push origin main                              ← 完了！
```
