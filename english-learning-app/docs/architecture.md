# アーキテクチャ

## 全体像

サーバーを持たない。ブラウザ内で完結する単一の静的サイトである。

```text
[ブラウザ]
  ├─ Next.js App Router（静的エクスポート済み HTML/JS）
  │    ├─ app/            画面
  │    ├─ components/     表示専用の部品
  │    └─ lib/            ロジック層（下記）
  ├─ localStorage         アカウント・セッション・学習進捗
  ├─ Web Speech API       読み上げ（端末の音声合成）
  └─ Service Worker       オフライン用キャッシュ
        ↑
[GitHub Pages（静的配信のみ）]
```

ネットワークは初回のアセット取得にしか使わない。学習中の通信は発生しない。

## レイヤと依存の向き

```text
app/ ──▶ components/ ──▶ lib/util/
  │                        ▲
  └──────▶ lib/auth/ ──────┤
           lib/content/ ───┤
           lib/progress/ ──┤──▶ lib/storage/
           lib/srs/  ──────┘
           lib/tts/
           lib/theme/
```

- `lib/` は React へ依存しない純関数を基本とする。React が必要なもの（context / hook）だけ `.tsx` にする。
- 保存へ触れるのは `lib/storage/kv.ts` だけである。将来サーバー同期へ移すとき、置き換える範囲をここへ閉じ込める。
- `lib/srs` と `lib/content` は時刻を読まない。`now: Date` を引数で受け取るため、テストが決定的になる。

## 主要モジュール

| モジュール | 責務 | 差し替え可能性 |
| --- | --- | --- |
| `lib/auth/types.ts` | `AuthProvider` interface（認証境界の契約） | — |
| `lib/auth/local-provider.ts` | localStorage 実装 | Supabase 実装へ差し替える口 |
| `lib/auth/crypto.ts` | PBKDF2 によるハッシュと検証 | サーバー認証へ移れば不要になる |
| `lib/content/levels.ts` | レベル体系の正本 | — |
| `lib/content/quiz.ts` | 4 択問題の生成と誤答選択 | — |
| `lib/content/placement.ts` | プレースメントの構成と判定 | — |
| `lib/srs/sm2.ts` | 間隔反復のスケジューリング | FSRS 等へ差し替え可能 |
| `lib/progress/store.ts` | 進捗の読み書きとスキーマ移行 | 保存先を替えても関数境界は同じ |
| `lib/progress/session.ts` | 1 セッション分の出題計画 | — |
| `lib/storage/kv.ts` | 端末内 key-value | IndexedDB / サーバーへ差し替え |

## データの持ち方

学習コンテンツはビルド時に JS バンドルへ埋め込む。理由は次のとおり。

- 実行時 fetch を無くせば、オフライン動作が Service Worker の作り込みに依存しなくなる。
- 静的エクスポートでは動的な API ルートを持てない。
- 現在の規模（240 語 / 12 パッセージ）では、埋め込みによるバンドル増加は問題にならない。

規模が数千語を超えたらこの判断は成り立たなくなる。レベルごとの分割ロードへ切り替える閾値は `docs/plan.md` の BL-001 で扱う。

## 配信

`NEXT_PUBLIC_BASE_PATH` で `basePath` / `assetPrefix` を切り替える。GitHub Pages のプロジェクトサイトでは `/<repo>` が入り、独自ドメインのルート配信では空になる。manifest 内のパスは相対にしてあるため、どちらでも解決する。

## ネイティブアプリ化の見通し

Capacitor は「静的な web アセットを WebView で開く」構成である。本アプリは既に静的エクスポート済みの `web/out` を出すため、`capacitor.config.ts` の `webDir` をそこへ向ければ包める。サーバー通信も動的ルートも無いので、追加の分離作業は要らない。

App Store 配布には Apple Developer Program（年 99 ドル）が要る。Phase 3 で扱う。
