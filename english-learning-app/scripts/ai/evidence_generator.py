#!/usr/bin/env python3
"""実行捕捉から Evidence Schema v2 artifact を生成する最小 generator（N-602A）。

責務は docs/specs/N-602-evidence-truthfulness-generation.md の Operation Flow 1〜5 である。

1. 実行前に command manifest を canonical 固定して hash する。
2. executable preflight を行い、未解決なら command を実行しない。相対 executable は command の
   実行 cwd 基準で絶対化し、解決済み絶対 path を実行し、実行後に再 hash して差し替えを
   fail-close する（executable binding・外部再監査 2026-08-15 P0-04）。
3. bounded timeout・sanitized environment・既定 argv（shell なし）で実行し raw stream を捕捉する。
4. raw stream から typed value と hash を生成し、excerpt を redact / limit する。
5. capture-time validator（``scripts/ai/evidence_contract.py``）で再導出して attestation を作る。

真偽規則そのものは本 module に持たない。判定は必ず ``evidence_contract`` を経由する
（Common Validator Boundary・規則のコピー実装禁止）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ai.evidence_contract import (  # noqa: E402
    CLAIM_TYPE_PARSERS,
    CLAIM_TYPES,
    COMMAND_ROLES,
    EXCERPT_ELISION_TEMPLATE,
    EXCERPT_HEAD_BYTES,
    EXCERPT_MAX_BYTES_PER_STREAM,
    EXCERPT_TAIL_BYTES,
    EXECUTION_MODES,
    FORBIDDEN_EXIT_CODES,
    GENERATOR_MARKER,
    KNOWN_ARTIFACT_KINDS,
    MANIFEST_SCHEMA_VERSION,
    RAW_DERIVED_CLAIM_TYPES,
    SCHEMA_VERSION_V2,
    CapturedStream,
    ContractViolation,
    DerivationError,
    ValidationResult,
    canonical_command_sha256,
    canonical_content_bytes,
    canonical_content_sha256,
    canonical_hash_with_null_fields,
    contract_source_git_oid,
    contract_source_sha256,
    derive_observed_value,
    forbidden_matches,
    git_blob_oid,
    sha256_bytes,
    shell_contract_violations,
    validate_command_manifest,
    validate_evidence_contract,
)

DEFAULT_REPOSITORY: Final = "KosGit-Dev/english-learning-app"
DEFAULT_SHELL: Final = "bash"
DEFAULT_TIMEOUT_MS: Final = 60_000
DEFAULT_MANIFEST_REF_PREFIX: Final = "docs/ai/evidence-manifests"
DEFAULT_NORMALIZED_INPUT_REF_PREFIX: Final = "docs/ai/evidence-normalized-inputs"
GENERATOR_SOURCE_PATH: Final[Path] = Path(__file__).resolve()

# raw stream が巨大な場合に decode/redaction する窓（先頭・末尾それぞれ）。
_DECODE_WINDOW_BYTES: Final = 262_144

# sanitized environment の既定値。呼び出し側の環境変数はここに列挙した分しか渡らない。
_BASE_ENVIRONMENT: Final[Mapping[str, str]] = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
}
_DEFAULT_PATH: Final = "/usr/local/bin:/usr/bin:/bin"
_CREDENTIAL_ENV_TOKENS: Final[tuple[str, ...]] = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "APIKEY",
    "API_KEY",
    "CREDENTIAL",
    "PRIVATE_KEY",
)

# excerpt へ書く前に適用する secret / PII redaction 規則（順序に意味がある）。
# module 属性として公開し、redaction を無効化したときに forbidden scan が fail-close することを
# 試験できるようにしている。
REDACTION_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "[REDACTED:private-key]",
    ),
    ("github_token", re.compile(r"\bgh[opsur]_[A-Za-z0-9_]{20,}"), "[REDACTED:token]"),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:token]"),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:token]"),
    (
        "provider_api_key",
        re.compile(r"\bsk-(?:proj-|ant-api03-)?[A-Za-z0-9_-]{20,}"),
        "[REDACTED:token]",
    ),
    (
        "authorization_header",
        re.compile(r"\b(authorization)[ \t]*:[ \t]*\S+", re.IGNORECASE),
        r"\1: [REDACTED:authorization]",
    ),
    (
        "bearer_token",
        re.compile(r"\bBearer[ \t]+[A-Za-z0-9._~+/-]{8,}=*", re.IGNORECASE),
        "[REDACTED:bearer]",
    ),
    (
        "cookie_header",
        re.compile(r"\b(set-cookie|cookie)[ \t]*:[ \t]*.*", re.IGNORECASE),
        r"\1: [REDACTED:cookie]",
    ),
    (
        "credential_assignment",
        re.compile(
            r"\b([A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIALS?))"
            r"[ \t]*=[ \t]*(?:\"[^\"]*\"|'[^']*'|\S+)",
            re.IGNORECASE,
        ),
        r"\1=[REDACTED:env]",
    ),
    (
        "connection_string",
        re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]+:[^\s/@]+@\S+"),
        "[REDACTED:connection-string]",
    ),
    (
        "email_address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED:email]",
    ),
)


class EvidenceGenerationError(RuntimeError):
    """artifact を生成できない（= pass にできない）実行事象。"""

    def __init__(self, code: str, message: str, *, command_id: str | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.command_id = command_id


class ForbiddenContentError(EvidenceGenerationError):
    """redaction 後も禁止 pattern が残ったため artifact を書けない。"""

    def __init__(self, message: str, *, command_id: str | None = None) -> None:
        super().__init__("forbidden_content", message, command_id=command_id)


# ---------------------------------------------------------------------------
# command 定義と manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    """実行前に固定する 1 command の契約。"""

    command_id: str
    role: str
    claim_type: str
    argv: tuple[str, ...] = ()
    script: str = ""
    execution_mode: str = "argv"
    expected_exit_codes: tuple[int, ...] = (0,)
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    executable: str | None = None
    claim_template: str | None = None
    free_text_claim: str | None = None

    @property
    def display_command(self) -> str:
        """artifact へ保存する表示用文字列を返す（argv 境界の復元には使わない）。"""
        if self.execution_mode == "argv":
            return shlex.join(self.argv)
        return self.script

    @property
    def derivation(self) -> str:
        """claim type に対応する typed parser ID を返す。"""
        return CLAIM_TYPE_PARSERS[self.claim_type]

    @property
    def preflight_executable(self) -> str:
        """preflight で解決する executable 名を返す。

        この名前を解決した絶対 path が hash され、実行時の argv[0]（shell では bash）にも
        置換される。``executable`` を明示した場合は argv[0] ではなくその executable が実行体になる。
        """
        if self.executable:
            return self.executable
        if self.execution_mode == "argv":
            return self.argv[0]
        return DEFAULT_SHELL


@dataclass(frozen=True)
class CommandManifest:
    """実行前に canonical 固定した command manifest。"""

    data: dict[str, Any]
    raw_bytes: bytes
    sha256: str
    ref: str
    specs: tuple[CommandSpec, ...]

    def write(self, directory: Path) -> Path:
        """content-addressed path へ manifest の raw bytes を書き出す。"""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.sha256}.json"
        path.write_bytes(self.raw_bytes)
        return path


def _validate_spec(spec: CommandSpec) -> None:
    """CommandSpec の静的契約を実行前に検査する。"""
    if not spec.command_id:
        raise EvidenceGenerationError("manifest_invalid", "command_id が空")
    if spec.role not in COMMAND_ROLES:
        raise EvidenceGenerationError(
            "manifest_invalid", f"未知の role: {spec.role}", command_id=spec.command_id
        )
    if spec.claim_type not in CLAIM_TYPES:
        raise EvidenceGenerationError(
            "manifest_invalid", f"未知の claim_type: {spec.claim_type}", command_id=spec.command_id
        )
    if spec.execution_mode not in EXECUTION_MODES:
        raise EvidenceGenerationError(
            "manifest_invalid",
            f"未知の execution_mode: {spec.execution_mode}",
            command_id=spec.command_id,
        )
    if spec.claim_type == "free_text" and spec.role == "verification":
        raise EvidenceGenerationError(
            "free_text_verification",
            "free_text claim は verification に使えない",
            command_id=spec.command_id,
        )
    if spec.execution_mode == "argv" and not spec.argv:
        raise EvidenceGenerationError("manifest_invalid", "argv が空", command_id=spec.command_id)
    if spec.execution_mode == "shell" and not spec.script:
        raise EvidenceGenerationError("manifest_invalid", "script が空", command_id=spec.command_id)
    if not spec.expected_exit_codes:
        raise EvidenceGenerationError(
            "manifest_invalid", "expected_exit_codes が空", command_id=spec.command_id
        )
    forbidden = sorted(set(spec.expected_exit_codes) & FORBIDDEN_EXIT_CODES)
    if forbidden:
        raise EvidenceGenerationError(
            "expected_exit_code_forbidden",
            f"exit {forbidden} は expected list に書けない",
            command_id=spec.command_id,
        )
    if spec.timeout_ms <= 0:
        raise EvidenceGenerationError(
            "manifest_invalid", "timeout_ms は正の整数", command_id=spec.command_id
        )
    if spec.execution_mode == "shell":
        masked = shell_contract_violations(spec.script, role=spec.role)
        if masked:
            raise EvidenceGenerationError(
                "shell_masking_rejected",
                f"失敗を隠す shell 構文があるため実行前に拒否する: {', '.join(masked)}",
                command_id=spec.command_id,
            )


def _manifest_entry(spec: CommandSpec) -> dict[str, Any]:
    """manifest の 1 command entry を作る。"""
    entry: dict[str, Any] = {
        "command_id": spec.command_id,
        "role": spec.role,
        "command": spec.display_command,
        "execution_mode": spec.execution_mode,
        "expected_exit_codes": list(spec.expected_exit_codes),
        "claim_type": spec.claim_type,
        "derivation": spec.derivation,
        "timeout_ms": spec.timeout_ms,
        "executable": spec.preflight_executable,
    }
    if spec.execution_mode == "argv":
        entry["argv"] = list(spec.argv)
        entry["canonical_command_sha256"] = canonical_command_sha256("argv", argv=spec.argv)
    else:
        entry["script"] = spec.script
        entry["canonical_command_sha256"] = canonical_command_sha256("shell", script=spec.script)
    return entry


def build_command_manifest(
    specs: Sequence[CommandSpec],
    *,
    manifest_id: str,
    ref: str | None = None,
) -> CommandManifest:
    """実行前 command manifest を canonical 固定して返す（Operation Flow 1）。"""
    if not specs:
        raise EvidenceGenerationError("manifest_invalid", "command が 1 件もない")
    seen: set[str] = set()
    for spec in specs:
        _validate_spec(spec)
        if spec.command_id in seen:
            raise EvidenceGenerationError(
                "duplicate_command_id", "command_id が重複している", command_id=spec.command_id
            )
        seen.add(spec.command_id)

    verification_ids = sorted(spec.command_id for spec in specs if spec.role == "verification")
    if not verification_ids:
        raise EvidenceGenerationError(
            "empty_verification_set", "verification command が 1 件もない"
        )

    data: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_id": manifest_id,
        "commands": [_manifest_entry(spec) for spec in specs],
        "required_command_ids": sorted(spec.command_id for spec in specs),
        "required_verification_ids": verification_ids,
    }
    raw_bytes = canonical_content_bytes(data)
    digest = sha256_bytes(raw_bytes)
    violations = validate_command_manifest(data, raw_bytes=raw_bytes)
    if violations:
        raise EvidenceGenerationError("manifest_invalid", _render(violations))
    return CommandManifest(
        data=data,
        raw_bytes=raw_bytes,
        sha256=digest,
        ref=ref or f"{DEFAULT_MANIFEST_REF_PREFIX}/{digest}.json",
        specs=tuple(specs),
    )


def _render(violations: Sequence[ContractViolation]) -> str:
    """violation 群を 1 行文字列へ畳む。"""
    return " / ".join(violation.render() for violation in violations)


# ---------------------------------------------------------------------------
# redaction と excerpt
# ---------------------------------------------------------------------------


def redact_text(text: str) -> str:
    """secret / PII pattern を除去した文字列を返す。"""
    redacted = text
    for _name, pattern, replacement in REDACTION_RULES:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _truncate_head(text: str, limit_bytes: int) -> str:
    """先頭から UTF-8 で ``limit_bytes`` 以内に収まる部分文字列を返す。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text
    return encoded[:limit_bytes].decode("utf-8", errors="ignore")


