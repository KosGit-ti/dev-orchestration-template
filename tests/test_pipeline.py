"""パイプラインのテスト。

Pipeline の E2E テストと制約評価の境界値テスト。
"""

from __future__ import annotations

import math

import pytest

from my_package.core.config import PipelineConfig
from my_package.core.exceptions import ConstraintViolationError
from my_package.core.types import PipelineInput, Status
from my_package.domain.constraints import check_constraints
from my_package.domain.pipeline import Pipeline

# ---------------------------------------------------------------------------
# 制約チェックテスト
# ---------------------------------------------------------------------------


class TestConstraints:
    """制約評価のテスト。"""

    def test_valid_input_passes(self) -> None:
        """有効な入力は制約チェックを通過する。"""
        inp = PipelineInput(name="test", values=(1.0, 2.0, 3.0))
        config = PipelineConfig()
        check_constraints(inp, config)  # 例外なし

    def test_c001_max_values_exceeded(self) -> None:
        """C-001: 入力値の数が上限を超えると ConstraintViolationError。"""
        inp = PipelineInput(name="test", values=tuple(range(10)))
        config = PipelineConfig(max_values=5)
        with pytest.raises(ConstraintViolationError, match="C-001") as exc_info:
            check_constraints(inp, config)
        assert exc_info.value.constraint_id == "C-001"

    def test_c001_boundary_at_limit(self) -> None:
        """C-001: 入力値の数が上限ちょうどは通過する。"""
        inp = PipelineInput(name="test", values=tuple(range(5)))
        config = PipelineConfig(max_values=5)
        check_constraints(inp, config)  # 例外なし

    def test_c001_boundary_one_over(self) -> None:
        """C-001: 入力値の数が上限+1 は違反する。"""
        inp = PipelineInput(name="test", values=tuple(range(6)))
        config = PipelineConfig(max_values=5)
        with pytest.raises(ConstraintViolationError, match="C-001"):
            check_constraints(inp, config)

    def test_c002_nan_rejected(self) -> None:
        """C-002: NaN を含む入力は拒否される。"""
        inp = PipelineInput(name="test", values=(1.0, float("nan"), 3.0))
        config = PipelineConfig()
        with pytest.raises(ConstraintViolationError, match="C-002") as exc_info:
            check_constraints(inp, config)
        assert exc_info.value.constraint_id == "C-002"
        assert "values[1]" in exc_info.value.detail

    def test_c002_inf_rejected(self) -> None:
        """C-002: 無限大を含む入力は拒否される。"""
        inp = PipelineInput(name="test", values=(1.0, math.inf))
        config = PipelineConfig()
        with pytest.raises(ConstraintViolationError, match="C-002"):
            check_constraints(inp, config)

    def test_c002_neg_inf_rejected(self) -> None:
        """C-002: 負の無限大を含む入力は拒否される。"""
        inp = PipelineInput(name="test", values=(-math.inf, 1.0))
        config = PipelineConfig()
        with pytest.raises(ConstraintViolationError, match="C-002"):
            check_constraints(inp, config)

    def test_c002_valid_zero(self) -> None:
        """C-002: ゼロは有効な入力。"""
        inp = PipelineInput(name="test", values=(0.0,))
        config = PipelineConfig()
        check_constraints(inp, config)  # 例外なし

    def test_c002_valid_negative(self) -> None:
        """C-002: 負の値は有効な入力（NaN/inf でなければ）。"""
        inp = PipelineInput(name="test", values=(-1.0, -2.0))
        config = PipelineConfig()
        check_constraints(inp, config)  # 例外なし


# ---------------------------------------------------------------------------
# パイプラインテスト
# ---------------------------------------------------------------------------


class TestPipeline:
    """Pipeline の E2E テスト。"""

    def test_success_basic(self) -> None:
        """基本的な成功ケース。"""
        config = PipelineConfig()
        pipeline = Pipeline(config)
        inp = PipelineInput(name="basic", values=(10.0, 20.0, 30.0))
        output = pipeline.run(inp)

        assert output.status == Status.SUCCESS
        assert output.input_name == "basic"
        assert output.result.total == pytest.approx(60.0)
        assert output.result.count == 3
        assert output.result.average == pytest.approx(20.0)

    def test_success_with_multiplier(self) -> None:
        """multiplier を適用した処理。"""
        config = PipelineConfig(multiplier=2.0)
        pipeline = Pipeline(config)
        inp = PipelineInput(name="mult", values=(5.0, 10.0))
        output = pipeline.run(inp)

        assert output.status == Status.SUCCESS
        assert output.result.total == pytest.approx(30.0)  # (5*2 + 10*2)
        assert output.result.average == pytest.approx(15.0)

    def test_single_value(self) -> None:
        """単一値の入力。"""
        config = PipelineConfig()
        pipeline = Pipeline(config)
        inp = PipelineInput(name="single", values=(42.0,))
        output = pipeline.run(inp)

        assert output.result.total == pytest.approx(42.0)
        assert output.result.count == 1
        assert output.result.average == pytest.approx(42.0)

    def test_constraint_violation_propagates(self) -> None:
        """制約違反は ConstraintViolationError として伝搬する。"""
        config = PipelineConfig(max_values=2)
        pipeline = Pipeline(config)
        inp = PipelineInput(name="too_many", values=(1.0, 2.0, 3.0))

        with pytest.raises(ConstraintViolationError, match="C-001"):
            pipeline.run(inp)

    def test_nan_input_rejected(self) -> None:
        """NaN 入力は制約チェックで拒否される。"""
        config = PipelineConfig()
        pipeline = Pipeline(config)
        inp = PipelineInput(name="nan", values=(1.0, float("nan")))

        with pytest.raises(ConstraintViolationError, match="C-002"):
            pipeline.run(inp)

    def test_zero_values(self) -> None:
        """全ゼロの入力は正常処理される。"""
        config = PipelineConfig()
        pipeline = Pipeline(config)
        inp = PipelineInput(name="zeros", values=(0.0, 0.0, 0.0))
        output = pipeline.run(inp)

        assert output.status == Status.SUCCESS
        assert output.result.total == pytest.approx(0.0)
        assert output.result.average == pytest.approx(0.0)

    def test_output_has_utc_timestamp(self) -> None:
        """出力のタイムスタンプは UTC。"""
        import datetime

        config = PipelineConfig()
        pipeline = Pipeline(config)
        inp = PipelineInput(name="ts", values=(1.0,))
        output = pipeline.run(inp)

        assert output.timestamp.tzinfo == datetime.UTC

    def test_reproducibility(self) -> None:
        """同一入力は同一結果を返す（タイムスタンプ除く）。"""
        config = PipelineConfig(multiplier=1.5)
        pipeline = Pipeline(config)
        inp = PipelineInput(name="repro", values=(3.0, 6.0, 9.0))

        output1 = pipeline.run(inp)
        output2 = pipeline.run(inp)

        assert output1.result == output2.result
        assert output1.status == output2.status
