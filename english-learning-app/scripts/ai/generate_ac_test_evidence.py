#!/usr/bin/env python3
"""AC 単位の test evidence（Evidence Schema v2・``artifact_kind=test``）を binding から生成する。

DEC-20260815-003 決定 3（AC 単位の検証と検出力の担保）の生成 helper である。tracked binding
file が AC → pytest node ID exact 集合と AC 文言の source hash を固定し、本 script は

1. binding の ``ac_record.raw_sha256`` を audit materialization manifest の
   ``ac_leaf_registry[<ac>].ac_record.raw_sha256`` と exact 照合する（AC 文言が変われば
   binding を更新するまで fail-close）。
2. node ID を canonical 化（UTF-8 昇順・重複なし）し、test kind の contract が許す唯一の形
   ``python -m pytest -q -rA -p no:cacheprovider <node ids>`` の command manifest を固定する。
3. ``scripts/ai/evidence_generator.generate_evidence_artifact(artifact_kind="test")`` で実行し、
   core の typed parser（``pytest-ra-stdout-v1``）が再導出した observed value から
   ``test_run_summary`` を埋め、test adapter で検証したうえで
   ``docs/ai/evidence/ac/<task>/<ac-slug>-<date>-v2.json`` へ書く（manifest と normalized
   input も content-addressed path へ書く）。

``--assert-negative <node id>`` は mutation 検証の自己テスト mode で、その node を実行集合から
落とした run が required 集合との不一致（``test_node_set_mismatch``）で pass にならないことを
確認する（artifact は書かない）。

binding file（既定 ``docs/ai/ac-test-bindings/<task>.yml``）の形::

    schema_version: 1
    task_id: N-594B
    bindings:
      ac:N-594B:AC-01:
        ac_record:
          raw_sha256: <manifest ac_leaf_registry の値>
        test_node_ids:
          - tests/ai/test_x.py::test_a
          - tests/ai/test_x.py::test_b

真偽規則と kind 固有検査は本 script に持たない（``evidence_contract`` と ``evidence_adapter``
が唯一の実装）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai import evidence_adapter  # noqa: E402
from scripts.ai import evidence_generator as eg  # noqa: E402
from scripts.ai.evidence_contract import (  # noqa: E402
    PYTEST_OUTCOME_KEYS,
    pytest_run_value_violation,
    utf8_sorted_unique,
)

DEFAULT_MANIFEST_PATH: Final = "docs/audits/audit-materialization-manifest-2026-08-12.yml"
DEFAULT_BINDING_DIR: Final = "docs/ai/ac-test-bindings"
DEFAULT_OUTPUT_DIR: Final = "docs/ai/evidence/ac"
DEFAULT_TIMEOUT_MS: Final = 900_000
BINDING_SCHEMA_VERSION: Final = 1
TEST_KIND: Final = "test"

_AC_TOKEN_RE: Final = re.compile(r"^ac:(?P<task>[A-Za-z0-9][A-Za-z0-9._-]*):(?P<ac>AC-[0-9]{2,3})$")
_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_HEX40_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EXIT_OK: Final = 0
EXIT_NOT_PASS: Final = 1
EXIT_INPUT_ERROR: Final = 2


class AcTestEvidenceError(RuntimeError):
    """入力（binding／manifest／引数）の契約違反。fail-close で exit 2 にする。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class AcBinding:
    """binding file から取り出した 1 AC 分の束縛。"""

    ac_token: str
    task_id: str
    ac_id: str
    ac_record_raw_sha256: str
    node_ids: tuple[str, ...]

    @property
    def ac_slug(self) -> str:
        """artifact file 名に使う slug（例: ``ac-01``）を返す。"""
        return self.ac_id.lower()

    @property
    def command_id(self) -> str:
        """pytest command の安定 ID を返す。"""
        return f"pytest-{self.task_id.lower()}-{self.ac_slug}"