def _truncate_tail(text: str, limit_bytes: int) -> str:
    """末尾から UTF-8 で ``limit_bytes`` 以内に収まる部分文字列を返す。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text
    return encoded[-limit_bytes:].decode("utf-8", errors="ignore")


def build_excerpt(raw: bytes, *, command_id: str | None = None) -> tuple[str, bool]:
    """raw stream から redaction 済み excerpt と truncation flag を作る。

    UTF-8 replacement decode → redaction → byte limit の順に適用する。redaction を
    先に全体へ適用することで、head/tail 分割で secret が分断されて検出を逃れることを防ぐ。
    """
    if len(raw) > 2 * _DECODE_WINDOW_BYTES:
        head_text = redact_text(raw[:_DECODE_WINDOW_BYTES].decode("utf-8", errors="replace"))
        tail_text = redact_text(raw[-_DECODE_WINDOW_BYTES:].decode("utf-8", errors="replace"))
        # head と tail は raw 上で連続しない別々の区間なので、区切りなく直接連結すると
        # 境界で禁止パターン（例: ghp_ トークン）が偶発的に生成されうる（raw には実在しない）。
        # 改行を挟んで境界跨ぎのパターン生成を防ぐ（Round 2 是正）。
        text = head_text + "\n" + tail_text
        oversized = True
    else:
        text = redact_text(raw.decode("utf-8", errors="replace"))
        oversized = False

    encoded_len = len(text.encode("utf-8"))
    if not oversized and encoded_len <= EXCERPT_MAX_BYTES_PER_STREAM:
        excerpt = text
        truncated = False
    else:
        head = _truncate_head(text, EXCERPT_HEAD_BYTES)
        tail = _truncate_tail(text, EXCERPT_TAIL_BYTES)
        omitted = max(len(raw) - len(head.encode("utf-8")) - len(tail.encode("utf-8")), 0)
        excerpt = head + EXCERPT_ELISION_TEMPLATE.format(omitted=omitted) + tail
        truncated = True

    found = forbidden_matches(excerpt)
    if found:
        raise ForbiddenContentError(
            f"redaction 後も禁止 pattern が残っている: {', '.join(found)}", command_id=command_id
        )
    return excerpt, truncated


# ---------------------------------------------------------------------------
# preflight と runner
# ---------------------------------------------------------------------------


def sanitized_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """command 実行に渡す最小 environment を作る。"""
    env: dict[str, str] = dict(_BASE_ENVIRONMENT)
    env["PATH"] = os.environ.get("PATH", _DEFAULT_PATH)
    for key, value in (extra or {}).items():
        upper = key.upper()
        if any(token in upper for token in _CREDENTIAL_ENV_TOKENS):
            raise EvidenceGenerationError(
                "credential_environment", f"認証情報らしい環境変数は渡せない: {key}"
            )
        env[key] = value
    return env


_STREAMING_HASH_CHUNK_BYTES: Final = 1_048_576


def _sha256_file_streaming(path: Path) -> str:
    """file を全量 read せず streaming で SHA-256 を計算する。

    ``preflight_executable`` が解決する executable はサイズが読めるまで不定であり、
    ``sha256_bytes(path.read_bytes())`` のように全量をメモリへ読み込むと巨大 binary で
    資源を浪費しうる（Backlog #1310）。
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_STREAMING_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ExecutablePreflight:
    """preflight が解決した executable の内部記録（executable binding）。

    artifact へ出すのは ``record()`` が返す closed field
    （``executable`` / ``resolved_path_sha256`` / ``available``）だけである。解決済み絶対 path・
    realpath・実行後再 hash は artifact 非出力の内部値として保持し、artifact への emit は
    後続 PR（B2）が contract 側の受理と揃えて行う。
    """

    executable: str
    """spec が指定した executable 名（表示用。artifact の ``preflight.executable``）。"""

    resolved_path: str
    """実行 cwd 基準で絶対化した path。実際に argv[0]（shell では bash）へ渡す値。"""

    realpath: str
    """``resolved_path`` の symlink 解決後の実体 path。"""

    sha256: str
    """preflight 時点で ``resolved_path`` を開いて得た raw byte SHA-256。

    artifact の ``resolved_path_sha256`` に対応する。
    """

    post_execution_sha256: str | None = None
    """実行完了後に同じ path を再 hash した値。preflight と一致しなければ fail-close する。"""

    def record(self) -> dict[str, Any]:
        """artifact の ``preflight`` object（closed field のみ）を返す。"""
        return {
            "executable": self.executable,
            "resolved_path_sha256": self.sha256,
            "available": True,
        }


