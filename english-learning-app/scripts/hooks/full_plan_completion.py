#!/usr/bin/env python3
"""全プラン実行モードの完了認証。

Next が空なだけでは完了扱いにしない。plan.md の状態・ゴール・自動 Backlog・
成果証跡をあわせて確認し、矛盾があれば fail-close する。

PR レビュー対応は最大 3 ラウンド。Round 3 後の非ブロッキング Must/Should は Backlog 化、
即時ブロッカーは fail-close。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT_FOR_IMPORT = str(REPO_ROOT)
if _REPO_ROOT_FOR_IMPORT not in sys.path:
    sys.path.insert(0, _REPO_ROOT_FOR_IMPORT)

PLAN_PATH = REPO_ROOT / "docs" / "plan.md"
FULL_PLAN_FLAG = REPO_ROOT / ".github" / "full-plan-execution.flag"
OUTCOME_EVIDENCE_HEADING = "成果証跡"
FORBIDDEN_EVIDENCE_PLACEHOLDERS = ("未設定", "未確認", "未記録", "TBD")
REQUIRED_OUTCOME_MARKERS = (
    "検証日時",
    "検証コミット",
    "CI結果",
    "監査結果",
    "Issue / PR",
    "Webリリース",
    "ML改善",
    "リリース運用",
    "再現コマンド",
    "ロールバック方針",
)
REQUIRED_WEB_MARKERS = (
    "main 本番URL",
    "develop Preview URL",
    "production backend /health",
    "preview backend /health",
    "frontend-ci deploy",
)
REQUIRED_HTTP_EVIDENCE_KEYS = (
    "main 本番URL",
    "develop Preview URL",
    "production backend /health",
    "preview backend /health",
)
REQUIRED_ML_MARKERS = (
    "ベースライン指標",
    "変更後指標",
    "比較結果",
    "再現性",
)
REQUIRED_RELEASE_MARKERS = (
    "リリース対象",
    "リリースノート",
    "ロールバック手順",
)
# full-plan delivery loop の各ステップ完了フラグ。ai/*.yml と
# .github/full-plan-execution.flag.example のステップ名と整合させる。
# ci_final_gate_passed は「CI final gate = PR の必須チェックが全て green である
# こと（`gh pr checks` で確認）」を意味する一般的な完了条件を指す。
REQUIRED_DELIVERY_STATE_FLAGS = (
    "changes_committed_and_pushed",
    "pr_created",
    "push_review_loop_completed",
    "ci_final_gate_passed",
    "release_manager_approved",
    "merged_to_main",
    "main_pulled_after_merge",
    "plan_updated_after_merge",
    "execution_ledger_updated_after_merge",
)
OUT_OF_SCOPE_VALUES = ("対象外", "該当なし", "N/A")
TOKEN_BOUNDARY_CHARS = r"A-Za-z0-9_一-龯ぁ-んァ-ン"
EVIDENCE_ITEM_PATTERN = re.compile(r"^-\s*([^:：]+)\s*[:：]\s*(.*?)\s*$")
POSITIVE_RESULT_PATTERN = re.compile(
    r"\b(?:success|pass|passed|ok)\b|成功|合格|完了",
    re.IGNORECASE,
)
NEGATIVE_RESULT_PATTERN = re.compile(
    r"\b(?:not|no)\s+(?:success|pass|passed|ok)\b|\bfail(?:ed|ure)?\b"
    r"|(?:未|不|非|無)(?:成功|合格|完了)"
    r"|(?:成功|合格|完了)\s*(?:しない|なし|せず|ではない)",
    re.IGNORECASE,
)
HTTP_SUCCESS_PATTERN = re.compile(r"\b(?:HTTP(?:/\d(?:\.\d)?)?\s*)?200(?:\s+OK)?\b", re.IGNORECASE)
ML_POSITIVE_RESULT_PATTERN = re.compile(r"改善|同等以上|同等|向上")
ML_NEGATIVE_RESULT_PATTERN = re.compile(
    r"(?:改善|向上|同等以上|同等)\s*(?:なし|しない|せず|ではない|未満)|悪化|低下"
)
OUT_OF_SCOPE_PATTERN = re.compile(
    rf"^(?:{'|'.join(re.escape(value) for value in OUT_OF_SCOPE_VALUES)})"
    r"(?:$|[\s（(、。,.:：/])",
    re.IGNORECASE,
)
PLACEHOLDER_VALUE_PATTERN = re.compile(
    rf"(?<![{TOKEN_BOUNDARY_CHARS}])"
    rf"(?:{'|'.join(re.escape(value) for value in FORBIDDEN_EVIDENCE_PLACEHOLDERS)})"
    rf"(?![{TOKEN_BOUNDARY_CHARS}])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CompletionCheck:
    """全プラン完了認証の結果。"""

    is_complete: bool
    reasons: tuple[str, ...]


def load_full_plan_flag(flag_path: Path | None = None) -> dict[str, Any] | None:
    """全プラン実行フラグを読み込む。壊れている場合は active 扱いで返す。"""
    path = flag_path or FULL_PLAN_FLAG
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active": True, "mode": "full_plan", "_invalid": True}
    if not isinstance(data, dict):
        return {"active": True, "mode": "full_plan", "_invalid": True}
    return data


def _section(text: str, heading: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(text)
    if match is None:
        return ""
    next_heading = re.search(r"^##\s+", text[match.end() :], re.MULTILINE)
    if next_heading is None:
        return text[match.end() :]
    return text[match.end() : match.end() + next_heading.start()]


# ゴール項目行の装飾許容プレフィックス（太字 `**`／打ち消し `~~` の任意順・任意組合せ）。
# `- G1` / `- ~~G1` / `- **G1**` / `- **~~G1…~~** ✅` を等しく「ゴール項目行」として
# 認識する（装飾付きゴール行が未完了検出をすり抜ける非対称を防ぐ）。
_GOAL_LINE_DECORATOR_PREFIX = r"^-\s+\**(?:~~)?\**"


def _has_unfinished_goal(goals_section: str) -> bool:
    """「今月のゴール」節に未完了のゴール項目行が残っているか判定する。

    太字装飾（`- **G1** ...`）や太字+打ち消しの複合（`- **~~G1…~~** ✅` 等）の
    ゴール行も認識する。装飾の有無に関わらずゴール行として認識したうえで、
    `~~` と `✅` が両方そろって初めて完了扱いとする。これにより (1) 装飾付き
    ゴール行が未完了検出をすり抜けて完了認証される非対称、(2) 打ち消し線はあるが
    `✅` を付け忘れた行が検出漏れになる fail-open ギャップ、の両方を防ぐ。
    """
    for raw_line in goals_section.splitlines():
        line = raw_line.strip()
        if not re.match(_GOAL_LINE_DECORATOR_PREFIX + "G", line):
            continue
        if "~~" in line and "✅" in line:
            continue
        return True
    return False


def _outcome_evidence_issue(evidence_section: str) -> str:
    if not evidence_section.strip():
        return "成果証跡が空です"
    evidence_items: dict[str, str] = {}
    for raw_line in evidence_section.splitlines():
        match = EVIDENCE_ITEM_PATTERN.match(raw_line.strip())
        if match is None:
            continue
        key, value = match.groups()
        evidence_items[key.strip()] = value.strip()
    required_keys = REQUIRED_OUTCOME_MARKERS
    missing_keys = [key for key in required_keys if key not in evidence_items]
    if missing_keys:
        return "成果証跡の共通項目が不足しています: " + ", ".join(missing_keys)
    unsettled_keys = [
        key
        for key in required_keys
        if not evidence_items[key] or PLACEHOLDER_VALUE_PATTERN.search(evidence_items[key])
    ]
    if unsettled_keys:
        return "成果証跡の共通項目が未確定です: " + ", ".join(unsettled_keys)
    if not _has_positive_result(evidence_items["CI結果"]):
        return "成果証跡のCI結果が成功を示していません"
    if not _has_positive_result(evidence_items["監査結果"]):
        return "成果証跡の監査結果が成功を示していません"
    if _requires_category_evidence(evidence_items["Webリリース"]):
        web_issue = _category_evidence_issue("Webリリース", evidence_items, REQUIRED_WEB_MARKERS)
        if web_issue:
            return web_issue
        if not all(_has_http_success(evidence_items[key]) for key in REQUIRED_HTTP_EVIDENCE_KEYS):
            return "成果証跡のWebリリースにHTTP 200確認が不足しています"
        if not _has_positive_result(evidence_items["frontend-ci deploy"]):
            return "成果証跡のfrontend-ci deploy が成功を示していません"
    if _requires_category_evidence(evidence_items["ML改善"]):
        ml_issue = _category_evidence_issue("ML改善", evidence_items, REQUIRED_ML_MARKERS)
        if ml_issue:
            return ml_issue
        if not _has_ml_positive_result(evidence_items["比較結果"]):
            return "成果証跡のML比較結果が改善または同等以上を示していません"
    if _requires_category_evidence(evidence_items["リリース運用"]):
        return _category_evidence_issue("リリース運用", evidence_items, REQUIRED_RELEASE_MARKERS)
    return ""


def _requires_category_evidence(value: str) -> bool:
    return OUT_OF_SCOPE_PATTERN.search(value.strip()) is None


def _has_positive_result(value: str) -> bool:
    if NEGATIVE_RESULT_PATTERN.search(value):
        return False
    return POSITIVE_RESULT_PATTERN.search(value) is not None


def _has_http_success(value: str) -> bool:
    return HTTP_SUCCESS_PATTERN.search(value) is not None


def _has_ml_positive_result(value: str) -> bool:
    return (
        ML_NEGATIVE_RESULT_PATTERN.search(value) is None
        and ML_POSITIVE_RESULT_PATTERN.search(value) is not None
    )


def _category_evidence_issue(
    category: str,
    evidence_items: dict[str, str],
    required_keys: tuple[str, ...],
) -> str:
    if not all(key in evidence_items for key in required_keys):
        missing_keys = [key for key in required_keys if key not in evidence_items]
        prefix = f"成果証跡の{category}項目" if category else "成果証跡の項目"
        return f"{prefix}が不足しています: " + ", ".join(missing_keys)
    unsettled_keys = [
        key
        for key in required_keys
        if not evidence_items[key] or PLACEHOLDER_VALUE_PATTERN.search(evidence_items[key])
    ]
    if unsettled_keys:
        prefix = f"成果証跡の{category}項目" if category else "成果証跡の項目"
        return f"{prefix}が未確定です: " + ", ".join(unsettled_keys)
    return ""


_COMPLETED_MARKER_PATTERN = re.compile(r"(?<!未)完了")


def _has_active_auto_backlog_task(backlog_section: str) -> bool:
    """Backlog 節に未完了の自動実行対象タスクが残っているか判定する。

    自動実行対象の契約は `- B-\\d+`（Next 昇格待ち行列）のみである。太字装飾
    （`- **B-<番号>**` 等、bold 耐性）にも対応するが、`- **Backlog-N…**` /
    `- **BACKLOG-…**` 派生エントリは意図的に対象外とする。

    派生 Backlog はレビュー毎に恒常的に増える繰延改善であり、tier・残リスク付きで
    登録され随時消化・frontier 報告の対象として運用される。これを完了ゲートの対象に
    すると、レビューが新規 Backlog を生む度にゲートが恒久ブロックされる runaway に
    陥る（一部のゴールを「## 将来ゲート」へ移設したのと同じ設計判断）。

    完了マーカー `完了` は否定語「未」を除いた形で判定する。素朴な部分一致では
    `- **B-<番号>**：未完了` のような未完了タスクの説明文に含まれる「未完了」の
    「完了」部分に誤反応して素通りしてしまうため。
    """
    for raw_line in backlog_section.splitlines():
        line = raw_line.strip()
        if not re.match(r"^- \*{0,2}B-\d+", line):
            continue
        if (
            "~~" in line
            or "✅" in line
            or _COMPLETED_MARKER_PATTERN.search(line)
            or "Merged" in line
            or "integrated" in line
        ):
            continue
        if "N-" in line and "昇格" in line:
            continue
        return True
    return False


def _full_plan_completion_fail(reason: str) -> str:
    """fail-close 理由文字列を返す（ログ出力用ヘルパー）。

    Args:
        reason: fail-close の理由。

    Returns:
        同じ reason をそのまま返す（呼出元で reasons.append に渡す）。
    """
    return reason


def _has_pr_identifier(value: object) -> bool:
    """PR 番号または URL が記録されているかを判定する。"""
    if isinstance(value, int):
        return value > 0
    if isinstance(value, str):
        return bool(value.strip())
    return False


def _delivery_state_issue(flag_data: dict[str, Any]) -> str:
    """full-plan delivery loop の完了状態を flag から検証する。

    plan.md の成果証跡だけでは、PR 作成後の review / CI / release-manager /
    merge / main pull が実際に進んだかを機械判定できない。全プラン実行
    フラグに各 delivery step の状態を残し、完了認証時に fail-close する。
    """
    delivery = flag_data.get("delivery")
    if not isinstance(delivery, dict):
        return "full-plan delivery state が未記録です（delivery object 不在）"

    incomplete = [
        marker for marker in REQUIRED_DELIVERY_STATE_FLAGS if delivery.get(marker) is not True
    ]
    if incomplete:
        return "full-plan delivery state が未完了です: " + ", ".join(incomplete)

    if _has_pr_identifier(flag_data.get("current_pr")):
        return "full-plan delivery state が未完了です: current_pr が残っています"
    if not _has_pr_identifier(flag_data.get("last_merged_pr")):
        return "full-plan delivery state が未完了です: last_merged_pr が未記録です"
    return ""


# ---------------------------------------------------------------------------
# gated_idle 終端状態
#
# 全プラン実行フラグが active=true のまま、残タスクが全て user/infra-gated の
# 場合、Stop hook は毎セッション終了時に「Next が現在なしではない」等の理由で
# 誤 block し続ける。flag JSON の任意フィールド `status="gated_idle"` を検出した
# ときだけ、以下の専用検証パス（fail-close は維持）へ切り替える。
# status フィールド不在・他値の場合は本セクションの判定を一切行わず、従来の
# check_full_plan_completion() 経路のまま動作する（後方互換）。
#
# gated_idle が迂回してよいのは「今月のゴール」未完了判定・成果証跡プレースホルダ
# 検査など "今月のゴール・成果証跡" 側の確認だけである。`## Backlog` 節に残る
# 自動実行対象タスク（`- B-\d+` 契約）の取りこぼし検査（`_has_active_auto_backlog_task`）
# は check_full_plan_completion() 経路と同様に独立実行し、gated_idle でも
# 迂回させない。
# ---------------------------------------------------------------------------

GATED_IDLE_STATUS = "gated_idle"

# flag.remaining_tasks の各要素が満たすべき marker（仕様 (b)）。
GATED_TASK_FLAG_MARKERS: tuple[str, ...] = (
    "user-gated",
    "infra-gated",
    "gated:",
    "red 配線",
    "ユーザー認可待ち",
    "user/infra-gated",
)

# plan.md 側で許容する marker（仕様 (c) は (b) に「認可待ち」を追加）。
PLAN_GATED_EVIDENCE_MARKERS: tuple[str, ...] = (*GATED_TASK_FLAG_MARKERS, "認可待ち")

# 実 docs/plan.md の「## Next」節では、個別タスク見出し本文へ都度 marker を
# 書く代わりに、節冒頭のガバナンス注記（例：「gated（偽装せず明示・自動実行は
# skip して次へ）：N-<番号> step3（user）／N-<番号>（user）／…」）で対象タスク ID を
# まとめて宣言する慣習がある。この宣言行を拾うため、上記の厳密な marker 文字列に
# 加えて英単語 "gated" 単体（大小文字を区別しない・単語境界必須）も許容する
# （"gated（"・"human-gated" 等の表記ゆれの吸収が目的。ハイフン区切りは非単語文字
# のため `\bgated\b` でも "human-gated" は問題なくマッチする）。単語境界を課さない
# 単純部分一致だと "delegated"（サブエージェント委譲）・"ungated"・"investigated"
# 等、語尾に "gated" を含むだけの無関係な英単語まで gated 宣言として誤検出し、
# gated_idle 偽装防止（P-003）を意図せず緩めてしまう。
#
# `GATED_TASK_FLAG_MARKERS` に含まれる "gated:" は `\bgated\b` を追加した後も
# それ自体は re.escape で無境界の literal alternative として残るため、
# "ungated:" の部分文字列としてなお誤マッチする（"ungated:" は「gated:」を
# 含む）。他の marker 文字列と分離し、直前が英字でない場合のみマッチする否定
# 後読みを付けて単語結合の "ungated:" を除外する。
_GATED_COLON_MARKER = "gated:"
_PLAN_GATED_EVIDENCE_MARKERS_WITHOUT_COLON = tuple(
    marker for marker in PLAN_GATED_EVIDENCE_MARKERS if marker != _GATED_COLON_MARKER
)
_PLAN_GATED_EVIDENCE_PATTERN = re.compile(
    "|".join(re.escape(marker) for marker in _PLAN_GATED_EVIDENCE_MARKERS_WITHOUT_COLON)
    + r"|\bgated\b"
    + rf"|(?<![A-Za-z]){re.escape(_GATED_COLON_MARKER)}",
    re.IGNORECASE,
)

# Next 節のタスク見出しは `### N-xxx` に加え、Backlog 昇格タスクが `### B-xxx` の
# ままいったん Next へ戻る運用（docs/plan.md の「Backlog 昇格タスク」慣習）にも
# 対応する。
_NEXT_TASK_HEADING_PATTERN = re.compile(r"^###\s+([NB]-\d+)\b.*$", re.MULTILINE)
# 見出し本文の完了マーカー。docs/plan.md の実運用では「## Done」へ移設済みの
# 見出しにのみ ✅ が付与され、「## Next」節に残存する完了タスクは本文中の
# `- 状態：**完了（merged）**` 等で完了を示す（仕様 (c) が言う「✅ の付かない
# 見出し」の実運用上の等価表現として採用する）。「状態（着手順 step 2 = 完了・
# タスク全体は継続）」のように 状態 の直後が「：」以外（例：「（」）の行は
# タスク全体としては未完了のまま個別 step のみ完了、という別の意味であり、
# 本パターンは意図的にマッチしない。
# 「完了」の直後に TOKEN_BOUNDARY_CHARS（英数字/かな/カナ/漢字）が続く場合は
# 「完了待ち」「完了予定」等の未完了状態を表す語の一部であるため除外する
# （否定先読み）。「完了（merged）」「完了**」「完了。」「完了」（行末）等、
# 区切り文字・括弧・強調終端・行末が続く場合のみ完了と判定する
# （前方一致のみでは「完了待ち」を誤って完了扱いしてしまうため）。
_TASK_COMPLETION_STATE_PATTERN = re.compile(
    rf"状態\s*[:：]\s*\*{{0,2}}完了(?![{TOKEN_BOUNDARY_CHARS}])"
)
# Next 節冒頭の宣言行や本文からタスク ID を抽出する際、Backlog 昇格タスク
# （`### B-xxx` として Next に残るケース）も拾えるよう N-/B- 双方に対応する。
_TASK_ID_TOKEN_PATTERN = re.compile(r"(?<!\d)[NB]-\d+(?!\d)")


def _has_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    """text に markers のいずれかが部分文字列として含まれるか判定する。"""
    return any(marker in text for marker in markers)


def _next_section_text(text: str) -> str:
    """plan.md 全文から「## Next」セクション本文を取得する（新旧見出し両対応）。"""
    next_section = _section(text, "Next（自動実行対象：優先順）")
    if not next_section.strip():
        next_section = _section(text, "Next（自動実行対象：最大3件）")
    return next_section


