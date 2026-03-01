"""core パッケージ — 型定義、例外、設定管理。

最下層モジュール。他のドメインモジュールに依存しない。
"""

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

__all__ = [
    "ConfigError",
    "ConstraintViolationError",
    "DomainError",
    "PipelineConfig",
    "PipelineInput",
    "PipelineOutput",
    "ProcessingResult",
    "Status",
    "ValidationError",
    "load_config",
]
