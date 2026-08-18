#!/usr/bin/env python3
"""Evidence Schema v2（N-602）の schema 検証と Truthfulness Rules 1〜12 の唯一の実装。

本 module は docs/specs/N-602-evidence-truthfulness-generation.md の
「Evidence Schema v2」「Truthfulness Rules」「Stream Hash, Excerpt, Redaction」
「Common Validator Boundary」を実装する共通 core である。

利用契約（重要）:
    - review / release / audit / test / ci / research result の各 adapter は、artifact kind
      固有の field を artifact へ足したうえで ``validate_evidence_contract()`` を呼ぶ。
      kind 固有 field の内容検査（test の required node ID exact 集合や ci の conclusion）は
      adapter が担い、core は schema 境界（他 kind の field 混入拒否）と typed parser だけを持つ。
    - 真偽規則（Truthfulness Rules）を adapter や gate 側へコピー実装することを禁止する。
      規則を変えるときは本 module だけを変更し、adapter は呼び出し方だけを持つ。
    - 判断不能な入力は必ず fail もしくは inconclusive へ倒す（P-010 フェイルクローズ）。

hash 契約は docs/design.md「共通 content hash 契約」の ``canonical-content-v1`` に従う。
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

SCHEMA_VERSION_V2: Final = 2
SCHEMA_VERSION_V1: Final = 1
MANIFEST_SCHEMA_VERSION: Final = 1
GENERATOR_MARKER: Final = "scripts/ai/evidence_generator.py"

# schema が列挙する artifact kind の全体集合。
KNOWN_ARTIFACT_KINDS: Final[frozenset[str]] = frozenset(
    {"review", "release", "audit", "test", "ci", "research_result"}
)
# Phase A（N-602A）で最初に gate 結線した 3 kind（review／release／audit gate の互換境界）。
# 値は変えない。gate pass の可否は ``GATE_ELIGIBLE_KINDS`` で判定する。
PHASE_A_ARTIFACT_KINDS: Final[frozenset[str]] = frozenset({"review", "release", "audit"})
# typed 結果 kind（DEC-20260815-003 決定 3・PR-E3d）。AC 単位の検証 evidence を review kind へ
# 偽装せず生成するために gate 対象へ加えた。kind 固有 field の内容検査は adapter が担う。
TYPED_RESULT_KINDS: Final[frozenset[str]] = frozenset({"test", "ci"})
# gate pass を許す kind。research_result だけが Phase B（N-602B）の adapter 待ちで inconclusive。
GATE_ELIGIBLE_KINDS: Final[frozenset[str]] = PHASE_A_ARTIFACT_KINDS | TYPED_RESULT_KINDS

# kind 固有 top-level field（schema の閉鎖境界）。typed 結果 kind の field は他 kind の artifact に
# 現れてはならず、typed 結果 kind の artifact は Phase A kind の field を持てない（kind 偽装の
# 防止）。Phase A 3 kind 相互の混入は既存 gate 挙動を変えないため本 rule の対象外とする。
KIND_SPECIFIC_TOP_LEVEL_FIELDS: Final[Mapping[str, frozenset[str]]] = {
    "review": frozenset({"review_provider"}),
    "release": frozenset({"release_pr", "release_decision"}),
    "audit": frozenset({"audit_target"}),
    "test": frozenset(
        {"test_target", "test_framework", "required_test_node_ids", "test_run_summary"}
    ),
    "ci": frozenset({"ci_target", "ci_checks"}),
}

COMMAND_ROLES: Final[frozenset[str]] = frozenset({"verification", "diagnostic"})
EXECUTION_MODES: Final[frozenset[str]] = frozenset({"argv", "shell"})
ARTIFACT_RESULTS: Final[frozenset[str]] = frozenset({"pass", "fail", "inconclusive"})

# claim type と typed parser（derivation）の対応。derivation の差替えは Rule 1 で拒否する。
CLAIM_TYPE_PARSERS: Final[Mapping[str, str]] = {
    "exit_success": "exit-status-v1",
    "count": "stdout-single-count-v1",
    "no_match": "no-match-exit-v1",
    "sha": "stdout-sha256-token-v1",
    "pytest_run": "pytest-ra-stdout-v1",
    "free_text": "free-text-v1",
}
CLAIM_TYPES: Final[frozenset[str]] = frozenset(CLAIM_TYPE_PARSERS)
# raw stdout がなければ再導出できない claim type（Rule 11）。
RAW_DERIVED_CLAIM_TYPES: Final[frozenset[str]] = frozenset({"count", "sha", "pytest_run"})
# exit code だけから offline 再導出できる claim type。
EXIT_DERIVED_CLAIM_TYPES: Final[frozenset[str]] = frozenset({"exit_success", "no_match"})

# pytest_run claim の typed observed value（``pytest-ra-stdout-v1``）。
# ``python -m pytest -q -rA`` の stdout から短い test summary と最終行を再導出した結果で、
# outcome 件数 7 種と PASSED 行の node ID exact 集合（UTF-8 byte 昇順・重複なし）を持つ。
PYTEST_OUTCOME_KEYS: Final[tuple[str, ...]] = (
    "passed",
    "failed",
    "skipped",
    "xfailed",
    "xpassed",
    "deselected",
    "errors",
)
PYTEST_RUN_VALUE_FIELDS: Final[frozenset[str]] = frozenset(
    {*PYTEST_OUTCOME_KEYS, "passed_node_ids"}
)

# Rule 3: expected list に書いても必ず invalid になる exit code。
FORBIDDEN_EXIT_CODES: Final[frozenset[int]] = frozenset({126, 127})
# Rule 5: no-match を正常扱いする command の唯一の expected 契約。
NO_MATCH_EXPECTED_EXIT_CODES: Final[tuple[int, ...]] = (0, 1)

EMPTY_SHA256: Final = hashlib.sha256(b"").hexdigest()

# excerpt の上限（Stream Hash, Excerpt, Redaction 節）。
EXCERPT_MAX_BYTES_PER_STREAM: Final = 4096
EXCERPT_MAX_BYTES_PER_COMMAND: Final = 8192
EXCERPT_HEAD_BYTES: Final = 3072
EXCERPT_TAIL_BYTES: Final = 1024
EXCERPT_ELISION_TEMPLATE: Final = "\n[... {omitted} bytes omitted ...]\n"
EXCERPT_ELISION_RE: Final = re.compile(r"\n\[\.\.\. \d+ bytes omitted \.\.\.\]\n")

_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_HEX40_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_RFC3339_RE: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_COUNT_TOKEN_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)$")

# claim 本文と observed_value の整合検査に使う token 抽出。
_CLAIM_COUNT_KV_RE: Final = re.compile(
    r"(?:count|matches|lines|files|件数)[ \t]*[=:][ \t]*(\d+)", re.IGNORECASE
)
_CLAIM_COUNT_UNIT_RE: Final = re.compile(
    r"(\d+)[ \t]*(?:件|matches|lines|files|entries)", re.IGNORECASE
)
_CLAIM_INT_RE: Final = re.compile(r"\d+")
_CLAIM_NO_MATCH_RE: Final = re.compile(r"no_match[ \t]*[=:][ \t]*(true|false)", re.IGNORECASE)
_CLAIM_EXIT_RE: Final = re.compile(r"exit[ \t]*[=:]?[ \t]*(\d+)", re.IGNORECASE)
# pytest_run claim 本文の outcome token（例: ``passed=2 failed=0``）。
_CLAIM_PYTEST_TOKEN_RE: Final = re.compile(
    r"\b(passed|failed|skipped|xfailed|xpassed|deselected|errors)[ \t]*[=:][ \t]*(\d+)",
    re.IGNORECASE,
)

# pytest ``-q -rA`` stdout の typed parser（``pytest-ra-stdout-v1``）。
_PYTEST_SHORT_SUMMARY_HEADER_RE: Final = re.compile(r"^=+ short test summary info =+$")
_PYTEST_FINAL_LINE_RE: Final = re.compile(
    r"^(?P<body>(?:\d+ [a-z]+)(?:, \d+ [a-z]+)*|no tests ran)"
    r" in \d+(?:\.\d+)?s(?: \(\d+:\d{2}:\d{2}\))?$"
)
_PYTEST_FINAL_TOKEN_RE: Final = re.compile(r"^(\d+) ([a-z]+)$")
_PYTEST_FINAL_TOKEN_KEYS: Final[Mapping[str, str]] = {
    "passed": "passed",
    "failed": "failed",
    "skipped": "skipped",
    "xfailed": "xfailed",
    "xpassed": "xpassed",
    "deselected": "deselected",
    "error": "errors",
    "errors": "errors",
}
# warning は test outcome ではないため件数へ含めない（final 行にだけ現れる）。
_PYTEST_FINAL_IGNORED_TOKENS: Final[frozenset[str]] = frozenset({"warning", "warnings"})
_PYTEST_SUMMARY_LINE_RE: Final = re.compile(
    r"^(?P<outcome>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (?P<rest>.*)$"
)
_PYTEST_SKIPPED_COUNT_RE: Final = re.compile(r"^\[(\d+)\] ")

# redaction 後に残っていたら artifact を書かずに fail する pattern（Stream 節）。
FORBIDDEN_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA|DSA|EC|OPENSSH|PRIVATE) ?(?:PRIVATE )?KEY-----"),
    ),
    ("github_token", re.compile(r"\bgh[opsur]_[A-Za-z0-9_]{20,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("provider_api_key", re.compile(r"\bsk-(?:proj-|ant-api03-)?[A-Za-z0-9_-]{20,}")),
    ("bearer_token", re.compile(r"\bBearer[ \t]+[A-Za-z0-9._~+/-]{8,}=*", re.IGNORECASE)),
    (
        "authorization_header",
        re.compile(r"\bauthorization[ \t]*:[ \t]*(?!\[REDACTED)\S+", re.IGNORECASE),
    ),
    (
        "credential_assignment",
        re.compile(
            r"\b[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIALS?)[ \t]*="
            r"[ \t]*(?!\[REDACTED)\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "connection_string",
        re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]+:[^\s/@]+@\S+"),
    ),
    ("email_address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
)

# shell command の失敗隠蔽 pattern（verification では実行前に拒否する）。
SHELL_MASKING_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("pipefail_disabled", re.compile(r"set[ \t]+\+o[ \t]+pipefail")),
    ("errexit_disabled", re.compile(r"set[ \t]+\+e(?![a-zA-Z0-9_])")),
    ("failure_masked_by_true", re.compile(r"\|\|[ \t]*(?:true|:)[ \t]*(?:;|$)", re.MULTILINE)),
    ("exit_code_overridden", re.compile(r"(?:^|[;&\n])[ \t]*exit[ \t]+\d+[ \t]*;?[ \t]*$")),
)

# command entry / preflight / attestation は closed schema とする（field の密輸を防ぐ）。
_COMMAND_ENTRY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "command_id",
        "role",
        "command",
        "execution_mode",
        "canonical_command_sha256",
        "expected_exit_codes",
        "observed_exit_code",
        "started_at",
        "duration_ms",
        "stdout_sha256",
        "stderr_sha256",
        "stdout_bytes",
        "stderr_bytes",
        "stdout_excerpt",
        "stderr_excerpt",
        "stdout_truncated",
        "stderr_truncated",
        "claim",
        "claim_type",
        "observed_value",
        "derivation",
        "preflight",
    }
)
# preflight の必須 field（Rule 8）。
_PREFLIGHT_REQUIRED_FIELDS: Final[frozenset[str]] = frozenset(
    {"executable", "resolved_path_sha256", "available"}
)
# preflight の optional field（executable binding・外部再監査 2026-08-15 P0-04）。
# generator が実行 cwd 基準で絶対化した解決済み path・symlink 解決後の realpath・実行後再 hash を
# 記録するための field であり、欠落は許容し、存在すれば型検査する。emit は後続 PR（B2）が行う。
_PREFLIGHT_OPTIONAL_FIELDS: Final[frozenset[str]] = frozenset(
    {"resolved_path", "realpath", "post_execution_sha256"}
)
_PREFLIGHT_FIELDS: Final[frozenset[str]] = _PREFLIGHT_REQUIRED_FIELDS | _PREFLIGHT_OPTIONAL_FIELDS
_ATTESTATION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "validator_source_sha256",
        "validator_source_git_oid",
        "command_manifest_sha256",
        "artifact_body_sha256",
        "verified_command_ids",
        "verified_claim_ids",
        "normalized_inputs",
        "result",
        "validated_at",
        "attestation_sha256",
    }
)
_NORMALIZED_INPUT_FIELDS: Final[frozenset[str]] = frozenset(
    {"command_id", "parser_id", "ref", "raw_sha256"}
)
_REQUIRED_TOP_LEVEL_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "artifact_kind",
    "producer",
    "generated_at",
    "repository",
    "head_sha",
    "result",
    "command_manifest_ref",
    "command_manifest_sha256",
    "required_command_ids",
    "required_verification_ids",
    "commands",
    "corrections",
    "research_binding",
    "research_result",
    "validation_attestation",
    "artifact_sha256",
)

CONTRACT_SOURCE_PATH: Final[Path] = Path(__file__).resolve()


# ---------------------------------------------------------------------------
# canonical-content-v1（docs/design.md「共通 content hash 契約」）
# ---------------------------------------------------------------------------

_MAX_SAFE_INTEGER: Final = 2**53 - 1


class CanonicalContentError(ValueError):
    """canonical-content-v1 の入力契約違反。"""


def _canonical_string(value: str) -> str:
    """RFC 8785 の string serialization を返す（NFC でない文字列は拒否）。"""
    if not unicodedata.is_normalized("NFC", value):
        raise CanonicalContentError("NFC 正規化されていない文字列は暗黙変換せず拒否する")
    out: list[str] = ['"']
    for char in value:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif code == 0x08:
            out.append("\\b")
        elif code == 0x09:
            out.append("\\t")
        elif code == 0x0A:
            out.append("\\n")
        elif code == 0x0C:
            out.append("\\f")
        elif code == 0x0D:
            out.append("\\r")
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _canonical_value(value: object) -> str:
    """canonical-content-v1 の serialization を返す。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise CanonicalContentError("safe integer 範囲外の JSON number は拒否する")
        return str(value)
    if isinstance(value, float):
        # 精密な時刻・秒・予算は plain decimal string で表す規約のため float を受けない。
        raise CanonicalContentError(
            "float は canonical-content-v1 の入力にできない（plain decimal string を使う）"
        )
    if isinstance(value, str):
        return _canonical_string(value)
    if isinstance(value, Mapping):
        items: list[tuple[bytes, str, object]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalContentError("object key は string でなければならない")
            items.append((key.encode("utf-16-be"), key, item))
        items.sort(key=lambda entry: entry[0])
        body = ",".join(
            f"{_canonical_string(key)}:{_canonical_value(item)}" for _, key, item in items
        )
        return "{" + body + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_value(item) for item in value) + "]"
    raise CanonicalContentError(f"canonical-content-v1 が扱えない型: {type(value).__name__}")


