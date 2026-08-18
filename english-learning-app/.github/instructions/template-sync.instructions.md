# テンプレート同期 手順書（モデル非依存）

このファイルは、テンプレート（ai-dev-template、旧: dev-orchestration-template）との
同期を **どの AI アシスタント（Claude Code / GitHub Copilot / Codex / その他）でも
同じ手順で** 実行できるようにするための手順書である。

決定的な処理は `scripts/template_update.py` が担う。AI は本手順書に従って
コマンドを実行し、差分をレビューするだけでよい。判断に迷ったら安全側（更新を止めて人へ確認）に倒す。

入口ファイル（`CLAUDE.md` / `AGENTS.md` / `.github/copilot-instructions.md`）から本ファイルが参照される。

---

## トリガーフレーズと対応手順

利用者が次のいずれかのフレーズを述べたら、対応する手順を実行する。

| トリガーフレーズ | 実行する手順 |
| --- | --- |
| 「アップデートを確認」 | 手順 A（check） |
| 「アップデートを適用」 | 手順 B（apply） |
| 「テンプレートに変更を反映」 | 手順 C（export） |

前提: `python`（3.11+）と `git` が使えること。ネットワークからテンプレートを取得できること。
`PyYAML` は任意（無い場合は同梱の簡易パーサーで動作する）。

---

## 手順 A: 「アップデートを確認」（check）

テンプレート最新版と、このリポジトリの適用済み版を比較するだけ。**変更は一切行わない**。

1. 次を実行する。

   ```bash
   python scripts/template_update.py check
   ```

2. 終了コードで結果を判定する。

   | exit code | 意味 | 次のアクション |
   | --- | --- | --- |
   | `0` | 最新（更新不要） | 「最新です」と報告して終了 |
   | `10` | 更新あり | 変更履歴の抜粋を要約して利用者へ提示 |
   | `2` | エラー（取得失敗等） | 失敗理由を報告（下記「失敗時の対処」） |

3. exit code が `10` のときは、出力された **変更履歴（抜粋）** を日本語で要約し、
   「適用しますか？」と確認する。勝手に適用しない。

---

## 手順 B: 「アップデートを適用」（apply）

テンプレートの変更をこのリポジトリへ取り込む。**必ず check → dry-run → 本適用の順**で進める。

1. まず check（手順 A）で更新有無を確認する。更新が無ければここで終了。

2. dry-run で影響範囲を確認する。

   ```bash
   python scripts/template_update.py apply --dry-run
   ```

   - レポートの「未分類」が **0 件** であることを確認する。
   - 「更新（always_update）」「追加（add_only）」の一覧に、上書きされて困る
     プロジェクト固有ファイルが含まれていないか確認する（含まれる場合は
     `.template-update.yml` の `never_update` へ移して分類を直す）。

3. 問題なければ本適用する。

   ```bash
   python scripts/template_update.py apply
   ```

   スクリプトは自動で次を行う。
   - バックアップブランチ作成（`backup-before-template-update-YYYYMMDD-HHMMSS`）
   - テンプレートを一時クローンして選択的にコピー
   - `.template-version.yml`（適用状態）の書き出し
   - ポストチェック（ruff / policy_check。未導入ならスキップ）

4. 検証する。プロジェクトの lint / test を実行して緑にする。

   ```bash
   python -m ruff check .
   python -m ruff format --check .
   python -m pytest -q
   python ci/policy_check.py
   ```

   失敗が出たら、その場で修正する（P-065 fix-on-discovery）。

5. 入口ファイル・`project-config.yml` の手動取り込み（任意）。
   `CLAUDE.md` / `AGENTS.md` / `.github/copilot-instructions.md` / `.claude/settings.json` は
   `add_only`（既存は保護）のため自動更新されない。改善を取り込みたい場合は
   check の差分レポートを基に、一時 clone のテンプレート側ファイルとの手動 diff で
   必要な差分だけ反映する（`docs/UPGRADE_GUIDE.md` を同梱しているリポジトリでは
   同ガイドの該当手順も参照できる）。

6. 変更を確認してコミットする（利用者の指示があれば push）。

   ```bash
   git add -A && git commit -m "chore: テンプレートアップデート適用"
   ```

---

## 手順 C: 「テンプレートに変更を反映」（export・逆方向同期）

このリポジトリで育てたテンプレート基盤（`always_update` / `add_only` 分類のファイル。分類はテンプレート側マニフェストを優先して読む）を
**テンプレート本体へ還元** する。テンプレートのメンテナが行う操作。

1. テンプレートをローカルへクローンし、作業ブランチを作る。

   ```bash
   git clone https://github.com/KosGit-Dev/dev-orchestration-template.git /tmp/ai-dev-template
   git -C /tmp/ai-dev-template switch -c chore/sync-from-project
   ```

   取得元 URL は `template-catalog.yml` の `template.repository` が正本。

2. export を実行して基盤ファイルを反映する。

   ```bash
   python scripts/template_update.py export --template-dir /tmp/ai-dev-template
   ```

   - `always_update` は上書き、`add_only` は宛先に無い場合のみコピーされる。
   - `never_update` / `sample_only`（プロジェクト固有・サンプル）は対象外。

3. スクリプトが出力する **汎用化チェックリスト** を必ず実施する。
   - ドメイン固有語の混入確認（grep）、プロジェクト固有 ID の除去
   - 入口ファイルの第一目的を汎用プレースホルダへ戻す
   - `template-catalog.yml` の `template.version` 更新（新機能は `features` に追記）
   - `docs/TEMPLATE_CHANGELOG.md` に変更エントリを追記
   - テンプレート側で `apply --dry-run` を実行し未分類 0 件を確認

4. テンプレート側でコミット・push・PR 作成（PR 本文は日本語・テンプレート準拠）。

---

## 失敗時の対処

| 症状 | 原因と対処 |
| --- | --- |
| `テンプレートの取得に失敗しました`（check/apply, exit 2） | ネットワーク不通 or URL 誤り。`template-catalog.yml` の `template.repository` を確認。オフラインなら `--template-url` にローカルクローンのパスを渡してもよい。 |
| `.template-update.yml が見つかりません` | リポジトリルートで実行していない、またはマニフェスト未取得。ルートで実行するか、テンプレートから取得する。 |
| dry-run で「未分類」が出る | テンプrepに新規ファイルが増えた。`.template-update.yml` に適切なカテゴリで追記してから再実行する。 |
| `template-catalog.yml に version がありません`（check, exit 2） | テンプレート側のカタログ不備。テンプレートのメンテナへ連絡。 |
| ポストチェック（ruff / policy_check）が NG | 取り込んだ変更とプロジェクト設定の差異。エラーを読み、その場で修正する。 |
| 適用結果を戻したい | `git checkout backup-before-template-update-...`（apply が作るバックアップブランチ）で復元。 |

---

## 補足

- `check` は読み取りのみ・安全。`apply` は変更あり（バックアップは自動）。`export` はテンプレート側のみ変更。
- 版の正本は `template-catalog.yml` の `template.version`。適用状態は子リポジトリの `.template-version.yml`。
- テンプレートのリポジトリ名が将来 `ai-dev-template` に変わっても、旧 URL は GitHub の
  自動リダイレクトで動作するため、既存の設定変更は不要。
