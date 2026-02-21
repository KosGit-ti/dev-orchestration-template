# コード品質基準ガイド

> プロジェクトのコード品質を担保するためのツール設定・開発手法・テスト戦略の標準ガイド

---

## 1. コード品質基準の概要

このテンプレートは以下の3つの柱でコード品質を担保する。

| 柱 | ツール / 手法 | 目的 |
|---|---|---|
| **型安全性** | mypy strict | コンパイル時の型エラー検出 |
| **コードスタイル** | ruff | リンティング + フォーマッティング |
| **テスト品質** | pytest + Hypothesis | プロパティベーステストによるカバレッジ品質の向上 |

### 品質レベルの定義

- **mypy strict**: `strict = true` を有効化。すべての関数に型アノテーションを必須とし、`Any` 型の使用を明示的に管理する
- **ruff**: PEP 8 準拠に加え、`N`（命名規約）、`UP`（Python バージョン互換性）、`B`（バグ予防）、`SIM`（簡素化）、`TCH`（型チェック最適化）ルールを適用
- **Coverage quality**: 行カバレッジ（量）だけでなく、テストが実際にコードの意味的な振る舞いを検証しているかを評価する。境界値テスト、Property-based testing の活用が指標となる

---

## 2. Spec-Driven Development の実践ガイド

### Design by Contract（契約プログラミング）

Spec-Driven Development では、コードを書く前に各関数の **契約** を定義する。

| 契約の種類 | 説明 | 記述場所 |
|---|---|---|
| **事前条件（Precondition）** | 関数が正しく動作するために呼び出し側が保証すべき条件 | docstring + `assert` |
| **事後条件（Postcondition）** | 関数が正常終了した場合に保証される結果の条件 | docstring + `assert` |
| **不変条件（Invariant）** | クラスのインスタンスが常に満たすべき条件 | docstring + `__post_init__` |

### docstring への記述パターン

```python
def process(entity: ExampleEntity, multiplier: float) -> float:
    """エンティティの value に multiplier を適用し結果を返す。

    事前条件 (Precondition):
        - multiplier は 0.0 より大きいこと

    事後条件 (Postcondition):
        - 戻り値は 0.0 以上であること

    Args:
        entity: 処理対象のエンティティ。
        multiplier: 乗数。0.0 より大きいこと。

    Returns:
        計算結果（0.0 以上）。

    Raises:
        AssertionError: 事前条件または事後条件に違反した場合。
    """
    assert multiplier > 0.0, f"multiplier must be > 0.0, got {multiplier}"

    result = entity.value * multiplier

    assert result >= 0.0, f"postcondition failed: result={result}"
    return result
```

### 型アノテーションの原則

- **すべての関数引数と戻り値に型を付与する**（mypy strict 準拠）
- `Any` は明示的な理由がある場合のみ使用し、コメントで理由を記載する
- `dataclass` を活用してドメインオブジェクトを型安全に定義する

### サンプルコードの参照先

型アノテーション・docstring・アサーションの記述サンプルは以下を参照:
- `src/sample/example_module.py` — データクラスと契約付き関数のサンプル

---

## 3. Property-based Testing

### 概要

Property-based testing は、テストケースを手動で個別に記述するのではなく、
コードが満たすべき **「性質（Property）」** を定義し、
Hypothesis フレームワークが自動的に多数の入力データを生成して検証する手法である。

従来の Example-based testing との比較:

| 観点 | Example-based | Property-based |
|---|---|---|
| テストケース数 | 手動で数個〜数十個 | 自動で数百〜数千個 |
| 境界値の発見 | 開発者の経験に依存 | フレームワークが自動探索 |
| 保守コスト | ケース追加のたびに増加 | プロパティ定義は安定 |

### セットアップ

```bash
# 依存ライブラリのインストール
uv sync --extra testing
```

### 基本的なテストパターン

#### パターン1: 事後条件テスト

```python
from hypothesis import given, strategies as st

@given(value=st.floats(min_value=0.0, max_value=1e10, allow_nan=False))
def test_result_is_non_negative(value: float) -> None:
    """任意の有効入力に対して、出力が非負であること。"""
    result = some_function(value)
    assert result >= 0.0
```

#### パターン2: 単調性テスト

```python
from hypothesis import given, strategies as st, assume

@given(x1=st.floats(...), x2=st.floats(...))
def test_monotonicity(x1: float, x2: float) -> None:
    """x1 < x2 ならば f(x1) <= f(x2) であること。"""
    assume(x1 < x2)
    assert some_function(x1) <= some_function(x2)
```

#### パターン3: 不変条件の防御テスト

```python
import pytest
from hypothesis import given, strategies as st

@given(value=st.floats(max_value=-0.001))
def test_invalid_input_rejected(value: float) -> None:
    """不正な入力がアサーションエラーで拒否されること。"""
    with pytest.raises(AssertionError):
        SomeEntity(value=value)
```

### サンプルテストの参照先

Property-based testing の完全なサンプルは以下を参照:
- `tests/test_sample_properties.py` — 事後条件・単調性・不変条件テストのサンプル

---

## 4. CI パイプラインでの品質チェック

### 実行コマンド一覧

```bash
# --- 型チェック（mypy strict） ---
uv run mypy src/ tests/

# --- リンター（ruff check） ---
uv run ruff check .

# --- フォーマットチェック（ruff format） ---
uv run ruff format --check .

# --- テスト実行（pytest） ---
uv run pytest -q --tb=short

# --- テスト実行 + カバレッジ ---
uv run pytest --cov=src --cov-report=term-missing

# --- Property-based testing のみ実行 ---
uv run pytest tests/test_sample_properties.py -q
```

### CI ワークフローでの統合

`.github/workflows/ci.yml` の品質チェックセクションで上記コマンドを実行する。
テンプレートでは `{{RUN_PREFIX}}` プレースホルダーが使用されており、
`project-config.yml` の `toolchain.run_prefix` で置換される。

---

## 5. PyVeritas に関する注記

PyVeritas は、LLM を用いて Python コードを C にトランスパイルし、CBMC による有界モデル検査を適用する
形式検証フレームワークである。

> **参照情報**: 論文の正確な arXiv ID ・ URL は [arXiv](https://arxiv.org/) で「PyVeritas formal verification」を検索して確認すること。

### 現時点での制限事項

- **学術研究プロトタイプ** であり、プロダクション品質のツールではない
- 適用可能なプログラムは **概ね15〜50行以内の小規模関数** に限定される
- 複雑なデータ構造、外部依存、非同期処理を含むコードには適用できない
- アクティブに開発中であり、API やサポート範囲が変更される可能性がある

### 推奨方針

- **CI 必須要件としての採用は推奨しない**
- 小規模な純粋関数（数学的計算、バリデーションロジック等）に限定的に実験適用することは可能
- Property-based testing（Hypothesis）で十分なカバレッジが得られる場合は、そちらを優先する

---

## ファイル構成

```
src/
├── sample/
│   ├── __init__.py              # パッケージ初期化（空ファイル）
│   └── example_module.py        # 型アノテーション・docstring・アサーションのサンプル
tests/
├── __init__.py                  # テストパッケージ初期化
└── test_sample_properties.py    # Property-based testing のサンプルテスト
docs/
└── quality-guide.md             # 本ガイド
```
