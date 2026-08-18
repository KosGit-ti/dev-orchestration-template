#!/usr/bin/env python3
"""review / release / audit / test / ci の gate 経路が共有する Evidence Schema v2 adapter。

docs/specs/N-602-evidence-truthfulness-generation.md の「Common Validator Boundary」と
「Operation Flow 6」を実装する。各 gate は artifact kind 固有 field を検査したうえで、
必ず ``scripts/ai/evidence_contract.validate_evidence_contract()`` を呼ぶ。

責務境界（重要）:
    - 真偽規則（Truthfulness Rules 1〜12）は本 module に **持たない**。規則の実装は
      ``scripts/ai/evidence_contract.py`` だけであり、本 module は入力解決（manifest /
      normalized input の読み出し）と kind 固有 field の検査に限る。
    - 判断不能な入力は必ず違反として扱う（P-010 フェイルクローズ）。manifest が読めない
      場合も「検証省略」ではなく core の ``manifest_missing`` 違反へ落とす。
    - test kind（DEC-20260815-003 決定 3・PR-E3d）は AC 単位の検証 evidence を review kind へ
      偽装せず生成するための typed 結果 kind である。skip／xfail／xpass／deselection／
      failure／error のいずれかがあれば pass にせず、required test node ID の exact 集合を
      manifest argv と ``pytest -rA`` の実行結果（core の typed parser）の両方と照合する。
    - ci kind は schema と validator だけを持つ（取得 command は将来）。conclusion が
      ``success`` でない check が 1 件でもあれば pass にしない。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai.evidence_contract import (  # noqa: E402
    GATE_ELIGIBLE_KINDS,
    GENERATOR_MARKER,
    PYTEST_OUTCOME_KEYS,
    SCHEMA_VERSION_V2,
    CapturedStream,
    ContractViolation,
    ValidationResult,
    canonical_content_bytes,
    canonical_hash_with_null_fields,
    classify_schema_version,
    is_utf8_sorted_unique,
    manifest_command_index,
    pytest_run_value_violation,
    sha256_bytes,
    validate_evidence_contract,
)

__all__ = [
    "CI_CHECK_FIELDS",
    "CI_TARGET_RE",
    "CORRECTION_FILENAME_SUFFIX",
    "EVIDENCE_V2_REFERENCE_RE",
    "FULL_SHA_RE",
    "KIND_REQUIRED_FIELDS",
    "PYTEST_ARGV_PREFIX",
    "TEST_FRAMEWORK",
    "TEST_TARGET_RE",
    "AdapterResult",
    "EvidenceAdapterError",
    "EvidenceReference",
    "build_pytest_argv",
    "extract_evidence_v2_references",
    "git_stdout",
    "head_is_compatible",
    "is_correction_document",
    "is_correction_path",
    "is_evidence_v2_document",
    "is_test_node_id",
    "load_json_document",
    "pytest_node_ids_from_argv",
    "validate_correction_artifact",
    "validate_evidence_file",
    "validate_evidence_reference",
    "validate_kind_evidence",
]

# commit SHA の完全形（40 桁 lower hex）。head 束縛は完全 SHA 同士でだけ評価する。
FULL_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")

# PR 本文・comment から v2 artifact を機械抽出するための互換 marker。
# release judgement の ``release-manager-decision:v1`` と同じ HTML コメント形式に揃え、
# 参照先 path と content hash を同時に束縛する（別 artifact への差替えを防ぐ）。
EVIDENCE_V2_REFERENCE_RE: Final = re.compile(
    r"<!--\s*evidence-v2\s+kind=(?P<kind>review|release|audit|test|ci)\s+"
    r"path=(?P<path>[^\s<>]+)\s+sha256=(?P<sha256>[0-9a-f]{64})\s*-->"
)

# kind 固有の必須 top-level field。真偽規則ではなく「どの gate 向けの artifact か」を固定する。
KIND_REQUIRED_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "review": ("review_provider",),
    "release": ("release_pr", "release_decision"),
    "audit": ("audit_target",),
    "test": ("test_target", "test_framework", "required_test_node_ids", "test_run_summary"),
    "ci": ("ci_target", "ci_checks"),
}
RELEASE_DECISIONS: Final[frozenset[str]] = frozenset({"MERGE", "REJECT"})

# --- test kind ---------------------------------------------------------------
# test_target は AC leaf／claim／task の typed token に限る（自由文を許さない）。
TEST_TARGET_RE: Final = re.compile(
    r"^(?:ac:[A-Za-z0-9][A-Za-z0-9._-]*:AC-[0-9]{2,3}"
    r"|claim:[A-Za-z0-9][A-Za-z0-9._:/-]*"
    r"|task:[A-Za-z0-9][A-Za-z0-9._-]*)$"
)
TEST_FRAMEWORK: Final = "pytest"
# 許可する pytest command の唯一の形。node ID を argv に持ち、``-k``／directory／file 指定や
# capture 無効化を許さない（required 集合と argv の node ID 集合を exact 一致させるため）。
PYTEST_ARGV_PREFIX: Final[tuple[str, ...]] = ("-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider")
_PYTHON_EXECUTABLE_RE: Final = re.compile(r"^python(?:\d+(?:\.\d+)?)?(?:\.exe)?$")
_TEST_NODE_ID_RE: Final = re.compile(r"^[^\s\-][^\r\n]*\.py::[^\r\n]+$")
_TEST_RUN_SUMMARY_FIELDS: Final[frozenset[str]] = frozenset(PYTEST_OUTCOME_KEYS)

# --- ci kind -----------------------------------------------------------------
CI_TARGET_RE: Final = re.compile(r"^(?:pr:[1-9][0-9]*|sha:[0-9a-f]{40})$")
CI_CHECK_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "conclusion",
        "run_id",
        "run_attempt",
        "head_sha",
        "workflow_blob_sha",
        "fetched_at",
        "response_sha256",
    }
)
CI_SUCCESS_CONCLUSION: Final = "success"
_HEX40_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_RFC3339_RE: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

# correction artifact（§Correction Contract）。元 evidence を消さずに誤りを訂正する追記証跡で、
# review-result schema とは別 schema である。gate は通常 review report の検査を誤適用せず、
# correction 契約として検証する（無視はしない）。
CORRECTION_FILENAME_SUFFIX: Final = ".correction-v2.json"
CORRECTION_SCHEMA_VERSION: Final = 1
CORRECTION_KIND: Final = "correction"
_CORRECTION_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "correction_schema_version",
    "corrects",
    "reason",
    "incorrect_fields",
    "replacement_claims",
    "replacement_evidence",
    "producer",
    "artifact_sha256",
)

# artifact から辿ってよい参照の上限（path traversal と巨大読み込みの抑止）。
_MAX_REFERENCE_BYTES: Final = 4_194_304

KindFieldChecks = Callable[[Mapping[str, Any]], Sequence[ContractViolation]]


class EvidenceAdapterError(ValueError):
    """artifact を読み込めない（= 検証できない）入力事象。"""


@dataclass(frozen=True)
class EvidenceReference:
    """PR 本文などが宣言した v2 artifact 参照。"""

    kind: str
    path: str
    sha256: str

    def render(self) -> str:
        """1 行表現を返す。"""
        return f"kind={self.kind} path={self.path} sha256={self.sha256}"


@dataclass(frozen=True)
class AdapterResult:
    """1 artifact 分の adapter 判定結果。"""

    path: str
    kind: str
    adapter_violations: tuple[ContractViolation, ...] = ()
    validation: ValidationResult | None = None

    @property
    def ok(self) -> bool:
        """gate pass の根拠に使えるかを返す（fail-close）。"""
        if self.adapter_violations:
            return False
        return self.validation is not None and self.validation.ok

    def violations(self) -> tuple[ContractViolation, ...]:
        """adapter と core の違反を連結して返す。"""
        core = self.validation.violations if self.validation is not None else ()
        return (*self.adapter_violations, *core)

    def codes(self) -> frozenset[str]:
        """検出した violation code 集合を返す。"""
        return frozenset(violation.code for violation in self.violations())

    def render(self) -> str:
        """人間可読な要約を返す。"""
        head = f"{self.path} kind={self.kind} ok={self.ok}"
        lines = [head]
        lines.extend(f"  - {violation.render()}" for violation in self.violations())
        if self.validation is not None:
            lines.extend(
                f"  ~ inconclusive: {reason}" for reason in self.validation.inconclusive_reasons
            )
        return "\n".join(lines)


def is_evidence_v2_document(document: object) -> bool:
    """document を Evidence Schema v2 artifact として扱うべきかを返す。

    判定は ``schema_version`` だけで行う。v2 を名乗る以上は v2 契約で検査し、
    足りない field は「v1 として読み替える」のではなく違反として扱う（fail-close）。
    """
    return isinstance(document, Mapping) and document.get("schema_version") == SCHEMA_VERSION_V2


def load_json_document(path: Path) -> dict[str, Any]:
    """JSON object を読み込む。読めない場合は ``EvidenceAdapterError`` を送出する。

    ``_MAX_REFERENCE_BYTES`` 超過は read 前に ``stat().st_size`` で検出し、無制限
    read を避ける（``validate_evidence_reference`` と同じ境界。Round 1 是正）。
    """
    try:
        if path.stat().st_size > _MAX_REFERENCE_BYTES:
            msg = f"artifact が上限 {_MAX_REFERENCE_BYTES} bytes を超える: {path.as_posix()}"
            raise EvidenceAdapterError(msg)
        raw = path.read_bytes()
    except OSError as exc:
        msg = f"artifact を読み込めない: {path.as_posix()}: {exc}"
        raise EvidenceAdapterError(msg) from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"artifact が JSON として読めない: {path.as_posix()}: {exc}"
        raise EvidenceAdapterError(msg) from exc
    if not isinstance(value, dict):
        msg = f"artifact が JSON object でない: {path.as_posix()}"
        raise EvidenceAdapterError(msg)
    return value


def safe_repo_path(root: Path, ref: str) -> Path | None:
    """repo 相対参照を解決する。絶対パス・親参照・root 外は ``None`` を返す。"""
    if not ref or ref.startswith(("/", "\\")):
        return None
    candidate = Path(ref.replace("\\", "/"))
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        return None
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def resolve_command_manifest(
    artifact: Mapping[str, Any], *, root: Path
) -> tuple[dict[str, Any] | None, bytes | None]:
    """artifact の ``command_manifest_ref`` から実行前 manifest を読み出す。

    読めない場合は ``(None, None)`` を返す。core 側が ``manifest_missing`` として
    fail-close するため、ここで「検証省略」にはしない。``_MAX_REFERENCE_BYTES`` 超過
    参照は read 前に ``stat().st_size`` で検出し、無制限 read を避けたうえで
    同じく ``(None, None)`` へ落とす（巨大 file による資源浪費の抑止）。
    """
    ref = artifact.get("command_manifest_ref")
    if not isinstance(ref, str):
        return None, None
    path = safe_repo_path(root, ref)
    if path is None or not path.is_file():
        return None, None
    try:
        if path.stat().st_size > _MAX_REFERENCE_BYTES:
            return None, None
        raw = path.read_bytes()
    except OSError:
        return None, None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(value, dict):
        return None, None
    return value, raw


def resolve_normalized_inputs(artifact: Mapping[str, Any], *, root: Path) -> dict[str, bytes]:
    """attestation が宣言した normalized input を content-addressed path から読み出す。

    ``_MAX_REFERENCE_BYTES`` 超過参照は read 前に ``stat().st_size`` で検出して
    読み飛ばす。core 側は該当 ref が ``resolved`` に無いことをもって不足として扱う
    （「検証省略」ではなく fail-close 側へ落ちる）。
    """
    attestation = artifact.get("validation_attestation")
    if not isinstance(attestation, Mapping):
        return {}
    entries = attestation.get("normalized_inputs")
    if not isinstance(entries, list):
        return {}
    resolved: dict[str, bytes] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, str):
            continue
        path = safe_repo_path(root, ref)
        if path is None or not path.is_file():
            continue
        try:
            if path.stat().st_size > _MAX_REFERENCE_BYTES:
                continue
            resolved[ref] = path.read_bytes()
        except OSError:
            continue
    return resolved


def is_test_node_id(value: object) -> bool:
    """pytest node ID として argv／required 集合に置ける文字列かを返す。

    ``<repo-relative-path>.py::<name>`` の形だけを許し、option（先頭 ``-``）、directory／file
    単独指定、絶対 path や ``..`` を含む file 部、前後空白、改行を拒否する。parametrize ID 内の
    空白は許す（argv 要素として分離される）。
    """
    if not isinstance(value, str) or value != value.strip():
        return False
    if not _TEST_NODE_ID_RE.match(value):
        return False
    file_part = value.split(".py::", 1)[0]
    segments = file_part.replace("\\", "/").split("/")
    return not file_part.startswith("/") and ".." not in segments


def build_pytest_argv(python_executable: str, node_ids: Sequence[str]) -> tuple[str, ...]:
    """test kind の contract が許す唯一の形の pytest argv を作る（helper と test の共通入口）。"""
    return (python_executable, *PYTEST_ARGV_PREFIX, *node_ids)


def pytest_node_ids_from_argv(argv: Sequence[str]) -> tuple[list[str], str | None]:
    """argv が test kind の pytest command 契約に従うかを検査し、node ID list を返す。

    Returns:
        ``(node_ids, problem)``。契約違反なら ``problem`` に説明を入れる（``node_ids`` は空）。
    """
    if len(argv) <= 1 + len(PYTEST_ARGV_PREFIX):
        return [], "pytest command に node ID がない"
    executable = Path(argv[0]).name
    if not _PYTHON_EXECUTABLE_RE.match(executable):
        return [], f"pytest command の実行体が python でない: {argv[0]!r}"
    prefix = tuple(argv[1 : 1 + len(PYTEST_ARGV_PREFIX)])
    if prefix != PYTEST_ARGV_PREFIX:
        return [], (
            "pytest command は `python -m pytest -q -rA -p no:cacheprovider <node ids>` の形に限る"
            f"（実際の option 列: {list(prefix)}）"
        )
    node_ids = list(argv[1 + len(PYTEST_ARGV_PREFIX) :])
    invalid = [item for item in node_ids if not is_test_node_id(item)]
    if invalid:
        return [], f"pytest command の argv に node ID でない要素がある: {invalid}"
    if len(set(node_ids)) != len(node_ids):
        return [], "pytest command の argv に node ID の重複がある"
    return node_ids, None


def _test_kind_violations(
    artifact: Mapping[str, Any], manifest: Mapping[str, Any] | None
) -> list[ContractViolation]:
    """test kind 固有 field と pytest command 契約を検査する（真偽規則は core が持つ）。

    検査対象:
        1. ``test_target``／``test_framework``／``required_test_node_ids``／``test_run_summary``
           の型と canonical 形（node ID は UTF-8 昇順・重複なし・非空）。
        2. verification command はちょうど 1 件で ``claim_type=pytest_run``。manifest の argv は
           許可された形だけで、argv の node ID 集合が required 集合と exact 一致する。
        3. core が typed parser で再導出した observed value（PASSED 行の node ID 集合と件数）に
           対し、required 集合との exact 一致、skip／xfail／xpass／deselection／failure／error
           ゼロ、top-level ``test_run_summary`` との一致を要求する。
    """
    violations: list[ContractViolation] = []

    def add(code: str, message: str, command_id: str | None = None) -> None:
        violations.append(ContractViolation(code=code, message=message, command_id=command_id))

    target = artifact.get("test_target")
    if "test_target" in artifact and (
        not isinstance(target, str) or not TEST_TARGET_RE.match(target)
    ):
        add(
            "kind_field_invalid",
            f"test_target が typed token（ac:／claim:／task:）でない: {target!r}",
        )
    framework = artifact.get("test_framework")
    if "test_framework" in artifact and framework != TEST_FRAMEWORK:
        add("kind_field_invalid", f"test_framework が {TEST_FRAMEWORK!r} でない: {framework!r}")

    required_raw = artifact.get("required_test_node_ids")
    required: list[str] | None = None
    if "required_test_node_ids" in artifact:
        if not isinstance(required_raw, list) or not required_raw:
            add("kind_field_invalid", "required_test_node_ids が非空 list でない")
        elif not all(is_test_node_id(item) for item in required_raw):
            add("kind_field_invalid", "required_test_node_ids に pytest node ID でない要素がある")
        elif not is_utf8_sorted_unique(required_raw):
            add("kind_field_invalid", "required_test_node_ids が UTF-8 昇順・重複なしでない")
        else:
            required = list(required_raw)

    summary = artifact.get("test_run_summary")
    summary_ok = False
    if "test_run_summary" in artifact:
        if not isinstance(summary, Mapping) or set(summary) != _TEST_RUN_SUMMARY_FIELDS:
            add(
                "kind_field_invalid",
                f"test_run_summary の field 集合が {sorted(_TEST_RUN_SUMMARY_FIELDS)} でない",
            )
        elif any(
            isinstance(summary[key], bool) or not isinstance(summary[key], int) or summary[key] < 0
            for key in PYTEST_OUTCOME_KEYS
        ):
            add("kind_field_invalid", "test_run_summary の件数が非負 int でない")
        else:
            summary_ok = True

    # verification command はちょうど 1 件の pytest_run。
    commands = artifact.get("commands")
    entries = (
        [entry for entry in commands if isinstance(entry, Mapping)]
        if isinstance(commands, list)
        else []
    )
    verifications = [entry for entry in entries if entry.get("role") == "verification"]
    if len(verifications) != 1:
        add(
            "manifest_invalid",
            f"test kind の verification command はちょうど 1 件（実際 {len(verifications)} 件）",
        )
        return violations
    pytest_entry = verifications[0]
    raw_command_id = pytest_entry.get("command_id")
    command_id = raw_command_id if isinstance(raw_command_id, str) else None
    if pytest_entry.get("claim_type") != "pytest_run":
        add(
            "manifest_invalid",
            f"test kind の verification command は claim_type=pytest_run に限る: "
            f"{pytest_entry.get('claim_type')!r}",
            command_id,
        )
        return violations

    # manifest argv の契約と required 集合との exact 一致（表示文字列からは復元しない）。
    argv_node_ids: list[str] | None = None
    if manifest is not None and command_id is not None:
        manifest_entry = manifest_command_index(manifest).get(command_id)
        argv = manifest_entry.get("argv") if manifest_entry is not None else None
        if (
            manifest_entry is None
            or manifest_entry.get("execution_mode") != "argv"
            or not isinstance(argv, list)
            or not all(isinstance(item, str) for item in argv)
        ):
            add("manifest_invalid", "manifest に pytest command の argv 定義がない", command_id)
        else:
            node_ids, problem = pytest_node_ids_from_argv(argv)
            if problem is not None:
                add("manifest_invalid", problem, command_id)
            else:
                argv_node_ids = node_ids
    if required is not None and argv_node_ids is not None and set(argv_node_ids) != set(required):
        add(
            "test_node_set_mismatch",
            "manifest argv の node ID 集合が required_test_node_ids と exact 一致しない: "
            f"missing={sorted(set(required) - set(argv_node_ids))} "
            f"extra={sorted(set(argv_node_ids) - set(required))}",
            command_id,
        )

    # core が再導出する observed value（構造は core と同じ判定で確認する）。
    observed = pytest_entry.get("observed_value")
    if pytest_run_value_violation(observed) is not None or not isinstance(observed, Mapping):
        add(
            "test_outcome_unavailable",
            "pytest_run の typed observed value がないため node ID 集合と outcome を照合できない",
            command_id,
        )
        return violations
    dirty = {key: observed[key] for key in PYTEST_OUTCOME_KEYS if key != "passed" and observed[key]}
    if dirty:
        add(
            "test_outcome_rejected",
            f"skip／xfail／xpass／deselection／failure／error があるため pass にできない: {dirty}",
            command_id,
        )
    passed_ids = observed.get("passed_node_ids")
    if required is not None and isinstance(passed_ids, list) and set(passed_ids) != set(required):
        add(
            "test_node_set_mismatch",
            "実行済み（PASSED）node ID 集合が required_test_node_ids と exact 一致しない: "
            f"missing={sorted(set(required) - set(passed_ids))} "
            f"extra={sorted(set(passed_ids) - set(required))}",
            command_id,
        )
    if summary_ok and isinstance(summary, Mapping):
        mismatched = {
            key: (summary[key], observed[key])
            for key in PYTEST_OUTCOME_KEYS
            if summary[key] != observed[key]
        }
        if mismatched:
            add(
                "test_summary_mismatch",
                "test_run_summary が pytest_run の観測値と一致しない（summary, observed）: "
                f"{mismatched}",
                command_id,
            )
    return violations


def _ci_kind_violations(artifact: Mapping[str, Any]) -> list[ContractViolation]:
    """ci kind 固有 field を検査する（schema と validator のみ・取得 command は将来）。"""
    violations: list[ContractViolation] = []

    def add(code: str, message: str) -> None:
        violations.append(ContractViolation(code=code, message=message))

    target = artifact.get("ci_target")
    target_sha: str | None = None
    if "ci_target" in artifact:
        if not isinstance(target, str) or not CI_TARGET_RE.match(target):
            add("kind_field_invalid", f"ci_target が pr:<N>／sha:<40hex> でない: {target!r}")
        elif target.startswith("sha:"):
            target_sha = target[4:]

    checks = artifact.get("ci_checks")
    if "ci_checks" not in artifact:
        return violations
    if not isinstance(checks, list) or not checks:
        add("kind_field_invalid", "ci_checks が非空 list でない")
        return violations
    seen: set[tuple[str, int, int]] = set()
    for index, check in enumerate(checks):
        label = f"ci_checks[{index}]"
        if not isinstance(check, Mapping) or set(check) != CI_CHECK_FIELDS:
            add("kind_field_invalid", f"{label} の field 集合が {sorted(CI_CHECK_FIELDS)} でない")
            continue
        name = check.get("name")
        conclusion = check.get("conclusion")
        run_id = check.get("run_id")
        run_attempt = check.get("run_attempt")
        if not isinstance(name, str) or not name.strip():
            add("kind_field_invalid", f"{label}.name が非空 string でない")
        if not isinstance(conclusion, str) or not conclusion.strip():
            add("kind_field_invalid", f"{label}.conclusion が非空 string でない")
        elif conclusion != CI_SUCCESS_CONCLUSION:
            add(
                "ci_conclusion_rejected",
                f"{label} {name!r} の conclusion が success でない: {conclusion!r}",
            )
        for field_name, number in (("run_id", run_id), ("run_attempt", run_attempt)):
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                add("kind_field_invalid", f"{label}.{field_name} が正の整数でない")
        head_sha = check.get("head_sha")
        if not isinstance(head_sha, str) or not _HEX40_RE.match(head_sha):
            add("kind_field_invalid", f"{label}.head_sha が 40 桁 lower hex でない")
        elif target_sha is not None and head_sha != target_sha:
            add("kind_field_invalid", f"{label}.head_sha が ci_target の sha と一致しない")
        blob = check.get("workflow_blob_sha")
        if not isinstance(blob, str) or not _HEX40_RE.match(blob):
            add("kind_field_invalid", f"{label}.workflow_blob_sha が 40 桁 lower hex でない")
        fetched = check.get("fetched_at")
        if not isinstance(fetched, str) or not _RFC3339_RE.match(fetched):
            add("kind_field_invalid", f"{label}.fetched_at が RFC3339 でない")
        digest = check.get("response_sha256")
        if not isinstance(digest, str) or not _HEX64_RE.match(digest):
            add("kind_field_invalid", f"{label}.response_sha256 が 64 桁 hex でない")
        if (
            isinstance(name, str)
            and isinstance(run_id, int)
            and isinstance(run_attempt, int)
            and not isinstance(run_id, bool)
        ):
            key = (name, run_id, run_attempt)
            if key in seen:
                add(
                    "kind_field_invalid",
                    f"{label} が同じ (name, run_id, run_attempt) で重複している",
                )
            seen.add(key)
    return violations


def _kind_field_violations(
    artifact: Mapping[str, Any], kind: str, *, manifest: Mapping[str, Any] | None = None
) -> list[ContractViolation]:
    """kind 固有 top-level field の存在と型を検査する（真偽規則ではない）。"""
    violations: list[ContractViolation] = []
    for name in KIND_REQUIRED_FIELDS.get(kind, ()):
        if name not in artifact:
            violations.append(
                ContractViolation(
                    code="kind_field_missing",
                    message=f"artifact_kind={kind} に必須の {name} がない",
                )
            )
    if kind == "test":
        violations.extend(_test_kind_violations(artifact, manifest))
    if kind == "ci":
        violations.extend(_ci_kind_violations(artifact))
    if kind == "review":
        provider = artifact.get("review_provider")
        if "review_provider" in artifact and (
            not isinstance(provider, str) or not provider.strip()
        ):
            violations.append(
                ContractViolation(
                    code="kind_field_invalid", message="review_provider が非空 string でない"
                )
            )
    if kind == "release":
        pr = artifact.get("release_pr")
        if "release_pr" in artifact and (
            isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0
        ):
            violations.append(
                ContractViolation(code="kind_field_invalid", message="release_pr が正の整数でない")
            )
        decision = artifact.get("release_decision")
        if "release_decision" in artifact and decision not in RELEASE_DECISIONS:
            violations.append(
                ContractViolation(
                    code="kind_field_invalid",
                    message=f"release_decision が MERGE/REJECT でない: {decision!r}",
                )
            )
    if kind == "audit":
        target = artifact.get("audit_target")
        if "audit_target" in artifact and (not isinstance(target, str) or not target.strip()):
            violations.append(
                ContractViolation(
                    code="kind_field_invalid", message="audit_target が非空 string でない"
                )
            )
    return violations


def validate_kind_evidence(
    artifact: Mapping[str, Any],
    *,
    kind: str,
    root: Path,
    path: str = "<memory>",
    purpose: Literal["gate", "historical_read"] = "gate",
    extra_checks: KindFieldChecks | None = None,
    manifest: Mapping[str, Any] | None = None,
    manifest_raw_bytes: bytes | None = None,
    raw_streams: Mapping[str, CapturedStream] | None = None,
    normalized_inputs: Mapping[str, bytes] | None = None,
) -> AdapterResult:
    """kind 固有 field を検査してから共通 core を呼ぶ（規則のコピー実装はしない）。

    Args:
        artifact: 検査対象 artifact。
        kind: 期待する artifact_kind（gate 対象は review / release / audit / test / ci）。
        root: repo root。manifest と normalized input の解決に使う。
        path: 診断表示用の artifact path。
        purpose: ``gate`` は v2 のみ許可、``historical_read`` は v1 の読み取り専用。
        extra_checks: 呼び出し側 gate が足す kind 固有検査。
        manifest / manifest_raw_bytes: 明示指定する実行前 manifest（省略時は artifact の
            ``command_manifest_ref`` から解決する）。
        raw_streams: capture 時だけ渡す raw stream（generator が file へ書く前の検証用）。
        normalized_inputs: 明示指定する normalized input（省略時は attestation の ref を
            root から解決する）。

    Returns:
        AdapterResult: ``ok`` が真のときだけ gate pass の根拠に使える。
    """
    violations: list[ContractViolation] = []
    if kind not in GATE_ELIGIBLE_KINDS:
        violations.append(
            ContractViolation(
                code="kind_not_wired", message=f"gate に結線していない artifact kind: {kind}"
            )
        )
    if manifest is None:
        manifest, manifest_raw_bytes = resolve_command_manifest(artifact, root=root)
    usage = classify_schema_version(artifact)
    if purpose == "gate" and usage != "gate_eligible":
        violations.append(
            ContractViolation(
                code="schema_v1_not_gate_eligible",
                message=(
                    "schema v2 でない evidence は gate pass にできない"
                    f"（schema_version={artifact.get('schema_version')!r}）"
                ),
            )
        )
    actual_kind = artifact.get("artifact_kind")
    if actual_kind != kind:
        violations.append(
            ContractViolation(
                code="artifact_kind_mismatch",
                message=f"artifact_kind が {kind} でない: {actual_kind!r}",
            )
        )
    violations.extend(_kind_field_violations(artifact, kind, manifest=manifest))
    if extra_checks is not None:
        violations.extend(extra_checks(artifact))

    if normalized_inputs is None:
        normalized_inputs = resolve_normalized_inputs(artifact, root=root)
    validation = validate_evidence_contract(
        artifact,
        manifest=manifest,
        manifest_raw_bytes=manifest_raw_bytes,
        raw_streams=raw_streams,
        normalized_inputs=normalized_inputs or None,
        purpose=purpose,
    )
    return AdapterResult(
        path=path,
        kind=kind,
        adapter_violations=tuple(violations),
        validation=validation,
    )


def validate_evidence_file(
    path: Path,
    *,
    kind: str,
    root: Path,
    purpose: Literal["gate", "historical_read"] = "gate",
    extra_checks: KindFieldChecks | None = None,
) -> AdapterResult:
    """file 上の v2 artifact を読み込んで検証する。読み込み失敗も違反として返す。"""
    display = path.as_posix()
    try:
        artifact = load_json_document(path)
    except EvidenceAdapterError as exc:
        return AdapterResult(
            path=display,
            kind=kind,
            adapter_violations=(ContractViolation(code="artifact_unreadable", message=str(exc)),),
        )
    return validate_kind_evidence(
        artifact,
        kind=kind,
        root=root,
        path=display,
        purpose=purpose,
        extra_checks=extra_checks,
    )


def extract_evidence_v2_references(text: str) -> tuple[EvidenceReference, ...]:
    """PR 本文などから ``evidence-v2`` marker を抽出する（重複は 1 件へ畳む）。"""
    seen: dict[tuple[str, str, str], EvidenceReference] = {}
    for match in EVIDENCE_V2_REFERENCE_RE.finditer(text or ""):
        reference = EvidenceReference(
            kind=match.group("kind"),
            path=match.group("path"),
            sha256=match.group("sha256"),
        )
        seen.setdefault((reference.kind, reference.path, reference.sha256), reference)
    return tuple(seen.values())


# ---------------------------------------------------------------------------
# head 束縛（artifact.head_sha ⇔ 検査対象 head）
# ---------------------------------------------------------------------------


def git_stdout(repo_root: Path, args: Sequence[str]) -> str | None:
    """git command の stdout を返す。失敗・実行不能は ``None``（呼び出し側が fail-close する）。"""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def head_is_compatible(
    repo_root: Path,
    expected_head: str,
    artifact_head: str,
    *,
    allowed_prefixes: Sequence[str] = (),
    allowed_path: Callable[[str], bool] | None = None,
) -> bool:
    """artifact の ``head_sha`` を検査対象 head へ束縛できるかを返す。

    review 経路の ``review_report_gate.review_head_is_compatible`` と同じ考え方を kind 非依存に
    共通化したもの。次のいずれかで真、それ以外は偽（判断不能・git 失敗も偽＝P-010）。

    1. ``artifact_head == expected_head``。
    2. ``artifact_head`` が ``expected_head`` の ancestor で、``artifact_head → expected_head`` が
       単一親の直線履歴であり、その各 commit の差分が許可 path（``allowed_prefixes`` の前置一致
       または ``allowed_path`` が真を返す path＝evidence 専用 path）だけで構成される。

    2 は「evidence を生成した commit の後に、その evidence 自身（と review 証跡）を追記する
    commit だけが積まれた」状態を許容するためにある。実質差分（code・docs・config）が 1 path でも
    混ざれば artifact は古い head の証跡であり、束縛できない。集約差分ではなく commit ごとに検査し、
    「変更して戻す」往復を見逃さない。

    Args:
        repo_root: git を実行する repository root。
        expected_head: 検査対象（PR head など）の完全 SHA。
        artifact_head: artifact が宣言した ``head_sha``。
        allowed_prefixes: ancestor 差分で許容する path 前置一致集合。
        allowed_path: 前置一致で表せない許可 path の述語（例: ``docs/ai/reviews/*.json`` 直下）。

    Returns:
        bool: 束縛できるなら真。
    """
    expected = (expected_head or "").strip().lower()
    actual = (artifact_head or "").strip().lower()
    if not FULL_SHA_RE.fullmatch(expected) or not FULL_SHA_RE.fullmatch(actual):
        return False
    if expected == actual:
        return True

    def _allowed(path: str) -> bool:
        normalized = path.strip().replace("\\", "/")
        if any(normalized.startswith(prefix) for prefix in allowed_prefixes):
            return True
        return allowed_path is not None and allowed_path(normalized)

    if git_stdout(repo_root, ["merge-base", "--is-ancestor", actual, expected]) is None:
        return False
    rev_list = git_stdout(repo_root, ["rev-list", "--reverse", f"{actual}..{expected}"])
    if rev_list is None:
        return False
    commits = [line.strip() for line in rev_list.splitlines() if line.strip()]
    if not commits:
        return False

    parent = actual
    for commit in commits:
        parents_out = git_stdout(repo_root, ["show", "-s", "--format=%P", commit])
        diff_out = git_stdout(repo_root, ["diff", "--no-renames", "--name-only", parent, commit])
        if parents_out is None or diff_out is None:
            return False
        if parents_out.split() != [parent]:
            return False
        changed = [
            line.strip().replace("\\", "/") for line in diff_out.splitlines() if line.strip()
        ]
        if any(not _allowed(path) for path in changed):
            return False
        parent = commit
    return parent == expected


def validate_evidence_reference(
    reference: EvidenceReference,
    *,
    root: Path,
    extra_checks: KindFieldChecks | None = None,
) -> AdapterResult:
    """marker が宣言した path / content hash を実 file と照合してから core 検証する。

    ``_MAX_REFERENCE_BYTES`` 超過参照は read 前に ``stat().st_size`` で検出し、無制限
    read を避けたうえで ``artifact_unreadable`` として fail-close する（Round 2 是正・
    manifest 解決や ``load_json_document()`` と同じ境界に揃える）。
    """
    display = reference.path
    path = safe_repo_path(root, reference.path)
    if path is None or not path.is_file():
        return AdapterResult(
            path=display,
            kind=reference.kind,
            adapter_violations=(
                ContractViolation(
                    code="reference_path_missing",
                    message=f"evidence-v2 marker の参照先が存在しない: {reference.path}",
                ),
            ),
        )
    try:
        if path.stat().st_size > _MAX_REFERENCE_BYTES:
            return AdapterResult(
                path=display,
                kind=reference.kind,
                adapter_violations=(
                    ContractViolation(
                        code="artifact_unreadable",
                        message=(
                            f"evidence-v2 marker の参照先が上限 {_MAX_REFERENCE_BYTES} bytes を"
                            "超えるため sha256 照合を実行できない（無制限 read の抑止）"
                        ),
                    ),
                ),
            )
        raw = path.read_bytes()
    except OSError as exc:
        return AdapterResult(
            path=display,
            kind=reference.kind,
            adapter_violations=(ContractViolation(code="artifact_unreadable", message=str(exc)),),
        )
    actual = sha256_bytes(raw)
    if actual != reference.sha256:
        return AdapterResult(
            path=display,
            kind=reference.kind,
            adapter_violations=(
                ContractViolation(
                    code="reference_hash_mismatch",
                    message=(
                        "evidence-v2 marker の sha256 が参照先 file の raw byte hash と一致しない"
                        f"（宣言 {reference.sha256} / 実測 {actual}）"
                    ),
                ),
            ),
        )
    return validate_evidence_file(path, kind=reference.kind, root=root, extra_checks=extra_checks)


# ---------------------------------------------------------------------------
# correction artifact（§Correction Contract）
# ---------------------------------------------------------------------------


def is_correction_path(relative_path: str) -> bool:
    """path が correction artifact の命名規約に一致するかを返す。"""
    return relative_path.replace("\\", "/").endswith(CORRECTION_FILENAME_SUFFIX)


def is_correction_document(document: object) -> bool:
    """document が correction artifact かを返す（review-result schema とは別 schema）。"""
    return isinstance(document, Mapping) and "correction_schema_version" in document


def _correction_normalized_inputs(
    document: Mapping[str, Any], violations: list[ContractViolation]
) -> dict[str, bytes]:
    """correction が同梱する normalized input を検算して bytes へ展開する。"""
    entries = document.get("replacement_evidence_normalized_inputs")
    resolved: dict[str, bytes] = {}
    if entries is None:
        return resolved
    if not isinstance(entries, list):
        violations.append(
            ContractViolation(
                code="correction_field_invalid",
                message="replacement_evidence_normalized_inputs が list でない",
            )
        )
        return resolved
    for entry in entries:
        if not isinstance(entry, Mapping):
            violations.append(
                ContractViolation(
                    code="correction_field_invalid",
                    message="replacement_evidence_normalized_inputs の要素が object でない",
                )
            )
            continue
        ref = entry.get("ref")
        content = entry.get("content")
        declared = entry.get("raw_sha256")
        if not isinstance(ref, str) or not isinstance(content, str):
            violations.append(
                ContractViolation(
                    code="correction_field_invalid",
                    message="normalized input の ref / content が string でない",
                )
            )
            continue
        if not isinstance(declared, str) or not declared:
            # 型不正・空・欠損（例: null）は照合不能な入力不正であり、hash 不一致
            # （correction_normalized_input_mismatch）とは診断を分ける（Round 2 是正）。
            violations.append(
                ContractViolation(
                    code="correction_field_invalid",
                    message="normalized input の raw_sha256 が非空 string でない",
                )
            )
            continue
        raw = content.encode("utf-8")
        if sha256_bytes(raw) != declared:
            violations.append(
                ContractViolation(
                    code="correction_normalized_input_mismatch",
                    message=f"normalized input の raw_sha256 が content と一致しない: {ref}",
                )
            )
            continue
        resolved[ref] = raw
    return resolved


def validate_correction_artifact(
    document: Mapping[str, Any], *, root: Path, path: str = "<memory>"
) -> AdapterResult:
    """correction artifact を Correction Contract と共通 core で検証する。

    通常の review-result schema 検査を誤適用せず、次を検査する。

    1. correction schema の必須 field と自己 hash。
    2. 訂正対象（``corrects.path`` / ``corrects.sha256``）が実在し、raw byte hash が
       一致すること（元 evidence が append-only で保持されている証明）。
    3. 自己参照 cycle の不在。
    4. 同梱した ``replacement_evidence``（Evidence Schema v2）を共通 core で検証すること。

    真偽規則は core が持つ。ここでは correction 固有の binding だけを検査する。
    """
    violations: list[ContractViolation] = []
    for name in _CORRECTION_REQUIRED_FIELDS:
        if name not in document:
            violations.append(
                ContractViolation(
                    code="correction_field_missing",
                    message=f"correction artifact に必須の {name} がない",
                )
            )
    version = document.get("correction_schema_version")
    if "correction_schema_version" in document and version != CORRECTION_SCHEMA_VERSION:
        violations.append(
            ContractViolation(
                code="correction_schema_unsupported",
                message=f"未知の correction_schema_version: {version!r}",
            )
        )

    producer = document.get("producer")
    if not isinstance(producer, Mapping) or producer.get("name") != GENERATOR_MARKER:
        violations.append(
            ContractViolation(
                code="handwritten_entry",
                message="correction artifact に generator marker がない",
            )
        )

    declared_hash = document.get("artifact_sha256")
    if not isinstance(declared_hash, str) or not declared_hash:
        # 型不正・空・欠損はいずれも自己 hash 検査の迂回経路になるため、
        # 「検査省略」ではなく必ず違反にする（fail-close の実穴の是正）。
        violations.append(
            ContractViolation(
                code="artifact_hash_invalid",
                message="correction artifact の artifact_sha256 が非空 string でない",
            )
        )
    else:
        recomputed = canonical_hash_with_null_fields(document, "artifact_sha256")
        if recomputed != declared_hash:
            violations.append(
                ContractViolation(
                    code="artifact_hash_mismatch",
                    message="correction artifact の artifact_sha256 が再計算値と一致しない",
                )
            )

    corrects = document.get("corrects")
    if not isinstance(corrects, Mapping):
        violations.append(
            ContractViolation(code="correction_field_invalid", message="corrects が object でない")
        )
    else:
        target_ref = corrects.get("path")
        target_sha = corrects.get("sha256")
        if not isinstance(target_ref, str) or not target_ref:
            violations.append(
                ContractViolation(
                    code="correction_field_invalid", message="corrects.path が非空 string でない"
                )
            )
        elif not isinstance(target_sha, str) or not target_sha:
            # 型不正・空・欠損（例: null）は照合不能な入力不正であり、hash 不一致
            # （correction_target_hash_mismatch）とは診断を分ける（Round 2 是正）。
            violations.append(
                ContractViolation(
                    code="correction_field_invalid",
                    message="corrects.sha256 が非空 string でない",
                )
            )
        elif target_ref.replace("\\", "/") == path.replace("\\", "/"):
            violations.append(
                ContractViolation(
                    code="correction_cycle",
                    message="correction artifact が自身を訂正対象にしている",
                )
            )
        else:
            target_path = safe_repo_path(root, target_ref)
            if target_path is None or not target_path.is_file():
                violations.append(
                    ContractViolation(
                        code="correction_target_missing",
                        message=f"訂正対象 evidence が存在しない: {target_ref}",
                    )
                )
            elif target_path.stat().st_size > _MAX_REFERENCE_BYTES:
                # 存在はするが上限超過（無制限 read の抑止）。存在しない場合の
                # correction_target_missing とは区別し、照合不能を示す専用 code にする
                # （Round 2 是正）。
                violations.append(
                    ContractViolation(
                        code="correction_target_unreadable",
                        message=(
                            f"訂正対象 evidence が上限 {_MAX_REFERENCE_BYTES} bytes を超えるため"
                            "raw byte hash を照合できない"
                        ),
                    )
                )
            else:
                actual = sha256_bytes(target_path.read_bytes())
                if actual != target_sha:
                    violations.append(
                        ContractViolation(
                            code="correction_target_hash_mismatch",
                            message=(
                                "訂正対象 evidence の raw byte hash が corrects.sha256 と一致しない"
                                "（元 evidence が改変された可能性がある）"
                            ),
                        )
                    )

    for name in ("incorrect_fields", "replacement_claims"):
        value = document.get(name)
        if name in document and (not isinstance(value, list) or not value):
            violations.append(
                ContractViolation(
                    code="correction_field_invalid", message=f"{name} が非空 list でない"
                )
            )

    normalized_inputs = _correction_normalized_inputs(document, violations)
    replacement = document.get("replacement_evidence")
    if not isinstance(replacement, Mapping):
        violations.append(
            ContractViolation(
                code="correction_field_invalid",
                message="replacement_evidence が Evidence Schema v2 object でない",
            )
        )
        return AdapterResult(path=path, kind=CORRECTION_KIND, adapter_violations=tuple(violations))

    manifest = document.get("replacement_evidence_command_manifest")
    manifest_raw: bytes | None = None
    if isinstance(manifest, Mapping):
        manifest_raw = canonical_content_bytes(manifest)
    else:
        manifest, manifest_raw = resolve_command_manifest(replacement, root=root)
    validation = validate_evidence_contract(
        replacement,
        manifest=manifest,
        manifest_raw_bytes=manifest_raw,
        normalized_inputs=normalized_inputs
        or resolve_normalized_inputs(replacement, root=root)
        or None,
    )
    return AdapterResult(
        path=path,
        kind=CORRECTION_KIND,
        adapter_violations=tuple(violations),
        validation=validation,
    )