def canonical_content_bytes(value: object) -> bytes:
    """canonical-content-v1（RFC 8785 / UTF-8 / 末尾改行なし）の byte 列を返す。"""
    return _canonical_value(value).encode("utf-8")


def canonical_content_sha256(value: object) -> str:
    """canonical-content-v1 byte 列の SHA-256 を返す。"""
    return hashlib.sha256(canonical_content_bytes(value)).hexdigest()


def canonical_hash_with_null_fields(obj: Mapping[str, Any], *fields: str) -> str:
    """指定 field を JSON null へ置いた projection の canonical hash を返す（Rule 9）。"""
    projection: dict[str, Any] = dict(obj)
    for name in fields:
        projection[name] = None
    return canonical_content_sha256(projection)


def sha256_bytes(data: bytes) -> str:
    """raw byte 列の SHA-256 を返す（canonical hash とは別規則）。"""
    return hashlib.sha256(data).hexdigest()


def git_blob_oid(data: bytes) -> str:
    """SHA-1 repo の git blob object ID を返す（``source_git_oid`` 用）。"""
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def canonical_command_sha256(
    execution_mode: str,
    *,
    argv: Sequence[str] | None = None,
    script: str | None = None,
) -> str:
    """command の canonical hash を返す。

    argv は UTF-8 JSON array（canonical-content-v1）、shell は command の UTF-8 byte 列を
    hash する。表示用文字列からは決して導出しない。
    """
    if execution_mode == "argv":
        if argv is None:
            raise CanonicalContentError("execution_mode=argv には argv が必要")
        return canonical_content_sha256(list(argv))
    if execution_mode == "shell":
        if script is None:
            raise CanonicalContentError("execution_mode=shell には script が必要")
        return sha256_bytes(script.encode("utf-8"))
    raise CanonicalContentError(f"未知の execution_mode: {execution_mode}")


def contract_source_sha256() -> str:
    """validator source（本 file）の raw byte SHA-256 を返す。"""
    return sha256_bytes(CONTRACT_SOURCE_PATH.read_bytes())


def contract_source_git_oid() -> str:
    """validator source（本 file）の git blob OID を返す。"""
    return git_blob_oid(CONTRACT_SOURCE_PATH.read_bytes())


# ---------------------------------------------------------------------------
# typed parser（Rule 6 / 7 / 11）
# ---------------------------------------------------------------------------


class DerivationError(ValueError):
    """typed parser が observed value を導出できない。"""


def parse_exit_success(*, exit_code: int) -> int:
    """exit_success claim の observed value（= 実測 exit code）を返す。

    成功判定そのものは ``expected_exit_codes`` と Rule 2 が担う。ここで exit 0 を強制すると
    「失敗を正直に記録した artifact」を表現できなくなるため、値の写しだけを行う。
    """
    return exit_code


def parse_count(stdout: bytes) -> int:
    """raw stdout 専用の件数 parser（Rule 6）。

    空 stdout、複数数値、非数値はすべて parse error とする。
    """
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - 実行環境依存の防御
        raise DerivationError("stdout が UTF-8 として解釈できない") from exc
    tokens = [line.strip() for line in text.splitlines() if line.strip()]
    if not tokens:
        raise DerivationError("stdout が空のため件数を導出できない")
    if len(tokens) > 1:
        raise DerivationError(f"stdout に数値が複数ある: {len(tokens)} 行")
    token = tokens[0]
    if not _COUNT_TOKEN_RE.match(token):
        raise DerivationError(f"件数として解釈できない stdout: {token!r}")
    return int(token)


def parse_no_match(*, exit_code: int) -> bool:
    """no_match claim の observed value を返す（exit 1 = no match）。"""
    if exit_code == 0:
        return False
    if exit_code == 1:
        return True
    raise DerivationError(f"no_match claim が扱えない exit code: {exit_code}")


def parse_sha(stdout: bytes) -> str:
    """raw stdout 先頭 token を SHA-256 として取り出す。"""
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - 実行環境依存の防御
        raise DerivationError("stdout が UTF-8 として解釈できない") from exc
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise DerivationError(f"SHA を導出できる stdout 行数ではない: {len(lines)}")
    token = lines[0].split()[0]
    if not _HEX64_RE.match(token):
        raise DerivationError(f"SHA-256 として解釈できない token: {token!r}")
    return token


def utf8_sorted_unique(items: Sequence[str]) -> list[str]:
    """UTF-8 byte 昇順・重複なしの canonical list を返す（node ID 集合の正規形）。"""
    return sorted(set(items), key=lambda item: item.encode("utf-8"))


