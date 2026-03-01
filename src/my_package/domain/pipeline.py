"""MVP パイプライン。

入力データ → 制約チェック → 処理 → 出力 の一連のフローを実装する。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from my_package.core.config import PipelineConfig

from my_package.core.exceptions import ConstraintViolationError, DomainError
from my_package.core.types import (
    PipelineInput,
    PipelineOutput,
    ProcessingResult,
    Status,
)
from my_package.domain.constraints import check_constraints

logger = logging.getLogger(__name__)


class Pipeline:
    """MVP パイプライン。

    入力→制約チェック→処理→出力のフローを実行する。
    制約違反時はフェイルクローズ（P-010）により処理を中断する。
    """

    def __init__(self, config: PipelineConfig) -> None:
        """パイプラインを初期化する。

        Args:
            config: パイプライン設定。
        """
        self._config = config
        logger.info(
            "パイプライン初期化: multiplier=%.2f, max_values=%d",
            config.multiplier,
            config.max_values,
        )

    def run(self, input_data: PipelineInput) -> PipelineOutput:
        """パイプラインを実行する。

        事前条件:
            - ``input_data`` は有効な PipelineInput。

        事後条件:
            - 返却値の ``status`` は SUCCESS。
            - 制約違反時は ConstraintViolationError を送出する（P-010 フェイルクローズ）。

        Args:
            input_data: パイプライン入力データ。

        Returns:
            パイプライン出力。
        """
        logger.info("パイプライン実行開始: %s", input_data.name)

        try:
            # Step 1: 制約チェック
            check_constraints(input_data, self._config)

            # Step 2: 処理
            result = self._process(input_data)

            # Step 3: 出力生成
            output = PipelineOutput(
                input_name=input_data.name,
                result=result,
                status=Status.SUCCESS,
            )
            logger.info(
                "パイプライン完了: %s (total=%.2f, avg=%.2f)",
                input_data.name,
                result.total,
                result.average,
            )
            return output

        except ConstraintViolationError:
            logger.warning("制約違反により処理中断: %s", input_data.name)
            raise
        except DomainError:
            logger.exception("ドメインエラー: %s", input_data.name)
            raise

    def _process(self, input_data: PipelineInput) -> ProcessingResult:
        """入力データを処理する。

        事前条件:
            - 制約チェックが通過済みであること。

        Args:
            input_data: パイプライン入力データ。

        Returns:
            処理結果。
        """
        multiplied = tuple(v * self._config.multiplier for v in input_data.values)
        total = sum(multiplied)
        count = len(multiplied)
        average = total / count

        return ProcessingResult(
            total=total,
            count=count,
            average=average,
        )