def _next_section_task_bodies(next_section_text: str) -> list[tuple[str, str]]:
    """Next 節内の `### N-xxx` 見出しごとに (タスクID, 本文) を返す。"""
    headings = list(_NEXT_TASK_HEADING_PATTERN.finditer(next_section_text))
    bodies: list[tuple[str, str]] = []
    for index, heading in enumerate(headings):
        body_start = heading.end()
        body_end = (
            headings[index + 1].start() if index + 1 < len(headings) else len(next_section_text)
        )
        bodies.append((heading.group(1), next_section_text[body_start:body_end]))
    return bodies


def _next_section_preamble(next_section_text: str) -> str:
    """Next 節冒頭（最初の `### N-xxx` 見出しより前）のガバナンス注記を返す。"""
    first_heading = _NEXT_TASK_HEADING_PATTERN.search(next_section_text)
    if first_heading is None:
        return next_section_text
    return next_section_text[: first_heading.start()]


def _preamble_declared_gated_task_ids(preamble_text: str) -> frozenset[str]:
    """Next 節冒頭の宣言行から、gated 宣言済みタスク ID を抽出する。

    行単位で marker（"gated" 等）と `N-\\d+` トークンの共起を確認し、共起した
    行に含まれる ID を「宣言済み gated」として扱う。marker を含まない行に
    タスク ID が単に言及されているだけでは宣言とみなさない（単なる言及と
    gated 宣言を混同しない）。
    """
    gated_ids: set[str] = set()
    for line in preamble_text.splitlines():
        if _PLAN_GATED_EVIDENCE_PATTERN.search(line) is None:
            continue
        gated_ids.update(_TASK_ID_TOKEN_PATTERN.findall(line))
    return frozenset(gated_ids)


