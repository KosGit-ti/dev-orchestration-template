# AI Operating Model

本書は、このリポジトリにおける AI 支援開発の安定した運用モデルを説明する。実行時の正本は `ai/*.yml` であり、本書は人間向けの要約である。

## 日常 3 コマンド

ユーザーは日常開発で通常、次の 3 コマンドだけを使う。

```text
◯◯のプランを見直して直近のプランに入れてほしい。詳細設計と要件定義もあわせて見直すこと。
```

```text
プランのうちバックログの中身をすべて直近のプランに入れて。
```

```text
プランの全実施。
```

意味は `ai/command-router.yml` が定義する。入口文書側で別解釈を増やさない。

## コンテキスト

`daily_development` は日常の無人開発である。エージェントは通常の戦術判断をユーザーへ聞き返さず、リポジトリ文脈で安全かつ可逆な判断を行い、必要に応じて `docs/ai/decision-ledger.md` に記録する。

`governance_change` は別扱いである。AI ハーネス、文書統治、branch/release policy、hook policy、長期 workflow など将来運用に影響する変更では、必要な確認・対話を許可する。

## Shogun 運用モデル（雑依頼の入口）

日常 3 コマンドと governance_change パターンに一致しない雑依頼は `shogun_dispatch` で処理する。現状確認 → intent / constraints / risk tier 分類 → work packet（最大 8）分解 → 既存サブエージェントへの委譲 → 統合 → Shogun Report Contract 報告、という流れであり、単純な質問・軽作業は分解せず直接処理する。司令塔は orchestrator 一本（入口拡張）で、新エージェント実体・新 tier・新自動マージ経路は作らない。merge は全プラン実行モードか、ユーザー発行の scoped lease がある場合のみ自律で進める。詳細は `docs/ai/shogun-operating-model.md`、安全境界は `docs/ai/shogun-safety-boundary.md`、実行配線は `ai/coherence-workflow.yml` / `ai/operation-policy.yml` を正本とする。

## 仕様駆動開発

意味のある作業は次の鎖を保つ。

```text
User Intent
→ Expectation Ledger
→ Requirements
→ Design
→ Spec
→ Implementation
→ Tests
→ Verification
→ Propagation
→ Runtime Smoke
→ Evidence
```

詳細は `ai/sdd-policy.yml` を正本とする。

## 文書統治

文書読込範囲は `ai/context-index.yml`、分類と棚卸し方針は `ai/document-governance.yml` を正本とする。初回棚卸しでは削除せず分類だけを行う。

## レビュー継続性

GitHub Actions / Copilot / review thread が利用可能な場合は GitHub 上の結果を一次判定にする。GitHub 側の課金、runner 未起動、Actions 障害、Copilot quota 等で実行不能な場合だけ、Codex / Claude Code review と代替 CI レビューへ切り替える。Codex / Claude Code review は API キー経路に限定せず、認証済み CLI / review command を使える。Claude API token 経路は Claude Max / Pro と別課金になり得るため既定では使わず、明示 opt-in がある場合だけ available とする。ただし通常実装では変更ファイルだけでなく関連ファイルを探索する `related_context` 以上を要求し、workflow / security / release / red risk では `full_repo_agentic` を要求する。repo-aware fallback は context fingerprint 単位で再利用し、関連ファイル数・投入文字数・探索コマンド数の budget を持つ。リリース判定は `gh pr checks` 等で確認する CI 全チェックの結果を正とし、実 CI failure は代替で上書きしない。

## 一回限りプロンプト

一回限りのセットアッププロンプトは運用者 artifact であり、日常コンテキストには含めない。監査目的でリポジトリに残す場合は `docs/ai/document-inventory.md` で `ARCHIVE` として分類し、`ai/context-index.yml` から除外する。