def _resolution_base(cwd: Path | None) -> Path:
    """相対 path を絶対化する基準（= command の実行 cwd）を絶対 path で返す。

    ``cwd`` 自体が相対なら ``subprocess`` と同じく generator process の cwd を起点に解決する。
    """
    if cwd is None:
        return Path.cwd()
    return Path.cwd() / cwd


def _resolve_bare_executable(name: str, env: Mapping[str, str]) -> str | None:
    """PATH の絶対要素だけを検索して bare executable 名を絶対 path へ解決する。

    PATH の相対要素（``.`` や空要素）は「どの cwd で解釈するか」で指す実体が変わり、generator
    process cwd と実行 cwd が異なる本 generator では preflight hash と実行体が食い違う温床になる。
    fail-close 側を選び、相対要素は preflight の検索対象から外す（相対要素経由でしか見つからない
    executable は ``preflight_missing_executable`` になる）。解決結果は絶対 path であり、実行時は
    その絶対 path を argv[0] に渡すので child 側で PATH が再解釈されることはない。
    """
    absolute_entries = [
        entry for entry in env.get("PATH", "").split(os.pathsep) if os.path.isabs(entry)
    ]
    if not absolute_entries:
        return None
    return shutil.which(name, path=os.pathsep.join(absolute_entries))


