#!/usr/bin/env python3
"""context-index が既存の安定制御ファイルを参照しているか検証する。"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "ai" / "context-index.yml"


def main() -> int:
    if yaml is None:
        print("validate_context_index.py には PyYAML が必要です", file=sys.stderr)
        return 1
    if not INDEX.exists():
        print(f"missing {INDEX}", file=sys.stderr)
        return 1
    data = yaml.safe_load(INDEX.read_text(encoding="utf-8")) or {}
    missing: list[str] = []
    refs: set[str] = set(data.get("always_read", []))
    for mode in (data.get("modes") or {}).values():
        if isinstance(mode, dict):
            refs.update(mode.get("required", []) or [])
    for ref in sorted(refs):
        if "<" in ref or "#" in ref:
            continue
        if not (ROOT / ref).exists():
            missing.append(ref)
    if missing:
        print("参照先ファイルが存在しません:", ", ".join(missing), file=sys.stderr)
        return 1
    print("context-index ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
