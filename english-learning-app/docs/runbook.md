# 運用手順（Runbook）

## 1. 開発環境

```bash
cd web
npm ci
npm run dev          # http://localhost:3000
```

初回起動時に `admin` と `shunsuke` が自動生成される。初期パスワードはログイン画面に表示される。

### 品質ゲート（PR 前に必ず通す）

```bash
cd web
npm run lint
npm run type-check
npm run format:check
npm run validate:content   # 完了ゲート G-5
npm test
npm run build              # 静的エクスポート
```

### 静的エクスポートの動作確認

`npm run dev` は開発サーバーであり、配信物とは挙動が違う。配信前は必ずエクスポート後の成果物を確認する。

```bash
cd web
npm run build
cd out && python3 -m http.server 4173
# http://localhost:4173/login/ を開く
```

SPA モードで配信するツール（`serve -s` 等）は使わない。全経路を `index.html` へ書き換えるため、実際の Pages 配信と挙動が変わる。

## 2. リポジトリを独立させる

本ディレクトリは新規リポジトリの中身として作ってある。独立させる手順は次のとおり。

```bash
# 1. GitHub 上で english-learning-app リポジトリを作る（Private 可）
# 2. 本ディレクトリの中身を新しいリポジトリの root へ移す
git init
git add -A
git commit -m "feat: 英語学習アプリの初期実装"
git branch -M main
git remote add origin https://github.com/<owner>/english-learning-app.git
git push -u origin main
```

`.github/workflows/` はリポジトリ root にある必要がある。サブディレクトリのままでは CI も Pages 配信も動かない。

## 3. GitHub Pages で配信する

1. リポジトリの Settings > Pages を開く。
2. Source を **GitHub Actions** にする。
3. `main` へ push すると `deploy-pages.yml` が走り、`https://<owner>.github.io/english-learning-app/` へ出る。

### Private リポジトリの場合

Private リポジトリの GitHub Pages は GitHub Pro 以上が要る。プランが足りない場合は次のいずれかを採る。

- リポジトリを Public にする（学習コンテンツとコードが公開される。パスワードは含まれない）
- Cloudflare Pages を使う。ビルドコマンド `cd web && npm ci && npm run build`、出力ディレクトリ `web/out`、環境変数 `NEXT_PUBLIC_BASE_PATH` は空でよい（ルート配信のため）
- ローカルで `npm run build` し、`web/out` を任意の静的ホスティングへ置く

## 4. iPhone へインストールする

1. Safari で配信 URL を開く。
2. 共有メニューから「ホーム画面に追加」。
3. ホーム画面のアイコンから起動すると、アドレスバーの無い単独アプリとして開く。

Chrome や Firefox からでは PWA として追加できない。iOS では Safari だけが対応する。

## 5. 学習コンテンツを追加する

### 語彙を足す

`web/content/words/level-<N>.json` へ追記する。

```json
{
  "id": "w-l3-041",
  "headword": "……",
  "pos": "verb",
  "ipa": "/…/",
  "definition": "英英定義。そのレベル以下の語彙で書く",
  "examples": ["用例を 1 件以上"],
  "collocations": ["よく共起する形"],
  "synonyms": ["近い語"],
  "antonyms": ["反対の語"],
  "register": "neutral",
  "level": 3
}
```

チェックリスト:

- [ ] `id` は `w-l<level>-<連番>` で、既存と重複しない
- [ ] `definition` にそのレベルより難しい語を使っていない
- [ ] `headword` が他レベルと重複していない
- [ ] 日本語を 1 文字も含んでいない
- [ ] `npm run validate:content` が pass する

### パッセージを足す

`web/content/passages/level-<N>.json` へ追記する。本文中の空所は `{{1}}` `{{2}}` … の連番で書き、`clozes` 配列の順序と一致させる。

チェックリスト:

- [ ] 本文の `{{n}}` と `clozes` の件数・順序が一致している
- [ ] 各 `cloze.options` に `answer` が含まれ、重複が無い
- [ ] 各 `question.answerIndex` が `options` の範囲に収まっている
- [ ] 各 `question.rationale` に、本文のどこからそう読めるかを英語で書いている
- [ ] `npm run validate:content` が pass する

## 6. 初期パスワードを変える

`web/lib/auth/local-provider.ts` の `SEED_ACCOUNTS` を編集する。ここに書く値は公開値であり、秘密を書かない（P-002）。実際のパスワードは初回ログイン後に各利用者が設定する。

## 7. 学習記録を消す

ブラウザの開発者ツールで `localStorage` の `eie:` で始まるキーを削除する。アカウントごと消したい場合は `eie:auth:accounts:v1` も消す。次回起動時に初期アカウントが再生成される。

## 8. トラブルシューティング

| 症状 | 原因 | 対処 |
| --- | --- | --- |
| ログイン後に「読み込み中…」が続く | 初回のアカウント生成で PBKDF2 を 2 回走らせている | 1 秒ほど待つ。2 回目以降は起きない |
| 「この端末ではデータを保存できません」と出る | プライベートブラウジング、または localStorage の容量超過 | 通常ウィンドウで開く。他サイトのデータを整理する |
| 音声の Listen ボタンが出ない | 端末が Web Speech API に未対応 | 対応ブラウザで開く。学習自体は音声なしで続けられる |
| Pages で 404 になる | `basePath` が配信パスと合っていない | `deploy-pages.yml` が `configure-pages` の `base_path` を渡しているか確認する |
| 変更が反映されない | Service Worker のキャッシュ | ハードリロード、または開発者ツールで Service Worker を unregister する |