def preflight_executable(
    spec: CommandSpec,
    env: Mapping[str, str],
    *,
    cwd: Path | None = None,
) -> ExecutablePreflight:
    """command 実行前に executable を解決し、実行体と束縛した記録を返す（Rule 8）。

    - path 形式（``os.sep`` を含む）の相対 executable は **実行 cwd**（``cwd``。未指定なら
      generator process の cwd）基準で絶対化する。generator process cwd 基準で解決すると、
      ``subprocess.run(cwd=...)`` が OS に再解決させる実行体と別 file を hash しうる
      （外部再監査 2026-08-15 P0-04）。
    - bare 名は PATH の絶対要素だけから解決する（``_resolve_bare_executable``）。
    - 解決した絶対 path・realpath・raw SHA-256 を返す。呼び出し側はこの絶対 path を argv[0] に
      渡し、実行後に再 hash して差し替えを検出する（``execute_command``）。

    未解決なら command を実行せず、exit 127 相当の失敗として例外にする。
    """
    name = spec.preflight_executable
    resolved: str | None
    if os.sep in name:
        candidate = Path(name) if os.path.isabs(name) else _resolution_base(cwd) / name
        resolved = str(candidate) if candidate.is_file() else None
    else:
        resolved = _resolve_bare_executable(name, env)
    if resolved is None:
        raise EvidenceGenerationError(
            "preflight_missing_executable",
            f"executable を解決できないため実行しない（exit 127 相当）: {name}",
            command_id=spec.command_id,
        )
    # hash は実行時と同じく resolved_path 経由で開く（symlink はその時点の実体へ追従する）。
    return ExecutablePreflight(
        executable=name,
        resolved_path=resolved,
        realpath=os.path.realpath(resolved),
        sha256=_sha256_file_streaming(Path(resolved)),
    )