def is_utf8_sorted_unique(items: Sequence[str]) -> bool:
    """list が UTF-8 byte 昇順かつ重複なしの canonical 形かを返す。"""
    return list(items) == utf8_sorted_unique(items) and len(set(items)) == len(items)


def parse_pytest_run(stdout: bytes) -> dict[str, object]:
    """``python -m pytest -q -rA`` の raw stdout から typed run summary を導出する。

    導出は 2 か所の機械出力だけを使う。

    1. 最終行（``N passed, M skipped in 0.12s`` または ``no tests ran in 0.10s``）から
       outcome 件数 7 種を読む。warning は outcome ではないため無視し、未知 token は拒否する。
    2. 最後の ``short test summary info`` 見出し以降の ``PASSED <node id>`` 行から、pass した
       node ID の exact 集合を読む。FAILED／ERROR／SKIPPED／XFAIL／XPASS 行は件数だけを数え、
       最終行の件数と cross-check する（不整合は fail-close）。

    test 自身が捕捉 stdout に偽の見出しや ``PASSED`` 行を印字しても、``-rA`` の PASSES 節は
    real な short summary より前に出るため、最後の見出しと最終行を使う本 parser は影響を
    受けない。``-s``（capture 無効化）は test kind の argv 契約が拒否する。
    """
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - 実行環境依存の防御
        raise DerivationError("stdout が UTF-8 として解釈できない") from exc
    lines = text.splitlines()
    non_empty = [index for index, line in enumerate(lines) if line.strip()]
    if not non_empty:
        raise DerivationError("stdout が空のため pytest run summary を導出できない")

    final_index = non_empty[-1]
    final_line = lines[final_index].strip().strip("=").strip()
    final_match = _PYTEST_FINAL_LINE_RE.match(final_line)
    if final_match is None:
        raise DerivationError(f"stdout 最終行が pytest の summary 行でない: {final_line!r}")
    counts: dict[str, int] = dict.fromkeys(PYTEST_OUTCOME_KEYS, 0)
    body = final_match.group("body")
    if body != "no tests ran":
        seen_tokens: set[str] = set()
        for token in body.split(", "):
            token_match = _PYTEST_FINAL_TOKEN_RE.match(token)
            if token_match is None:  # pragma: no cover - 上位 regex が形式を保証する
                raise DerivationError(f"pytest summary token を解釈できない: {token!r}")
            number, word = int(token_match.group(1)), token_match.group(2)
            if word in _PYTEST_FINAL_IGNORED_TOKENS:
                continue
            key = _PYTEST_FINAL_TOKEN_KEYS.get(word)
            if key is None:
                raise DerivationError(f"未知の pytest outcome token: {word!r}")
            if key in seen_tokens:
                raise DerivationError(f"pytest summary 行に {key} が複数回現れる")
            seen_tokens.add(key)
            counts[key] = number

    header_indices = [
        index
        for index, line in enumerate(lines[:final_index])
        if _PYTEST_SHORT_SUMMARY_HEADER_RE.match(line.strip())
    ]
    passed_ids: list[str] = []
    line_counts: dict[str, int] = dict.fromkeys(PYTEST_OUTCOME_KEYS, 0)
    if header_indices:
        for line in lines[header_indices[-1] + 1 : final_index]:
            if not line.strip():
                continue
            summary_match = _PYTEST_SUMMARY_LINE_RE.match(line)
            if summary_match is None:
                raise DerivationError(f"short test summary 行を解釈できない: {line!r}")
            outcome = summary_match.group("outcome")
            rest = summary_match.group("rest")
            if outcome == "PASSED":
                if not rest or rest != rest.strip():
                    raise DerivationError(f"PASSED 行の node ID が不正: {rest!r}")
                if rest in passed_ids:
                    raise DerivationError(f"PASSED 行に node ID が重複している: {rest}")
                passed_ids.append(rest)
                line_counts["passed"] += 1
            elif outcome == "FAILED":
                line_counts["failed"] += 1
            elif outcome == "ERROR":
                line_counts["errors"] += 1
            elif outcome == "XFAIL":
                line_counts["xfailed"] += 1
            elif outcome == "XPASS":
                line_counts["xpassed"] += 1
            else:  # SKIPPED [n] file:line: reason
                skipped_match = _PYTEST_SKIPPED_COUNT_RE.match(rest)
                if skipped_match is None:
                    raise DerivationError(f"SKIPPED 行の件数を解釈できない: {rest!r}")
                line_counts["skipped"] += int(skipped_match.group(1))
    elif any(counts[key] for key in PYTEST_OUTCOME_KEYS if key != "deselected"):
        raise DerivationError("outcome があるのに short test summary（-rA）が stdout にない")

    for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "errors"):
        if line_counts[key] != counts[key]:
            raise DerivationError(
                f"short test summary の {key} 行数 {line_counts[key]} が"
                f"最終行の件数 {counts[key]} と一致しない"
            )
    value: dict[str, object] = {key: counts[key] for key in PYTEST_OUTCOME_KEYS}
    value["passed_node_ids"] = utf8_sorted_unique(passed_ids)
    return value


def derive_observed_value(
    claim_type: str,
    *,
    exit_code: int,
    stdout: bytes | None = None,
) -> object:
    """claim type に対応する typed parser で observed value を導出する。"""
    if claim_type == "exit_success":
        return parse_exit_success(exit_code=exit_code)
    if claim_type == "no_match":
        return parse_no_match(exit_code=exit_code)
    if claim_type == "count":
        if stdout is None:
            raise DerivationError("count claim は raw stdout が必要")
        return parse_count(stdout)
    if claim_type == "sha":
        if stdout is None:
            raise DerivationError("sha claim は raw stdout が必要")
        return parse_sha(stdout)
    if claim_type == "pytest_run":
        if stdout is None:
            raise DerivationError("pytest_run claim は raw stdout が必要")
        return parse_pytest_run(stdout)
    if claim_type == "free_text":
        raise DerivationError("free_text claim は typed 再導出の対象にできない")
    raise DerivationError(f"未知の claim type: {claim_type}")


def pytest_run_value_violation(value: object) -> str | None:
    """pytest_run の observed value が typed 形（件数 7 種＋canonical node ID 集合）かを検査する。

    構造違反の説明文を返し、問題がなければ ``None`` を返す。core と adapter が同じ判定を使う。
    """
    if not isinstance(value, Mapping):
        return "pytest_run claim の observed_value が object でない"
    if set(value) != PYTEST_RUN_VALUE_FIELDS:
        return f"pytest_run observed_value の field 集合が不正: {sorted(value)}"
    for key in PYTEST_OUTCOME_KEYS:
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            return f"pytest_run observed_value.{key} が非負 int でない"
    node_ids = value.get("passed_node_ids")
    ids = _str_list(node_ids)
    if ids is None:
        return "pytest_run observed_value.passed_node_ids が string list でない"
    if not is_utf8_sorted_unique(ids):
        return "pytest_run observed_value.passed_node_ids が UTF-8 昇順・重複なしでない"
    if len(ids) != value.get("passed"):
        return "pytest_run observed_value の passed 件数と passed_node_ids 長が一致しない"
    return None


# ---------------------------------------------------------------------------
# excerpt / redaction の共有判定
# ---------------------------------------------------------------------------


def excerpt_content_bytes(excerpt: str) -> int:
    """省略マーカーを除いた excerpt の UTF-8 byte 数を返す。"""
    return len(EXCERPT_ELISION_RE.sub("", excerpt).encode("utf-8"))


def excerpt_has_elision(excerpt: str) -> bool:
    """excerpt が中間省略マーカーを持つかを返す。"""
    return EXCERPT_ELISION_RE.search(excerpt) is not None


def forbidden_matches(text: str) -> tuple[str, ...]:
    """redaction 後に残っている禁止 pattern 名を返す。"""
    return tuple(name for name, pattern in FORBIDDEN_PATTERNS if pattern.search(text))


def shell_contract_violations(script: str, *, role: str) -> tuple[str, ...]:
    """shell command の失敗隠蔽 pattern を返す（verification のみ拒否対象）。"""
    if role != "verification":
        return ()
    return tuple(name for name, pattern in SHELL_MASKING_PATTERNS if pattern.search(script))


# ---------------------------------------------------------------------------
# 結果型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapturedStream:
    """capture 時の raw stream と終了コード。artifact へは保存しない。"""

    stdout: bytes
    stderr: bytes
    exit_code: int


@dataclass(frozen=True)
class ContractViolation:
    """真偽規則違反 1 件。"""

    code: str
    message: str
    command_id: str | None = None

    def render(self) -> str:
        """人間可読な 1 行表現を返す。"""
        scope = f"[{self.command_id}] " if self.command_id else ""
        return f"{self.code}: {scope}{self.message}"


@dataclass(frozen=True)
class ValidationResult:
    """共通 validator の判定結果。

    ``contract_valid`` は artifact が契約として有効か（正直な fail 報告を含む）を表す。
    ``result`` は gate が使える最強の判定であり、``pass`` は全規則を満たす場合のみ。
    """

    result: Literal["pass", "fail", "inconclusive"]
    contract_valid: bool
    violations: tuple[ContractViolation, ...] = ()
    inconclusive_reasons: tuple[str, ...] = ()
    verified_command_ids: tuple[str, ...] = ()
    verified_claim_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """gate pass として使えるかを返す。"""
        return self.contract_valid and self.result == "pass"

    def codes(self) -> frozenset[str]:
        """検出した violation code 集合を返す。"""
        return frozenset(violation.code for violation in self.violations)

    def render(self) -> str:
        """判定結果の要約文字列を返す。"""
        head = f"result={self.result} contract_valid={self.contract_valid}"
        lines = [head]
        lines.extend(f"  - {violation.render()}" for violation in self.violations)
        lines.extend(f"  ~ inconclusive: {reason}" for reason in self.inconclusive_reasons)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# schema version の互換境界
# ---------------------------------------------------------------------------


