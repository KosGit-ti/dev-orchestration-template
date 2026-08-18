#!/usr/bin/env python3
"""docs/plan.md の Next／Backlog エントリが再肥大していないかの機械検査。

2026-08 文書軽量化 wave 3（第 3 波）で Backlog（54%・131KB）と Next（28%・68KB）が
plan.md 全体の 82% を占めるまで肥大化した根本原因は、個々のエントリの書式（経緯・
SHA 羅列・correction event の説明を本文へ書く慣習）が野放しだったことにある。本
スクリプトはその再発を防ぐため、1 エントリあたりの行数上限を
``ai/document-governance.yml`` の ``plan_entry_length`` 節から読み、超過を検出する。

対象:
  - ``## Next（自動実行対象：優先順）`` 節の各タスクエントリ（任意の ``### `` 見出し
    〜次の ``### `` 見出し直前まで）。
  - ``## Backlog`` 節の各生存エントリ（``- **Backlog-XXX**...`` /
    ``- ~~**Backlog-XXX**~~...`` bullet 〜次の bullet／``### `` 見出し直前まで）。
    旧形式の ``### B-nnn`` 見出しエントリ（B-390/B-373/B-378 の 3 件）は
    ``- B-\\d+`` bullet 契約（``scripts/hooks/full_plan_completion.py`` の
    ``_has_active_auto_backlog_task``）と衝突するため対象外とする
    （経緯は ``ai/document-governance.yml`` の ``plan_entry_length.rule`` 参照）。

超過エントリを検出したら本文を ``docs/archive/`` へ移設し、plan.md 側は 1 行要約
＋リンクへ揃える（手順は ``archive_governance`` と同じ・ADR-0003 削除禁止）。

fail-close の設計（P-010）: 対象節が見つからない・エントリが 1 件も検出できない
（Next の「現在なし」終端状態を除く）・設定が欠落している、のいずれも exit 1 と
する。「見つからない・検出できない＝pass」にしない（検査器自身が壊れている状態を
合格にしない）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

PLAN = "docs/plan.md"
GOVERNANCE_CONFIG = "ai/document-governance.yml"

NEXT_TERMINAL_MARKER = "現在なし"

_REQUIRED_CONFIG_KEYS = (
    "next_section_heading",
    "backlog_section_heading",
    "next_entry_max_lines",
    "backlog_entry_max_lines",
)

_H3_HEADING_RE = re.compile(r"^### .*$", re.MULTILINE)
_BACKLOG_BULLET_RE = re.compile(r"^- (?:~~)?\*\*(Backlog-[A-Za-z0-9\-\.]+)\*\*", re.MULTILINE)
_BACKLOG_BOUNDARY_RE = re.compile(r"^(?:- |### )", re.MULTILINE)


def _read(root: Path, rel: str) -> str | None:
    path = root / rel
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """``## {heading}`` から次の ``## `` 見出し直前までの本文を返す。"""
    pattern = re.compile(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(text)
    return match.group(1) if match else ""


def load_config(root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """``ai/document-governance.yml`` の ``plan_entry_length`` 節を読む。

    欠落・型不正は None + issue で返す（fail-close。空を合格にしない）。
    """
    path = root / GOVERNANCE_CONFIG
    if not path.exists():
        return None, [f"{GOVERNANCE_CONFIG} が見つかりません"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, [f"{GOVERNANCE_CONFIG} の YAML 解析に失敗しました: {exc}"]
    if not isinstance(data, dict):
        return None, [f"{GOVERNANCE_CONFIG} の内容が dict ではありません"]

    config = data.get("plan_entry_length")
    if not isinstance(config, dict):
        return None, [f"{GOVERNANCE_CONFIG} に plan_entry_length 節がありません"]

    missing = [key for key in _REQUIRED_CONFIG_KEYS if key not in config]
    if missing:
        return None, [
            f"{GOVERNANCE_CONFIG} の plan_entry_length に必須キーが欠落しています: "
            + ", ".join(missing)
        ]

    for key in ("next_entry_max_lines", "backlog_entry_max_lines"):
        value = config[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return None, [
                f"{GOVERNANCE_CONFIG} の plan_entry_length.{key} は正の整数である必要があります"
                f"（実値: {value!r}）"
            ]

    for key in ("next_section_heading", "backlog_section_heading"):
        value = config[key]
        if not isinstance(value, str) or not value.strip():
            return None, [
                f"{GOVERNANCE_CONFIG} の plan_entry_length.{key} は非空文字列である必要があります"
                f"（実値: {value!r}）"
            ]

    return config, []


def _next_entries(section_text: str) -> list[tuple[str, int]]:
    """Next 節内の各タスクエントリ（任意の H3）を (見出しテキスト, 行数) で返す。"""
    headings = list(_H3_HEADING_RE.finditer(section_text))
    entries: list[tuple[str, int]] = []
    for index, heading in enumerate(headings):
        start = heading.start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(section_text)
        body = section_text[start:end]
        entries.append((heading.group(0).strip(), len(body.splitlines())))
    return entries


def _backlog_entries(section_text: str) -> list[tuple[str, int]]:
    """Backlog 節内の生存エントリ（Backlog-XXX bullet）を (ID, 行数) で返す。"""
    bullets = list(_BACKLOG_BULLET_RE.finditer(section_text))
    boundaries = sorted(m.start() for m in _BACKLOG_BOUNDARY_RE.finditer(section_text))
    entries: list[tuple[str, int]] = []
    for bullet in bullets:
        start = bullet.start()
        later = [pos for pos in boundaries if pos > start]
        end = min(later) if later else len(section_text)
        body = section_text[start:end]
        entries.append((bullet.group(1), len(body.splitlines())))
    return entries


def check_next_entry_length(root: Path, config: dict[str, Any]) -> list[str]:
    text = _read(root, PLAN)
    if text is None:
        return [f"{PLAN} が見つかりません"]

    heading = config["next_section_heading"]
    section = _section(text, heading)
    if not section.strip():
        return [f"{PLAN} に「## {heading}」節が見つからないか空です"]

    entries = _next_entries(section)
    if not entries:
        if NEXT_TERMINAL_MARKER in section:
            return []
        return [
            f"「## {heading}」節にエントリ（### 見出し）が1件も見つかりません"
            "（parser 破損または節が空の疑い・fail-close。正当な完了状態は"
            f"「{NEXT_TERMINAL_MARKER}」の記載を伴う）"
        ]

    max_lines = config["next_entry_max_lines"]
    issues = []
    for entry_heading, line_count in entries:
        if line_count > max_lines:
            issues.append(
                f"Next エントリが上限行数を超えています: {entry_heading!r} は "
                f"{line_count} 行（上限 {max_lines} 行）。詳細を docs/archive/ へ"
                "移設し、本文は「何を／なぜ今／完了条件／詳細リンク」の要約へ"
                "揃えてください。"
            )
    return issues


def check_backlog_entry_length(root: Path, config: dict[str, Any]) -> list[str]:
    text = _read(root, PLAN)
    if text is None:
        return [f"{PLAN} が見つかりません"]

    heading = config["backlog_section_heading"]
    section = _section(text, heading)
    if not section.strip():
        return [f"{PLAN} に「## {heading}」節が見つからないか空です"]

    entries = _backlog_entries(section)
    if not entries:
        return [
            f"「## {heading}」節に生存エントリ（- **Backlog-XXX** bullet）が"
            "1件も見つかりません（parser 破損または節が空の疑い・fail-close）"
        ]

    max_lines = config["backlog_entry_max_lines"]
    issues = []
    for entry_id, line_count in entries:
        if line_count > max_lines:
            issues.append(
                f"Backlog エントリが上限行数を超えています: {entry_id!r} は "
                f"{line_count} 行（上限 {max_lines} 行）。本文を docs/archive/ へ"
                "移設し、1 行要約＋リンクへ揃えてください。"
            )
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="docs/plan.md の Next／Backlog エントリ行数上限の機械検査"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="リポジトリルート（既定はスクリプト位置から算出。fixture テストで override）",
    )
    args = parser.parse_args(argv)
    root: Path = args.root

    config, config_issues = load_config(root)
    if config_issues:
        for issue in config_issues:
            print(issue, file=sys.stderr)
        print("plan entry length NG（設定欠落）", file=sys.stderr)
        return 1
    assert config is not None

    issues: list[str] = []
    issues.extend(check_next_entry_length(root, config))
    issues.extend(check_backlog_entry_length(root, config))

    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        print(f"plan entry length NG（{len(issues)} 件）", file=sys.stderr)
        return 1

    print("plan entry length ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
