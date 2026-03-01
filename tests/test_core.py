"""core パッケージのテスト。

型定義・例外・設定管理の単体テスト。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from my_package.core.config import PipelineConfig, load_config
from my_package.core.exceptions import (
    ConfigError,
    ConstraintViolationError,
    DomainError,
    ValidationError,
)
from my_package.core.types import (
    PipelineInput,
    PipelineOutput,
    ProcessingResult,
    Status,
)

# ---------------------------------------------------------------------------
# 型定義テスト
# ---------------------------------------------------------------------------


class TestPipelineInput:
    """PipelineInput の不変条件テスト。"""

    def test_valid_input(self) -> None:
        """正常な入力を生成できる。"""
        inp = PipelineInput(name="test", values=(1.0, 2.0, 3.0))
        assert inp.name == "test"
        assert inp.values == (1.0, 2.0, 3.0)

    def test_empty_name_rejected(self) -> None:
        """空の name は拒否される。"""
        with pytest.raises(ValidationError, match="name は空にできない"):
            PipelineInput(name="", values=(1.0,))

    def test_empty_values_rejected(self) -> None:
        """空の values は拒否される。"""
        with pytest.raises(ValidationError, match="少なくとも1要素が必要"):
            PipelineInput(name="test", values=())

    def test_frozen(self) -> None:
        """frozen データクラスは変更不可。"""
        inp = PipelineInput(name="test", values=(1.0,))
        with pytest.raises(AttributeError):
            inp.name = "changed"  # type: ignore[misc]


class TestProcessingResult:
    """ProcessingResult の不変条件テスト。"""

    def test_valid_result(self) -> None:
        """正常な結果を生成できる。"""
        result = ProcessingResult(total=30.0, count=3, average=10.0)
        assert result.total == 30.0
        assert result.count == 3
        assert result.average == 10.0

    def test_negative_total_accepted(self) -> None:
        """負の total は許容される（M-001 修正）。"""
        result = ProcessingResult(total=-3.0, count=1, average=-3.0)
        assert result.total == -3.0

    def test_nan_total_rejected(self) -> None:
        """NaN の total は拒否される。"""
        with pytest.raises(ValidationError, match="有限値"):
            ProcessingResult(total=math.nan, count=1, average=0.0)

    def test_inf_total_rejected(self) -> None:
        """inf の total は拒否される。"""
        with pytest.raises(ValidationError, match="有限値"):
            ProcessingResult(total=math.inf, count=1, average=0.0)

    def test_zero_count_rejected(self) -> None:
        """0 の count は拒否される。"""
        with pytest.raises(ValidationError, match="count は正の整数"):
            ProcessingResult(total=0.0, count=0, average=0.0)

    def test_inconsistent_average_rejected(self) -> None:
        """total/count と一致しない average は拒否される。"""
        with pytest.raises(ValidationError, match="一致しない"):
            ProcessingResult(total=30.0, count=3, average=5.0)


class TestPipelineOutput:
    """PipelineOutput の不変条件テスト。"""

    def test_valid_output(self) -> None:
        """正常な出力を生成できる。"""
        result = ProcessingResult(total=10.0, count=1, average=10.0)
        output = PipelineOutput(
            input_name="test",
            result=result,
            status=Status.SUCCESS,
        )
        assert output.status == Status.SUCCESS
        assert output.input_name == "test"

    def test_pending_status_rejected(self) -> None:
        """PENDING status は出力として不正。"""
        result = ProcessingResult(total=10.0, count=1, average=10.0)
        with pytest.raises(ValidationError, match="SUCCESS か FAILED"):
            PipelineOutput(
                input_name="test",
                result=result,
                status=Status.PENDING,
            )


class TestStatus:
    """Status 列挙型のテスト。"""

    def test_all_values(self) -> None:
        """全ステータスが定義されている。"""
        assert set(Status) == {
            Status.PENDING,
            Status.RUNNING,
            Status.SUCCESS,
            Status.FAILED,
        }


# ---------------------------------------------------------------------------
# 例外テスト
# ---------------------------------------------------------------------------


class TestExceptions:
    """ドメイン例外のテスト。"""

    def test_domain_error_hierarchy(self) -> None:
        """すべてのドメイン例外は DomainError を継承する。"""
        assert issubclass(ValidationError, DomainError)
        assert issubclass(ConfigError, DomainError)
        assert issubclass(ConstraintViolationError, DomainError)

    def test_constraint_violation_attributes(self) -> None:
        """ConstraintViolationError は constraint_id と detail を保持する。"""
        exc = ConstraintViolationError("C-001", "テスト違反")
        assert exc.constraint_id == "C-001"
        assert exc.detail == "テスト違反"
        assert "[C-001]" in str(exc)

    def test_constraint_violation_catchable_as_domain_error(self) -> None:
        """ConstraintViolationError は DomainError としてキャッチできる。"""
        with pytest.raises(DomainError):
            raise ConstraintViolationError("C-001", "テスト")


# ---------------------------------------------------------------------------
# 設定管理テスト
# ---------------------------------------------------------------------------


class TestPipelineConfig:
    """PipelineConfig のテスト。"""

    def test_default_config(self) -> None:
        """デフォルト設定が適切に設定される。"""
        config = PipelineConfig()
        assert config.max_values == 1000
        assert config.multiplier == 1.0
        assert config.output_dir == "outputs"

    def test_invalid_max_values(self) -> None:
        """max_values が 0 以下は拒否される。"""
        with pytest.raises(ConfigError, match="max_values は正"):
            PipelineConfig(max_values=0)

    def test_invalid_multiplier(self) -> None:
        """multiplier が 0 以下は拒否される。"""
        with pytest.raises(ConfigError, match="multiplier は正"):
            PipelineConfig(multiplier=0.0)


class TestLoadConfig:
    """load_config のテスト。"""

    def test_load_none_returns_default(self) -> None:
        """None を渡すとデフォルト設定を返す。"""
        config = load_config(None)
        assert config == PipelineConfig()

    def test_load_missing_file(self) -> None:
        """存在しないファイルは ConfigError を送出する。"""
        with pytest.raises(ConfigError, match="見つからない"):
            load_config(Path("/nonexistent/config.toml"))

    def test_load_valid_toml(self, tmp_path: Path) -> None:
        """有効な TOML ファイルから設定を読み込める。"""
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            "[pipeline]\nmax_values = 500\nmultiplier = 2.5\noutput_dir = 'test_out'\n"
        )
        config = load_config(config_file)
        assert config.max_values == 500
        assert config.multiplier == 2.5
        assert config.output_dir == "test_out"

    def test_load_invalid_toml(self, tmp_path: Path) -> None:
        """不正な TOML は ConfigError を送出する。"""
        config_file = tmp_path / "bad.toml"
        config_file.write_text("not valid toml [[[")
        with pytest.raises(ConfigError, match="TOML パースエラー"):
            load_config(config_file)

    def test_load_invalid_values(self, tmp_path: Path) -> None:
        """不正な設定値は ConfigError を送出する。"""
        config_file = tmp_path / "bad_values.toml"
        config_file.write_text("[pipeline]\nmax_values = -1\n")
        with pytest.raises(ConfigError, match="設定値が不正"):
            load_config(config_file)

    def test_load_empty_pipeline_section(self, tmp_path: Path) -> None:
        """pipeline セクションが空でもデフォルト値で生成される。"""
        config_file = tmp_path / "empty.toml"
        config_file.write_text("[pipeline]\n")
        config = load_config(config_file)
        assert config == PipelineConfig()

    def test_load_no_pipeline_section(self, tmp_path: Path) -> None:
        """pipeline セクションがなくてもデフォルト値で生成される。"""
        config_file = tmp_path / "other.toml"
        config_file.write_text("[other]\nkey = 'value'\n")
        config = load_config(config_file)
        assert config == PipelineConfig()
