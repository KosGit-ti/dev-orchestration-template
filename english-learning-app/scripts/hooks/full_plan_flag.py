#!/usr/bin/env python3
"""全プラン実行モードのローカルフラグ判定。"""

import json
from pathlib import Path
from typing import Optional

FULL_PLAN_FLAG = Path(__file__).parent.parent.parent / ".github" / "full-plan-execution.flag"


def is_full_plan_execution_active(flag_path: Optional[Path] = None) -> bool:  # noqa: UP045
    """全プラン実行モードのローカルフラグが有効か判定する。"""
    path = flag_path or FULL_PLAN_FLAG
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(data, dict):
        return True
    return data.get("active", True) is not False
