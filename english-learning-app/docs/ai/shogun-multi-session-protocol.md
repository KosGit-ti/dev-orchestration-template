# Shogun 複数セッション層プロトコル（mailbox / lock / nudge-only）

本書は Shogun 運用モデル（[shogun-operating-model.md](shogun-operating-model.md)）の**複数セッション層（任意層）**の正本である。複数ターミナル / tmux で Claude Code・Codex 等の独立セッションを並走させる場合**のみ**使う。

> **単一セッション運用（既定・本リポジトリの通常状態）では本プロトコルを使用しない。** 既定層の並列はネイティブのサブエージェント／バックグラウンド実行で行い、mailbox / locks 実体は作らない（[shogun-operating-model.md](shogun-operating-model.md) §並列実行の二層設計）。

## 設計根拠（複数セッション運用で起きやすい障害と対策）

| 起きやすい障害 | 本プロトコルでの対策 |
|---|---|
| 司令塔への全文集約でコンテキストが圧迫される | 本文は YAML ファイル（mailbox）に置き、セッション間通知は短い wake-up（nudge）のみ。司令塔は要約だけを読む |
| 代理実行の隠蔽・虚偽報告 | `executed_by`（実際に実行したセッション）と `numeric_report`（数値報告）を必須フィールド化 |
| send-keys 本文送信の脆さ | 本文をターミナル注入しない。通知は「mailbox を見よ」の 1 行に限定 |
| ACK なし fire-and-forget での取りこぼし | `ack` 必須。受領確認が無いタスクは未配達として再 nudge する |

## 共有ルート（mailbox / locks 共通の前提）

- mailbox / locks の置き場は**主作業ツリー**配下を全セッション・全 worktree から**絶対パス**で共有する（git worktree 分離した作業ツリー内の `.shogun/` は別実体となり、配達・排他が成立しないため使わない）。
- 共有ルートの導出（POSIX）: `SHOGUN_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.shogun"` → mailbox は `"$SHOGUN_ROOT/mailbox"`、locks は `"$SHOGUN_ROOT/locks"`。以降の手順・nudge のパス表記はこの共有ルートを指す。

## mailbox（タスク通信）

- 置き場: `"$SHOGUN_ROOT/mailbox"`（上記共有ルート・**実体は git 管理外**。`.gitignore` は `.shogun/mailbox/*` + `!.shogun/mailbox/README.md` の「配下 ignore + README 例外」パターンで、README は追跡され実体だけが無視される。書式は `.shogun/` 導入時に作成する `mailbox/README.md` を参照）。
- 1 タスク = 1 YAML ファイル。ファイル名 `YYYYMMDD-HHMM-<from>-<to>-<slug>.yml`（例: `20260611-0930-shogun-worker1-fix-tests.yml`）。
- 本文の正本はファイルであり、nudge には本文を書かない（fire-and-forget 禁止）。

### YAML task format（必須フィールド）

```yaml
task_id: "20260611-0930-fix-tests"     # ファイル名と一致させる
intent: "flaky テストの調査と修正"        # 何をしてほしいか（1〜3 文）
constraints: "scripts/hooks/ 非接触"     # 制約（任意だがキー自体は必須・無ければ "なし"）
risk_tier: "green"                      # ai/operation-policy.yml の tiers を参照（複製しない）
assigned_to: "worker1"                  # 宛先セッションの論理名
executed_by: ""                         # 【必須・受領側が記入】実際に実行したセッション名。
                                        # 空のまま完了報告してはならない（代理実行の隠蔽対策）
ack: ""                                 # 【必須・受領側が記入】受領時刻（ISO8601）。
                                        # ack が無いタスクは未配達扱い＝再 nudge 対象（fire-and-forget 禁止）
numeric_report: {}                      # 【必須・完了時に記入】数値報告の掟＝検証可能な実数値
                                        # 例: {tests_passed: 18, files_changed: 3, exit_code: 0}
status: "new"                           # new / acked / in_progress / blocked / done / rejected
```

- `numeric_report` は「〜を確認した」等の自由文ではなく、**再実行で照合できる実数値**（テスト数・exit code・行数・SHA 等）を入れる（虚偽報告対策）。
- 完了報告（`status: done`）は **`ack`・`executed_by`・`numeric_report` のいずれかが空の場合は無効**とし、司令塔は受理しない（fail-close。`ack` 必須は配達保証〔fire-and-forget 禁止〕の一部であり、done 受理時にも検査する）。

## 排他 lock

- 置き場: `.shogun/locks/`（**実体は git 管理外**。`.gitignore` は `.shogun/locks/*` + `!.shogun/locks/README.md` の「配下 ignore + README 例外」パターンで、README は追跡され実体だけが無視される。書式は `.shogun/` 導入時に作成する `locks/README.md` を参照）。
- **lock 対象（書込前に lock 取得必須）**＝`ai/operation-policy.yml` の **`red_if_touches` 全項目** + 正本 docs + DB + release-manifest（red_if_touches 側が変わったら本リストも追従する）:
  - 正本 docs: `docs/plan.md` / `docs/requirements.md` / `docs/design.md` / `docs/adr/**`
  - リリース統制: `release-manifest.yml` / `.github/workflows/**`
  - データ・実行系（`red_if_touches` 全項目・プロジェクト固有の対象は `project-config.yml` で定義）: 例として `src/**/migrations/**` / `pyproject.toml` / ロックファイル / `.env*` / `configs/**`