def _next_section_unfinished_tasks_missing_gated_evidence(
    next_section_text: str,
) -> tuple[str, ...]:
    """Next 節内の未完了タスク見出しのうち、gated 根拠が確認できないものを返す。

    各見出しについて、(1) 本文の完了マーカー（`_TASK_COMPLETION_STATE_PATTERN`）が
    あれば完了扱いでスキップ、(2) 本文自体に gated marker があれば根拠ありでスキップ、
    (3) 節冒頭の宣言行にタスク ID が gated 宣言されていれば根拠ありでスキップ、
    のいずれにも該当しない見出しだけを「根拠不明」として返す。
    """
    task_bodies = _next_section_task_bodies(next_section_text)
    if not task_bodies:
        return ()
    preamble_gated_ids = _preamble_declared_gated_task_ids(
        _next_section_preamble(next_section_text)
    )
    missing: list[str] = []
    for task_id, body in task_bodies:
        if _TASK_COMPLETION_STATE_PATTERN.search(body):
            continue
        if _PLAN_GATED_EVIDENCE_PATTERN.search(body):
            continue
        if task_id in preamble_gated_ids:
            continue
        missing.append(task_id)
    return tuple(missing)


# 公式スキーマ（.github/agents/orchestrator.agent.md）の remaining_tasks は
# `["残タスクID"]` のようにタスク ID 単体の文字列配列である（marker 埋め込み
# 文字列は本リポジトリの実運用書式ではない）。単体 ID には marker 文字列が含まれ
# ないため、flag 側 (b) 検証ではこの形式を拒否せず、真偽の最終判定は plan.md 側の
# gated 宣言照合 (c) に委ねる。
_TASK_ID_ONLY_PATTERN = re.compile(r"^[NB]-\d+$")


