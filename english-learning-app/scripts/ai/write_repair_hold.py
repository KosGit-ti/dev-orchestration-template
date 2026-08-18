#!/usr/bin/env python3
"""repair-hold state-sync の `correction_repair` object（recovery schema v2）を決定的に生成する。

DEC-20260815-003 決定 6（post-S2 回復の formal 単位）の repair-hold を、手書きさせずに
materialization manifest と closeout event 集合から導出する。affected task／claim・
missing slot key・repair target・`blocked_by` token を `scripts/ai/repair_hold.py` の再導出で
決め、`resolve_current_state.validate_correction_repair_object` で自己検証してから YAML を
stdout へ出す。**plan／checkpoint／full-plan flag へは書かない**（Orchestrator が
state-sync branch で貼る。resolver／validator は同じ導出で exact 一致を再検査する）。

使い方::

    uv run python scripts/ai/write_repair_hold.py \\
        --task-ids N-602A N-598C N-594B \\
        --discovery-artifact docs/ai/reviews/<discovery>.json \\
        --discovered-at-head <40hex> \\
        --held-active-json '{"task_id":"N-600","substep":"root-cause-analysis",
                             "state":"blocked","source_plan_revision":"<40hex>"}' \\
        --resume-projection-json '{"task_id":"N-600","substep":"root-cause-analysis",
                                   "terminal":false}'

入力:

- `--task-ids`: 不完全 closeout batch を持つ task（seed）。各 task の unsuperseded closeout
  event 全件を `invalid_batch_ids` にする（「raw artifact が偽」ではなく「atomic closeout batch
  として不十分」の宣言。既存 raw artifact 自体を invalid にする場合は
  `--invalid-evidence-ids` を別途渡す）。
- `--discovery-artifact` / `--discovery-evidence-id`: 発見証跡（少なくとも一方）。artifact は
  repo 内 `docs/` 配下の実 file で raw SHA-256 を実測する（登録前 artifact を指せる）。
- `--discovered-at-head`: 発見時点の main head。
- `--held-active-json`: repair-hold 設置前の active block（task_id／substep／state／
  source_plan_revision）。`source_plan_revision` 時点の plan と一致することを検証する。
- `--resume-projection-json`: 全 affected truth 回復後に復元する projection。
- `--phase`: 既定 `staging`。

出力は `correction_repair:` を top-level に持つ YAML（plan の active block へそのまま貼れる形）
と、stderr へ `task_id`（repair target）／`substep`／`blocked_by` の同期値と導出 note。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import repair_hold  # noqa: E402
from scripts.ai import resolve_current_state as resolver  # noqa: E402
from scripts.ai import validate_current_state as current_state  # noqa: E402
from scripts.ai.task_closeout_event import TRACKED_PATH_RE, sha256_bytes  # noqa: E402

EXIT_DERIVATION_REJECTED = 2


class RepairHoldError(ValueError):
    """repair-hold の生成契約違反（fail-close）。"""


def _load_json_object(raw: str, *, label: str) -> dict[str, Any]:
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RepairHoldError(f"{label} を JSON として解析できない: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RepairHoldError(f"{label} は object でなければならない")
    return loaded


def load_manifest(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """materialization manifest を `(payload, issues)` で読む（読込不能は issue・fail-close）。"""
    if not path.is_file():
        return None, [f"manifest が存在しない: {path}"]
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, [f"manifest を読めない: {path}: {exc}"]
    if not isinstance(loaded, dict):
        return None, [f"manifest が object ではない: {path}"]
    return loaded, []


def _discovery_evidence(repo_root: Path, artifact_path: str) -> dict[str, Any]:
    if not TRACKED_PATH_RE.match(artifact_path) or ".." in artifact_path.split("/"):
        raise RepairHoldError(
            f"--discovery-artifact は `docs/` 配下の相対 path が必要である（traversal 拒否）: "
            f"{artifact_path}"
        )
    target = (repo_root / artifact_path).resolve()
    if not target.is_relative_to(repo_root.resolve()):
        raise RepairHoldError(f"--discovery-artifact が repo 外へ解決される: {artifact_path}")
    if not target.is_file():
        raise RepairHoldError(f"--discovery-artifact が存在しない: {artifact_path}")
    return {
        "artifact_path": artifact_path,
        "artifact_raw_sha256": sha256_bytes(target.read_bytes()),
    }


def build_correction_repair(
    *,
    manifest: dict[str, Any],
    events: dict[str, dict[str, Any]],
    task_ids: list[str],
    invalid_evidence_ids: list[str],
    discovery_evidence_id: str | None,
    discovery_evidence: dict[str, Any] | None,
    discovered_at_head: str,
    held_active: dict[str, Any],
    resume_projection: dict[str, Any],
    phase: str,
) -> tuple[dict[str, Any], repair_hold.RecoveryDerivation]:
    """`correction_repair` object と再導出結果を返す（導出不能は RepairHoldError）。"""
    current_events = repair_hold.unsuperseded_events(events)
    invalid_batch_ids: list[str] = []
    for task_id in task_ids:
        batch_ids = [
            event_id
            for event_id, event in current_events.items()
            if event.get("task_id") == task_id
        ]
        if not batch_ids:
            raise RepairHoldError(
                f"task:{task_id} の unsuperseded closeout event が存在しない（invalid batch を"
                " 特定できない）"
            )
        invalid_batch_ids.extend(batch_ids)
    invalid_batch_ids = repair_hold.utf8_sorted(invalid_batch_ids)
    invalid_evidence_ids = repair_hold.utf8_sorted(invalid_evidence_ids)

    derived = repair_hold.derive_recovery_sets(
        manifest,
        events=events,
        invalid_evidence_ids=invalid_evidence_ids,
        invalid_batch_ids=invalid_batch_ids,
    )
    if derived.issues:
        raise RepairHoldError("affected set を導出できない: " + "; ".join(derived.issues))
    recovered = resolver.recovered_affected_tasks(
        manifest=manifest, events=events, affected_task_ids=derived.affected_task_ids
    )
    target = repair_hold.repair_target_task_id(
        manifest, affected_task_ids=derived.affected_task_ids, recovered_task_ids=recovered
    )
    if target is None:
        raise RepairHoldError(
            "affected task が全て回復済みのため repair-hold を作る理由がない"
            f"（affected={list(derived.affected_task_ids)}）"
        )
    obj: dict[str, Any] = {
        "recovery_schema_version": repair_hold.RECOVERY_SCHEMA_VERSION,
        "discovery_evidence_id": discovery_evidence_id,
        "discovery_evidence": discovery_evidence,
        "discovered_at_head": discovered_at_head,
        "held_active": {name: held_active.get(name) for name in repair_hold.HELD_ACTIVE_FIELDS},
        "invalid_evidence_ids": invalid_evidence_ids,
        "invalid_batch_ids": invalid_batch_ids,
        "missing_slot_keys": [dict(key) for key in derived.missing_slot_keys],
        "affected_task_ids": list(derived.affected_task_ids),
        "affected_claim_ids": list(derived.affected_claim_ids),
        "repair_target_task_id": target,
        "resume_projection": {
            name: resume_projection.get(name) for name in repair_hold.RESUME_PROJECTION_FIELDS
        },
        "phase": phase,
    }
    return obj, derived


def dump_yaml(obj: dict[str, Any]) -> str:
    """決定的な YAML（挿入順・unicode・block style）。"""
    text: str = yaml.safe_dump(
        {"correction_repair": obj},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=4096,
    )
    return text


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=f"materialization manifest（既定: <repo-root>/{current_state.MANIFEST_REL}）",
    )
    parser.add_argument(
        "--task-ids",
        nargs="+",
        required=True,
        help=(
            "不完全 closeout batch を持つ task（各 task の unsuperseded event を"
            " invalid_batch_ids へ）"
        ),
    )
    parser.add_argument(
        "--invalid-evidence-ids",
        nargs="*",
        default=[],
        help="raw artifact 自体を invalid にする registry entry ID（省略可）",
    )
    parser.add_argument(
        "--discovery-artifact", default=None, help="発見証跡 artifact の repo 相対 path"
    )
    parser.add_argument(
        "--discovery-evidence-id", default=None, help="登録済み発見証跡の evidence ID"
    )
    parser.add_argument(
        "--discovered-at-head", required=True, help="発見時点の main head（40 hex）"
    )
    parser.add_argument(
        "--held-active-json", required=True, help="repair-hold 設置前の active block（JSON）"
    )
    parser.add_argument(
        "--resume-projection-json", required=True, help="回復後に復元する projection（JSON）"
    )
    parser.add_argument("--phase", choices=repair_hold.REPAIR_PHASES, default="staging")
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="常に print-only（plan／checkpoint／flag へは書かない。互換のため受理する）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root: Path = args.repo_root.resolve()
    manifest_path: Path = (
        args.manifest if args.manifest is not None else repo_root / current_state.MANIFEST_REL
    )
    manifest, manifest_issues = load_manifest(manifest_path)
    if manifest is None:
        for issue in manifest_issues:
            print(f"repair hold NG: {issue}", file=sys.stderr)
        return EXIT_DERIVATION_REJECTED

    events, event_issues = repair_hold.load_closeout_events(repo_root)
    if event_issues:
        for issue in event_issues:
            print(f"repair hold NG: {issue}", file=sys.stderr)
        return EXIT_DERIVATION_REJECTED

    try:
        if args.discovery_artifact is None and args.discovery_evidence_id is None:
            raise RepairHoldError(
                "--discovery-artifact と --discovery-evidence-id の少なくとも一方が必要である"
            )
        discovery_evidence = (
            _discovery_evidence(repo_root, args.discovery_artifact)
            if args.discovery_artifact is not None
            else None
        )
        held_active = _load_json_object(args.held_active_json, label="--held-active-json")
        resume_projection = _load_json_object(
            args.resume_projection_json, label="--resume-projection-json"
        )
        obj, derived = build_correction_repair(
            manifest=manifest,
            events=events,
            task_ids=list(args.task_ids),
            invalid_evidence_ids=list(args.invalid_evidence_ids),
            discovery_evidence_id=args.discovery_evidence_id,
            discovery_evidence=discovery_evidence,
            discovered_at_head=str(args.discovered_at_head),
            held_active=held_active,
            resume_projection=resume_projection,
            phase=args.phase,
        )
    except RepairHoldError as exc:
        print(f"repair hold NG: {exc}", file=sys.stderr)
        return EXIT_DERIVATION_REJECTED

    # 自己検証: resolver／validator と同じ完全検査を通らない object は出力しない。
    issues = resolver.validate_correction_repair_object(
        obj, manifest=manifest, repo_root=repo_root, events=events
    )
    if issues:
        for issue in issues:
            print(f"repair hold NG: {issue}", file=sys.stderr)
        print(f"repair hold NG（自己検証 {len(issues)} 件）", file=sys.stderr)
        return EXIT_DERIVATION_REJECTED

    print(dump_yaml(obj), end="")
    blocked_by = repair_hold.expected_blocked_by(obj, manifest)
    print(f"# task_id: {obj['repair_target_task_id']}", file=sys.stderr)
    print(f"# substep: {repair_hold.HOLD_SUBSTEP_EVIDENCE_CORRECTION}", file=sys.stderr)
    print(f"# state: {current_state.CLOSING_STATE}", file=sys.stderr)
    print(f"# blocked_by: {json.dumps(blocked_by)}", file=sys.stderr)
    print(
        f"# affected_task_ids: {list(derived.affected_task_ids)} / affected_claim_ids:"
        f" {len(derived.affected_claim_ids)} / missing_slot_keys: {len(derived.missing_slot_keys)}",
        file=sys.stderr,
    )
    for note in derived.notes:
        print(f"# note: {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
