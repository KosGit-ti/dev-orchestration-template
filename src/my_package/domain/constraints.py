"""制約評価スケルトン。

constraints.md で定義された制約を評価する。
制約違反時は ``ConstraintViolationError`` を送出し、フェイルクローズ（P-010）を実現する。
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from my_package.core.config import PipelineConfig
    from my_package.core.types import PipelineInput

from my_package.core.exceptions import ConstraintViolationError

logger = logging.getLogger(__name__)


def check_constraints(
    input_data: PipelineInput,
    config: PipelineConfig,
) -> None:
    """入力データが制約を満たしているか検証する。

    すべての制約チェックを一箇所に集約し、ドメインロジックが
    制約を回避できないようにする。

    Args:
        input_data: パイプライン入力データ。
        config: パイプライン設定。

    Raises:
        ConstraintViolationError: 制約に違反している場合。
    """
    _check_max_values(input_data, config)
    _check_value_range(input_data)
    logger.debug("制約チェック通過: %s", input_data.name)


def _check_max_values(
    input_data: PipelineInput,
    config: PipelineConfig,
) -> None:
    """C-001: 入力値の数が上限を超えていないか検証する。

    Args:
        input_data: パイプライン入力データ。
        config: パイプライン設定。

    Raises:
        ConstraintViolationError: 値の数が max_values を超えている場合。
    """
    if len(input_data.values) > config.max_values:
        raise ConstraintViolationError(
            constraint_id="C-001",
            detail=(
                f"入力値の数 ({len(input_data.values)}) が上限 ({config.max_values}) を超えている"
            ),
        )


def _check_value_range(input_data: PipelineInput) -> None:
    """C-002: 入力値が有効な範囲内か検証する。

    NaN や無限大を含む入力を拒否する（フェイルクローズ）。

    Args:
        input_data: パイプライン入力データ。

    Raises:
        ConstraintViolationError: 値に NaN や無限大が含まれている場合。
    """
    for i, v in enumerate(input_data.values):
        if math.isnan(v) or math.isinf(v):
            raise ConstraintViolationError(
                constraint_id="C-002",
                detail=f"values[{i}] が無効な値: {v}",
            )
