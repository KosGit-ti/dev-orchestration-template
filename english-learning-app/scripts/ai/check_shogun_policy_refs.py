#!/usr/bin/env python3
"""shogun 文書群と ai/*.yml の参照整合チェック（N-438・FR-159 AC-10・local advisory）。

shogun 文書（運用正本・安全境界・複数セッションプロトコル・.shogun/*/README）が
バッククォートで参照する snake_case 識別子・ドット区切りポリシーパスが、
ポリシー正本群（POLICY_FILES＝operation-policy / capability-registry /
coherence-workflow / context-index / command-router / sdd-policy の各 yml）に実在するか
（typo・改名 drift が無いか）を検査する。

設計判断（spec AC-N438-02）: 既定は advisory（警告を stderr に報告して exit 0）。
fail-close 化は --strict の opt-in に限る（CI には接続しない）。サマリは stdout、
WARNING 行は stderr（機械処理しやすい CLI 慣例）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

POLICY_FILES = [
    "ai/operation-policy.yml",
    "ai/capability-registry.yml",
    "ai/coherence-workflow.yml",
    "ai/context-index.yml",
    "ai/command-router.yml",
    "ai/sdd-policy.yml",
]

# 検査対象＝governance 参照を含む shogun 文書（机上分解例・dry-run 証跡は
# 実装コード識別子を多く含む歴史的記録のため対象外）
SCAN_FILES = [
    "docs/ai/shogun-operating-model.md",
    "docs/ai/shogun-safety-boundary.md",
    "docs/ai/shogun-multi-session-protocol.md",
    ".shogun/README.md",
    ".shogun/inbox/README.md",
    ".shogun/mailbox/README.md",
    ".shogun/locks/README.md",
    ".shogun/skills/README.md",
    ".shogun/dashboard.md",
]

# shogun 側で定義される運用語・プロトコルフィールド（ポリシー yml 由来ではないが正当）
ALLOWLIST = {
    # mailbox / lock プロトコルのフィールド（正本＝shogun-multi-session-protocol.md）
    "task_id",
    "assigned_to",
    "executed_by",
    "numeric_report",
    "acquired_at",
    "required_locks",
    "work_packets",
    "promotion_gate",
    "safety_floor",
    # テンプレート / 報告の運用語（正本＝.shogun/inbox/README.md・operating-model）
    "tests_passed",
    "files_changed",
    "exit_code",
    "in_progress",
    # 状態語・既知の blocked ステータス（plan 由来の運用語）
    "blocked_by_user_runner_registration_required",
    # 履歴記述（ADR-0018 背景・rollback）＝command-router の旧 default 値
    # （yml コメント内のため収集対象外）
    "classify_with_repository_context",
    # 記事技術対応表の引用語（記事 #41 の予算ゲート構想・本リポジトリには未導入＝Backlog）
    "max_spend_usd_per_model",
    # Claude Code Stop hook の入力フィールド（hook 実装由来・B-367 規約の参照）
    "stop_hook_active",
}

BARE_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DOTTED_PATH_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")


def collect_known_identifiers(root: Path) -> set[str]:
    """ポリシー yml 群から既知識別子（キー・識別子形のスカラー）を収集する。"""
    known: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and BARE_IDENT_RE.match(key):
                    known.add(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            for token in re.findall(r"[A-Za-z0-9_./*-]+", node):
                bare = token.strip(".,:;")
                if BARE_IDENT_RE.match(bare):
                    known.add(bare)

    for rel in POLICY_FILES:
        path = root / rel
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            # パース失敗はここでは黙ってスキップし、main 側で警告として計上する
            continue
        walk(data)
    return known


def resolve_dotted(root_data: dict[str, Any], dotted: str) -> bool:
    """ドット区切りパスが与えられた yml マッピング構造に実在するか（全ポリシー yml を横断適用）。"""
    node: Any = root_data
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    return True


def extract_candidates(text: str) -> tuple[set[str], set[str]]:
    """バッククォート内から bare 識別子とドット区切りパスを抽出する。"""
    bare: set[str] = set()
    dotted: set[str] = set()
    for raw in BACKTICK_RE.findall(text):
        token = raw.strip()
        # パス・コマンド・glob・テンプレート表記は対象外
        if any(ch in token for ch in "/ <>$\"'()=|"):
            continue
        if token.endswith(
            (".md", ".yml", ".yaml", ".py", ".json", ".db", ".lock", ".toml", ".txt", ".sh")
        ):
            continue
        if DOTTED_PATH_RE.match(token):
            dotted.add(token)
        elif BARE_IDENT_RE.match(token) and "_" in token:
            bare.add(token)
    return bare, dotted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="警告があれば exit 1（既定は advisory＝exit 0。CI には接続しない）",
    )
    parser.add_argument(
        "--require-all-files",
        action="store_true",
        help=(
            "検査対象 / ポリシー正本ファイルの欠落も警告にする"
            "（既定はスキップ＝fixture / 部分 checkout 互換。実リポジトリの rename drift 検出用）"
        ),
    )
    args = parser.parse_args()
    root: Path = args.root

    known = collect_known_identifiers(root) | ALLOWLIST
    # dotted パスは全ポリシー yml を横断して解決する（operation-policy 外の名前空間
    # 〔例: coherence-workflow の workflows.shogun_dispatch〕も実在検査の対象にするため。
    # head が既知というだけで抑制すると safety.<typo> を見逃す）
    policy_datas: list[dict[str, Any]] = []
    missing_policy: list[str] = []
    for rel in POLICY_FILES:
        path = root / rel
        if not path.exists():
            missing_policy.append(f"{rel}: ポリシー正本が存在しません（既知識別子の収集対象外）")
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            # advisory ツールとしてクラッシュさせず、欠落と同種の警告として報告する
            missing_policy.append(
                f"{rel}: ポリシー正本のパースに失敗しました（{exc.__class__.__name__}）"
            )
            continue
        if isinstance(data, dict):
            policy_datas.append(data)

    unknown_idents: list[str] = []
    unresolved_paths: list[str] = []
    missing_files: list[str] = []
    scanned = 0
    for rel in SCAN_FILES:
        path = root / rel
        if not path.exists():
            # 欠落は既定でスキップ（fixture / 部分 checkout 互換）。
            # 実リポジトリでの rename / 削除 drift 検出には --require-all-files を使う。
            missing_files.append(f"{rel}: 検査対象ファイルが存在しません")
            continue
        scanned += 1
        bare, dotted = extract_candidates(path.read_text(encoding="utf-8"))
        for token in sorted(bare):
            if token not in known:
                unknown_idents.append(
                    f"{rel}: 未知の識別子 `{token}`（ポリシー yml 群・allowlist に不在）"
                )
        for token in sorted(dotted):
            if not any(resolve_dotted(data, token) for data in policy_datas):
                unresolved_paths.append(f"{rel}: 未解決のポリシーパス `{token}`")

    effective: list[str] = unknown_idents + unresolved_paths
    if args.require_all_files:
        effective += missing_files + missing_policy
    for line in effective:
        print(f"WARNING: {line}", file=sys.stderr)
    print(
        f"scanned {scanned} files / unknown identifiers: {len(unknown_idents)} / "
        f"unresolved paths: {len(unresolved_paths)} / "
        f"missing scan files: {len(missing_files)} / "
        f"missing policy files: {len(missing_policy)}"
        + ("" if args.require_all_files else "（欠落は既定スキップ・--require-all-files で警告化）")
    )
    if effective and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
