````markdown
# 運用手順（Runbook）

## 目的

開発・検証環境を再現可能に運用し、失敗時に復旧できるようにする。

## 前提

- 秘密情報はリポジトリに含めない（P-002）
- <!-- プロジェクト固有の前提を追加 -->

## セットアップ

### 1. リポジトリのクローン

```bash
git clone <repository-url>
cd {{PROJECT_NAME}}
```

### 2. 環境の準備

<!-- 言語・ツールに合わせて記載 -->

#### Python (uv) の例

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --dev
```

#### Node.js (pnpm) の例

```bash
corepack enable
pnpm install
```

### 3. 環境変数の設定

```bash
cp .env.example .env
# .env を編集し、必要な値を設定する
# ⚠️ .env は絶対にコミットしない
```

## 代表コマンド

### 静的解析

```bash
# リンター
{{RUN_PREFIX}} {{LINTER}}

# フォーマッタ（チェックのみ）
{{RUN_PREFIX}} {{FORMATTER}}

# 型チェック
{{RUN_PREFIX}} {{TYPE_CHECKER}}
```

### テスト

```bash
# 全テスト実行
{{RUN_PREFIX}} {{TEST_RUNNER}}
```

### ポリシーチェック

```bash
# 禁止操作・秘密情報検出
{{RUN_PREFIX}} python ci/policy_check.py
```

## 生成物の扱い

| ディレクトリ | 内容 | git 管理 |
|---|---|---|
| `outputs/` | 実行結果、レポート | しない |
| `data/raw/` | 実データ | しない |
| `configs/` | 実行設定 | する（秘密情報を含めない） |

## 失敗時対応

### CI 失敗

1. GitHub Actions のログで失敗箇所を特定する
2. ローカルで再現する（`{{RUN_PREFIX}} {{TEST_RUNNER}}`）
3. 修正して再プッシュする

### 設定の破損

1. `configs/` のデフォルト設定に戻す
2. テストを実行して正常動作を確認する

### 依存関係の問題

1. ロックファイルを削除して再インストール
2. CI で動作確認する
````
