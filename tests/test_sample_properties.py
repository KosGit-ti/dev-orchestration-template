"""Property-based testing のサンプルテスト。

Hypothesis を用いたプロパティ定義の記述パターンを示す教材である。

このファイルが示すテストパターン:
  1. **事後条件テスト**: 任意の有効な入力に対して出力が契約を満たすこと
  2. **単調性テスト**: 入力の大小関係が出力に保存されること
  3. **不変条件テスト**: データクラスの生成時に不変条件が強制されること

CUSTOMIZE: ``ExampleEntity`` / ``process`` のインポート先を
    プロジェクトの実際のモジュールに変更すること。
"""

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

# CUSTOMIZE: インポート先をプロジェクトの実際のモジュールに変更すること。
from sample.example_module import ExampleEntity, process

# ---------------------------------------------------------------------------
# 戦略（Strategies）: テストデータの生成規則
# ---------------------------------------------------------------------------

# 有効な value の戦略: ExampleEntity の不変条件（>= 0.0）を満たす浮動小数点数
valid_values = st.floats(
    min_value=0.0,
    max_value=1e10,
    allow_nan=False,
    allow_infinity=False,
)

# 有効な multiplier の戦略: process の事前条件（> 0.0）を満たす浮動小数点数
valid_multipliers = st.floats(
    min_value=0.001,
    max_value=1e5,
    allow_nan=False,
    allow_infinity=False,
)


# ---------------------------------------------------------------------------
# プロパティテスト: process 関数
# ---------------------------------------------------------------------------


class TestProcessProperties:
    """``process`` 関数のプロパティテスト。

    個別の入力値ではなく、関数が満たすべき「性質（Property）」を定義し、
    Hypothesis が自動生成する多数の入力で検証する。
    """

    @given(value=valid_values, multiplier=valid_multipliers)
    @settings(max_examples=200)
    def test_postcondition_non_negative(self, value: float, multiplier: float) -> None:
        """事後条件のテスト: 出力は常に 0.0 以上であること。

        任意の有効な入力（value >= 0, multiplier > 0）に対して、
        process の戻り値が事後条件を満たすことを検証する。
        """
        entity = ExampleEntity(name="test", value=value)
        result = process(entity, multiplier)
        assert result >= 0.0

    @given(
        value=valid_values,
        m1=valid_multipliers,
        m2=valid_multipliers,
    )
    @settings(max_examples=200)
    def test_monotonicity(self, value: float, m1: float, m2: float) -> None:
        """単調性のテスト: multiplier が大きいほど結果も大きいこと。

        同一の entity に対して、m1 < m2 ならば
        process(entity, m1) <= process(entity, m2) が成立する。
        """
        assume(m1 != m2)
        entity = ExampleEntity(name="test", value=value)
        r1 = process(entity, m1)
        r2 = process(entity, m2)
        if m1 < m2:
            assert r1 <= r2
        else:
            assert r1 >= r2

    @given(multiplier=valid_multipliers)
    @settings(max_examples=200)
    def test_zero_value_yields_zero(self, multiplier: float) -> None:
        """ゼロ入力のテスト: value=0.0 の場合、結果は常に 0.0 であること。"""
        entity = ExampleEntity(name="test", value=0.0)
        result = process(entity, multiplier)
        assert result == 0.0


# ---------------------------------------------------------------------------
# プロパティテスト: ExampleEntity の不変条件
# ---------------------------------------------------------------------------


class TestExampleEntityInvariants:
    """``ExampleEntity`` の不変条件テスト。

    不正な入力でインスタンス生成を試み、
    不変条件がアサーションで正しく拒否されることを検証する。
    """

    @given(value=st.floats(max_value=-0.001, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100)
    def test_negative_value_rejected(self, value: float) -> None:
        """負の value でのインスタンス生成はアサーションエラーとなること。"""
        with pytest.raises(AssertionError, match="value must be >= 0.0"):
            ExampleEntity(name="test", value=value)

    def test_empty_name_rejected(self) -> None:
        """空文字列の name でのインスタンス生成はアサーションエラーとなること。"""
        with pytest.raises(AssertionError, match="name must not be empty"):
            ExampleEntity(name="", value=1.0)

    @given(value=valid_values)
    @settings(max_examples=100)
    def test_valid_construction(self, value: float) -> None:
        """有効な入力でのインスタンス生成は成功すること。"""
        entity = ExampleEntity(name="valid", value=value)
        assert entity.name == "valid"
        assert entity.value == value


# ---------------------------------------------------------------------------
# プロパティテスト: process 関数の事前条件
# ---------------------------------------------------------------------------


class TestProcessPreconditions:
    """``process`` 関数の事前条件違反テスト。"""

    @given(
        value=valid_values,
        multiplier=st.floats(max_value=0.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_non_positive_multiplier_rejected(self, value: float, multiplier: float) -> None:
        """multiplier <= 0.0 のとき事前条件違反でアサーションエラーとなること。"""
        entity = ExampleEntity(name="test", value=value)
        with pytest.raises(AssertionError, match="multiplier must be > 0.0"):
            process(entity, multiplier)
