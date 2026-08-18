# このディレクトリについて

本ディレクトリは **新しいリポジトリ `english-learning-app` の中身** として作った。`ai-dev-template` のブランチ上に置いてあるのは、本セッションから GitHub リポジトリを新規作成できなかったためである（セッションが接続済みリポジトリに固定されており、リポジトリ作成 API が 403 を返す）。

`ai-dev-template` の `master` へ merge する目的で置いたものではない。テンプレート本体には触れていない（差分はこのディレクトリの追加だけである）。

## 独立させる手順

```bash
# 1. GitHub で english-learning-app リポジトリを作る（Private 可）
# 2. このディレクトリの中身を、新しいリポジトリの root へ移す
git init
git add -A
git commit -m "feat: 英語学習アプリの初期実装"
git branch -M main
git remote add origin https://github.com/<owner>/english-learning-app.git
git push -u origin main
```

`.github/workflows/` はリポジトリ root にある必要がある。このディレクトリのままでは CI も Pages 配信も動かない。

## 中身の由来

- 開発環境（`ai/`・`.github/`・`.claude/`・`.agents/`・`scripts/`・`AGENTS.md`）は `ai-dev-template` を土台にし、`horse-racing-ai` の新しい版で前方移植した。競馬固有の記述はプロジェクト固有の内容へ置き換え、`horse-racing-ai` 専用の証跡コレクタは削除した。
- `web/`・`docs/`・`README.md`・`project-config.yml`・`CLAUDE.md` は本プロジェクト向けに新規に書いた。
