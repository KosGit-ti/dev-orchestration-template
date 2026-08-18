#!/usr/bin/env python3
"""Claude Code CLI を run_ai_review.py の provider command 形式で実行する。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = SCRIPT_ROOT / "docs" / "ai" / "review-result.schema.json"
SUPPORTED_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _load_claude_compatible_schema() -> str:
    """正本から Claude CLI が扱えないメタスキーマ宣言だけを除いて返す。"""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        msg = "review result schema must be a JSON object"
        raise ValueError(msg)
    dialect = schema.pop("$schema", None)
    if dialect != SUPPORTED_SCHEMA_DIALECT:
        msg = f"unsupported review result schema dialect: {dialect!r}"
        raise ValueError(msg)
    return json.dumps(schema, ensure_ascii=False)


def _extract_review_json(raw_output: str) -> str:
    stripped = raw_output.strip()
    if not stripped:
        return stripped
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(payload, dict) and "summary" in payload and "issues" in payload:
        return json.dumps(payload, ensure_ascii=False)
    structured_output = payload.get("structured_output") if isinstance(payload, dict) else None
    if isinstance(structured_output, dict):
        return json.dumps(structured_output, ensure_ascii=False)
    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        return cast("str", payload["result"]).strip()
    content = payload.get("content") if isinstance(payload, dict) else None
    if isinstance(content, list):
        texts = [
            item.get("text")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if texts:
            return "\n".join(cast("list[str]", texts)).strip()
    return stripped


def main() -> int:
    prompt = sys.stdin.read()
    if not prompt.strip():
        print("prompt is empty", file=sys.stderr)
        return 2
    claude = shutil.which("claude")
    if claude is None:
        print("claude CLI is not on PATH", file=sys.stderr)
        return 127
    result = subprocess.run(
        [
            claude,
            "-p",
            "--safe-mode",
            "--tools",
            "",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--effort",
            "high",
            "--output-format",
            "json",
            "--json-schema",
            _load_claude_compatible_schema(),
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
