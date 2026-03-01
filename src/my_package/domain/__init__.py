"""domain パッケージ — ドメインロジック。

core 層のみに依存する。
"""

from my_package.domain.constraints import check_constraints
from my_package.domain.pipeline import Pipeline

__all__ = [
    "Pipeline",
    "check_constraints",
]
