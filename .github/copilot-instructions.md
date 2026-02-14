# Copilot Repository Instructions

## Language（最優先）

- すべての成果物（PR タイトル/本文、Issue 本文、レビューコメント、ADR、docs 更新要約）は日本語で書く。
- コードの識別子は英語でよいが、コメント・docstring・説明文は日本語で書く。
- PR 本文は必ず `.github/PULL_REQUEST_TEMPLATE.md` の構成に合わせる。

## Scope & Safety（最優先）

- 禁止操作（P-001）を実装しない。
- API キー/トークン/認証情報/個人情報/実データをコミットしない（P-002）。`.env` はローカルのみ。
- 判断不能な場合は安全側に倒す（P-010: フェイルクローズ）。
- 制約は常に優先する（P-003）。制約回避のコードを書かない。

## Single Source of Truth（正本）

| 正本 | ファイル |
|---|---|
| 要件 | `docs/requirements.md` |
| ポリシー | `docs/policies.md` |
| 制約仕様 | `docs/constraints.md` |
| アーキテクチャ | `docs/architecture.md` |
| 運用手順 | `docs/runbook.md` |
| 重要判断 | `docs/adr/` |
| 計画 | `docs/plan.md` |

### 作業方針

- 会話ログではなく、必要な前提・決定は正本 docs へ反映する。
- `docs/plan.md` の「Next」以外に勝手に着手しない（人間が指示した場合を除く）。
- 正本に矛盾がある場合は修正を提案し、暗黙に無視しない。

## Development Workflow

- 変更は 1PR で理解できる粒度に分割する（P-031）。
- 変更を加えたら必ずローカルまたは CI でテストを通す。
- CI が失敗する PR は提出しない。
- PR には検証手順と結果を必ず記載する（AC-040）。

## Review & Audit Attitude

- 監査（review）は相互合意ではなく独立監査として行う。
- 指摘は「Must / Should / Nice」に分類し、根拠（ファイル/行/再現手順）を添える。
- 不確実な場合は仮説として述べ、確認手段（テスト追加、ログ追加、感度分析）を提案する。
