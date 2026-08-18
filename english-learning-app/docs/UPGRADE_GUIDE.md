# テンプレートアップグレードガイド（v2 機構）

> **対象読者**: ai-dev-template（旧: dev-orchestration-template）を「Use this template」等で
> コピーし、自分のプロジェクトで開発を進めている方。
>
> **前提**: `python`（3.11+）と `git` が使えること。特定の AI アシスタントには依存しない。
> Claude Code / GitHub Copilot / Codex 等、**使用中の AI アシスタント**に指示するだけでほぼ完了する。

---

## 目次

1. [アップデート機構 v2 の全体像](#1-アップデート機構-v2-の全体像)
2. [3つのサブコマンド](#2-3つのサブコマンド)
3. [標準的な流れ（check → dry-run → apply → 検証 → コミット）](#3-標準的な流れ)
4. [マニフェストと機能カタログ](#4-マニフェストと機能カタログ)
5. [.template-version.yml（適用状態）](#5-template-versionyml適用状態)
6. [逆方向同期（export）](#6-逆方向同期export)
7. [トラブルシューティング](#7-トラブルシューティング)
8. [FAQ](#8-faq)

---

## 1. アップデート機構 v2 の全体像

「Use this template」でコピーしたリポジトリはテンプレートと Git 履歴がつながっていないため、
単純な `git merge` ではプロジェクト固有ファイルを壊すおそれがある。v2 機構は
`scripts/template_update.py` が **ファイルごとにカテゴリ判定** し、安全に同期する。

構成要素:

| ファイル | 役割 |
| --- | --- |
| `template-catalog.yml` | テンプレートの **版と機能** の正本（テンプレート側にのみ存在） |
| `.template-update.yml` | ファイルを4カテゴリに分類するマニフェスト |
| `scripts/template_update.py` | `check` / `apply` / `export` を実行するスクリプト |
| `.template-version.yml` | 子リポジトリ側の **適用済み版** を記録する状態ファイル |
| `docs/TEMPLATE_CHANGELOG.md` | テンプレートの変更履歴（check が抜粋表示に使う） |

> **📎 AI アシスタントへ**: 「アップデートを確認して」「アップデートを適用して」と伝えれば、
> `.github/instructions/template-sync.instructions.md` の手順に従って実行されます。

---

## 2. 3つのサブコマンド

```bash
python scripts/template_update.py check              # 更新有無の確認（読み取りのみ）
python scripts/template_update.py apply --dry-run    # 影響範囲の確認（変更なし）
python scripts/template_update.py apply              # 適用（バックアップ自動）
python scripts/template_update.py export --template-dir PATH  # 逆方向同期（メンテナ向け）
```

- `check` の終了コード: `0`=最新 / `10`=更新あり / `2`=エラー。
- サブコマンドを省略すると `apply` 相当で動作するが **非推奨**（警告表示）。明示を推奨。

---

## 3. 標準的な流れ

### ステップ 1: 更新を確認する（check）

```bash
python scripts/template_update.py check
```

適用済み版と最新版が表示される。更新があれば `TEMPLATE_CHANGELOG.md` の抜粋が出る。

> **📎 AI アシスタントへ**: 「テンプレートのアップデートを確認して」

### ステップ 2: dry-run で影響範囲を確認する

```bash
python scripts/template_update.py apply --dry-run
```

**確認ポイント**:

| 見出し | 確認内容 |
| --- | --- |
| 更新（always_update） | 上書きされて困るファイルが無いか |
| 保護（never_update） | plan.md / requirements.md / README.md 等が保護されているか |
| 未分類 | **0 件であること**（0 でなければマニフェストを直す） |

### ステップ 3: 適用する（apply）

```bash
python scripts/template_update.py apply
```

自動で行われること: バックアップブランチ作成 → 選択的コピー →
`.template-version.yml` 書き出し → ポストチェック（ruff / policy_check）。

### ステップ 4: 検証する

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python ci/policy_check.py
```

失敗はその場で修正する。

### ステップ 5: 入口ファイルの手動取り込み（任意）

`CLAUDE.md` / `AGENTS.md` / `.github/copilot-instructions.md` / `.claude/settings.json` と
`project-config.yml` は自動更新されない（後述）。改善を取り込みたい場合のみ、
テンプレートとの差分を確認して手動で反映する。

> **📎 AI アシスタントへ**: 「テンプレートの CLAUDE.md と自分の CLAUDE.md の差分を見て、
> 自分の第一目的やカスタマイズを保ったまま、テンプレート側の改善だけ取り込んで」

### ステップ 6: コミット（必要なら push）

```bash
git add -A && git commit -m "chore: テンプレートアップデート適用"
```

---

## 4. マニフェストと機能カタログ

### `.template-update.yml` の4カテゴリ

| カテゴリ | 動作 | 例 |
| --- | --- | --- |
| `always_update` | 常にテンプレート最新版で **上書き** | `ai/`、`.claude/agents/`、`.github/workflows/`、`ci/policy_check.py` |
| `never_update` | **絶対にスキップ**（プロジェクト固有） | `docs/plan.md`、`docs/requirements.md`、`README.md`、`src/`、`tests/` |
| `add_only` | **無い場合のみ追加**、既存は保護 | `CLAUDE.md`、`.github/copilot-instructions.md`、`docs/ai/`、`.vscode/` |
| `sample_only` | **完全にスキップ**（テンプレのサンプル） | `src/sample/`、`src/my_package/`、`scripts/run_pipeline.py` |

### 分類の決まり方（最具体一致）

あるファイルに複数パターンがマッチしたら、**最も具体的（マッチしたパターン文字列が最長）**な
指定を採用する。長さが同じ場合のみ `sample_only > never_update > always_update > add_only` で決める。

これにより「ディレクトリ全体は `never` だが特定ファイルだけ `always`」を直感どおり表現できる。
例: `tests/` は `never_update` だが `tests/test_instruction_source.py`（hook テスト）は `always_update`。
`.github/instructions/` は `never_update` だが `review-loop.instructions.md` /
`template-sync.instructions.md` は `always_update`。

### `template-catalog.yml`（機能カタログ）

テンプレート側にのみ存在し、`template.version` と提供機能（`features`）を宣言する正本。
`check`/`apply` は `features[].policy` とマニフェスト分類の整合を検査し、矛盾があれば警告する。

### 取得元 URL の解決順

`--template-url`（明示） → `template-catalog.yml` の `template.repository`
→ `.template-update.yml` の `template_repository` → コード定数、の順。

---

## 5. .template-version.yml（適用状態）

`apply` 成功時に子リポジトリのルートへ自動生成・更新される状態ファイル。

```yaml
template_name: "ai-dev-template"
template_repository: "https://github.com/KosGit-Dev/dev-orchestration-template.git"
applied_version: "3.0.0"
applied_commit: "<テンプレート HEAD の sha>"
applied_at: "2026-07-07T12:34:56Z"
```

`check` はこの `applied_version` とテンプレートの `template.version` を比較して更新有無を判定する。
このファイルはコミットしてよい（次回以降の差分判定に使う）。手動編集は不要。

---

## 6. 逆方向同期（export）

テンプレートのメンテナが、子リポジトリで育てた基盤をテンプレートへ還元する操作。

```bash
git clone https://github.com/KosGit-Dev/dev-orchestration-template.git /tmp/ai-dev-template
python scripts/template_update.py export --template-dir /tmp/ai-dev-template
```

`always_update` は上書き、`add_only` は宛先に無い場合のみコピー、`never_update`/`sample_only` は対象外。
実行後にスクリプトが **汎用化チェックリスト**（ドメイン語 grep・固有 ID 除去・
`template-catalog.yml` の version 更新・`TEMPLATE_CHANGELOG.md` 追記・PR 作成）を表示するので、
必ず実施する。詳細は `.github/instructions/template-sync.instructions.md` の手順 C を参照。

---

## 7. トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| `テンプレートの取得に失敗しました`（exit 2） | URL / ネットワークを確認。オフラインは `--template-url` にローカルクローンのパスを渡す。 |
| `.template-update.yml が見つかりません` | リポジトリルートで実行する。無ければテンプレートから取得する。 |
| dry-run で「未分類」が出る | テンプレートに新規ファイルが増えた合図。`.template-update.yml` に分類を追記して再実行。 |
| ポストチェック / CI が失敗 | 取り込んだ変更と設定の差異。エラーを読み、その場で修正する。 |
| 適用を取り消したい | `git checkout backup-before-template-update-...`（apply が作るバックアップブランチ）。 |

---

## 8. FAQ

**Q. `docs/plan.md` や `README.md` はテンプレートで上書きされますか？**
いいえ。`never_update` のため絶対に上書きされません。改善を取り込みたい場合は手動で差分を確認します。

**Q. `CLAUDE.md` などの入口ファイルは？**
`add_only` です。既存は保護され自動更新されません。改善は[ステップ 5](#3-標準的な流れ)の手動取り込みで反映します。

**Q. 2回目以降も同じ手順ですか？**
はい。`check` → `apply --dry-run` → `apply` の3コマンドだけです。Git 履歴に依存しないため
「Use this template」でも `git clone` でも手順は同じです。

**Q. テンプレートの一部だけ取り込めますか？**
できます。`.template-update.yml` で対象ファイルを `never_update` に移せばスキップされます。

**Q. リポジトリ名が `ai-dev-template` に変わったら参照は壊れますか？**
壊れません。GitHub の自動リダイレクトで旧 URL が動作します。取得元 URL の正本は
`template-catalog.yml` の `template.repository` です。