- **lock 名（slug）の正規化規則（全セッション共通・排他の成立条件）**: slug ＝対象の正規パスから glob 部分（`/**`・`*`）を除去し、`/` を `-` に置換した文字列（大文字小文字は保持・**`.lock` サフィックスは slug に含めない**）。lock 実体名は **`<slug>.lock`**（`.lock` は手順側で 1 回だけ付与する）。例: `docs/plan.md` → slug `docs-plan.md` → lock 実体 `.shogun/locks/docs-plan.md.lock`、`docs/adr/**` → `docs-adr.lock`、`.env*` → `.env.lock`、`configs/**` → `configs.lock`。**独自の別名を作らない**（別名 lock は排他を成立させない）。
- **lock 置き場**: 上記「共有ルート」のとおり `"$SHOGUN_ROOT/locks"`（主作業ツリー配下・絶対パス共有）。worktree 内の `.shogun/locks/` は使わない。
- **複数対象の lock**: 1 つの work packet が複数の lock 対象を書き換える場合は、**対象ごとに 1 lock を全て取得**する（1 つの lock の meta.yml に複数 targets を書いて代用しない＝他セッションは別対象の lock を独立に取得できてしまうため）。取得順は **slug の辞書順**で固定し（デッドロック防止）、全対象を取得できなければ取得済み lock を解放して待機する。`meta.yml` の `targets` には当該 lock の対象 1 件のみを書く。
- 取得手順（運用手順・専用ツールは実装しない・**POSIX 互換**・**必ず共有ルート経由**）: `mkdir "$SHOGUN_ROOT/locks/<対象slug>.lock"`（`mkdir` は POSIX で原子的＝既存なら失敗。shell 依存の `set -C` を使わず、**相対パス `.shogun/locks/` を直接使わない**）→ `"$SHOGUN_ROOT/locks/<対象slug>.lock/meta.yml"` に `holder`（セッション名）・`acquired_at`・`targets`（当該対象 1 件）を書く → 作業 → `rm -r` で解放。`flock` が使える環境では `flock` でもよい。
- 取得できない場合は**待つか依頼を返す**（強制解除しない）。stale lock（holder セッションが終了済み）は人間判断で解除する。
- 同一ファイル群を変更する work packet を並走させる場合は、lock の代わりに **git worktree 分離**を優先する（lock は正本・DB 等の単一実体のみに使う）。

## nudge（通知）規約

- nudge は「`$SHOGUN_ROOT/mailbox/<file>` を確認せよ」（共有ルートの絶対パス）という **1 行の wake-up のみ**。本文・指示・コード断片を nudge に含めない。
- 配送手段はセッション形態に依存する（tmux なら `send-keys` の 1 行、IDE 並走なら新規ユーザー入力等）。**いずれの手段でも本文はファイルが正本**。
- nudge 送信後、宛先の `ack` 記入をもって配達完了とする。一定時間 `ack` が無ければ再 nudge（最大 3 回）→ それでも無応答なら `status: blocked` として人間へ報告する。

## 既存 Stop hook との非干渉設計（メモ・hook 実装は本タスク非対象）

将来 inbox-watcher 系 hook（未読 mailbox があればセッション終了を block する等）を導入する場合の制約を先に固定する:

1. **既存 Stop hook（`stop_review_guard.py` / `full_plan_completion.py`）の判定を変更しない**。inbox-watcher は独立した追加 hook とし、既存 hook の fail-close 判定（実 CI 失敗・full-plan 未完）と OR で合成する（どちらかが block すれば block）。
2. inbox-watcher の block 条件は「**自セッション宛**（`assigned_to` が自分）かつ `status: new|acked|in_progress` の mailbox が存在する」に限定する。他セッション宛のタスクで block しない（全セッションが互いに block し合うデッドロックの防止）。
3. `stop_hook_active`（2 回目の終了試行）では inbox-watcher も必ず終了を許可する（Stop hook の無限ループ防止と同じ規約）。
4. nudge は PreToolUse / PostToolUse hook を経由しない（hook はガードであり通信路ではない。`docs/hooks-guide.md` の役割分担を維持）。
5. mailbox / locks の実体は `.gitignore` 済みのため、hook が実体ファイルを参照しても review loop / CI の判定（tracked file 基準）に影響しない。

## 衝突防止のまとめ

| 衝突の種類 | 対策 |
|---|---|
| 同一正本ファイルへの同時書込 | lock 対象リスト（上記）＋ lock 取得必須 |
| 同一コード領域の並列実装 | work packet 単位の git worktree 分離（`.shogun/` 導入時に作成する `inbox/README.md` の worktree フィールド） |
| タスクの二重実行 | `ack` + `executed_by` で受領・実行者を一意化 |
| 報告の信頼性 | `numeric_report` 必須（実数値・再検証可能） |
| 終了タイミングの競合 | Stop hook 非干渉設計（上記 1〜3） |

## 安全境界

本プロトコルはセッション間の通信・排他のみを定める。risk tier / hard_block / merge 経路は単一セッション層と同一の正本（[shogun-safety-boundary.md](shogun-safety-boundary.md)・`ai/operation-policy.yml`）に従い、本書は何も緩和しない。lock 対象リストは安全床ファイル群への書込統制の**強化**であり、lock を取得しても tier / ゲートの要件は免除されない。