def verify_executable_unchanged(
    spec: CommandSpec, preflight: ExecutablePreflight
) -> ExecutablePreflight:
    """実行完了後に同じ絶対 path を再解決・再 hash し、preflight と一致する記録を返す。

    TOCTOU 緩和。hash 不一致・realpath 不一致（symlink の差し替え）・再 hash 不能（削除など）は
    ``executable_replaced`` で fail-close する。実行体が preflight で hash した file と同一である
    保証がない artifact は pass にできない。
    """
    try:
        post_realpath = os.path.realpath(preflight.resolved_path)
        post_sha = _sha256_file_streaming(Path(preflight.resolved_path))
    except OSError as exc:
        raise EvidenceGenerationError(
            "executable_replaced",
            f"実行後に executable を再 hash できない: {preflight.resolved_path} ({exc})",
            command_id=spec.command_id,
        ) from exc
    if post_sha != preflight.sha256 or post_realpath != preflight.realpath:
        raise EvidenceGenerationError(
            "executable_replaced",
            "実行後の executable が preflight と一致しない（hash または realpath の差し替え）ため "
            f"pass にできない: {preflight.resolved_path}",
            command_id=spec.command_id,
        )
    return replace(preflight, post_execution_sha256=post_sha)


@dataclass(frozen=True)
class CommandExecution:
    """1 command の実行結果。"""

    spec: CommandSpec
    stream: CapturedStream
    started_at: str
    duration_ms: int
    preflight: dict[str, Any]
    """artifact へ出す preflight object（closed field のみ）。"""

    preflight_internal: ExecutablePreflight
    """解決済み絶対 path・realpath・実行後 hash を含む内部記録（artifact 非出力）。"""


def _resolved_argv(spec: CommandSpec, resolved_executable: str) -> list[str]:
    """実際に起動する argv を返す（argv[0] は preflight の解決済み絶対 path に置換する）。

    argv mode は argv[0] だけを解決済み path に置き換え、shell mode は解決済み bash に
    pipefail を強制して script を渡す。OS に argv[0] を再解決させないことで、preflight で
    hash した file と実行体を一致させる。
    """
    if spec.execution_mode == "argv":
        return [resolved_executable, *spec.argv[1:]]
    return [resolved_executable, "-o", "pipefail", "-c", spec.script]