def classify_schema_version(
    artifact: Mapping[str, Any],
) -> Literal["gate_eligible", "historical_read_only", "unsupported"]:
    """artifact の schema version を用途区分へ分類する。

    v2 だけが新規/変更 evidence と gate pass に使える。v1 は historical read 専用で、
    gate pass の根拠にはできない（Fail-close / Compatibility Boundary）。
    """
    version = artifact.get("schema_version")
    if version == SCHEMA_VERSION_V2:
        return "gate_eligible"
    if version == SCHEMA_VERSION_V1:
        return "historical_read_only"
    return "unsupported"


def is_gate_eligible_schema(artifact: Mapping[str, Any]) -> bool:
    """gate pass に使える schema version かを返す。"""
    return classify_schema_version(artifact) == "gate_eligible"


# ---------------------------------------------------------------------------
# command manifest
# ---------------------------------------------------------------------------

_MANIFEST_COMMAND_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "command_id",
    "role",
    "command",
    "execution_mode",
    "canonical_command_sha256",
    "expected_exit_codes",
    "claim_type",
    "derivation",
)


def validate_command_manifest(
    manifest: Mapping[str, Any],
    *,
    raw_bytes: bytes | None = None,
) -> tuple[ContractViolation, ...]:
    """実行前 command manifest の契約検査を行う（Rule 1 / AC-12 の manifest 側）。"""
    violations: list[ContractViolation] = []

    def add(code: str, message: str, command_id: str | None = None) -> None:
        violations.append(ContractViolation(code=code, message=message, command_id=command_id))

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        add("manifest_invalid", "manifest schema_version が 1 でない")
    manifest_id = manifest.get("manifest_id")
    if not isinstance(manifest_id, str) or not manifest_id:
        add("manifest_invalid", "manifest_id が非空 string でない")

    commands = manifest.get("commands")
    if not isinstance(commands, list) or not commands:
        add("manifest_invalid", "manifest commands が非空 list でない")
        return tuple(violations)

    seen: set[str] = set()
    verification_ids: list[str] = []
    all_ids: list[str] = []
    for raw_entry in commands:
        if not isinstance(raw_entry, Mapping):
            add("manifest_invalid", "manifest command entry が object でない")
            continue
        entry: Mapping[str, Any] = raw_entry
        command_id = entry.get("command_id")
        if not isinstance(command_id, str) or not command_id:
            add("manifest_invalid", "command_id が非空 string でない")
            continue
        if command_id in seen:
            add("duplicate_command_id", "manifest に重複 command_id がある", command_id)
            continue
        seen.add(command_id)
        all_ids.append(command_id)
        for name in _MANIFEST_COMMAND_REQUIRED_FIELDS:
            if name not in entry:
                add("manifest_invalid", f"manifest command に {name} がない", command_id)
        role = entry.get("role")
        if role not in COMMAND_ROLES:
            add("manifest_invalid", f"未知の role: {role!r}", command_id)
        elif role == "verification":
            verification_ids.append(command_id)
        mode = entry.get("execution_mode")
        if mode not in EXECUTION_MODES:
            add("manifest_invalid", f"未知の execution_mode: {mode!r}", command_id)
        claim_type = entry.get("claim_type")
        if claim_type not in CLAIM_TYPES:
            add("manifest_invalid", f"未知の claim_type: {claim_type!r}", command_id)
        elif entry.get("derivation") != CLAIM_TYPE_PARSERS[str(claim_type)]:
            add(
                "manifest_invalid",
                f"claim_type={claim_type} の derivation が規定 parser でない",
                command_id,
            )
        if claim_type == "free_text" and role == "verification":
            add("free_text_verification", "free_text claim を verification に使えない", command_id)

        expected = entry.get("expected_exit_codes")
        expected_codes = _int_list(expected)
        if expected_codes is None or not expected_codes:
            add("manifest_invalid", "expected_exit_codes が非空 int list でない", command_id)
        else:
            if len(set(expected_codes)) != len(expected_codes):
                add("manifest_invalid", "expected_exit_codes に重複がある", command_id)
            forbidden = sorted(set(expected_codes) & FORBIDDEN_EXIT_CODES)
            if forbidden:
                add(
                    "expected_exit_code_forbidden",
                    f"exit {forbidden} を expected list に書くことはできない",
                    command_id,
                )
            if claim_type == "no_match" and tuple(expected_codes) != NO_MATCH_EXPECTED_EXIT_CODES:
                add(
                    "no_match_contract",
                    "no_match claim は expected_exit_codes=[0, 1] を必須とする",
                    command_id,
                )
            if claim_type != "no_match" and tuple(expected_codes) == NO_MATCH_EXPECTED_EXIT_CODES:
                add(
                    "no_match_contract",
                    "expected_exit_codes=[0, 1] は claim_type=no_match のみで使える",
                    command_id,
                )

        # canonical hash を argv / script から再計算し、manifest 記載値と exact 照合する。
        if mode == "argv":
            argv = entry.get("argv")
            argv_list = _str_list(argv)
            if argv_list is None or not argv_list:
                add("manifest_invalid", "execution_mode=argv に argv がない", command_id)
            else:
                expected_hash = canonical_command_sha256("argv", argv=argv_list)
                if entry.get("canonical_command_sha256") != expected_hash:
                    add(
                        "manifest_hash_mismatch",
                        "argv から再計算した canonical_command_sha256 と一致しない",
                        command_id,
                    )
        elif mode == "shell":
            script = entry.get("script")
            if not isinstance(script, str) or not script:
                add("manifest_invalid", "execution_mode=shell に script がない", command_id)
            else:
                expected_hash = canonical_command_sha256("shell", script=script)
                if entry.get("canonical_command_sha256") != expected_hash:
                    add(
                        "manifest_hash_mismatch",
                        "script から再計算した canonical_command_sha256 と一致しない",
                        command_id,
                    )
                masked = shell_contract_violations(script, role=str(role))
                if masked:
                    add(
                        "shell_masking_rejected",
                        f"verification で失敗を隠す shell 構文がある: {', '.join(masked)}",
                        command_id,
                    )

    if not verification_ids:
        add("empty_verification_set", "manifest に verification command が 1 件もない")

    declared_all = manifest.get("required_command_ids")
    if declared_all is not None and _str_list(declared_all) != sorted(all_ids):
        add(
            "required_command_ids_mismatch", "manifest の required_command_ids が commands と不一致"
        )
    declared_verification = manifest.get("required_verification_ids")
    if declared_verification is not None and _str_list(declared_verification) != sorted(
        verification_ids
    ):
        add(
            "required_verification_ids_mismatch",
            "manifest の required_verification_ids が commands と不一致",
        )

    if raw_bytes is not None and raw_bytes != canonical_content_bytes(manifest):
        add(
            "manifest_raw_bytes_not_canonical",
            "manifest の raw bytes が canonical-content-v1 出力と exact 一致しない",
        )
    return tuple(violations)


def manifest_command_index(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """manifest の command entry を command_id で引ける index を返す。"""
    index: dict[str, Mapping[str, Any]] = {}
    commands = manifest.get("commands")
    if not isinstance(commands, list):
        return index
    for entry in commands:
        if isinstance(entry, Mapping):
            command_id = entry.get("command_id")
            if isinstance(command_id, str):
                index[command_id] = entry
    return index


# ---------------------------------------------------------------------------
# 内部 helper
# ---------------------------------------------------------------------------


def _int_list(value: object) -> list[int] | None:
    """int だけの list を返す（bool は int として受け付けない）。"""
    if not isinstance(value, list):
        return None
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        result.append(item)
    return result


def _str_list(value: object) -> list[str] | None:
    """string だけの list を返す。"""
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        result.append(item)
    return result


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX64_RE.match(value))


def _is_rfc3339(value: object) -> bool:
    return isinstance(value, str) and bool(_RFC3339_RE.match(value))


def _claimed_counts(claim: str) -> set[int] | None:
    """claim 本文から件数として主張された整数集合を返す（明示がなければ None）。"""
    explicit = {int(m) for m in _CLAIM_COUNT_KV_RE.findall(claim)}
    explicit |= {int(m) for m in _CLAIM_COUNT_UNIT_RE.findall(claim)}
    if explicit:
        return explicit
    return None


# ---------------------------------------------------------------------------
# 共通 validator
# ---------------------------------------------------------------------------