def _remaining_task_item_is_gated(item: object) -> bool:
    """remaining_tasks の1要素が gated 根拠（仕様 (b)）を持つか判定する。

    受け入れる形式:
    - marker 埋め込み文字列（例: `"user-gated: N-<番号> ..."`）。従来からの
      テスト/運用書式。
    - 公式スキーマのタスクID単体文字列（例: `"N-<番号>"`、`"B-<番号>"`）。
      `.github/agents/orchestrator.agent.md` が定義する `remaining_tasks: ["残タスクID"]`
      形式そのもの。marker を含まないため本関数では拒否せず、plan.md 側の
      gated 宣言照合（`_gated_idle_plan_issue`）に判定を委ねる。
    - dict 形式（例: `{"task": "N-<番号>", "reason": "user-gated"}`）。dict の
      文字列値のいずれかに marker またはタスクID単体が含まれれば受け入れる。
    """
    if isinstance(item, str):
        stripped = item.strip()
        if _TASK_ID_ONLY_PATTERN.match(stripped):
            return True
        return _has_any_marker(item, GATED_TASK_FLAG_MARKERS)
    if isinstance(item, dict):
        for value in item.values():
            if not isinstance(value, str):
                continue
            if _TASK_ID_ONLY_PATTERN.match(value.strip()):
                return True
            if _has_any_marker(value, GATED_TASK_FLAG_MARKERS):
                return True
        return False
    return False


