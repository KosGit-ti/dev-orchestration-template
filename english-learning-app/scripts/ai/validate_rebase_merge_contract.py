#!/usr/bin/env python3
"""rebase merge 直前の PR head・commit 数・終端 anchor を検証する。

GitHub の rebase merge は元 branch の SHA を main 上で作り直すため、mainline 上の
commit 群だけから PR 境界を完全には復元できない。本 CLI は merge 前の GitHub PR
metadata を trusted input として、次を fail-close で照合する。

- checkout 中の ``HEAD`` と PR の ``headRefOid`` が一致する
- PR の base が ``main`` で、最初の PR commit の親が現在の ``baseRefOid`` と一致する
- PR の全 commit 中、anchor-looking token は最終 commit 件名の 1 件だけである
- 最終件名が ``[PR #N; commits=M]`` で終わり、N は対象 PR、M は実 commit 数と一致する
- 最終 commit の oid が ``headRefOid`` と一致する
- 全 PR commit が local に存在する 1-parent の連続鎖で、元から空の commit ではない
- final anchor の ``docs/ai/reviews/*.json`` が HEAD tree 上の通常ファイルとして
  存在し、非空かつ JSON として解析できる
- terminal state-sync は厳密 marker と terminal sentinel の組を要求し、checkpoint／
  ledger／plan の意味遷移、pre-PR 証跡、evidence-only anchor を merge 前に検証する
- main の required status checks が strict（最新 base 必須）である
- 対象 head に対する最新の trusted release judgement comment が ``MERGE`` marker と
  Evidence Schema v2 の release evidence bundle（``scripts/ai/release_evidence_bundle.py``）を
  同梱し、bundle 検証が pass している（marker 単独の MERGE は merge 根拠にしない）

検証済み SHA は ``--head-sha-only`` で出力し、``gh pr merge --match-head-commit`` へ
そのまま渡す。検証後に head または base が動いた場合は GitHub 側が merge を拒否する。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import release_evidence_bundle  # noqa: E402
from scripts.ai import validate_current_state as current_state  # noqa: E402
from scripts.ai import validate_release_evidence as vre  # noqa: E402

SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
ANCHOR_RE = re.compile(r"\[PR #([1-9][0-9]*); commits=([1-9][0-9]*)\]")
ANCHOR_MARKER_RE = re.compile(r"\[[Pp][Rr][ \t]*#")
REVIEW_EVIDENCE_PATH_RE = re.compile(r"^docs/ai/reviews/[^/]+\.json$")
# release judgement comment を信頼する author association（scripts/ops/pr_autopilot.sh と同じ）。
RELEASE_TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _commit_headline(commit: Any, *, index: int) -> tuple[str | None, str | None]:
    if not isinstance(commit, dict):
        return None, f"commits[{index}] が object ではありません"
    headline = commit.get("messageHeadline")
    if not isinstance(headline, str) or not headline:
        return None, f"commits[{index}].messageHeadline が空または文字列ではありません"
    return headline, None


def validate_contract_payload(
    *,
    pr_number: int,
    local_head: str,
    payload: Any,
    terminal_changed_paths: list[str],
) -> tuple[list[str], str | None, int | None]:
    """GitHub PR metadata と local HEAD を照合する。"""
    issues: list[str] = []
    if pr_number <= 0:
        issues.append("PR 番号は正整数で指定してください")
    if not SHA_RE.fullmatch(local_head):
        issues.append(f"local HEAD が 40 桁 SHA ではありません: {local_head!r}")
    if not terminal_changed_paths:
        issues.append("anchor commit の変更 path を取得できないか、変更がありません")
    invalid_terminal_paths = [
        path for path in terminal_changed_paths if not REVIEW_EVIDENCE_PATH_RE.fullmatch(path)
    ]
    if invalid_terminal_paths:
        issues.append(
            "anchor commit は docs/ai/reviews/*.json だけを変更できます: "
            + "、".join(invalid_terminal_paths)
        )
    if not isinstance(payload, dict):
        return [*issues, "gh pr view の JSON root が object ではありません"], None, None

    base_ref_name = payload.get("baseRefName")
    if base_ref_name != "main":
        issues.append(f"baseRefName が main ではありません: {base_ref_name!r}")
    base_ref_oid = payload.get("baseRefOid")
    if not isinstance(base_ref_oid, str) or not SHA_RE.fullmatch(base_ref_oid):
        issues.append("baseRefOid が 40 桁 SHA ではありません")

    head_ref_oid = payload.get("headRefOid")
    if not isinstance(head_ref_oid, str) or not SHA_RE.fullmatch(head_ref_oid):
        issues.append("headRefOid が 40 桁 SHA ではありません")
        head_ref_oid = None
    elif head_ref_oid != local_head:
        issues.append(f"local HEAD {local_head} と PR headRefOid {head_ref_oid} が一致しません")

    commits = payload.get("commits")
    if not isinstance(commits, list) or not commits:
        issues.append("commits が空または配列ではありません")
        return issues, head_ref_oid, None

    headlines: list[str] = []
    for index, commit in enumerate(commits):
        headline, issue = _commit_headline(commit, index=index)
        if issue is not None:
            issues.append(issue)
            continue
        assert headline is not None
        headlines.append(headline)
    if len(headlines) != len(commits):
        return issues, head_ref_oid, len(commits)

    marker_locations = [
        (index, match.start())
        for index, headline in enumerate(headlines)
        for match in ANCHOR_MARKER_RE.finditer(headline)
    ]
    if len(marker_locations) != 1 or marker_locations[0][0] != len(headlines) - 1:
        issues.append("anchor-looking token は最終 commit 件名の 1 件だけにしてください")

    terminal_subject = headlines[-1]
    anchors = list(ANCHOR_RE.finditer(terminal_subject))
    if len(anchors) != 1 or anchors[0].end() != len(terminal_subject):
        issues.append("最終 commit 件名は単一の `[PR #N; commits=M]` anchor で終える必要があります")
    else:
        anchor = anchors[0]
        if int(anchor.group(1)) != pr_number:
            issues.append(
                f"anchor の PR 番号 #{anchor.group(1)} が対象 PR #{pr_number} と一致しません"
            )
        if int(anchor.group(2)) != len(commits):
            issues.append(
                f"anchor の commit 数 {anchor.group(2)} が GitHub の実件数"
                f" {len(commits)} と一致しません"
            )

    terminal = commits[-1]
    terminal_oid = terminal.get("oid") if isinstance(terminal, dict) else None
    if not isinstance(terminal_oid, str) or not SHA_RE.fullmatch(terminal_oid):
        issues.append("最終 commit の oid が 40 桁 SHA ではありません")
    elif head_ref_oid is not None and terminal_oid != head_ref_oid:
        issues.append(f"最終 commit oid {terminal_oid} と headRefOid {head_ref_oid} が一致しません")

    return issues, head_ref_oid, len(commits)


def validate_anchor_review_evidence_blobs(
    *,
    head_sha: str,
    changed_paths: list[str],
    repo_root: Path,
) -> list[str]:
    """anchor の review JSON が最終 HEAD tree 上の有効な通常ファイルか検証する。"""
    if not SHA_RE.fullmatch(head_sha):
        # SHA 形式のエラーは共通契約 validator が報告する。
        return []

    issues: list[str] = []
    for path in changed_paths:
        if REVIEW_EVIDENCE_PATH_RE.fullmatch(path) is None:
            # path scope のエラーは共通契約 validator が報告する。
            continue

        tree_result = subprocess.run(
            ["git", "ls-tree", "-z", head_sha, "--", path],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
        if tree_result.returncode != 0:
            detail = tree_result.stderr.decode("utf-8", errors="replace").strip()
            issues.append(
                f"anchor evidence の tree entry を取得できません（{path}）:"
                f" {detail or f'exit code {tree_result.returncode}'}"
            )
            continue
        entry = tree_result.stdout.removesuffix(b"\x00")
        metadata, separator, listed_path = entry.partition(b"\t")
        metadata_parts = metadata.split()
        try:
            decoded_path = listed_path.decode("utf-8")
        except UnicodeDecodeError:
            decoded_path = ""
        if (
            not separator
            or decoded_path != path
            or len(metadata_parts) != 3
            or metadata_parts[0] not in (b"100644", b"100755")
            or metadata_parts[1] != b"blob"
        ):
            issues.append(
                "anchor evidence は最終 HEAD tree 上に通常ファイルとして"
                f"存在する必要があります: {path}"
            )
            continue

        blob_result = subprocess.run(
            ["git", "cat-file", "blob", f"{head_sha}:{path}"],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
        if blob_result.returncode != 0:
            detail = blob_result.stderr.decode("utf-8", errors="replace").strip()
            issues.append(
                f"anchor evidence blob を取得できません（{path}）:"
                f" {detail or f'exit code {blob_result.returncode}'}"
            )
            continue
        if not blob_result.stdout:
            issues.append(f"anchor evidence JSON が空です: {path}")
            continue
        try:
            json.loads(blob_result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            issues.append(f"anchor evidence JSON を解析できません（{path}）: {exc}")
    return issues


def validate_local_pr_commit_chain(*, payload: Any, repo_root: Path) -> list[str]:
    """GitHub が rebase 時に落とさない 1-parent・非空の連続 commit 鎖か検査する。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("commits"), list):
        return ["local commit 鎖を検査できる commits 配列がありません"]
    commits = payload["commits"]
    if not commits:
        return ["local commit 鎖が空です"]
    base_ref_oid = payload.get("baseRefOid")
    if not isinstance(base_ref_oid, str) or not SHA_RE.fullmatch(base_ref_oid):
        return ["local commit 鎖を検査できる baseRefOid がありません"]

    issues: list[str] = []
    previous_oid: str | None = None
    for index, commit in enumerate(commits):
        oid = commit.get("oid") if isinstance(commit, dict) else None
        if not isinstance(oid, str) or not SHA_RE.fullmatch(oid):
            issues.append(f"commits[{index}].oid が 40 桁 SHA ではありません")
            previous_oid = None
            continue

        parents_result = _run(["git", "show", "-s", "--format=%P", oid], cwd=repo_root)
        if parents_result.returncode != 0:
            detail = parents_result.stderr.strip() or f"exit code {parents_result.returncode}"
            issues.append(f"commits[{index}] {oid} の親取得に失敗しました: {detail}")
            previous_oid = oid
            continue
        parents = parents_result.stdout.split()
        if len(parents) != 1:
            issues.append(
                f"commits[{index}] {oid} の親数が {len(parents)} です"
                "（rebase 1:1 境界のため 1 親だけを許可）"
            )
            previous_oid = oid
            continue
        expected_parent = base_ref_oid if previous_oid is None else previous_oid
        if parents[0] != expected_parent:
            issues.append(
                f"commits[{index}] {oid} の親 {parents[0]} が期待する親"
                f" {expected_parent} と一致しません"
            )

        diff_result = _run(
            ["git", "diff-tree", "--quiet", parents[0], oid, "--"],
            cwd=repo_root,
        )
        if diff_result.returncode == 0:
            issues.append(
                f"commits[{index}] {oid} は元から空の commit です"
                "（GitHub rebase merge が drop するため拒否）"
            )
        elif diff_result.returncode != 1:
            detail = diff_result.stderr.strip() or f"exit code {diff_result.returncode}"
            issues.append(f"commits[{index}] {oid} の tree 差分取得に失敗しました: {detail}")
        previous_oid = oid
    return issues


