#!/usr/bin/env python3
"""MVP パイプラインの実行スクリプト。

Usage:
    uv run python scripts/run_pipeline.py
    uv run python scripts/run_pipeline.py --config configs/pipeline_default.toml
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from my_package.core.config import load_config
from my_package.core.exceptions import ConstraintViolationError, DomainError
from my_package.core.types import PipelineInput
from my_package.domain.pipeline import Pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    """パイプラインを実行する。

    Returns:
        終了コード（0: 成功、1: パイプラインエラー）。
    """
    parser = argparse.ArgumentParser(description="MVP パイプライン実行")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="設定ファイルのパス（デフォルト: 組み込み設定）",
    )
    args = parser.parse_args()

    # 設定読み込み
    config = load_config(args.config)
    logger.info("設定読み込み完了: %s", config)

    # サンプル入力データ
    sample_input = PipelineInput(
        name="sample-run",
        values=(10.0, 20.0, 30.0, 40.0, 50.0),
    )

    # パイプライン実行
    pipeline = Pipeline(config)
    try:
        output = pipeline.run(sample_input)
        logger.info("結果: %s", output)
        return 0
    except (ConstraintViolationError, DomainError) as e:
        logger.error("パイプライン失敗: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