def _gated_idle_flag_issue(flag_data: dict[str, Any]) -> str:
    """gated_idle status における flag 側検証（仕様 (a)(b)）。空文字なら成立。"""
    current_task = flag_data.get("current_task")
    if current_task not in ("", None):
        return f"current_task が空ではありません: {current_task!r}"

    remaining_tasks = flag_data.get("remaining_tasks")
    if not isinstance(remaining_tasks, list) or not remaining_tasks:
        return "remaining_tasks が空、または配列ではありません"

    ungated_items = [
        str(item) for item in remaining_tasks if not _remaining_task_item_is_gated(item)
    ]
    if ungated_items:
        return (
            "remaining_tasks に gated マーカー無しの項目があります（gated_idle 偽装の疑い）: "
            + ", ".join(ungated_items)
        )
    return ""


def _next_section_declared_gated_task_ids(next_section_text: str) -> frozenset[str]:
    """Next 節で「gated 宣言済み」と確認できるタスク ID 集合を返す。

    本文に gated marker がある見出し、または節冒頭の宣言行で言及された ID を
    「宣言済み」として扱う（`_preamble_declared_gated_task_ids` は見出しの実在有無に
    関わらず宣言行に書かれた ID をそのまま集合へ入れる。「N-<番号>（forward-capture
    蓄積・Backlog）」のように Next に対応見出しを持たない ID も実運用の宣言慣習として
    許容する）。完了マーカーのみの見出し（例：`状態：**完了（merged）**` のみの見出し）は
    「終わった」ことを示すのであって「gated（人間/外部待ち）」ではないため対象に
    含めない（remaining_tasks が完了済みタスクの ID を騙るケースを見逃さない）。
    """
    task_bodies = _next_section_task_bodies(next_section_text)
    declared_ids: set[str] = set(
        _preamble_declared_gated_task_ids(_next_section_preamble(next_section_text))
    )
    for task_id, body in task_bodies:
        if _PLAN_GATED_EVIDENCE_PATTERN.search(body):
            declared_ids.add(task_id)
    return frozenset(declared_ids)


