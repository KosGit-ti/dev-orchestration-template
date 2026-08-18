#!/usr/bin/env python3
"""UserPromptSubmit hook: 指示元権限（P-066）を常時・非ブロッキングで注入する。

毎ターンの user prompt について指示元（人間オペレーター vs 非人間 = 自動/エージェント）を
best-effort 判別し、権限ルールを additionalContext として注入する。

要件（ユーザー指示 2026-06-30）:
  - 継続している作業を中断させない（常に exit 0・block しない・fail-open）。
  - 人間の指示か AI 同士の対話／自動メッセージかの判別を明らかにする。
  - 人間の指示の場合は必ず従う旨を注記。
  - AI 同士の対話の場合は定められた作業フローに従う旨を注記。

判別ロジックの正本は ``scripts/hooks/instruction_source.py``（純関数・テスト可能）。
本ファイルは Claude Code の UserPromptSubmit hook 結線（stdin JSON → stdout JSON）のみを担う。
"""

import contextlib
import json
import sys
from pathlib import Path

# 共通正本ロジック（scripts/hooks/instruction_source.py）を import する。
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "hooks"))


def main() -> int:
    """UserPromptSubmit hook 本体。常に exit 0（継続作業を中断しない）。"""
    prompt = ""
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
            if isinstance(data, dict):
                prompt = str(data.get("prompt", "") or "")
    except (json.JSONDecodeError, ValueError, OSError):
        prompt = ""

    try:
        from instruction_source import (  # type: ignore[import-not-found]
            build_additional_context,
            classify_source,
        )

        context = build_additional_context(classify_source(prompt))
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
        sys.stdout.write(json.dumps(output, ensure_ascii=False))
    except Exception:
        # fail-open: 判別注入に失敗（import 失敗等）してもセッションは止めない。
        # 最小限の妥当な JSON（空 additionalContext）を出力し、hook runner が
        # 不正出力と解釈する余地を残さない。注入が無くても正本ポリシー P-066 は
        # CLAUDE.md / policies.md に常駐するため運用は維持される。
        with contextlib.suppress(Exception):
            sys.stdout.write(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "UserPromptSubmit",
                            "additionalContext": "",
                        }
                    },
                    ensure_ascii=False,
                )
            )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
