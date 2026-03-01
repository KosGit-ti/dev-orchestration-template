"""ドメイン型定義。

プロジェクト固有のデータモデルを定義する。
すべての型は不変（frozen）データクラスで表現し、
不変条件は ``__post_init__`` で検証する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from my_package.core.exceptions import ValidationError


class Status(Enum):
    """パイプライン実行状態。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True)
class PipelineInput:
    """パイプラインへの入力データ。

    不変条件:
        - ``name`` は空でない。
        - ``values`` は少なくとも1要素を持つ。

    Raises:
        ValidationError: 不変条件に違反した場合。
    """

    name: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        """不変条件を検証する。"""
        if not self.name:
            raise ValidationError("name は空にできない")
        if len(self.values) == 0:
            raise ValidationError("values は少なくとも1要素が必要")


@dataclass(frozen=True)
class ProcessingResult:
    """処理結果。

    不変条件:
        - ``total`` は有限値（NaN / inf を含まない）。
        - ``count`` は正の整数。
        - ``average`` は ``total / count`` に等しい。

    Raises:
        ValidationError: 不変条件に違反した場合。
    """

    total: float
    count: int
    average: float

    def __post_init__(self) -> None:
        """不変条件を検証する。"""
        if not math.isfinite(self.total):
            raise ValidationError(f"total は有限値でなければならない: {self.total}")
        if self.count <= 0:
            raise ValidationError(f"count は正の整数でなければならない: {self.count}")
        expected_avg = self.total / self.count
        if abs(self.average - expected_avg) >= 1e-9:
            raise ValidationError(
                f"average ({self.average}) は total/count ({expected_avg}) と一致しない"
            )


@dataclass(frozen=True)
class PipelineOutput:
    """パイプラインの出力。

    不変条件:
        - ``status`` は SUCCESS または FAILED。
        - ``timestamp`` は UTC。

    Raises:
        ValidationError: 不変条件に違反した場合。
    """

    input_name: str
    result: ProcessingResult
    status: Status
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def __post_init__(self) -> None:
        """不変条件を検証する。"""
        if self.status not in {Status.SUCCESS, Status.FAILED}:
            raise ValidationError(
                f"出力の status は SUCCESS か FAILED でなければならない: {self.status}"
            )
        tz = self.timestamp.tzinfo
        if tz is None or tz.utcoffset(None) != UTC.utcoffset(None):
            raise ValidationError("timestamp は UTC でなければならない")