def _remaining_task_item_ids(item: object) -> frozenset[str]:
    """remaining_tasks の1要素からタスク ID（`N-xxx`/`B-xxx`）を抽出する。

    文字列要素はその文字列自体から、dict 要素は文字列値全てから走査してトークン
    抽出する。ID を含まない自由文（marker のみの説明文等）は空集合を返す。
    呼出し側はこの空集合を「ID 抽出不能・照合対象外（後方互換でマーカー判定のみに
    委ねる）」の合図として扱う。
    """
    if isinstance(item, str):
        return frozenset(_TASK_ID_TOKEN_PATTERN.findall(item))
    if isinstance(item, dict):
        ids: set[str] = set()
        for value in item.values():
            if isinstance(value, str):
                ids.update(_TASK_ID_TOKEN_PATTERN.findall(value))
        return frozenset(ids)
    return frozenset()


def _gated_idle_remaining_task_ids_issue(
    next_section_text: str,
    flag_data: dict[str, Any],
) -> str:
    """flag.remaining_tasks から抽出できるタスク ID が Next の gated 宣言済み

    見出し集合に存在するかを照合する（仕様 (c) 拡張）。ID を抽出できない要素
    （自由文 marker のみ等）は対象外とし、従来どおり flag 側のマーカー判定
    （`_gated_idle_flag_issue`）のみに委ねる（後方互換）。
    """
    remaining_tasks = flag_data.get("remaining_tasks")
    if not isinstance(remaining_tasks, list):
        return ""

    referenced_ids: set[str] = set()
    for item in remaining_tasks:
        referenced_ids.update(_remaining_task_item_ids(item))
    if not referenced_ids:
        return ""

    declared_ids = _next_section_declared_gated_task_ids(next_section_text)
    missing_ids = sorted(referenced_ids - declared_ids)
    if missing_ids:
        return (
            "flag の remaining_tasks に含まれるタスク ID が plan.md の Next の"
            "gated 宣言に見つかりません（古い/誤った flag の疑い）: " + ", ".join(missing_ids)
        )
    return ""


