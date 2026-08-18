# Shogun 運用モデル（正本）

本書は Shogun 運用モデルの運用正本である。実行時の配線は `ai/command-router.yml`（default 接続）・`ai/coherence-workflow.yml`（`shogun_dispatch` steps）・`ai/operation-policy.yml`（`shogun_report_contract_v1` variant・`capability_lease`）・`ai/context-index.yml`（読込モード）を正とし、本書はそれらが参照する運用定義（役割対応・二層並列・lease 慣行・報告契約）を一箇所に集約する。

## 用語

- 本リポジトリでは**「ハーネス」は AI プロバイダ（Claude Code / Codex / GitHub Copilot）**を指す（harness-symmetric-review）。外部の類似記事でいう "AI Harness" は本リポジトリでは**「Shogun 運用モデル」**と呼び、ディレクトリは `.shogun/` を使う。新概念に "harness" という語を使わない。
- **work packet**・**Capability Lease** は外部記事由来ではなく本モデルで定義した運用語である。

## 役割と既存サブエージェント対応（論理役割・実体新設なし）

Shogun / Karo / Gunshi / Ashigaru は**委譲パターン名**であり、新しいエージェント実体を新設しない。司令塔は一本（既存 orchestrator の入口拡張）である。

| 論理役割 | 責務 | 対応する既存実体（委譲先） |
| --- | --- | --- |
| Shogun | 雑依頼の解釈・work packet 分解・委譲・統合・Shogun Report Contract 報告 | orchestrator（`.github/agents/orchestrator.agent.md` の Shogun 入口プロトコル節） |
| Karo | キュー / lock の整理・進行管理・状態の圧縮翻訳 | orchestrator 内部処理 + implementer-single-file（軽量な台帳更新） |
| Gunshi | 整合性・リスク・品質の判断 | auditor-spec / auditor-security / auditor-reliability / release-manager / Plan |
| Ashigaru | 並列 worker（実装・テスト・探索） | implementer / implementer-single-file / test-engineer + 各ハーネスのネイティブ探索機能（Claude Code の Explore 等・roster 外のハーネス機能） |

固定役割表は作らない（現実と乖離する drift 源。worker は動的タスク割当）。

## work packet（確定テンプレート）

