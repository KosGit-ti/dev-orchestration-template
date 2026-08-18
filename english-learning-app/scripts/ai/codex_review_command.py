#!/usr/bin/env python3
"""Codex CLI を run_ai_review.py の明示 review command 形式で実行する。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = SCRIPT_ROOT / "docs" / "ai" / "review-result.schema.json"


def _extract_review_json(raw_output: str) -> str:
    """Codex の最終出力を provider JSON として返す。"""
    stripped = raw_output.strip()
    if not stripped:
        return stripped
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(payload, dict) and "summary" in payload and "issues" in payload:
        return json.dumps(payload, ensure_ascii=False)
    return stripped


def main() -> int:
    prompt = sys.stdin.read()
    if not prompt.strip():
        print("prompt is empty", file=sys.stderr)
        return 2
    codex = shutil.which("codex")
    if codex is None:
        print("codex CLI is not on PATH", file=sys.stderr)
        return 127
    result = subprocess.run(
        [
            codex,
            "exec",
            "--strict-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--color",
            "never",
            "--config",
            'model_reasoning_effort="high"',
            "--config",
            "project_doc_max_bytes=0",
            "--output-schema",
            str(SCHEMA),
            "-",
        ],
        cwd=SCRIPT_ROOT,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=1800,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr)
        return result.returncode
    print(_extract_review_json(result.stdout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