def validate_terminal_state_sync_candidate(
    *,
    pr_number: int,
    payload: Any,
    repo_root: Path,
) -> list[str]:
    """terminal state-sync PR だけを current-state の意味検証へ渡す。

    通常の次タスク PR も先頭で checkpoint／ledger／plan を同期するため、変更 path
    だけでは terminal と判定しない。current-state 側の厳密 marker と、結果 checkpoint
    の terminal sentinel がそろう場合に限り専用検証を実行する。
    """
    if not isinstance(payload, dict):
        return []
    commits = payload.get("commits")
    if not isinstance(commits, list) or not commits:
        return []

    commit_oids: list[str] = []
    for commit in commits:
        oid = commit.get("oid") if isinstance(commit, dict) else None
        if not isinstance(oid, str) or not SHA_RE.fullmatch(oid):
            # payload の形式エラーは共通契約 validator が報告する。
            return []

        commit_oids.append(oid)

    unit = current_state.MainlineUnit(
        kind="rebase",
        commits=tuple(commit_oids),
        terminal_sha=commit_oids[-1],
        pr_number=str(pr_number),
    )
    is_candidate, candidate_issue = current_state.is_terminal_state_sync_candidate(
        unit,
        repo_root=repo_root,
    )
    if candidate_issue is not None:
        return [candidate_issue]
    if not is_candidate:
        return []

    checkpoint_result = _run(
        [
            "git",
            "show",
            f"{unit.terminal_sha}:{current_state.CHECKPOINT_REL}",
        ],
        cwd=repo_root,
    )
    if checkpoint_result.returncode != 0:
        return ["terminal state-sync の結果 checkpoint を最終 HEAD から取得できません"]
    checkpoint_text = checkpoint_result.stdout
    checkpoint_sha = current_state.extract_checkpoint_sha(checkpoint_text)
    if checkpoint_sha is None:
        return ["terminal state-sync の結果 checkpoint から merge SHA を抽出できません"]
    return current_state.check_terminal_state_sync_unit(
        unit,
        checkpoint_sha=checkpoint_sha,
        repo_root=repo_root,
    )


