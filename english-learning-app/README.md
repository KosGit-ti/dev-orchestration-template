# English in English

英語を英語のまま理解して覚えるための学習アプリ。Web と iPhone（PWA）で動き、将来は Capacitor で iOS / Android のネイティブアプリにできる。

日本語訳は出さない。単語も文章も、英英定義・文脈・用例・音声だけで意味が立ち上がる経路だけを用意している。到達点は TOEFL iBT 120 / TOEIC 990（いずれも満点）相当の語彙・読解水準に置く。

## できること

| モード | 内容 |
| --- | --- |
| 単語 | 英英定義と見出し語を双方向で問う 4 択。解答後に用例・コロケーション・類義語・IPA・音声。SM-2 派生の間隔反復で次回出題日を決める |
| 文章 | 英文パッセージを読み、穴埋め → 読解設問の順に進む。読み上げは 0.6〜1.2 倍で速度調整でき、シャドーイングに使える |
| レベル別 | CEFR A1〜C2 の 6 レベル。24 問のプレースメントで開始レベルを判定し、以後は手でも切り替えられる |

このほかに、ログイン（admin / 学習者）、学習進捗の端末内保存、システム設定に追従するダークモード、オフライン学習を備える。

## レベル体系

| Level | 名称 | CEFR | TOEIC L&R | TOEFL iBT |
| --- | --- | --- | --- | --- |
| L1 | Starter | A1 | 120–400 | 範囲外 |
| L2 | Explorer | A2 | 400–550 | 範囲外 |
| L3 | Bridge | B1 | 550–700 | 42–71 |
| L4 | Fluent | B2 | 700–850 | 72–94 |
| L5 | Advanced | C1 | 850–950 | 95–113 |
| L6 | Mastery | C2 | 950–**990** | 114–**120** |

スコア帯は CEFR 対応表に基づく目安であり、得点保証ではない。根拠は `docs/level-framework.md` に書いた。

## 収録状況

現時点で **語 240 件**（6 レベル × 40）、**パッセージ 12 本**（6 レベル × 2）。

L6 の目安語彙は 15,000 語なので、満点レンジに必要な規模には遠く届いていない。レベル体系と学習の仕組みは満点まで作ってあり、コンテンツの拡充が Phase 2 の継続課題である（`docs/plan.md` BL-001）。収録数は管理画面からも確認できる。

## 始め方

```bash
cd web
npm ci
npm run dev      # http://localhost:3000
```

### 初期アカウント

| ユーザー名 | 初期パスワード | 権限 |
| --- | --- | --- |
| `admin` | `change-me-admin` | 管理者 |
| `shunsuke` | `change-me-shunsuke` | 学習者 |

**この 2 つは公開値であり、秘密ではない。** だから初回ログイン時にパスワード変更を強制し、変更するまで学習画面へ入れないようにしてある。

### iPhone で使う

配信 URL を **Safari** で開き、共有メニューから「ホーム画面に追加」する。アドレスバーの無い単独アプリとして起動し、一度開いたページはオフラインでも読める。iOS では Chrome や Firefox から PWA として追加できない。

## セキュリティについて（重要）

認証と学習記録はブラウザ内で完結する。サーバーへは何も送らない。

**この方式は端末を掌握した攻撃者に対して無力である。** パスワードは PBKDF2-HMAC-SHA256（600,000 回）でハッシュ化して保存するが、それが守るのは「保存領域を覗いた者に素のパスワードを渡さない」ことだけで、進捗データの改竄もアカウントの偽装も端末上では防げない。

学習用途に限る前提であり、金銭・個人情報・業務データを扱う機能をこの認証の内側へ置かない。判断の経緯は `docs/adr/ADR-0001-client-side-auth.md` に残した。

## 技術構成

- Next.js 15（App Router、`output: "export"` の静的エクスポート）/ React 19 / TypeScript 5.7
- Tailwind CSS 3（配色は CSS 変数を単一の正本にし、`.dark` クラスで切り替え）
- 保存は localStorage。読み書きは `lib/storage/kv.ts` の 1 箇所へ集約し、将来のサーバー同期で置き換える範囲を閉じてある
- 音声は Web Speech API（端末の音声合成）。外部 API を使わない
- Service Worker はページを network-first、静的アセットを stale-while-revalidate で扱う
- 実行時の外部通信なし。学習コンテンツはビルド時にバンドルへ埋め込む

## ディレクトリ

```text
web/
  app/          画面（App Router）
  components/   表示専用の部品
  lib/
    auth/       認証境界（AuthProvider interface とクライアント完結実装）
    content/    コンテンツの型・レベル体系・出題生成・プレースメント
    srs/        間隔反復（SM-2 派生）
    progress/   進捗の保存・移行・セッション計画
    storage/    端末内 key-value
    theme/      配色（ライト / ダーク / システム追従）
    tts/        読み上げ
  content/      学習データ（英語のみ。日本語訳を置かない）
  scripts/      コンテンツ検証・アイコン生成
  __tests__/    単体テスト
docs/           要件・設計・レベル体系・制約・計画・運用手順・ADR
ai/             AI 駆動開発の運用ポリシー（ai-dev-template 由来）
```

## 開発時に通すもの

```bash
cd web
npm run lint && npm run type-check && npm run format:check && npm run validate:content && npm test && npm run build
```

`validate:content` は学習コンテンツへの日本語混入と設問の不整合を落とす。警告ではなく fail する。

## 配信

`main` へ push すると GitHub Actions が静的エクスポートを作り、GitHub Pages へ載せる。Settings > Pages の Source を「GitHub Actions」にしておく。

Private リポジトリの Pages は GitHub Pro 以上が要る。プランが足りない場合の代替は `docs/runbook.md` に書いた。

## ドキュメント

| 内容 | ファイル |
| --- | --- |
| 要件と受入条件 | `docs/requirements.md` |
| 実装判断とその理由 | `docs/design.md` |
| レベル体系の根拠 | `docs/level-framework.md` |
| 全体構成 | `docs/architecture.md` |
| 実行時制約 | `docs/constraints.md` |
| ポリシー | `docs/policies.md` |
| 計画と Backlog | `docs/plan.md` |
| 運用手順 | `docs/runbook.md` |
| 重要判断 | `docs/adr/` |