def _gated_idle_plan_issue(
    plan_path: Path | None,
    flag_data: dict[str, Any] | None = None,
) -> str:
    """gated_idle status における plan.md 側検証（仕様 (c)）。空文字なら成立。

    `flag_data` を渡すと、その `remaining_tasks` から抽出できるタスク ID が
    Next の gated 宣言済み見出し集合に存在するかも追加で照合する。省略時
    （既定 None）はこの追加照合を行わない（直接呼び出す既存テストとの後方互換のため）。
    """
    path = plan_path or PLAN_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"plan.md を読めません: {exc}"

    next_section = _next_section_text(text)
    if not next_section.strip():
        return "plan.md の Next セクションが見つからないか空です"

    # 本関数は _gated_idle_verification_issue から (a)(b) 検証（remaining_tasks が
    # 非空）成立後にのみ呼ばれる。にもかかわらず Next 節に `### N-xxx`/`### B-xxx`
    # 見出しが1件も見つからない場合、flag が申告する残タスクを plan.md 側と
    # 照合する手がかりが皆無であり、gated_idle が正しいと積極的に確認できない。
    # 照合不能を成立扱いにせず fail-close する（見出しゼロの Next では偽の
    # flag 申告でも検証をすり抜けてしまうため）。
    if not _next_section_task_bodies(next_section):
        return (
            "plan.md の Next セクションに ### N-xxx / ### B-xxx 見出しが1件も"
            "見つかりません（remaining_tasks との照合不能・gated_idle 偽装の疑い）"
        )

    missing = _next_section_unfinished_tasks_missing_gated_evidence(next_section)
    if missing:
        return (
            "plan.md の Next セクションに gated 根拠が確認できない未完了タスクがあります"
            "（gated_idle 偽装の疑い）: " + ", ".join(missing)
        )

    if flag_data is not None:
        remaining_ids_issue = _gated_idle_remaining_task_ids_issue(next_section, flag_data)
        if remaining_ids_issue:
            return remaining_ids_issue

    return ""


def _gated_idle_backlog_issue(plan_path: Path | None) -> str:
    """gated_idle でも維持する自動 Backlog 検査。空文字なら成立。

    gated_idle が迂回してよいのは「今月のゴール」未完了・成果証跡プレースホルダ等
    "今月のゴール・成果証跡" 側の確認だけである。`## Backlog` 節に残る自動実行対象
    タスク（`- B-\\d+` 契約）の取りこぼしは check_full_plan_completion() 経路と
    同一の `_has_active_auto_backlog_task` で独立検査し、gated_idle でも迂回させない。
    """
    path = plan_path or PLAN_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"plan.md を読めません: {exc}"

    backlog_section = _section(text, "Backlog")
    if not backlog_section.strip():
        return "必須セクション Backlog が見つからないか空です"
    if _has_active_auto_backlog_task(backlog_section):
        return "自動実行対象 Backlog に未完了タスクが残っています"
    return ""


def _gated_idle_verification_issue(
    flag_data: dict[str, Any],
    plan_path: Path | None,
) -> str:
    """gated_idle status の完了認証（仕様 (a)(b)(c) + 自動 Backlog 検査）。空文字なら成立。"""
    flag_issue = _gated_idle_flag_issue(flag_data)
    if flag_issue:
        return flag_issue
    plan_issue = _gated_idle_plan_issue(plan_path, flag_data)
    if plan_issue:
        return plan_issue
    return _gated_idle_backlog_issue(plan_path)


def full_plan_pre_release_safety_block_reason(
    *,
    flag_path: Path | None = None,
    plan_path: Path | None = None,
) -> str:
    """release-manager 前に必要な安全床（フラグ整合性）だけを検査する。

    PR merge 前は Next / Backlog / delivery state が未完了で正常なため、
    最終完了認証ではなく全プラン実行フラグ自体の整合性（破損していない・
    active と mode が矛盾しない）だけを安全床として見る。

    Args:
        flag_path: 全プラン実行フラグのパス（既定: FULL_PLAN_FLAG）。
        plan_path: plan.md のパス（既定: PLAN_PATH）。現状は使用しないが、
            呼出元 API の互換性のために受け付ける。
    """
    del plan_path  # 現状は未使用（API 互換のために受け付ける）
    flag_data = load_full_plan_flag(flag_path)
    if flag_data is None:
        return ""

    active = flag_data.get("active", True) is not False
    mode = flag_data.get("mode")
    if flag_data.get("_invalid"):
        return "全プラン実行フラグが破損しています"
    if not active:
        return ""
    if mode == "single_task":
        return (
            "全プラン実行フラグの mode が single_task ですが active=true です。"
            "single_task は active=false の単発モードでのみ許可されます。"
        )
    if mode not in {"full_plan", "full-plan-execution"}:
        actual_mode = "未設定" if mode is None else str(mode)
        return (
            f"全プラン実行フラグの mode が不明です: {actual_mode}。"
            "期待値は full_plan / full-plan-execution です。"
        )

    return ""


def check_full_plan_completion(
    plan_path: Path | None = None,
) -> CompletionCheck:
    """plan.md が全プラン完了を自己矛盾なく示しているか確認する。

    Args:
        plan_path: plan.md のパス（既定: PLAN_PATH）。
    """
    path = plan_path or PLAN_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return CompletionCheck(False, (f"plan.md を読めません: {exc}",))

    reasons: list[str] = []
    status_section = _section(text, "現状（Status）")
    goals_section = _section(text, "今月のゴール")
    # Nextセクション見出し新旧両対応
    next_section = _section(text, "Next（自動実行対象：優先順）")
    if not next_section.strip():
        next_section = _section(text, "Next（自動実行対象：最大3件）")
    backlog_section = _section(text, "Backlog")
    evidence_section = _section(text, OUTCOME_EVIDENCE_HEADING)

    required_sections = {
        "現状（Status）": status_section,
        "今月のゴール": goals_section,
        "Next（自動実行対象）": next_section,  # メッセージは汎用的にする
        "Backlog": backlog_section,
        OUTCOME_EVIDENCE_HEADING: evidence_section,
    }
    for heading, section in required_sections.items():
        if not section.strip():
            reasons.append(f"必須セクション {heading} が見つからないか空です")

    if "全自動実行対象完了" not in status_section and "全プラン完了" not in status_section:
        reasons.append("現状（Status）が全自動実行対象完了を示していません")
    if "現在なし" not in next_section:
        reasons.append("Next が『現在なし』ではありません")
    if _has_unfinished_goal(goals_section):
        reasons.append("今月のゴールに未完了項目が残っています")
    if _has_active_auto_backlog_task(backlog_section):
        reasons.append("自動実行対象 Backlog に未完了タスクが残っています")
    if evidence_section.strip():
        evidence_issue = _outcome_evidence_issue(evidence_section)
        if evidence_issue:
            reasons.append(evidence_issue)

    return CompletionCheck(not reasons, tuple(reasons))