@dataclass
class AcTestEvidenceOutcome:
    """生成結果。``ok`` は adapter＋core を通った gate pass 可能な artifact を意味する。"""

    binding: AcBinding
    artifact: dict[str, Any]
    manifest: eg.CommandManifest
    generated: eg.GeneratedEvidence
    result: evidence_adapter.AdapterResult
    written_paths: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result.ok


# ---------------------------------------------------------------------------
# binding と manifest
# ---------------------------------------------------------------------------


def parse_ac_token(ac_token: str) -> tuple[str, str]:
    """``ac:<task>:<AC-id>`` を (task_id, ac_id) に分解する。"""
    match = _AC_TOKEN_RE.match(ac_token)
    if match is None:
        raise AcTestEvidenceError(
            "ac_token_invalid", f"--ac は ac:<task>:AC-NN の形に限る: {ac_token!r}"
        )
    return match.group("task"), match.group("ac")


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PR #1396 Round 2 指摘: UnicodeDecodeError を捕捉しないと生の例外で落ち、
    # fail-close の error 体系（AcTestEvidenceError）から外れる。
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise AcTestEvidenceError(
            f"{label}_unreadable", f"{label} を読めない: {path}: {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise AcTestEvidenceError(f"{label}_invalid", f"{label} が mapping でない: {path}")
    return loaded


def load_binding(binding_path: Path, ac_token: str) -> AcBinding:
    """binding file から ``ac_token`` の束縛を取り出し、node ID を canonical 化する。"""
    task_id, ac_id = parse_ac_token(ac_token)
    document = _load_yaml_mapping(binding_path, label="binding")
    if document.get("schema_version") != BINDING_SCHEMA_VERSION:
        raise AcTestEvidenceError(
            "binding_invalid", f"binding schema_version が {BINDING_SCHEMA_VERSION} でない"
        )
    if document.get("task_id") != task_id:
        raise AcTestEvidenceError(
            "binding_invalid",
            f"binding task_id={document.get('task_id')!r} が --ac の task {task_id!r} と一致しない",
        )
    bindings = document.get("bindings")
    if not isinstance(bindings, Mapping):
        raise AcTestEvidenceError("binding_invalid", "binding に bindings mapping がない")
    entry = bindings.get(ac_token)
    if not isinstance(entry, Mapping):
        raise AcTestEvidenceError("binding_missing", f"binding に {ac_token} の entry がない")
    ac_record = entry.get("ac_record")
    raw_sha = ac_record.get("raw_sha256") if isinstance(ac_record, Mapping) else None
    if not isinstance(raw_sha, str) or not _HEX64_RE.match(raw_sha):
        raise AcTestEvidenceError(
            "binding_invalid", f"{ac_token} の ac_record.raw_sha256 が 64 桁 hex でない"
        )
    node_ids = entry.get("test_node_ids")
    if not isinstance(node_ids, list) or not node_ids:
        raise AcTestEvidenceError(
            "binding_invalid", f"{ac_token} の test_node_ids が非空 list でない"
        )
    invalid = [item for item in node_ids if not evidence_adapter.is_test_node_id(item)]
    if invalid:
        raise AcTestEvidenceError(
            "binding_invalid",
            f"{ac_token} の test_node_ids に pytest node ID でない要素がある: {invalid}",
        )
    canonical = utf8_sorted_unique([str(item) for item in node_ids])
    return AcBinding(
        ac_token=ac_token,
        task_id=task_id,
        ac_id=ac_id,
        ac_record_raw_sha256=raw_sha,
        node_ids=tuple(canonical),
    )


def manifest_ac_record_sha256(manifest_path: Path, ac_token: str) -> str:
    """audit materialization manifest から AC 文言の source hash を読む。"""
    document = _load_yaml_mapping(manifest_path, label="manifest")
    registry = document.get("ac_leaf_registry")
    if not isinstance(registry, Mapping):
        raise AcTestEvidenceError("manifest_invalid", "manifest に ac_leaf_registry がない")
    leaf = registry.get(ac_token)
    if not isinstance(leaf, Mapping):
        raise AcTestEvidenceError(
            "ac_not_in_manifest", f"manifest ac_leaf_registry に {ac_token} がない"
        )
    record = leaf.get("ac_record")
    raw_sha = record.get("raw_sha256") if isinstance(record, Mapping) else None
    if not isinstance(raw_sha, str) or not _HEX64_RE.match(raw_sha):
        raise AcTestEvidenceError(
            "manifest_invalid", f"manifest の {ac_token} ac_record.raw_sha256 が 64 桁 hex でない"
        )
    return raw_sha


def check_binding_source_hash(binding: AcBinding, manifest_path: Path) -> None:
    """binding の AC source hash が manifest と exact 一致することを要求する（fail-close）。"""
    expected = manifest_ac_record_sha256(manifest_path, binding.ac_token)
    if binding.ac_record_raw_sha256 != expected:
        raise AcTestEvidenceError(
            "ac_source_hash_mismatch",
            f"{binding.ac_token} の binding ac_record.raw_sha256={binding.ac_record_raw_sha256} が"
            f" manifest の {expected} と一致しない（AC 文言が変わった場合は binding を更新する）",
        )


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def build_test_command_spec(
    binding: AcBinding,
    *,
    python_executable: str,
    node_ids: Sequence[str] | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> eg.CommandSpec:
    """test kind の contract が許す唯一の形の pytest command spec を作る。"""
    ids = tuple(node_ids) if node_ids is not None else binding.node_ids
    argv = evidence_adapter.build_pytest_argv(python_executable, ids)
    return eg.CommandSpec(
        command_id=binding.command_id,
        role="verification",
        claim_type="pytest_run",
        argv=argv,
        expected_exit_codes=(0,),
        timeout_ms=timeout_ms,
        claim_template=(
            "pytest_run: passed={value[passed]} failed={value[failed]} skipped={value[skipped]}"
            " xfailed={value[xfailed]} xpassed={value[xpassed]} deselected={value[deselected]}"
            " errors={value[errors]} exit={exit_code}"
        ),
    )


def _extra_fields(binding: AcBinding) -> dict[str, Any]:
    return {
        "test_target": binding.ac_token,
        "test_framework": evidence_adapter.TEST_FRAMEWORK,
        "required_test_node_ids": list(binding.node_ids),
    }


def generate_ac_test_evidence(
    binding: AcBinding,
    *,
    root: Path,
    head_sha: str,
    python_executable: str,
    manifest_id: str,
    executed_node_ids: Sequence[str] | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    generated_at: str | None = None,
    validated_at: str | None = None,
    repository: str = eg.DEFAULT_REPOSITORY,
) -> AcTestEvidenceOutcome:
    """binding から test kind artifact を生成し、adapter＋core で検証した結果を返す。

    ``executed_node_ids`` は自己テスト（``--assert-negative``）で実行集合だけを変えるために使う。
    required 集合は常に binding の canonical 集合である。
    """
    spec = build_test_command_spec(
        binding,
        python_executable=python_executable,
        node_ids=executed_node_ids,
        timeout_ms=timeout_ms,
    )
    manifest = eg.build_command_manifest([spec], manifest_id=manifest_id)
    generated = eg.generate_evidence_artifact(
        artifact_kind=TEST_KIND,
        manifest=manifest,
        head_sha=head_sha,
        repository=repository,
        cwd=root,
        generated_at=generated_at,
        validated_at=validated_at,
        extra_fields=_extra_fields(binding),
        persist_normalized_inputs=True,
    )
    artifact = generated.artifact
    entry = next(
        (item for item in artifact["commands"] if item.get("command_id") == binding.command_id),
        None,
    )
    observed = entry.get("observed_value") if entry is not None else None
    if pytest_run_value_violation(observed) is None and isinstance(observed, Mapping):
        artifact["test_run_summary"] = {key: observed[key] for key in PYTEST_OUTCOME_KEYS}
    else:
        # typed 再導出できない run（pytest 起動失敗など）は summary を捏造せず null で残す。
        # adapter は kind_field_invalid で拒否し、core の attestation は正直な fail を保つ。
        artifact["test_run_summary"] = None
    eg.finalize_artifact_hashes(artifact)
    result = evidence_adapter.validate_kind_evidence(
        artifact,
        kind=TEST_KIND,
        root=root,
        path=f"<memory:{binding.ac_token}>",
        manifest=manifest.data,
        manifest_raw_bytes=manifest.raw_bytes,
        raw_streams=generated.raw_streams,
        normalized_inputs=generated.normalized_inputs,
    )
    return AcTestEvidenceOutcome(
        binding=binding,
        artifact=artifact,
        manifest=manifest,
        generated=generated,
        result=result,
    )


def artifact_output_path(root: Path, binding: AcBinding, date: str) -> Path:
    """既定の artifact 出力 path を返す。"""
    return root / DEFAULT_OUTPUT_DIR / binding.task_id / f"{binding.ac_slug}-{date}-v2.json"


def write_outcome(outcome: AcTestEvidenceOutcome, *, root: Path, artifact_path: Path) -> list[Path]:
    """manifest／normalized input／artifact を書き、書いた path を返す。

    既存 artifact は上書きしない（append-only）。
    """
    if artifact_path.exists():
        raise AcTestEvidenceError(
            "artifact_exists", f"既存 evidence を上書きしない（append-only）: {artifact_path}"
        )
    written: list[Path] = []
    written.append(outcome.manifest.write(root / eg.DEFAULT_MANIFEST_REF_PREFIX))
    written.extend(
        eg.write_normalized_inputs(outcome.generated, root / eg.DEFAULT_NORMALIZED_INPUT_REF_PREFIX)
    )
    validation = outcome.result.validation
    if validation is None:
        raise AcTestEvidenceError("validation_missing", "core の validation 結果がない")
    written.append(
        eg.write_evidence_artifact(
            eg.GeneratedEvidence(artifact=outcome.artifact, validation=validation), artifact_path
        )
    )
    outcome.written_paths = written
    return written


# ---------------------------------------------------------------------------
# 自己テスト（mutation 検証補助）
# ---------------------------------------------------------------------------


def assert_negative(
    binding: AcBinding,
    dropped_node_id: str,
    *,
    root: Path,
    head_sha: str,
    python_executable: str,
    manifest_id: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> tuple[bool, AcTestEvidenceOutcome]:
    """``dropped_node_id`` を落とした実行集合が required 不一致で pass にならないことを確認する。

    Returns:
        ``(detected, outcome)``。``detected`` は adapter が ``test_node_set_mismatch`` を出し
        かつ ok でないときだけ真。
    """
    if dropped_node_id not in binding.node_ids:
        raise AcTestEvidenceError(
            "negative_node_not_bound",
            f"--assert-negative の node が binding にない: {dropped_node_id}",
        )
    reduced = [item for item in binding.node_ids if item != dropped_node_id]
    if not reduced:
        raise AcTestEvidenceError(
            "negative_set_empty",
            "--assert-negative は node ID が 2 件以上の binding でだけ使える（空集合は実行しない）",
        )
    outcome = generate_ac_test_evidence(
        binding,
        root=root,
        head_sha=head_sha,
        python_executable=python_executable,
        manifest_id=manifest_id,
        executed_node_ids=reduced,
        timeout_ms=timeout_ms,
    )
    detected = (not outcome.ok) and "test_node_set_mismatch" in outcome.result.codes()
    return detected, outcome


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _git_head_sha(root: Path) -> str:
    """root の HEAD SHA を読む（read-only）。"""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AcTestEvidenceError(
            "head_sha_unavailable", f"git rev-parse HEAD に失敗: {exc}"
        ) from exc
    sha = completed.stdout.strip()
    if completed.returncode != 0 or not _HEX40_RE.match(sha):
        raise AcTestEvidenceError(
            "head_sha_unavailable", "git rev-parse HEAD が 40 桁 SHA を返さない"
        )
    return sha


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _display_path(path: Path, root: Path) -> str:
    """root 配下なら相対 path、そうでなければ絶対 path を表示用に返す。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AC 単位の test evidence（Evidence Schema v2）生成"
    )
    parser.add_argument("--ac", required=True, help="AC token（例: ac:N-594B:AC-01）")
    parser.add_argument(
        "--binding",
        type=Path,
        help=f"binding file（既定: <root>/{DEFAULT_BINDING_DIR}/<task>.yml）",
    )
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="repo root（cwd と path 解決の基準）"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help=f"audit materialization manifest（既定: <root>/{DEFAULT_MANIFEST_PATH}）",
    )
    parser.add_argument(
        "--head-sha", help="artifact に記録する head SHA（既定: git rev-parse HEAD）"
    )
    parser.add_argument("--python", default=sys.executable, help="pytest を起動する python 実行体")
    parser.add_argument("--date", help="artifact file 名の日付 YYYY-MM-DD（既定: 今日 UTC）")
    parser.add_argument("--output", type=Path, help="artifact の出力 path（既定は規約 path）")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--repository", default=eg.DEFAULT_REPOSITORY)
    parser.add_argument(
        "--write-failed",
        action="store_true",
        help="adapter で pass にならない artifact も正直な fail 記録として書く（既定は書かない）",
    )
    parser.add_argument(
        "--assert-negative",
        metavar="NODE_ID",
        help="自己テスト: この node を落とした実行集合が required 不一致で fail することを確認する",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        task_id, _ = parse_ac_token(args.ac)
        binding_path = args.binding or (root / DEFAULT_BINDING_DIR / f"{task_id}.yml")
        manifest_path = args.manifest or (root / DEFAULT_MANIFEST_PATH)
        binding = load_binding(binding_path, args.ac)
        check_binding_source_hash(binding, manifest_path)
        head_sha = args.head_sha or _git_head_sha(root)
        if not _HEX40_RE.match(head_sha):
            raise AcTestEvidenceError(
                "head_sha_invalid", f"--head-sha が 40 桁 lower hex でない: {head_sha!r}"
            )
        date = args.date or _today_utc()
        if not _DATE_RE.match(date):
            raise AcTestEvidenceError("date_invalid", f"--date が YYYY-MM-DD でない: {date!r}")
        manifest_id = f"ac-test:{binding.ac_token}:{date}"

        if args.assert_negative is not None:
            detected, outcome = assert_negative(
                binding,
                args.assert_negative,
                root=root,
                head_sha=head_sha,
                python_executable=args.python,
                manifest_id=f"{manifest_id}:negative",
                timeout_ms=args.timeout_ms,
            )
            print(outcome.result.render())
            verdict = "detected" if detected else "NOT detected"
            print(
                f"assert-negative dropped={args.assert_negative} test_node_set_mismatch={verdict}"
            )
            return EXIT_OK if detected else EXIT_NOT_PASS

        outcome = generate_ac_test_evidence(
            binding,
            root=root,
            head_sha=head_sha,
            python_executable=args.python,
            manifest_id=manifest_id,
            timeout_ms=args.timeout_ms,
            repository=args.repository,
        )
        print(outcome.result.render())
        artifact_path = (args.output or artifact_output_path(root, binding, date)).resolve()
        if outcome.ok or args.write_failed:
            for path in write_outcome(outcome, root=root, artifact_path=artifact_path):
                print(f"written: {_display_path(path, root)}")
            # 書いた file だけから再検証する（raw stream なし・manifest／normalized input は
            # ref 解決＝gate 時と同じ offline 経路）。
            reloaded = evidence_adapter.validate_evidence_file(
                artifact_path, kind=TEST_KIND, root=root
            )
            print(f"reloaded: ok={reloaded.ok}")
            if outcome.ok and not reloaded.ok:
                print(reloaded.render())
                return EXIT_NOT_PASS
        else:
            print(
                "not written: adapter で pass にならない artifact"
                "（--write-failed で正直な fail 記録を残せる）"
            )
        return EXIT_OK if outcome.ok else EXIT_NOT_PASS
    except AcTestEvidenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except eg.EvidenceGenerationError as exc:
        print(f"error: generation failed: {exc}", file=sys.stderr)
        return EXIT_NOT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
