# レビューレポート検証結果

> 対象レポート: `docs/review-report.md`
> 検証日: 2026-02-23
> 検証方法: 各指摘をソースコードと直接照合

---

## 1. 概要

`docs/review-report.md` の各指摘について、実際のソースコードを参照して正確性を検証した。
大半の指摘はコードで裏付けられたが、1件に事実誤認が確認された。
また、レポートで言及されていない新たな問題が2件発見された。

---

## 2. ✅ 正確な指摘（全12件確認済み）

以下の指摘はすべてコードで正確に裏付けられている。

| レポート箇所 | 指摘内容 | 確認根拠 |
|---|---|---|
| §3① | `policy_check.py` の `SCAN_DIRS` に `.github/` が含まれない | `ci/policy_check.py:22-26` で `src`, `tests`, `scripts` のみ定義 |
| §3② | `sk-[A-Za-z0-9]{32,}` パターンが不完全 | `ci/policy_check.py:65`。ハイフンを含む `sk-ant-api03-` 等にマッチしない |
| §3④ | `production.yml` で `actions/checkout@v4`（タグ指定） | `production.yml` は `@v4`、`ci.yml` は `@34e114876b0b...`（ハッシュ）と不一致 |
| §2① | 全エージェントが `Claude Opus 4.6` | `.github/agents/` 配下の全7ファイルで `model: Claude Opus 4.6 (copilot)` を確認 |
| §4 Lint等 | CI の品質チェックがほぼコメントアウト | `ci.yml` で Lint・Format・Type check・Test がすべてコメントアウト |
| §4⑤ | `production-record` が `echo` のみ | `production.yml:99-106` で `echo` のみ、ファイル書き込みなし |
| §3③ | `git add -A` の使用 | `orchestrator.agent.md` Step 7 のコード例で `git add -A` を確認 |
| §5② | `get_tracer()` が `-> Any` | `src/observability/tracing.py` で `def get_tracer() -> Any:` を確認 |
| §5③ | `init_packages.sh` が空ファイル生成 | `scripts/init_packages.sh` で `touch "$init_file"` による空生成を確認 |
| §6① | `requirements.md` がプレースホルダーのまま | 機能要件（FR-001, FR-010）が `<!-- ... -->` コメントのみ |
| §6③ | `constraints.md` の閾値が `<!-- 値 -->` | 具体的な数値定義がテンプレートのまま |
| §4② | `concurrency` 設定がない | `ci.yml` / `production.yml` 両方に `concurrency:` なし |

---

## 3. ❌ 事実誤認の訂正

### レポートの該当記述（§4 CI/CDパイプライン）

> テンプレートの性質上コメントアウトは理解できるが、**bootstrap.sh 実行後に自動的に
> アンコメントされる仕組みがないことが問題**。

### 訂正

**この記述は事実誤認。** `scripts/bootstrap.sh` を確認すると以下の実装が存在する。

```bash
# bootstrap.sh L256-312（抜粋）
CI_FILE="$REPO_ROOT/.github/workflows/ci.yml"

if [ -f "$CI_FILE" ] && [ "$LANGUAGE" = "python" ]; then
    cat > "$CI_FILE" << 'CIFILE'
    # ... Lint・Format check・Type check・Test を含む完全な ci.yml を生成 ...
    CIFILE
fi
```

Python プロジェクトに対して `bootstrap.sh` を実行すると、Lint・Format check・Type check・Test
がすべて有効な状態の `ci.yml` に**丸ごと上書き生成**される。「アンコメントされない」は誤り。

---

## 4. 補足：レポートで未言及の新問題

### 問題A：bootstrap.sh 実行後にハッシュ固定が失われる

`bootstrap.sh` が生成する `ci.yml` 内の GitHub Actions バージョン指定（L274/L276/L284）:

```yaml
- uses: actions/checkout@v4        # タグ指定
- uses: actions/setup-python@v5    # タグ指定
- uses: actions/cache@v4           # タグ指定
```

テンプレートの `ci.yml` が持つハッシュ固定（`actions/checkout@34e114876b0b...`）が、
bootstrap 実行後に**タグ指定へ逆行する**。

これはレポートの P0 指摘（`production.yml` のピン留め漏れ）と同種の問題が、
CI の基盤である `ci.yml` 生成プロセス自体に内包されていることを意味する。
つまり P0 の対処方針として「`bootstrap.sh` 内の Action バージョンをハッシュ固定に変更する」
対応が追加で必要になる。

### 問題B：staging.yml のタグ指定がレポートで未言及

`staging.yml`（bootstrap.sh では生成されないテンプレートファイル）でも `actions/checkout@v4`
（タグ指定）を3箇所で使用している。

```
L23  - uses: actions/checkout@v4
L70  - uses: actions/checkout@v4
L98  - uses: actions/checkout@v4
```

`production.yml` と同じ問題を抱えているが、レポートでは `staging.yml` への言及がない。

---

## 5. 総合判定

| 区分 | 件数 | 判定 |
|---|---|---|
| ✅ 正確な指摘 | 12件 | コードで全件裏付け確認済み |
| ❌ 事実誤認 | 1件 | §4「bootstrap でアンコメントされない」→ 誤り |
| 補足（未言及の新問題） | 2件 | bootstrap 後のハッシュ固定消滅 / staging.yml 未言及 |

レポートの事実誤認は1箇所のみで、全体的な指摘品質は高い。ただし §4 の誤認により、
問題の性質が「CI 未機能」から「セキュリティ強化の逆行（bootstrap 後にタグ指定に戻る）」
へと変わるため、P0 の対処方針を以下のように更新する必要がある。

### 更新後の P0 対処方針

| 旧（レポート） | 新（訂正後） |
|---|---|
| CI の品質チェックをコメントアウトから復活させる（bootstrap で自動有効化する仕組みを追加） | bootstrap.sh が生成する ci.yml 内の Action バージョン（checkout/setup-python/cache）をハッシュ固定に変更する |
| production.yml の `actions/checkout@v4` をハッシュ固定 | production.yml・staging.yml の `actions/checkout@v4` をハッシュ固定（staging.yml を追加） |