def full_plan_completion_block_reason(
    *,
    flag_path: Path | None = None,
    plan_path: Path | None = None,
    require_delivery_state: bool = True,
) -> str:
    """完了をブロックすべき理由を返す。空文字なら許可。

    Args:
        flag_path: 全プラン実行フラグのパス（既定: FULL_PLAN_FLAG）。
        plan_path: plan.md のパス（既定: PLAN_PATH）。
        require_delivery_state: True の場合は最終完了認証として plan / delivery state
            まで要求する。False の場合は release-manager 前安全床だけを検査する。
    """
    flag_data = load_full_plan_flag(flag_path)
    if flag_data is None:
        return ""
    if not require_delivery_state:
        return full_plan_pre_release_safety_block_reason(
            flag_path=flag_path,
            plan_path=plan_path,
        )

    active = flag_data.get("active", True) is not False
    mode = flag_data.get("mode")
    if flag_data.get("_invalid"):
        check = check_full_plan_completion(plan_path)
        reasons = ["フラグが破損しています", *check.reasons]
        state = "active=true" if active else "active=false"
        return f"全プラン実行フラグは {state} ですが、完了認証に失敗しました: " + " / ".join(
            reasons
        )
    if not active:
        return ""
    if mode == "single_task":
        return (
            "全プラン実行フラグの mode が single_task ですが active=true です。"
            "single_task は active=false の単発モードでのみ許可されます。"
        )
    if mode not in {"full_plan", "full-plan-execution"}:
        actual_mode = "未設定" if mode is None else str(mode)
        return (
            f"全プラン実行フラグの mode が不明です: {actual_mode}。"
            "期待値は full_plan / full-plan-execution です。"
            "single_task は active=false の単発モードでのみ許可されます。"
        )

    if flag_data.get("status") == GATED_IDLE_STATUS:
        gated_idle_issue = _gated_idle_verification_issue(flag_data, plan_path)
        if gated_idle_issue:
            return (
                "全プラン実行フラグは gated_idle を名乗っていますが検証に失敗しました"
                "（P-003 gated_idle 偽装防止・fail-close）: " + gated_idle_issue
            )
        if require_delivery_state:
            delivery_issue = _delivery_state_issue(flag_data)
            if delivery_issue:
                return (
                    "全プラン実行フラグは gated_idle ですが、完了認証に失敗しました: "
                    + _full_plan_completion_fail(delivery_issue)
                )
        return ""

    check = check_full_plan_completion(plan_path)
    reasons = list(check.reasons)
    if require_delivery_state:
        delivery_issue = _delivery_state_issue(flag_data)
        if delivery_issue:
            reasons.append(_full_plan_completion_fail(delivery_issue))
    if not reasons:
        return ""

    state = "active=true" if active else "active=false"
    return f"全プラン実行フラグは {state} ですが、完了認証に失敗しました: " + " / ".join(reasons)


def full_plan_gated_idle_status_message(
    *,
    flag_path: Path | None = None,
    plan_path: Path | None = None,
) -> str:
    """status=gated_idle が成立している場合の非ブロッキング情報メッセージを返す。

    成立していない場合（status フィールド不在・他値・検証失敗）は空文字列を返す。
    block/allow の判定ロジックは重複させず、引き続き full_plan_completion_block_reason()
    が単独で担う。本関数は「gated_idle 終端状態に到達した」ことを呼出元（Stop hook 等）
    へ伝える付随情報のみを提供する。
    """
    flag_data = load_full_plan_flag(flag_path)
    if flag_data is None or flag_data.get("status") != GATED_IDLE_STATUS:
        return ""
    reason = full_plan_completion_block_reason(
        flag_path=flag_path,
        plan_path=plan_path,
    )
    if reason:
        return ""
    remaining_tasks = flag_data.get("remaining_tasks")
    count = len(remaining_tasks) if isinstance(remaining_tasks, list) else 0
    return (
        f"gated_idle: 残タスクは全て user/infra-gated（{count} 件）。"
        "人間側アクションの一覧は docs/plan.md を参照。"
    )