@dataclass
class _State:
    """検査中に蓄積する状態。"""

    violations: list[ContractViolation] = field(default_factory=list)
    inconclusive: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class _EvidenceValidator:
    """Evidence Schema v2 の全規則を 1 か所で評価する内部実装。"""

    def __init__(
        self,
        artifact: Mapping[str, Any],
        *,
        manifest: Mapping[str, Any] | None,
        manifest_raw_bytes: bytes | None,
        raw_streams: Mapping[str, CapturedStream] | None,
        normalized_inputs: Mapping[str, bytes] | None,
        trusted_validator_sources: frozenset[str] | None,
        trusted_producer_sources: frozenset[str] | None,
        require_attestation: bool,
        purpose: Literal["gate", "historical_read"],
    ) -> None:
        self.artifact = artifact
        self.manifest = manifest
        self.manifest_raw_bytes = manifest_raw_bytes
        self.raw_streams = raw_streams or {}
        self.normalized_inputs = normalized_inputs or {}
        self.trusted_validator_sources = trusted_validator_sources
        self.trusted_producer_sources = trusted_producer_sources
        self.require_attestation = require_attestation
        self.purpose = purpose
        self.state = _State()
        self.claimed_result = artifact.get("result")
        self.pass_claimed = self.claimed_result == "pass"
        self.command_ids: list[str] = []
        self.verification_ids: list[str] = []

    # -- 記録 --------------------------------------------------------------

    def add(self, code: str, message: str, command_id: str | None = None) -> None:
        """violation を記録する。"""
        self.state.violations.append(
            ContractViolation(code=code, message=message, command_id=command_id)
        )

    def add_inconclusive(self, reason: str) -> None:
        """pass にできない未確定要因を記録する。"""
        if reason not in self.state.inconclusive:
            self.state.inconclusive.append(reason)

    def add_note(self, note: str) -> None:
        """判定を変えない観測メモを記録する。"""
        if note not in self.state.notes:
            self.state.notes.append(note)

    # -- 個別検査 ----------------------------------------------------------

    def check_schema(self) -> bool:
        """schema version と gate 対象 kind の境界を検査する。"""
        usage = classify_schema_version(self.artifact)
        if usage == "unsupported":
            self.add("schema_version_unsupported", "schema_version が 1 でも 2 でもない")
            return False
        if usage == "historical_read_only":
            if self.purpose == "gate":
                self.add(
                    "schema_v1_not_gate_eligible",
                    "schema v1 evidence は historical read 専用で gate pass にできない",
                )
                return False
            self.add_inconclusive("historical_read_only")
            return False

        kind = self.artifact.get("artifact_kind")
        if not isinstance(kind, str) or kind not in KNOWN_ARTIFACT_KINDS:
            self.add("invalid_field", f"未知の artifact_kind: {kind!r}")
            return False
        if kind not in GATE_ELIGIBLE_KINDS:
            # research_result は Phase B（N-602B）の adapter 待ち。core は kind 値だけを検証する。
            self.add_inconclusive(f"artifact_kind_phase_b:{kind}")
        return True

    def check_kind_field_boundary(self) -> None:
        """typed 結果 kind（test／ci）の固有 field が kind をまたいで混入していないかを検査する。

        review artifact に test の結果 field を持ち込むなど、kind を偽装した artifact を
        schema 境界で拒否する。Phase A 3 kind 相互の混入は既存 gate 挙動を保つため対象外。
        """
        kind = self.artifact.get("artifact_kind")
        if not isinstance(kind, str):
            return
        for other_kind, names in KIND_SPECIFIC_TOP_LEVEL_FIELDS.items():
            if other_kind == kind:
                continue
            if kind not in TYPED_RESULT_KINDS and other_kind not in TYPED_RESULT_KINDS:
                continue
            leaked = sorted(names & set(self.artifact))
            if leaked:
                self.add(
                    "invalid_field",
                    f"artifact_kind={kind} に {other_kind} kind 固有の field がある: {leaked}",
                )

    def check_top_level(self) -> None:
        """top-level field の存在と型を検査する（Rule 9 / 10 を含む）。"""
        for name in _REQUIRED_TOP_LEVEL_FIELDS:
            if name not in self.artifact:
                self.add("missing_field", f"top-level field {name} がない")

        producer = self.artifact.get("producer")
        if not isinstance(producer, Mapping):
            self.add("handwritten_entry", "producer object がない（generator marker 不在）")
        else:
            if producer.get("name") != GENERATOR_MARKER:
                self.add(
                    "handwritten_entry",
                    f"producer.name が generator marker と一致しない: {producer.get('name')!r}",
                )
            if not _is_hex64(producer.get("source_sha256")):
                self.add("handwritten_entry", "producer.source_sha256 が 64 桁 hex でない")
            elif (
                self.trusted_producer_sources is not None
                and producer.get("source_sha256") not in self.trusted_producer_sources
            ):
                self.add("unknown_producer_source", "producer.source_sha256 が信頼集合にない")
            if not isinstance(producer.get("source_git_oid"), str):
                self.add("handwritten_entry", "producer.source_git_oid がない")

        if not _is_rfc3339(self.artifact.get("generated_at")):
            self.add("invalid_field", "generated_at が RFC3339 でない")
        repository = self.artifact.get("repository")
        if not isinstance(repository, str) or not repository:
            self.add("invalid_field", "repository が非空 string でない")
        head_sha = self.artifact.get("head_sha")
        if not isinstance(head_sha, str) or not _HEX40_RE.match(head_sha):
            self.add("invalid_field", "head_sha が 40 桁 lower hex でない")
        if self.claimed_result not in ARTIFACT_RESULTS:
            self.add(
                "invalid_field", f"result が pass/fail/inconclusive でない: {self.claimed_result!r}"
            )

        if not isinstance(self.artifact.get("corrections"), list):
            self.add("invalid_field", "corrections が list でない")

        if not _is_hex64(self.artifact.get("artifact_sha256")):
            self.add(
                "handwritten_entry", "artifact_sha256 が 64 桁 hex でない（content hash 欠損）"
            )
        else:
            recomputed = canonical_hash_with_null_fields(self.artifact, "artifact_sha256")
            if recomputed != self.artifact.get("artifact_sha256"):
                self.add(
                    "artifact_hash_mismatch", "artifact_sha256 が canonical 再計算値と一致しない"
                )

    def check_research_fields(self) -> None:
        """research 拡張 field の kind 別契約を検査する（Rule 12 の Phase A 分）。"""
        kind = self.artifact.get("artifact_kind")
        binding = self.artifact.get("research_binding")
        result = self.artifact.get("research_result")
        if kind == "research_result":
            if not isinstance(binding, Mapping):
                self.add(
                    "research_binding_invalid", "research_result kind に research_binding がない"
                )
            if not isinstance(result, Mapping):
                self.add(
                    "research_binding_invalid", "research_result kind に research_result がない"
                )
            # decision manifest 由来の再導出は N-602B の adapter が担う。
            self.add_inconclusive("research_result_adapter_phase_b")
            return
        if binding is not None:
            self.add(
                "research_fields_not_null",
                f"artifact_kind={kind} では research_binding を null にする",
            )
        if result is not None:
            self.add(
                "research_fields_not_null",
                f"artifact_kind={kind} では research_result を null にする",
            )

    def check_manifest_binding(self) -> dict[str, Mapping[str, Any]]:
        """command manifest binding を検査し manifest index を返す（Rule 1 / AC-12）。"""
        manifest_ref = self.artifact.get("command_manifest_ref")
        if not isinstance(manifest_ref, str) or not manifest_ref:
            self.add("invalid_field", "command_manifest_ref が非空 string でない")
        manifest_sha = self.artifact.get("command_manifest_sha256")
        if not _is_hex64(manifest_sha):
            self.add("invalid_field", "command_manifest_sha256 が 64 桁 hex でない")

        if self.manifest is None:
            self.add(
                "manifest_missing", "実行前 command manifest がないため binding を検証できない"
            )
            return {}

        manifest_violations = validate_command_manifest(
            self.manifest, raw_bytes=self.manifest_raw_bytes
        )
        self.state.violations.extend(manifest_violations)

        if self.manifest_raw_bytes is not None:
            actual = sha256_bytes(self.manifest_raw_bytes)
            if actual != manifest_sha:
                self.add(
                    "manifest_hash_mismatch",
                    "command_manifest_sha256 が manifest raw byte hash と一致しない",
                )
        else:
            # raw bytes が渡されない場合は canonical 出力の hash と照合する。
            expected = canonical_content_sha256(self.manifest)
            if expected != manifest_sha:
                self.add(
                    "manifest_hash_mismatch",
                    "command_manifest_sha256 が manifest canonical hash と一致しない",
                )
        return manifest_command_index(self.manifest)

    def check_command_id_sets(self, index: Mapping[str, Mapping[str, Any]]) -> None:
        """command ID 集合と required 集合の exact 一致を検査する（Rule 1）。"""
        commands = self.artifact.get("commands")
        if not isinstance(commands, list) or not commands:
            self.add("empty_commands", "commands が非空 list でない")
            return

        ids: list[str] = []
        verification_ids: list[str] = []
        for entry in commands:
            if not isinstance(entry, Mapping):
                self.add("invalid_field", "command entry が object でない")
                continue
            command_id = entry.get("command_id")
            if not isinstance(command_id, str) or not command_id:
                self.add("invalid_field", "command_id が非空 string でない")
                continue
            if command_id in ids:
                self.add("duplicate_command_id", "command_id が重複している", command_id)
                continue
            ids.append(command_id)
            if entry.get("role") == "verification":
                verification_ids.append(command_id)
        self.command_ids = ids
        self.verification_ids = verification_ids

        declared_all = _str_list(self.artifact.get("required_command_ids"))
        if declared_all is None:
            self.add("invalid_field", "required_command_ids が string list でない")
        else:
            if len(set(declared_all)) != len(declared_all):
                self.add("required_command_ids_mismatch", "required_command_ids に重複がある")
            if set(declared_all) != set(ids):
                self.add(
                    "command_id_set_mismatch",
                    "commands の ID 集合が required_command_ids と exact 一致しない",
                )

        declared_verification = _str_list(self.artifact.get("required_verification_ids"))
        if declared_verification is None:
            self.add("invalid_field", "required_verification_ids が string list でない")
        else:
            if not declared_verification:
                self.add("empty_verification_set", "required_verification_ids が空集合")
            if set(declared_verification) != set(verification_ids):
                self.add(
                    "required_verification_ids_mismatch",
                    "verification command 集合が required_verification_ids と exact 一致しない",
                )
        if not verification_ids:
            self.add("empty_verification_set", "verification command が 1 件もない")

        if index:
            manifest_ids = set(index)
            if manifest_ids != set(ids):
                missing = sorted(manifest_ids - set(ids))
                extra = sorted(set(ids) - manifest_ids)
                self.add(
                    "command_id_set_mismatch",
                    f"manifest との command 集合差分: missing={missing} extra={extra}",
                )
            manifest_verifications = {
                command_id
                for command_id, entry in index.items()
                if entry.get("role") == "verification"
            }
            if declared_verification is not None and set(declared_verification) != (
                manifest_verifications
            ):
                self.add(
                    "required_verification_ids_mismatch",
                    "required_verification_ids が manifest の verification 集合と一致しない",
                )

    def check_commands(self, index: Mapping[str, Mapping[str, Any]]) -> None:
        """各 command entry の真偽規則を検査する。"""
        commands = self.artifact.get("commands")
        if not isinstance(commands, list):
            return
        for entry in commands:
            if isinstance(entry, Mapping):
                self._check_command_entry(entry, index)

    def _check_command_entry(
        self,
        entry: Mapping[str, Any],
        index: Mapping[str, Mapping[str, Any]],
    ) -> None:
        raw_id = entry.get("command_id")
        command_id = raw_id if isinstance(raw_id, str) else None

        unknown = set(entry) - _COMMAND_ENTRY_FIELDS
        if unknown:
            self.add("invalid_field", f"未知の command field: {sorted(unknown)}", command_id)
        missing = _COMMAND_ENTRY_FIELDS - set(entry)
        if missing:
            # timing 欠損は手書き entry の主要指紋（Rule 10）。
            code = (
                "handwritten_entry" if missing & {"started_at", "duration_ms"} else "missing_field"
            )
            self.add(code, f"command field が欠けている: {sorted(missing)}", command_id)

        role = entry.get("role")
        if role not in COMMAND_ROLES:
            self.add("invalid_field", f"未知の role: {role!r}", command_id)
        claim_type = entry.get("claim_type")
        if claim_type not in CLAIM_TYPES:
            self.add("invalid_field", f"未知の claim_type: {claim_type!r}", command_id)
        if claim_type == "free_text" and role == "verification":
            self.add(
                "free_text_verification",
                "free_text claim は verification や top-level pass の根拠にできない",
                command_id,
            )
        if entry.get("derivation") != CLAIM_TYPE_PARSERS.get(str(claim_type), object()):
            self.add(
                "derivation_mismatch",
                f"claim_type={claim_type!r} に対応しない derivation: {entry.get('derivation')!r}",
                command_id,
            )
        if not _is_rfc3339(entry.get("started_at")):
            self.add(
                "handwritten_entry",
                "started_at が RFC3339 でない（command timing 欠損）",
                command_id,
            )
        duration = entry.get("duration_ms")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
            self.add("handwritten_entry", "duration_ms が非負 int でない", command_id)
        if not _is_hex64(entry.get("canonical_command_sha256")):
            self.add("invalid_field", "canonical_command_sha256 が 64 桁 hex でない", command_id)
        display = entry.get("command")
        if not isinstance(display, str) or not display:
            self.add("invalid_field", "command 表示文字列が非空 string でない", command_id)
        if entry.get("execution_mode") not in EXECUTION_MODES:
            self.add(
                "invalid_field",
                f"未知の execution_mode: {entry.get('execution_mode')!r}",
                command_id,
            )

        expected = _int_list(entry.get("expected_exit_codes"))
        if expected is None or not expected:
            self.add("invalid_field", "expected_exit_codes が非空 int list でない", command_id)
            expected = []
        forbidden = sorted(set(expected) & FORBIDDEN_EXIT_CODES)
        if forbidden:
            self.add(
                "expected_exit_code_forbidden",
                f"exit {forbidden} は expected list に書いても無効（Rule 3）",
                command_id,
            )

        observed = entry.get("observed_exit_code")
        if isinstance(observed, bool) or not isinstance(observed, int):
            self.add("invalid_field", "observed_exit_code が int でない", command_id)
            observed = None
        else:
            if observed < 0:
                self.add(
                    "signal_termination",
                    f"signal で終了した command は pass にできない: returncode={observed}",
                    command_id,
                )
            if observed in FORBIDDEN_EXIT_CODES:
                self.add(
                    "exit_126_127",
                    f"exit {observed} は role/expected list に関係なく artifact を invalid にする",
                    command_id,
                )
            if expected and observed not in expected:
                if self.pass_claimed:
                    self.add(
                        "unexpected_exit",
                        f"observed_exit_code={observed} が expected={expected} に含まれない",
                        command_id,
                    )
                else:
                    self.add_note(
                        f"{command_id}: unexpected exit {observed}"
                        "（result!=pass のため正直な fail 記録）"
                    )

        # Rule 5: no-match 契約
        if claim_type == "no_match" and tuple(expected) != NO_MATCH_EXPECTED_EXIT_CODES:
            self.add(
                "no_match_contract",
                "no_match claim は expected_exit_codes=[0, 1] を必須とする",
                command_id,
            )
        if claim_type != "no_match" and tuple(expected) == NO_MATCH_EXPECTED_EXIT_CODES:
            self.add(
                "no_match_contract",
                "expected_exit_codes=[0, 1] は claim_type=no_match のみで使える",
                command_id,
            )

        self._check_preflight(entry, command_id)
        self._check_streams(entry, command_id)
        self._check_manifest_contract(entry, index, command_id)
        if isinstance(observed, int):
            # expected 契約を満たさない command は typed 値が導出できないことがある。
            # その場合でも「pass を主張していない」なら正直な fail 記録として許す。
            exit_in_expected = bool(expected) and observed in expected
            self._check_claim(
                entry,
                command_id,
                observed_exit_code=observed,
                strict=self.pass_claimed or exit_in_expected,
            )

    def _check_preflight(self, entry: Mapping[str, Any], command_id: str | None) -> None:
        preflight = entry.get("preflight")
        if not isinstance(preflight, Mapping):
            self.add("preflight_missing", "preflight object がない（Rule 8）", command_id)
            return
        unknown = set(preflight) - _PREFLIGHT_FIELDS
        if unknown:
            self.add("invalid_field", f"未知の preflight field: {sorted(unknown)}", command_id)
        executable = preflight.get("executable")
        if not isinstance(executable, str) or not executable:
            self.add("preflight_missing", "preflight.executable が非空 string でない", command_id)
        if not _is_hex64(preflight.get("resolved_path_sha256")):
            self.add(
                "preflight_missing",
                "preflight.resolved_path_sha256 が 64 桁 hex でない",
                command_id,
            )
        available = preflight.get("available")
        if available is not True:
            self.add(
                "preflight_unavailable",
                "executable を解決できない command は実行せず exit 127 相当の fail とする",
                command_id,
            )
        self._check_preflight_binding(preflight, command_id)

    def _check_preflight_binding(
        self, preflight: Mapping[str, Any], command_id: str | None
    ) -> None:
        """optional な executable binding field を、存在する場合だけ型検査する。

        欠落は許容する（emit は後続 PR）。存在するなら ``resolved_path`` と ``realpath`` は
        非空の絶対 path 文字列、``post_execution_sha256`` は 64 桁 hex でなければならない。
        ``post_execution_sha256`` が ``resolved_path_sha256`` と一致しない artifact は、
        generator が ``executable_replaced`` で fail-close すべき状態を書いた矛盾なので拒否する。
        """
        if "resolved_path" in preflight:
            resolved_path = preflight.get("resolved_path")
            if (
                not isinstance(resolved_path, str)
                or not resolved_path
                or not os.path.isabs(resolved_path)
            ):
                self.add(
                    "invalid_field",
                    "preflight.resolved_path が非空の絶対 path 文字列でない",
                    command_id,
                )
        if "realpath" in preflight:
            realpath = preflight.get("realpath")
            # generator は os.path.realpath(resolved_path) を入れるので常に絶対 path。相対 path の
            # ような曖昧な値は「symlink 解決後の実体」を示さないため拒否する（PR #1374 Round 1）。
            if not isinstance(realpath, str) or not realpath or not os.path.isabs(realpath):
                self.add(
                    "invalid_field",
                    "preflight.realpath が非空の絶対 path 文字列でない",
                    command_id,
                )
        if "post_execution_sha256" in preflight:
            post_sha = preflight.get("post_execution_sha256")
            if not _is_hex64(post_sha):
                self.add(
                    "invalid_field",
                    "preflight.post_execution_sha256 が 64 桁 hex でない",
                    command_id,
                )
            elif _is_hex64(preflight.get("resolved_path_sha256")) and post_sha != preflight.get(
                "resolved_path_sha256"
            ):
                self.add(
                    "executable_replaced",
                    "preflight.post_execution_sha256 が resolved_path_sha256 と一致しない"
                    "（実行中に executable が差し替わった記録は pass にできない）",
                    command_id,
                )

    def _check_streams(self, entry: Mapping[str, Any], command_id: str | None) -> None:
        content_total = 0
        for stream in ("stdout", "stderr"):
            digest = entry.get(f"{stream}_sha256")
            size = entry.get(f"{stream}_bytes")
            excerpt = entry.get(f"{stream}_excerpt")
            truncated = entry.get(f"{stream}_truncated")
            if not _is_hex64(digest):
                self.add("invalid_field", f"{stream}_sha256 が 64 桁 hex でない", command_id)
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                self.add("invalid_field", f"{stream}_bytes が非負 int でない", command_id)
                size = None
            if size == 0 and digest != EMPTY_SHA256:
                self.add(
                    "empty_stream_hash_mismatch",
                    f"{stream} が空なのに空 stream の SHA-256 と一致しない",
                    command_id,
                )
            if not isinstance(excerpt, str):
                self.add("invalid_field", f"{stream}_excerpt が string でない", command_id)
                continue
            if not isinstance(truncated, bool):
                self.add("invalid_field", f"{stream}_truncated が bool でない", command_id)
                truncated = None

            content = excerpt_content_bytes(excerpt)
            content_total += content
            if content > EXCERPT_MAX_BYTES_PER_STREAM:
                self.add(
                    "excerpt_limit_exceeded",
                    f"{stream}_excerpt が上限 {EXCERPT_MAX_BYTES_PER_STREAM} bytes 超過: {content}",
                    command_id,
                )
            has_elision = excerpt_has_elision(excerpt)
            if truncated is True and not has_elision:
                self.add(
                    "truncation_marker_missing",
                    f"{stream}_truncated=true なのに中間省略マーカーがない",
                    command_id,
                )
            if truncated is False:
                if has_elision:
                    self.add(
                        "truncation_flag_missing",
                        f"{stream}_excerpt に省略マーカーがあるのに truncated=false",
                        command_id,
                    )
                # redaction は excerpt を縮めるため byte 差だけでは判定しない。
                # raw が上限を超えている場合は必ず truncation が起きている。
                if isinstance(size, int) and size > EXCERPT_MAX_BYTES_PER_STREAM:
                    self.add(
                        "truncation_flag_missing",
                        f"{stream} が {size} bytes なのに truncated=false（silent truncate）",
                        command_id,
                    )
            for name in forbidden_matches(excerpt):
                self.add(
                    "forbidden_content",
                    f"{stream}_excerpt に redaction 漏れがある: {name}",
                    command_id,
                )

            raw = self.raw_streams.get(command_id) if command_id else None
            if raw is not None:
                raw_bytes = raw.stdout if stream == "stdout" else raw.stderr
                if digest != sha256_bytes(raw_bytes):
                    self.add(
                        "stream_hash_mismatch",
                        f"{stream}_sha256 が raw stream の SHA-256 と一致しない",
                        command_id,
                    )
                if isinstance(size, int) and size != len(raw_bytes):
                    self.add(
                        "stream_bytes_mismatch",
                        f"{stream}_bytes が raw stream 長と一致しない",
                        command_id,
                    )

        if content_total > EXCERPT_MAX_BYTES_PER_COMMAND:
            self.add(
                "excerpt_command_limit_exceeded",
                f"1 command の excerpt 合計が {EXCERPT_MAX_BYTES_PER_COMMAND} bytes を超えている",
                command_id,
            )

    def _check_manifest_contract(
        self,
        entry: Mapping[str, Any],
        index: Mapping[str, Mapping[str, Any]],
        command_id: str | None,
    ) -> None:
        if command_id is None or command_id not in index:
            return
        manifest_entry = index[command_id]
        for name in (
            "role",
            "command",
            "execution_mode",
            "canonical_command_sha256",
            "claim_type",
            "derivation",
        ):
            if entry.get(name) != manifest_entry.get(name):
                self.add(
                    "command_contract_mismatch",
                    f"{name} が manifest と一致しない: "
                    f"artifact={entry.get(name)!r} manifest={manifest_entry.get(name)!r}",
                    command_id,
                )
        if _int_list(entry.get("expected_exit_codes")) != _int_list(
            manifest_entry.get("expected_exit_codes")
        ):
            self.add(
                "command_contract_mismatch",
                "expected_exit_codes が manifest と一致しない",
                command_id,
            )

    def _check_claim(
        self,
        entry: Mapping[str, Any],
        command_id: str | None,
        *,
        observed_exit_code: int,
        strict: bool,
    ) -> None:
        claim = entry.get("claim")
        claim_type = entry.get("claim_type")
        observed_value = entry.get("observed_value")
        if not isinstance(claim, str) or not claim.strip():
            self.add("invalid_field", "claim が非空 string でない", command_id)
            claim = ""
        for name in forbidden_matches(claim):
            self.add("forbidden_content", f"claim に禁止 pattern がある: {name}", command_id)

        if claim_type == "free_text":
            if observed_value is not None:
                self.add(
                    "claim_value_mismatch",
                    "free_text claim の observed_value は null にする",
                    command_id,
                )
            return

        raw = self.raw_streams.get(command_id) if command_id else None
        if raw is not None:
            if raw.exit_code != observed_exit_code:
                self.add(
                    "observed_exit_code_mismatch",
                    f"observed_exit_code={observed_exit_code} が実測 exit {raw.exit_code} と異なる",
                    command_id,
                )
            self._verify_derivation(
                claim_type=str(claim_type),
                command_id=command_id,
                observed_value=observed_value,
                exit_code=raw.exit_code,
                stdout=raw.stdout,
                strict=strict,
            )
        elif claim_type in EXIT_DERIVED_CLAIM_TYPES:
            self._verify_derivation(
                claim_type=str(claim_type),
                command_id=command_id,
                observed_value=observed_value,
                exit_code=observed_exit_code,
                stdout=None,
                strict=strict,
            )
        elif claim_type in RAW_DERIVED_CLAIM_TYPES:
            self._verify_offline_raw_claim(
                entry,
                command_id=command_id,
                claim_type=str(claim_type),
                observed_value=observed_value,
                strict=strict,
            )

        if observed_value is None and not strict:
            self.add_note(f"{command_id}: expected 外 exit のため typed claim を導出していない")
            return

        # raw の有無に関わらず offline で検出できる構造的矛盾（Rule 6）。
        if claim_type == "count":
            size = entry.get("stdout_bytes")
            if isinstance(size, int) and size == 0:
                self.add(
                    "count_empty_stdout",
                    "stdout が空なのに件数 claim を持っている",
                    command_id,
                )
            if isinstance(observed_value, bool) or not isinstance(observed_value, int):
                self.add(
                    "claim_value_mismatch",
                    "count claim の observed_value が int でない",
                    command_id,
                )
        if claim_type == "sha" and not _is_hex64(observed_value):
            self.add(
                "claim_value_mismatch",
                "sha claim の observed_value が 64 桁 hex でない",
                command_id,
            )
        if claim_type == "pytest_run":
            size = entry.get("stdout_bytes")
            if isinstance(size, int) and size == 0:
                self.add(
                    "claim_value_mismatch",
                    "stdout が空なのに pytest_run claim を持っている",
                    command_id,
                )
            problem = pytest_run_value_violation(observed_value)
            if problem is not None:
                self.add("claim_value_mismatch", problem, command_id)
        if claim_type == "no_match" and not isinstance(observed_value, bool):
            self.add(
                "claim_value_mismatch",
                "no_match claim の observed_value が bool でない",
                command_id,
            )
        if claim_type == "exit_success" and (
            isinstance(observed_value, bool) or not isinstance(observed_value, int)
        ):
            self.add(
                "claim_value_mismatch",
                "exit_success claim の observed_value が int でない",
                command_id,
            )

        self._check_claim_text(
            claim=claim,
            claim_type=str(claim_type),
            observed_value=observed_value,
            observed_exit_code=observed_exit_code,
            command_id=command_id,
        )

    def _verify_derivation(
        self,
        *,
        claim_type: str,
        command_id: str | None,
        observed_value: object,
        exit_code: int,
        stdout: bytes | None,
        strict: bool,
    ) -> None:
        try:
            derived = derive_observed_value(claim_type, exit_code=exit_code, stdout=stdout)
        except DerivationError as exc:
            if strict:
                self.add("derivation_failed", f"typed 再導出に失敗した: {exc}", command_id)
            else:
                self.add_note(f"{command_id}: typed 再導出不能（{exc}）")
            return
        if observed_value is None and not strict:
            return
        if derived != observed_value or isinstance(derived, bool) != isinstance(
            observed_value, bool
        ):
            self.add(
                "claim_value_mismatch",
                f"observed_value={observed_value!r} が再導出値 {derived!r} と一致しない",
                command_id,
            )

    def _verify_offline_raw_claim(
        self,
        entry: Mapping[str, Any],
        *,
        command_id: str | None,
        claim_type: str,
        observed_value: object,
        strict: bool,
    ) -> None:
        """raw 削除後の offline 再導出を試みる（Rule 11）。"""
        normalized = self._normalized_input_for(command_id)
        if normalized is None:
            if entry.get("role") == "verification" and self.pass_claimed:
                self.add_inconclusive(f"offline_rederivation_unavailable:{command_id}")
            return
        ref, expected_sha = normalized
        data = self.normalized_inputs.get(ref)
        if data is None:
            if entry.get("role") == "verification" and self.pass_claimed:
                self.add_inconclusive(f"offline_rederivation_unavailable:{command_id}")
            return
        if sha256_bytes(data) != expected_sha:
            self.add(
                "normalized_input_invalid",
                "normalized input の raw_sha256 が実体と一致しない",
                command_id,
            )
            return
        observed_exit = entry.get("observed_exit_code")
        exit_code = observed_exit if isinstance(observed_exit, int) else 0
        self._verify_derivation(
            claim_type=claim_type,
            command_id=command_id,
            observed_value=observed_value,
            exit_code=exit_code,
            stdout=data,
            strict=strict,
        )

    def _normalized_input_for(self, command_id: str | None) -> tuple[str, str] | None:
        attestation = self.artifact.get("validation_attestation")
        if command_id is None or not isinstance(attestation, Mapping):
            return None
        entries = attestation.get("normalized_inputs")
        if not isinstance(entries, list):
            return None
        for item in entries:
            if not isinstance(item, Mapping) or item.get("command_id") != command_id:
                continue
            ref = item.get("ref")
            digest = item.get("raw_sha256")
            if isinstance(ref, str) and _is_hex64(digest):
                return ref, str(digest)
        return None

    def _check_claim_text(
        self,
        *,
        claim: str,
        claim_type: str,
        observed_value: object,
        observed_exit_code: int,
        command_id: str | None,
    ) -> None:
        if claim_type == "count" and isinstance(observed_value, int):
            claimed = _claimed_counts(claim)
            if claimed is None:
                numbers = {int(token) for token in _CLAIM_INT_RE.findall(claim)}
                if not numbers:
                    self.add("claim_text_mismatch", "count claim に件数の記載がない", command_id)
                elif observed_value not in numbers:
                    self.add(
                        "claim_text_mismatch",
                        f"claim 本文に observed_value={observed_value} が現れない",
                        command_id,
                    )
            elif claimed != {observed_value}:
                self.add(
                    "claim_text_mismatch",
                    f"claim 本文の件数 {sorted(claimed)} が observed={observed_value} と異なる",
                    command_id,
                )
        elif claim_type == "sha" and isinstance(observed_value, str):
            if observed_value not in claim:
                self.add("claim_text_mismatch", "claim 本文に observed SHA が現れない", command_id)
        elif claim_type == "no_match" and isinstance(observed_value, bool):
            tokens = {token.lower() for token in _CLAIM_NO_MATCH_RE.findall(claim)}
            expected_token = "true" if observed_value else "false"
            if tokens and tokens != {expected_token}:
                self.add(
                    "claim_text_mismatch",
                    f"claim 本文の no_match token が observed_value={observed_value} と異なる",
                    command_id,
                )
        elif claim_type == "exit_success":
            tokens = {int(token) for token in _CLAIM_EXIT_RE.findall(claim)}
            if tokens and tokens != {observed_exit_code}:
                self.add(
                    "claim_text_mismatch",
                    f"claim 本文の exit token が observed_exit_code={observed_exit_code} と異なる",
                    command_id,
                )
        elif claim_type == "pytest_run" and isinstance(observed_value, Mapping):
            for key, number in _CLAIM_PYTEST_TOKEN_RE.findall(claim):
                expected = observed_value.get(key.lower())
                if isinstance(expected, int) and int(number) != expected:
                    self.add(
                        "claim_text_mismatch",
                        f"claim 本文の {key.lower()}={number} が observed {expected} と異なる",
                        command_id,
                    )

    def check_attestation(self) -> None:
        """capture-time validator の attestation を検査する（Rule 11）。"""
        attestation = self.artifact.get("validation_attestation")
        if attestation is None:
            if self.require_attestation:
                self.add("attestation_missing", "validation_attestation がない")
            return
        if not isinstance(attestation, Mapping):
            self.add("attestation_missing", "validation_attestation が object でない")
            return

        unknown = set(attestation) - _ATTESTATION_FIELDS
        if unknown:
            self.add("invalid_field", f"未知の attestation field: {sorted(unknown)}")
        missing = _ATTESTATION_FIELDS - set(attestation)
        if missing:
            self.add("attestation_missing", f"attestation field が欠けている: {sorted(missing)}")
            return

        source_sha = attestation.get("validator_source_sha256")
        if not _is_hex64(source_sha):
            self.add("attestation_missing", "validator_source_sha256 が 64 桁 hex でない")
        elif (
            self.trusted_validator_sources is not None
            and source_sha not in self.trusted_validator_sources
        ):
            self.add(
                "unknown_validator_source",
                "validator_source_sha256 が信頼済み validator source 集合にない",
            )
        if not isinstance(attestation.get("validator_source_git_oid"), str):
            self.add("attestation_missing", "validator_source_git_oid がない")
        if not _is_rfc3339(attestation.get("validated_at")):
            self.add("invalid_field", "validated_at が RFC3339 でない")

        if attestation.get("command_manifest_sha256") != self.artifact.get(
            "command_manifest_sha256"
        ):
            self.add(
                "attestation_manifest_mismatch",
                "attestation の command_manifest_sha256 が artifact と一致しない",
            )

        body_hash = canonical_hash_with_null_fields(
            self.artifact, "validation_attestation", "artifact_sha256"
        )
        if attestation.get("artifact_body_sha256") != body_hash:
            self.add("artifact_body_hash_mismatch", "artifact_body_sha256 が再計算値と一致しない")

        attestation_hash = canonical_hash_with_null_fields(attestation, "attestation_sha256")
        if attestation.get("attestation_sha256") != attestation_hash:
            self.add("attestation_hash_mismatch", "attestation_sha256 が再計算値と一致しない")

        verified_commands = _str_list(attestation.get("verified_command_ids"))
        if verified_commands is None or set(verified_commands) != set(self.command_ids):
            self.add(
                "attestation_id_set_mismatch",
                "verified_command_ids が artifact の command 集合と exact 一致しない",
            )
        verified_claims = _str_list(attestation.get("verified_claim_ids"))
        if verified_claims is None or set(verified_claims) != set(self.verification_ids):
            self.add(
                "attestation_id_set_mismatch",
                "verified_claim_ids が verification 集合と exact 一致しない",
            )

        attestation_result = attestation.get("result")
        if attestation_result not in ARTIFACT_RESULTS:
            self.add("invalid_field", "attestation.result が pass/fail/inconclusive でない")
        elif self.pass_claimed and attestation_result != "pass":
            self.add(
                "attestation_result_mismatch",
                f"result=pass の artifact に attestation result={attestation_result} が付いている",
            )

        entries = attestation.get("normalized_inputs")
        if not isinstance(entries, list):
            self.add("invalid_field", "normalized_inputs が list でない")
            return
        for item in entries:
            if not isinstance(item, Mapping):
                self.add("normalized_input_invalid", "normalized input entry が object でない")
                continue
            if set(item) != _NORMALIZED_INPUT_FIELDS:
                self.add("normalized_input_invalid", "normalized input の field 集合が不正")
                continue
            command_id = item.get("command_id")
            if not isinstance(command_id, str) or command_id not in self.command_ids:
                self.add(
                    "normalized_input_invalid", "normalized input が未知の command を指している"
                )
                continue
            if not _is_hex64(item.get("raw_sha256")):
                self.add(
                    "normalized_input_invalid", "normalized input の raw_sha256 が不正", command_id
                )
            ref = item.get("ref")
            if not isinstance(ref, str) or not ref:
                self.add("normalized_input_invalid", "normalized input の ref が不正", command_id)
            parser_id = item.get("parser_id")
            entry = self._command_entry(command_id)
            if entry is not None and parser_id != entry.get("derivation"):
                self.add(
                    "normalized_input_invalid",
                    "normalized input の parser_id が command の derivation と一致しない",
                    command_id,
                )

    def _command_entry(self, command_id: str) -> Mapping[str, Any] | None:
        commands = self.artifact.get("commands")
        if not isinstance(commands, list):
            return None
        for entry in commands:
            if isinstance(entry, Mapping) and entry.get("command_id") == command_id:
                return entry
        return None

    # -- 実行 --------------------------------------------------------------

    def run(self) -> ValidationResult:
        """全規則を評価して判定を返す。"""
        if not self.check_schema():
            return self._finalize()
        self.check_top_level()
        self.check_kind_field_boundary()
        self.check_research_fields()
        index = self.check_manifest_binding()
        self.check_command_id_sets(index)
        self.check_commands(index)
        self.check_attestation()
        return self._finalize()

    def _finalize(self) -> ValidationResult:
        violations = tuple(self.state.violations)
        inconclusive = tuple(self.state.inconclusive)
        notes = tuple(self.state.notes)
        if violations:
            return ValidationResult(
                result="fail",
                contract_valid=False,
                violations=violations,
                inconclusive_reasons=inconclusive,
                verified_command_ids=tuple(self.command_ids),
                verified_claim_ids=tuple(self.verification_ids),
                notes=notes,
            )
        if inconclusive:
            return ValidationResult(
                result="inconclusive",
                contract_valid=True,
                inconclusive_reasons=inconclusive,
                verified_command_ids=tuple(self.command_ids),
                verified_claim_ids=tuple(self.verification_ids),
                notes=notes,
            )
        claimed = self.claimed_result if self.claimed_result in ARTIFACT_RESULTS else "fail"
        result: Literal["pass", "fail", "inconclusive"]
        if claimed == "pass":
            result = "pass"
        elif claimed == "inconclusive":
            result = "inconclusive"
        else:
            result = "fail"
        return ValidationResult(
            result=result,
            contract_valid=True,
            verified_command_ids=tuple(self.command_ids),
            verified_claim_ids=tuple(self.verification_ids),
            notes=notes,
        )