- 雑依頼を分解した委譲単位。**最大 8**。各 packet は目的・委譲先パターン・モデル割当・worktree 隔離要否・完了条件（機械検証可能）を持つ。**書式の正本（確定テンプレート）は `.shogun/` 導入時に作成する `inbox/README.md`**（本モデルを実際に採用する際に整備する）。分解の実例は [shogun-decomposition-examples.md](shogun-decomposition-examples.md)（架空の一般例）を参照。
- テンプレートは intent / constraints（ユーザー記入）と reclassify / risk_tier / capability_lease / required_locks / work_packets / promotion_gate / safety_floor / status / 報告（Shogun 記入）で構成し、`shogun_dispatch` の steps（再分類・tier 分類・lease 確認・分解・委譲・安全床検証・報告）に 1:1 対応する。
- モデル割当は Bloom 風の論理 3 段（軽量＝理解・抽出 / 標準＝実装・テスト / 上位＝設計・リスク・監査）を**サブエージェント定義の model 指定への写像**として扱う（プロバイダ固有モデル名を書かない）。対応表はテンプレート内に置く。この写像方針は `ai/operation-policy.yml` の `subagent_delegation.model_tiering`（`model_tiering_v1`）を正本とする（[docs/orchestration.md §モデル階層委譲](../orchestration.md#モデル階層委譲model_tiering_v1)）。
- **分解の規模は依頼の規模に比例させる**。単純な質問・単発の軽作業は分解せず直接処理し、報告も既定の `caveman_lite_v1` で簡潔に返す。Shogun Report Contract は実作業を伴う dispatch にのみ適用する。

## 並列実行の二層設計

### (a) 単一セッション層（既定）

物理並列は各ハーネスのネイティブなサブエージェント／バックグラウンド実行で行う。外部の類似事例で語られる技術は、本リポジトリでは次のようにネイティブ機能へ写像する。

| 技術領域 | 本リポジトリでの写像 |
| --- | --- |
| Bloom's Taxonomy ルーティング | サブエージェント定義の model 指定（軽作業=軽量、実装=標準、設計/リスク=上位相当） |
| エージェント別ブランチ隔離 | git worktree 分離（ネイティブ機能） |
| 司令塔の圧死対策＝全文を集約しない | サブエージェント応答を `docs/orchestration.md` §4 の構造化要約スキーマで返させる（既存契約の再利用） |

tmux 多窓・mailbox 実体・lock 実体はこの層では**使わない**。

### (b) 複数セッション層（任意）

複数ターミナル / tmux で Claude Code・Codex 等を並走させる場合のみ、mailbox / lock / nudge-only プロトコル（本文は YAML ファイル・通知は短い wake-up のみ・ACK なし fire-and-forget 禁止）を使う。**正本は [shogun-multi-session-protocol.md](shogun-multi-session-protocol.md)**＝YAML task format（`executed_by`・`ack`・`numeric_report` 必須）・排他 lock 対象・nudge 規約・既存 Stop hook との非干渉設計。

### 期待値の正直な明記

複数セッション並列の性能は**複数の定額契約・複数 CLI のレート枠分散が前提**である。単一サブスクリプションの単一セッションでは並列はレート枠を共有するため、**導入初期の主価値は「雑依頼の構造化・報告契約・整合性検査」であり、スループット倍増ではない**。

## Capability Lease（軽量慣行）

新しい機構ではなく既存慣行（ユーザーの明示指示による scoped 認可）の形式化である（`ai/operation-policy.yml` の `capability_lease`）。

- 成立条件：`docs/plan.md` の自動実行 優先順注記、または `docs/ai/decision-ledger.md` に「発行者（granted_by＝ユーザーの明示指示の引用・日付）/ scope / 許可内容（granted）/ 除外（excluded）/ 失効条件（expiry_condition）」を 1 ブロックで記録する。**発行者はユーザーのみ（AI の自己記録では成立しない）**。
- lease 不可：**`safety.hard_block` の全項目**（代表例: secret 値出力・実資金の移動・重要な安全装置の緩和。項目集合の正本は `ai/operation-policy.yml`）。
- マージ自律性：全プラン実行モード、または「Shogun 判断で進めて」「マージまで進めて」等の明示句による scoped lease がある場合のみ自律マージへ進む。それ以外は人間判断。
- lease の記録例は `docs/ai/decision-ledger.md` の記入例を参照。

## Shogun Report Contract（shogun_report_contract_v1）

実作業を伴う `shogun_dispatch` の最終報告は次の 8 項目で行う（`ai/operation-policy.yml` `reporter_communication.variants` が配線正本。既定の `caveman_lite_v1` を置換しない＝「既定＋scoped variant」の関係）：

1. 状態: Green / Yellow / Red / Blocked
2. 完了したこと（PR / commit / artifact / test / docs）
3. 重要な発見
4. 残 blocker（AI で進められる / 人間判断が必要 / 外部待ち に分類）
5. AI が次に自律で進められること
6. 人間判断が必要なこと
7. 推奨する次の一手を 1 つ
8. ユーザーがそのまま送れる短い指示候補（例:「1で進めて」「Shogun判断で続けて」「そこは保留」）

## 導入方針（採用 / 翻案 / 保留 / 対象外）

本モデルを新しい環境へ導入する際は、外部の類似運用事例で語られる技術を次の 4 区分で判定する。

- **採用**：既存機構にそのまま乗せられるもの（例: 雑依頼の司令塔入口、報告契約、既存 instructions ファイル群の役割定義）。
- **翻案**：実行環境のネイティブ機能へ写像するもの（例: Bloom ルーティング→サブエージェント定義の model 指定、mailbox/lock→複数セッション層限定、タスク永続化→既存 flag / ledger 機構）。
- **保留（Backlog）**：将来の拡張候補として `docs/plan.md` の Backlog に登録するだけに留め、実装しないもの（例: 双方向通知、リモート指揮、音声入力、モデル別予算ゲート等）。
- **対象外**：導入しない技術と理由を明記するもの（例: 単一サブスクリプション環境では前提が成立しない多重 CLI 並列、既存 P-001〜P-050 と重複する規律演出リスト等）。

この 4 区分の判定結果は、必要になった時点で本書または `docs/plan.md` の Backlog へ追記する。

## 昇格（promotion gate）— チェックリスト

Shogun 出力（work packet 成果）を PR 化し main へ昇格させる経路の**主ゲートは既存 3 段＝pre-PR critical review → tier 分類 → release-manager**（下表の区分「主」＝#1 / #2 / #5）であり、残りの行（区分「補助」）は 3 段の前後で従来から必須の既存チェック（レビューループ・CI 全チェック等）を昇格順に並べたものである。本節はその対応付けの文書化のみで、**新ゲート・新 required check・新スクリプトを一切作らない**（根拠は `docs/adr/` の重要判断記録）。

### 昇格チェックリスト（既存機構との対応）

| # | 区分 | チェック | 既存機構（正本） | 実行・確認方法 |
| --- | --- | --- | --- | --- |
| 0 | 補助 | work packet の完了条件が実数値で充足している | 確定テンプレート（`.shogun/` 導入時に作成する `inbox/README.md`） | packet の「完了条件」と成果を突合（数値報告の掟） |
| 1 | **主①** | **pre-PR critical review** PASS | `ai/pre-pr-review-policy.yml` / `scripts/ai/run_pre_pr_critical_review.py` | `uv run python scripts/ai/run_pre_pr_critical_review.py --changed-only --allow-should`（クリーン worktree で実行し変更ファイル数を実 diff と整合させる） |
| 2 | **主②** | **tier 分類**（green / yellow / red）確定 | `ai/operation-policy.yml` の `release_to_main`（tiers / `red_if_touches`） | 変更ファイル集合を `red_if_touches` と突合 |
| 3 | 補助 | push 後 AI レビューループ完了 | `.github/instructions/review-loop.instructions.md` | 最大 3 ラウンド・全スレッド返信→解決（**resolve は必ず返信後**）・Round 3 後の非ブロッキングは Backlog 化・停止宣言 |
| 4 | 補助 | CI 全チェック成功 | `gh pr checks` | 全 required check の pass を確認する |
| 5 | **主③** | **release-manager** 判定 MERGE 承認 | `.github/agents/release-manager.agent.md` | 受入条件（`docs/requirements.md`）の実体照合・一次証跡検証（受入条件の正本は requirements 側） |
| 6 | 補助 | merge 実行（自律可否は正本に従う） | `ai/coherence-workflow.yml` の `shogun_dispatch` 配下 `merge_autonomy` | 単発モード＝ユーザー認可・全プラン実行モード＝規約どおり |

新設ゼロの確認＝本チェックリストの全行が既存正本への参照で構成される。

## 効果測定

導入後の効果は、以下の指標を軽量に測定できる（closeout / ledger / GitHub 実測から導出可能な値のみ）。

| 指標 | 定義 | 出典の取り方 |
| --- | --- | --- |
| 指示短文化 | ユーザー指示の文字数（導入時の長文指示 vs 運用時の定型短文） | `docs/ai/decision-ledger.md`（lease 記録）+ 実測の転記 |
| 自律完遂率 | dispatch（タスク）のうち、人間介入が**設計上の認可ポイントのみ**（post / merge 個別認可・AskUserQuestion）で report まで到達した割合。是正指示（やり直し・対応漏れ指摘）があれば非完遂と数える | plan closeout + 会話記録 |
| ゲート停止回数 | hard_block 発動数 / fail-close（ゲート fail）からの再実行数と原因 | closeout の知見欄 |
| 補助指標 | PR 数・merge までの時間・レビュースレッド処理数・追加テスト数・安全床 diff（hard_block 項目数 / red_if_touches パターン数の前後比較） | `gh pr view` / `gh api graphql` / pytest collect / operation-policy スナップショット |

実測値は `docs/ai/decision-ledger.md` や `docs/ai/execution-ledger.md` に記録し、本節には具体的な実測記録を置かない（テンプレートには実運用データが存在しないため）。

## 撤去手順

本モデルを撤去する場合は、`ai/command-router.yml` の `default.on_unmatched_user_request` を `shogun_dispatch` 導入前の状態（例: `classify_with_repository_context`）へ戻し、関連 PR を revert する。ランタイム実体（`.shogun/runtime/**` 等）は git 管理外のため履歴残留物は無い。撤去判断は `docs/adr/` に重要判断として記録することを推奨する。

## 安全境界

[docs/ai/shogun-safety-boundary.md](shogun-safety-boundary.md) を参照（既存安全床への参照のみ・複製しない）。