def run_command(
    spec: CommandSpec,
    *,
    env: Mapping[str, str],
    preflight: ExecutablePreflight,
    cwd: Path | None = None,
) -> CapturedStream:
    """bounded timeout・sanitized environment で command を実行し raw stream を捕捉する。

    ``preflight`` が解決した絶対 path を argv[0] に渡す（``_resolved_argv``）。
    raw stream は 0600 の一時領域へ書き、読み出し後に必ず削除する（git へは保存しない）。
    """
    with tempfile.TemporaryDirectory(prefix="evidence-raw-") as tmpdir:
        os.chmod(tmpdir, 0o700)
        stdout_path = Path(tmpdir) / "stdout.bin"
        stderr_path = Path(tmpdir) / "stderr.bin"
        stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            completed = subprocess.run(
                _resolved_argv(spec, preflight.resolved_path),
                stdout=stdout_fd,
                stderr=stderr_fd,
                cwd=str(cwd) if cwd else None,
                env=dict(env),
                timeout=spec.timeout_ms / 1000,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise EvidenceGenerationError(
                "timeout",
                f"bounded timeout {spec.timeout_ms}ms を超えたため pass にできない",
                command_id=spec.command_id,
            ) from exc
        except OSError as exc:
            raise EvidenceGenerationError(
                "stream_capture_failed",
                f"command を実行できない: {exc}",
                command_id=spec.command_id,
            ) from exc
        finally:
            os.close(stdout_fd)
            os.close(stderr_fd)
        stdout = stdout_path.read_bytes()
        stderr = stderr_path.read_bytes()
    if completed.returncode < 0:
        raise EvidenceGenerationError(
            "signal_termination",
            f"signal {-completed.returncode} で終了したため pass にできない",
            command_id=spec.command_id,
        )
    return CapturedStream(stdout=stdout, stderr=stderr, exit_code=completed.returncode)


def execute_command(
    spec: CommandSpec,
    *,
    env: Mapping[str, str],
    cwd: Path | None = None,
) -> CommandExecution:
    """preflight → 実行 → 実行後再 hash → timing 記録までを行う。

    preflight は command と同じ ``cwd`` 基準で executable を解決し（executable binding）、
    実行後に ``verify_executable_unchanged`` で差し替えを検出する。
    """
    preflight = preflight_executable(spec, env, cwd=cwd)
    started_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    started = time.monotonic()
    stream = run_command(spec, env=env, preflight=preflight, cwd=cwd)
    duration_ms = max(int((time.monotonic() - started) * 1000), 0)
    bound = verify_executable_unchanged(spec, preflight)
    return CommandExecution(
        spec=spec,
        stream=stream,
        started_at=started_at,
        duration_ms=duration_ms,
        preflight=bound.record(),
        preflight_internal=bound,
    )


# ---------------------------------------------------------------------------
# artifact 生成
# ---------------------------------------------------------------------------


def _render_claim(spec: CommandSpec, observed_value: object, exit_code: int) -> str:
    """typed observed value から claim 本文を生成する（自然言語からは逆に読まない）。"""
    if spec.claim_type == "free_text":
        return spec.free_text_claim or f"{spec.command_id}: 診断コマンドを実行した"
    if observed_value is None:
        return (
            f"{spec.command_id}: expected exit {list(spec.expected_exit_codes)} に対し "
            f"実測 exit {exit_code} のため typed claim を導出できない"
        )
    if spec.claim_template:
        return spec.claim_template.format(value=observed_value, exit_code=exit_code)
    if spec.claim_type == "count":
        return f"{spec.command_id}: count={observed_value} 件"
    if spec.claim_type == "sha":
        return f"{spec.command_id}: sha256={observed_value}"
    if spec.claim_type == "no_match":
        return f"{spec.command_id}: no_match={'true' if observed_value else 'false'}"
    return f"{spec.command_id}: exit={observed_value}"


def _command_entry(execution: CommandExecution) -> tuple[dict[str, Any], bool]:
    """command entry と「expected 契約を満たしたか」を返す。"""
    spec = execution.spec
    stream = execution.stream
    exit_ok = stream.exit_code in spec.expected_exit_codes

    stdout_excerpt, stdout_truncated = build_excerpt(stream.stdout, command_id=spec.command_id)
    stderr_excerpt, stderr_truncated = build_excerpt(stream.stderr, command_id=spec.command_id)

    observed_value: object | None
    if spec.claim_type == "free_text":
        observed_value = None
    else:
        try:
            observed_value = derive_observed_value(
                spec.claim_type, exit_code=stream.exit_code, stdout=stream.stdout
            )
        except DerivationError as exc:
            if exit_ok:
                # 契約どおりに終了したのに typed 値を導出できない = 証跡として使えない。
                raise EvidenceGenerationError(
                    "derivation_failed", str(exc), command_id=spec.command_id
                ) from exc
            observed_value = None

    claim = _render_claim(spec, observed_value, stream.exit_code)
    found = forbidden_matches(claim)
    if found:
        raise ForbiddenContentError(
            f"claim に禁止 pattern がある: {', '.join(found)}", command_id=spec.command_id
        )

    entry: dict[str, Any] = {
        "command_id": spec.command_id,
        "role": spec.role,
        "command": spec.display_command,
        "execution_mode": spec.execution_mode,
        "canonical_command_sha256": canonical_command_sha256(
            spec.execution_mode,
            argv=spec.argv if spec.execution_mode == "argv" else None,
            script=spec.script if spec.execution_mode == "shell" else None,
        ),
        "expected_exit_codes": list(spec.expected_exit_codes),
        "observed_exit_code": stream.exit_code,
        "started_at": execution.started_at,
        "duration_ms": execution.duration_ms,
        "stdout_sha256": sha256_bytes(stream.stdout),
        "stderr_sha256": sha256_bytes(stream.stderr),
        "stdout_bytes": len(stream.stdout),
        "stderr_bytes": len(stream.stderr),
        "stdout_excerpt": stdout_excerpt,
        "stderr_excerpt": stderr_excerpt,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "claim": claim,
        "claim_type": spec.claim_type,
        "observed_value": observed_value,
        "derivation": spec.derivation,
        "preflight": execution.preflight,
    }
    return entry, exit_ok


def finalize_artifact_hashes(artifact: dict[str, Any]) -> dict[str, Any]:
    """attestation と artifact の self hash を規約順に確定する（Rule 9 / 11）。

    1. ``artifact_body_sha256``: validation_attestation と artifact_sha256 を null にした projection
    2. ``attestation_sha256``: attestation 自身の同 field を null にした projection
    3. ``artifact_sha256``: 同 field を null にした artifact projection
    """
    attestation = artifact.get("validation_attestation")
    if not isinstance(attestation, dict):
        raise EvidenceGenerationError("attestation_missing", "validation_attestation がない")
    body_hash = canonical_hash_with_null_fields(
        artifact, "validation_attestation", "artifact_sha256"
    )
    attestation["artifact_body_sha256"] = body_hash
    attestation["attestation_sha256"] = canonical_hash_with_null_fields(
        attestation, "attestation_sha256"
    )
    artifact["artifact_sha256"] = canonical_hash_with_null_fields(artifact, "artifact_sha256")
    return artifact


def generator_source_sha256() -> str:
    """generator source（本 file）の raw byte SHA-256 を返す。"""
    return sha256_bytes(GENERATOR_SOURCE_PATH.read_bytes())


def generator_source_git_oid() -> str:
    """generator source（本 file）の git blob OID を返す。"""
    return git_blob_oid(GENERATOR_SOURCE_PATH.read_bytes())


@dataclass
class GeneratedEvidence:
    """生成した artifact と capture-time 判定。"""

    artifact: dict[str, Any]
    validation: ValidationResult
    raw_streams: dict[str, CapturedStream] = field(default_factory=dict)
    normalized_inputs: dict[str, bytes] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """gate pass に使えるかを返す。"""
        return self.validation.ok


def _utc_now_rfc3339() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def generate_evidence_artifact(
    *,
    artifact_kind: str,
    manifest: CommandManifest,
    head_sha: str,
    repository: str = DEFAULT_REPOSITORY,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    generated_at: str | None = None,
    validated_at: str | None = None,
    extra_fields: Mapping[str, Any] | None = None,
    persist_normalized_inputs: bool = False,
    normalized_input_ref_prefix: str = DEFAULT_NORMALIZED_INPUT_REF_PREFIX,
) -> GeneratedEvidence:
    """manifest の全 command を実行して Evidence Schema v2 artifact を生成する。

    Args:
        artifact_kind: review / release / audit のいずれか（Phase A）。
        manifest: ``build_command_manifest()`` が固定した実行前 manifest。
        head_sha: 対象 commit の 40 桁 SHA。
        repository: artifact に記録する repository 名。
        cwd: command の作業ディレクトリ。
        env: sanitized environment へ追加する変数（認証情報らしい key は拒否する）。
        generated_at / validated_at: RFC3339 の時刻。試験では固定値を渡す。
        extra_fields: adapter が足す kind 固有 top-level field。
        persist_normalized_inputs: offline 再導出用の normalized input を保持するか。
        normalized_input_ref_prefix: normalized input の content-addressed path prefix。

    Returns:
        GeneratedEvidence: ``validation.ok`` が真のときだけ gate pass に使える。
    """
    if artifact_kind not in KNOWN_ARTIFACT_KINDS:
        raise EvidenceGenerationError("invalid_field", f"未知の artifact_kind: {artifact_kind}")
    violations = validate_command_manifest(manifest.data, raw_bytes=manifest.raw_bytes)
    if violations:
        raise EvidenceGenerationError("manifest_invalid", _render(violations))

    environment = sanitized_environment(env)
    entries: list[dict[str, Any]] = []
    raw_streams: dict[str, CapturedStream] = {}
    all_expected_ok = True
    for spec in manifest.specs:
        execution = execute_command(spec, env=environment, cwd=cwd)
        raw_streams[spec.command_id] = execution.stream
        entry, exit_ok = _command_entry(execution)
        entries.append(entry)
        all_expected_ok = all_expected_ok and exit_ok

    normalized_inputs: dict[str, bytes] = {}
    normalized_records: list[dict[str, Any]] = []
    if persist_normalized_inputs:
        for spec in manifest.specs:
            if spec.claim_type not in RAW_DERIVED_CLAIM_TYPES:
                continue
            data = raw_streams[spec.command_id].stdout
            found = forbidden_matches(data.decode("utf-8", errors="replace"))
            if found:
                raise ForbiddenContentError(
                    f"normalized input に禁止 pattern がある: {', '.join(found)}",
                    command_id=spec.command_id,
                )
            digest = sha256_bytes(data)
            ref = f"{normalized_input_ref_prefix}/{digest}.stdout"
            normalized_inputs[ref] = data
            normalized_records.append(
                {
                    "command_id": spec.command_id,
                    "parser_id": spec.derivation,
                    "ref": ref,
                    "raw_sha256": digest,
                }
            )

    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_V2,
        "artifact_kind": artifact_kind,
        "producer": {
            "name": GENERATOR_MARKER,
            "source_sha256": generator_source_sha256(),
            "source_git_oid": generator_source_git_oid(),
        },
        "generated_at": generated_at or _utc_now_rfc3339(),
        "repository": repository,
        "head_sha": head_sha,
        "result": "pass" if all_expected_ok else "fail",
        "command_manifest_ref": manifest.ref,
        "command_manifest_sha256": manifest.sha256,
        "required_command_ids": sorted(spec.command_id for spec in manifest.specs),
        "required_verification_ids": sorted(
            spec.command_id for spec in manifest.specs if spec.role == "verification"
        ),
        "commands": entries,
        "corrections": [],
        "research_binding": None,
        "research_result": None,
        "validation_attestation": None,
        "artifact_sha256": None,
    }
    for key, value in (extra_fields or {}).items():
        artifact[key] = value

    # capture-time validator: raw stream が消える前に typed claim を再導出する（Rule 11）。
    # attestation 挿入前でも self hash 契約を満たすよう、先に artifact_sha256 を確定させる。
    artifact["artifact_sha256"] = canonical_hash_with_null_fields(artifact, "artifact_sha256")
    capture_result = validate_evidence_contract(
        artifact,
        manifest=manifest.data,
        manifest_raw_bytes=manifest.raw_bytes,
        raw_streams=raw_streams,
        require_attestation=False,
    )
    artifact["validation_attestation"] = {
        "validator_source_sha256": contract_source_sha256(),
        "validator_source_git_oid": contract_source_git_oid(),
        "command_manifest_sha256": manifest.sha256,
        "artifact_body_sha256": "",
        "verified_command_ids": sorted(entry["command_id"] for entry in entries),
        "verified_claim_ids": sorted(
            entry["command_id"] for entry in entries if entry["role"] == "verification"
        ),
        "normalized_inputs": normalized_records,
        "result": capture_result.result,
        "validated_at": validated_at or _utc_now_rfc3339(),
        "attestation_sha256": "",
    }
    finalize_artifact_hashes(artifact)

    validation = validate_evidence_contract(
        artifact,
        manifest=manifest.data,
        manifest_raw_bytes=manifest.raw_bytes,
        raw_streams=raw_streams,
        normalized_inputs=normalized_inputs or None,
    )
    return GeneratedEvidence(
        artifact=artifact,
        validation=validation,
        raw_streams=raw_streams,
        normalized_inputs=normalized_inputs,
    )


def write_evidence_artifact(generated: GeneratedEvidence, path: Path) -> Path:
    """契約として有効な artifact だけを canonical bytes で書き出す。"""
    if not generated.validation.contract_valid:
        raise EvidenceGenerationError(
            "contract_invalid",
            f"契約違反 artifact は書き出さない: {generated.validation.render()}",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_content_bytes(generated.artifact))
    return path


def write_normalized_inputs(generated: GeneratedEvidence, directory: Path) -> list[Path]:
    """offline 再導出用 normalized input を content-addressed path へ書き出す。"""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for ref, data in generated.normalized_inputs.items():
        path = directory / Path(ref).name
        path.write_bytes(data)
        written.append(path)
    return written


def load_artifact(path: Path) -> dict[str, Any]:
    """artifact JSON を読み込む（historical read を含む）。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvidenceGenerationError("invalid_field", "artifact が object でない")
    return value


def artifact_canonical_sha256(artifact: Mapping[str, Any]) -> str:
    """artifact の canonical hash を返す（外部 validator の再計算用）。"""
    return canonical_content_sha256(artifact)