def validate_evidence_contract(
    artifact: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any] | None,
    manifest_raw_bytes: bytes | None = None,
    raw_streams: Mapping[str, CapturedStream] | None = None,
    normalized_inputs: Mapping[str, bytes] | None = None,
    trusted_validator_sources: frozenset[str] | Sequence[str] | None = None,
    trusted_producer_sources: frozenset[str] | Sequence[str] | None = None,
    require_attestation: bool = True,
    purpose: Literal["gate", "historical_read"] = "gate",
) -> ValidationResult:
    """Evidence Schema v2 artifact を Truthfulness Rules 1〜12 で検査する。

    本関数が review / release / audit / research result の共通 validator core である。
    adapter は artifact kind 固有 field を artifact へ足したうえで本関数を呼ぶこと。
    規則を adapter 側へコピー実装してはならない（docs/specs/N-602 §Common Validator
    Boundary）。

    Args:
        artifact: 検査対象の Evidence Schema v2 artifact。
        manifest: 実行前に固定した command manifest。``None`` は binding 検証不能を意味し、
            fail-close で ``manifest_missing`` violation になる。
        manifest_raw_bytes: manifest 参照先の raw byte 列。渡された場合は
            ``command_manifest_sha256`` の raw byte 一致と canonical 出力一致を検査する。
        raw_streams: capture 時の raw stream（command_id → CapturedStream）。渡された場合は
            hash、byte 数、exit code、typed value を実測から再導出して照合する。
        normalized_inputs: offline 再導出に使う content-addressed normalized input
            （attestation の ``ref`` → raw bytes）。
        trusted_validator_sources: 信頼する validator source SHA-256 集合。
        trusted_producer_sources: 信頼する generator source SHA-256 集合。
        require_attestation: ``validation_attestation`` を必須にするか。capture 時の
            attestation 生成前だけ ``False`` にする。
        purpose: ``gate`` は v2 のみ許可。``historical_read`` は v1 を読み取り専用で受ける。

    Returns:
        ValidationResult: ``ok`` が真のときだけ gate pass の根拠に使える。
    """
    validator = _EvidenceValidator(
        artifact,
        manifest=manifest,
        manifest_raw_bytes=manifest_raw_bytes,
        raw_streams=raw_streams,
        normalized_inputs=normalized_inputs,
        trusted_validator_sources=(
            frozenset(trusted_validator_sources) if trusted_validator_sources is not None else None
        ),
        trusted_producer_sources=(
            frozenset(trusted_producer_sources) if trusted_producer_sources is not None else None
        ),
        require_attestation=require_attestation,
        purpose=purpose,
    )
    return validator.run()