def fetch_pr_issue_comments(*, pr_number: int, repo_root: Path) -> tuple[list[Any], str | None]:
    """PR の issue comment を全ページ取得する（``gh api --paginate`` の連結 JSON を解析）。"""
    result = _run(
        [
            "gh",
            "api",
            f"repos/{{owner}}/{{repo}}/issues/{pr_number}/comments?per_page=100",
            "--paginate",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        return [], f"release judgement comment を取得できません: {detail}"
    decoder = json.JSONDecoder()
    text = result.stdout
    position = 0
    comments: list[Any] = []
    try:
        while position < len(text):
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text):
                break
            page, position = decoder.raw_decode(text, position)
            if not isinstance(page, list):
                return [], "release judgement comment の page が配列ではありません"
            comments.extend(page)
    except json.JSONDecodeError as exc:
        return [], f"release judgement comment の JSON を解析できません: {exc}"
    return comments, None


def select_latest_release_judgement_comment(
    comments: list[Any],
    *,
    pr_number: int,
    head_sha: str,
    allowed_actors: frozenset[str] | None = None,
) -> dict[str, Any] | None:
    """対象 PR / head の互換 marker を含む trusted comment のうち最新の 1 件を返す。

    trusted は author association（OWNER / MEMBER / COLLABORATOR）と、指定があれば
    actor allowlist の両方を満たす投稿に限る。最新は ``(updated_at, id)`` の最大で決める
    （``scripts/ops/pr_autopilot.sh`` の ``latest_release_decision`` と同じ規則）。
    """
    head = head_sha.lower()
    candidates: list[tuple[str, int, dict[str, Any]]] = []
    for item in comments:
        if not isinstance(item, dict):
            continue
        user = item.get("user")
        login = str((user or {}).get("login") or "").lower() if isinstance(user, dict) else ""
        association = str(item.get("author_association") or "").upper()
        if association not in RELEASE_TRUSTED_ASSOCIATIONS:
            continue
        if allowed_actors is not None and login not in allowed_actors:
            continue
        body = item.get("body")
        if not isinstance(body, str):
            continue
        markers = vre.parse_release_decision_markers(body)
        if not any(marker.pr == pr_number and marker.head_sha == head for marker in markers):
            continue
        comment_id = item.get("id")
        candidates.append(
            (
                str(item.get("updated_at") or ""),
                comment_id if isinstance(comment_id, int) else 0,
                item,
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda entry: (entry[0], entry[1]))[2]


def validate_release_evidence_comment(*, pr_number: int, head_sha: str, body: str) -> list[str]:
    """判定 comment の marker と Evidence Schema v2 bundle を検証し、issue 一覧を返す。"""
    result = release_evidence_bundle.validate_bundle_comment(
        body, expected_pr=pr_number, expected_head_sha=head_sha
    )
    issues: list[str] = []
    if result.marker is None or result.marker.decision != vre.MERGE_DECISION:
        decision = "なし" if result.marker is None else result.marker.decision
        issues.append(
            "release judgement の最新 marker が対象 head の MERGE ではありません"
            f"（decision={decision}）"
        )
    if not result.ok:
        for violation in result.violations():
            issues.append(f"release evidence: {violation.code}: {violation.message}")
        if not result.violations():
            issues.append("release evidence: bundle 検証が pass していません")
    return issues


def validate_release_evidence_gate(
    *,
    pr_number: int,
    head_sha: str,
    repo_root: Path,
    comment_file: Path | None = None,
    allowed_actors: frozenset[str] | None = None,
) -> list[str]:
    """対象 head の最新 release judgement comment に有効な release evidence bundle を要求する。

    ``comment_file`` が与えられた場合はその本文だけを検証する（network 不要・試験や
    archive 前の確認用）。それ以外は GitHub の PR issue comment を取得して選ぶ。
    """
    if comment_file is not None:
        try:
            body = comment_file.read_text(encoding="utf-8")
        except OSError as exc:
            return [f"release judgement comment file を読めません: {exc}"]
        return validate_release_evidence_comment(pr_number=pr_number, head_sha=head_sha, body=body)

    comments, fetch_issue = fetch_pr_issue_comments(pr_number=pr_number, repo_root=repo_root)
    if fetch_issue is not None:
        return [fetch_issue]
    latest = select_latest_release_judgement_comment(
        comments, pr_number=pr_number, head_sha=head_sha, allowed_actors=allowed_actors
    )
    if latest is None:
        return [
            "対象 head の release judgement comment（release-manager-decision:v1 marker）が"
            " trusted actor から投稿されていません"
        ]
    return validate_release_evidence_comment(
        pr_number=pr_number, head_sha=head_sha, body=str(latest.get("body") or "")
    )


def validate_strict_base_protection(*, repo_root: Path) -> list[str]:
    """main の required status checks が最新 base 必須かを検査する。"""
    result = _run(
        [
            "gh",
            "api",
            "repos/{owner}/{repo}/branches/main/protection/required_status_checks",
            "--jq",
            ".strict",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        return [f"main の required status checks 設定を取得できません: {detail}"]
    if result.stdout.strip() != "true":
        return [
            "main の required status checks が strict=true ではありません"
            "（検査後の base 更新を GitHub 側で拒否できないため merge 不可）"
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True, help="検証する GitHub PR 番号")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="リポジトリルート",
    )
    parser.add_argument(
        "--head-sha-only",
        action="store_true",
        help="成功時に検証済み head SHA だけを stdout へ出力する",
    )
    parser.add_argument(
        "--release-evidence-comment-file",
        type=Path,
        help="release judgement comment 本文の file（指定時は GitHub から取得せずこれを検証する）",
    )
    parser.add_argument(
        "--release-actors",
        default="",
        help="release judgement comment を信頼する actor login のカンマ区切り allowlist（省略時は"
        " author association だけで判定）",
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    allowed_actors: frozenset[str] | None = None
    actor_logins = {item.strip().lower() for item in args.release_actors.split(",") if item.strip()}
    if actor_logins:
        allowed_actors = frozenset(actor_logins)

    head_result = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    if head_result.returncode != 0:
        detail = head_result.stderr.strip() or f"exit code {head_result.returncode}"
        print(f"git rev-parse HEAD に失敗しました: {detail}", file=sys.stderr)
        return 1
    local_head = head_result.stdout.strip()

    paths_result = _run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        cwd=repo_root,
    )
    if paths_result.returncode != 0:
        detail = paths_result.stderr.strip() or f"exit code {paths_result.returncode}"
        print(f"anchor commit の変更 path 取得に失敗しました: {detail}", file=sys.stderr)
        return 1
    terminal_changed_paths = [path for path in paths_result.stdout.splitlines() if path.strip()]

    pr_result = _run(
        [
            "gh",
            "pr",
            "view",
            str(args.pr),
            "--json",
            "baseRefName,baseRefOid,headRefOid,commits",
        ],
        cwd=repo_root,
    )
    if pr_result.returncode != 0:
        detail = pr_result.stderr.strip() or f"exit code {pr_result.returncode}"
        print(f"gh pr view #{args.pr} に失敗しました: {detail}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(pr_result.stdout)
    except json.JSONDecodeError as exc:
        print(f"gh pr view #{args.pr} の JSON を解析できません: {exc}", file=sys.stderr)
        return 1

    issues, validated_head, commit_count = validate_contract_payload(
        pr_number=args.pr,
        local_head=local_head,
        payload=payload,
        terminal_changed_paths=terminal_changed_paths,
    )
    issues.extend(
        validate_anchor_review_evidence_blobs(
            head_sha=local_head,
            changed_paths=terminal_changed_paths,
            repo_root=repo_root,
        )
    )
    issues.extend(validate_local_pr_commit_chain(payload=payload, repo_root=repo_root))
    issues.extend(
        validate_terminal_state_sync_candidate(
            pr_number=args.pr,
            payload=payload,
            repo_root=repo_root,
        )
    )
    issues.extend(validate_strict_base_protection(repo_root=repo_root))
    issues.extend(
        validate_release_evidence_gate(
            pr_number=args.pr,
            head_sha=local_head,
            repo_root=repo_root,
            comment_file=args.release_evidence_comment_file,
            allowed_actors=allowed_actors,
        )
    )
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        print(f"rebase merge contract NG（{len(issues)} 件）", file=sys.stderr)
        return 1

    assert validated_head is not None
    assert commit_count is not None
    if args.head_sha_only:
        print(validated_head)
    else:
        print(
            f"rebase merge contract OK: PR #{args.pr},"
            f" head={validated_head}, commits={commit_count}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
