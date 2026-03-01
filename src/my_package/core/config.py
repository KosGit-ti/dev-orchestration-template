"""設定管理。

TOML 設定ファイルの読み込みと検証を行う。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from my_package.core.exceptions import ConfigError


@dataclass(frozen=True)
class PipelineConfig:
    """パイプライン設定。

    不変条件:
        - ``max_values`` は正の整数。
        - ``multiplier`` は正の数。
    """

    max_values: int = 1000
    multiplier: float = 1.0
    output_dir: str = "outputs"

    def __post_init__(self) -> None:
        """不変条件を検証する。

        Raises:
            ConfigError: 不変条件に違反する場合。
        """
        if self.max_values <= 0:
            msg = f"max_values は正でなければならない: {self.max_values}"
            raise ConfigError(msg)
        if self.multiplier <= 0.0:
            msg = f"multiplier は正でなければならない: {self.multiplier}"
            raise ConfigError(msg)


def load_config(path: Path | None = None) -> PipelineConfig:
    """TOML 設定ファイルからパイプライン設定を読み込む。

    Args:
        path: 設定ファイルのパス。None の場合はデフォルト設定を返す。

    Returns:
        パイプライン設定。

    Raises:
        ConfigError: 設定ファイルの読み込みまたはパースに失敗した場合。
    """
    if path is None:
        return PipelineConfig()

    if not path.exists():
        msg = f"設定ファイルが見つからない: {path}"
        raise ConfigError(msg)

    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        msg = f"TOML パースエラー: {e}"
        raise ConfigError(msg) from e

    pipeline_section = data.get("pipeline", {})

    try:
        return PipelineConfig(
            max_values=int(pipeline_section.get("max_values", 1000)),
            multiplier=float(pipeline_section.get("multiplier", 1.0)),
            output_dir=str(pipeline_section.get("output_dir", "outputs")),
        )
    except (TypeError, ValueError, ConfigError) as e:
        msg = f"設定値が不正: {e}"
        raise ConfigError(msg) from e
