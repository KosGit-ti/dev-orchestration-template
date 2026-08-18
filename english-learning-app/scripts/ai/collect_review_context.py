from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_MAX_RELATED_FILES = 40
DEFAULT_MAX_CONTEXT_CHARS = 180_000
DEFAULT_MAX_FILE_EXCERPT_CHARS = 12_000
DEFAULT_MAX_CONTEXT_COMMANDS = 12

VALID_CONTEXT_MODES = {"diff_only", "related_context", "full_repo_agentic"}
LOW_RISK_PREFIXES = ("docs/", "data/")
LOW_RISK_SUFFIXES = (".md", ".txt")
READABLE_TRACKED_FILE_MODES = {"100644", "100755"}


@dataclass(frozen=True)
class ReviewContextBudget:
    max_related_files: int = DEFAULT_MAX_RELATED_FILES
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS
    max_file_excerpt_chars: int = DEFAULT_MAX_FILE_EXCERPT_CHARS
    max_context_commands: int = DEFAULT_MAX_CONTEXT_COMMANDS

    @classmethod
    def from_env(cls) -> ReviewContextBudget:
        return cls(
            max_related_files=_env_int("AI_REVIEW_MAX_RELATED_FILES", DEFAULT_MAX_RELATED_FILES),
            max_context_chars=_env_int("AI_REVIEW_MAX_CONTEXT_CHARS", DEFAULT_MAX_CONTEXT_CHARS),
            max_file_excerpt_chars=_env_int(
                "AI_REVIEW_MAX_FILE_EXCERPT_CHARS",
                DEFAULT_MAX_FILE_EXCERPT_CHARS,
            ),
            max_context_commands=_env_int(
                "AI_REVIEW_MAX_CONTEXT_COMMANDS",
                DEFAULT_MAX_CONTEXT_COMMANDS,
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class RelatedFile:
    path: str
    reason: str
    score: int
    excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextCommand:
    command: str
    exit_code: int
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewContextManifest:
    repository_context_mode: str
    diff_only_reason: str
    changed_files: list[str]
    related_files: list[RelatedFile]
    scanned_paths: list[str]
    commands_run: list[ContextCommand]
    context_fingerprint: str
    context_budget: ReviewContextBudget
    context_truncated: bool
    context_truncated_reason: str
    context_budget_override_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository_context_mode": self.repository_context_mode,
            "diff_only_reason": self.diff_only_reason,
            "changed_files": self.changed_files,
            "related_files": [item.to_dict() for item in self.related_files],
            "scanned_paths": [str(p) for p in self.scanned_paths],
            "commands_run": [item.to_dict() for item in self.commands_run],
            "context_fingerprint": self.context_fingerprint,
            "context_budget": self.context_budget.to_dict(),
            "context_truncated": self.context_truncated,
            "context_truncated_reason": self.context_truncated_reason,
            "context_budget_override_reason": self.context_budget_override_reason,
        }


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(1, int(raw_value))
    except ValueError:
        return default


def _run_command(args: list[str], *, cwd: Path, timeout: int = 30) -> tuple[int, str, str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def _record_command(
    commands: list[ContextCommand],
    *,
    command: str,
    exit_code: int,
    summary: str,
    budget: ReviewContextBudget,
    truncation_reasons: list[str],
) -> bool:
    if len(commands) >= budget.max_context_commands:
        if "context command budget exceeded" not in truncation_reasons:
            truncation_reasons.append("context command budget exceeded")
        return False
    commands.append(ContextCommand(command=command, exit_code=exit_code, summary=summary))
    return True


def parse_diff_changed_files(diff: str) -> list[str]:
    paths: set[str] = set()
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3]
                if path.startswith("b/"):
                    paths.add(path[2:].replace("\\", "/"))
            continue
        if line.startswith("+++ b/"):
            paths.add(line[6:].replace("\\", "/"))
    return sorted(path for path in paths if path and path != "/dev/null")


def is_low_risk_diff_only(changed_files: list[str]) -> bool:
    if not changed_files:
        return False
    for path in changed_files:
        normalized = path.replace("\\", "/")
        if normalized.startswith(LOW_RISK_PREFIXES):
            continue
        if normalized.endswith(LOW_RISK_SUFFIXES):
            continue
        return False
    return True


def _git_tracked_modes(
    root: Path,
    commands: list[ContextCommand],
    budget: ReviewContextBudget,
    truncation_reasons: list[str],
) -> dict[str, str]:
    exit_code, stdout, stderr = _run_command(["git", "ls-files", "--stage", "-z"], cwd=root)
    records = [record for record in stdout.split("\0") if record]
    summary = f"{len(records)} tracked paths"
    if exit_code != 0:
        summary = stderr.strip() or "git ls-files failed"
    _record_command(
        commands,
        command="git ls-files --stage -z",
        exit_code=exit_code,
        summary=summary,
        budget=budget,
        truncation_reasons=truncation_reasons,
    )
    if exit_code != 0:
        return {}

    tracked_modes: dict[str, str] = {}
    for record in records:
        metadata, separator, path = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            continue
        mode, _object_id, stage = fields
        if stage != "0" or not path:
            continue
        tracked_modes[path.replace("\\", "/")] = mode
    return tracked_modes


def _safe_tracked_regular_path(
    root: Path,
    relative_path: str,
    tracked_modes: dict[str, str],
) -> Path | None:
    normalized = relative_path.replace("\\", "/")
    if tracked_modes.get(normalized) not in READABLE_TRACKED_FILE_MODES:
        return None

    relative = Path(normalized)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return None

    try:
        resolved_root = root.resolve(strict=True)
        current = resolved_root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return None
        resolved_path = current.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
        if not stat.S_ISREG(resolved_path.stat().st_mode):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved_path


def _read_tracked_text(
    root: Path,
    relative_path: str,
    tracked_modes: dict[str, str],
) -> str | None:
    if _safe_tracked_regular_path(root, relative_path, tracked_modes) is None:
        return None

    normalized = relative_path.replace("\\", "/")
    parts = Path(normalized).parts
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = -1
    file_fd = -1
    try:
        directory_fd = os.open(root.resolve(strict=True), directory_flags)
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            return None
        with os.fdopen(file_fd, encoding="utf-8", errors="replace") as stream:
            file_fd = -1
            return stream.read()
    except (OSError, RuntimeError):
        return None
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


def _read_excerpt(
    root: Path,
    relative_path: str,
    tracked_modes: dict[str, str],
    limit: int,
) -> tuple[str, bool, bool]:
    text = _read_tracked_text(root, relative_path, tracked_modes)
    if text is None:
        return "", False, False
    if len(text) <= limit:
        return text, False, True
    marker = "\n[excerpt truncated]\n"
    return text[: max(0, limit - len(marker))] + marker, True, True


def _module_to_paths(module: str) -> list[str]:
    module_path = module.replace(".", "/")
    return [
        f"{module_path}.py",
        f"{module_path}/__init__.py",
        f"src/{module_path}.py",
        f"src/{module_path}/__init__.py",
    ]


def _python_import_candidates(
    root: Path,
    changed_file: str,
    tracked_modes: dict[str, str],
) -> list[tuple[str, str, int]]:
    if Path(changed_file).suffix != ".py":
        return []
    text = _read_tracked_text(root, changed_file, tracked_modes)
    if text is None:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    candidates: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        for module in modules:
            if not module.startswith(("web", "scripts", "tests")):
                continue
            for candidate in _module_to_paths(module):
                if (
                    candidate != changed_file
                    and _safe_tracked_regular_path(root, candidate, tracked_modes) is not None
                ):
                    candidates.append((candidate, f"imported module {module}", 80))
    return candidates


def _test_pair_candidates(files: list[str], changed_file: str) -> list[tuple[str, str, int]]:
    stem = Path(changed_file).stem
    if not stem or stem.startswith("__"):
        return []
    candidates = []
    for file_path in files:
        normalized = file_path.replace("\\", "/")
        if not normalized.startswith("tests/"):
            continue
        if stem in Path(normalized).stem or stem in normalized:
            candidates.append((normalized, f"test paired with {changed_file}", 90))
    return candidates


def _policy_candidates(changed_file: str) -> list[tuple[str, str, int]]:
    normalized = changed_file.replace("\\", "/")
    candidates: list[tuple[str, str, int]] = []
    if normalized.startswith(".github/workflows/") or normalized.startswith("scripts/hooks/"):
        candidates.extend(
            [
                ("docs/runbook.md", f"operation docs related to {normalized}", 55),
                (
                    ".github/instructions/review-loop.instructions.md",
                    f"review loop instructions related to {normalized}",
                    70,
                ),
            ]
        )
    if normalized.startswith("docs/") or normalized.startswith("ai/"):
        candidates.extend(
            [
                ("ai/context-index.yml", f"context policy related to {normalized}", 55),
                ("ai/document-governance.yml", f"document governance related to {normalized}", 55),
            ]
        )
    if normalized == "scripts/run_ai_review.py" or normalized.startswith("scripts/ai/"):
        candidates.extend(
            [
                (
                    "docs/ai/copilot-independent-review.md",
                    f"review gate spec related to {normalized}",
                    80,
                ),
                ("docs/requirements.md", f"requirements related to {normalized}", 50),
                ("docs/design.md", f"design related to {normalized}", 50),
            ]
        )
    return candidates


def _reference_candidates(
    root: Path,
    changed_file: str,
    commands: list[ContextCommand],
    budget: ReviewContextBudget,
    truncation_reasons: list[str],
) -> list[tuple[str, str, int]]:
    stem = Path(changed_file).stem
    if len(stem) < 4:
        return []
    if len(commands) >= budget.max_context_commands:
        if "context command budget exceeded" not in truncation_reasons:
            truncation_reasons.append("context command budget exceeded")
        return []
    if shutil.which("rg") is not None:
        command = f"rg -l --glob !data/** --glob !outputs/** {stem}"
        exit_code, stdout, stderr = _run_command(
            ["rg", "-l", "--glob", "!data/**", "--glob", "!outputs/**", stem],
            cwd=root,
        )
    else:
        command = f"git grep -l -e {stem} -- . :(exclude)data/** :(exclude)outputs/**"
        exit_code, stdout, stderr = _run_command(
            [
                "git",
                "grep",
                "-l",
                "-e",
                stem,
                "--",
                ".",
                ":(exclude)data/**",
                ":(exclude)outputs/**",
            ],
            cwd=root,
        )
    summary = f"{len(stdout.splitlines())} files reference {stem}"
    if exit_code not in {0, 1}:
        summary = stderr.strip() or f"reference search failed for {stem}"
    _record_command(
        commands,
        command=command,
        exit_code=exit_code,
        summary=summary,
        budget=budget,
        truncation_reasons=truncation_reasons,
    )
    if exit_code not in {0, 1}:
        return []
    candidates = []
    for line in stdout.splitlines():
        path = line.strip().replace("\\", "/")
        if path and path != changed_file:
            candidates.append((path, f"references symbol/path stem {stem}", 45))
    return candidates


def _candidate_key(candidate: tuple[str, str, int]) -> str:
    return candidate[0].replace("\\", "/")


def collect_review_context(
    *,
    repo_root: Path,
    diff: str,
    mode: str = "related_context",
    budget: ReviewContextBudget | None = None,
    context_budget_override_reason: str = "",
    diff_only_reason: str = "",
) -> ReviewContextManifest:
    if mode not in VALID_CONTEXT_MODES:
        msg = f"invalid repository context mode: {mode}"
        raise ValueError(msg)

    budget = budget or ReviewContextBudget.from_env()
    changed_files = parse_diff_changed_files(diff)
    commands: list[ContextCommand] = []
    truncation_reasons: list[str] = []
    scanned_paths: set[str] = set(changed_files)

    if mode == "diff_only":
        if not diff_only_reason and not is_low_risk_diff_only(changed_files):
            truncation_reasons.append("diff_only used without low-risk reason")
        return _build_manifest(
            mode=mode,
            changed_files=changed_files,
            related_files=[],
            scanned_paths=sorted(scanned_paths),
            commands=commands,
            budget=budget,
            diff=diff,
            truncation_reasons=truncation_reasons,
            budget_override_reason=context_budget_override_reason,
            diff_only_reason=diff_only_reason,
        )

    tracked_modes = _git_tracked_modes(repo_root, commands, budget, truncation_reasons)
    files = sorted(tracked_modes)
    scanned_paths.add(".")

    candidates: dict[str, tuple[str, str, int]] = {}
    for changed_file in changed_files:
        for candidate in _test_pair_candidates(files, changed_file):
            key = _candidate_key(candidate)
            candidates[key] = _merge_candidate(candidates.get(key), candidate)
        for candidate in _python_import_candidates(repo_root, changed_file, tracked_modes):
            key = _candidate_key(candidate)
            candidates[key] = _merge_candidate(candidates.get(key), candidate)
        for candidate in _policy_candidates(changed_file):
            if candidate[0] in tracked_modes:
                key = _candidate_key(candidate)
                candidates[key] = _merge_candidate(candidates.get(key), candidate)
        if mode == "full_repo_agentic":
            for candidate in _reference_candidates(
                repo_root,
                changed_file,
                commands,
                budget,
                truncation_reasons,
            ):
                if candidate[0] in tracked_modes:
                    candidates[_candidate_key(candidate)] = _merge_candidate(
                        candidates.get(_candidate_key(candidate)),
                        candidate,
                    )

    safe_candidates: list[tuple[str, str, int]] = []
    for candidate in candidates.values():
        path_text = candidate[0]
        if _safe_tracked_regular_path(repo_root, path_text, tracked_modes) is None:
            truncation_reasons.append(f"unsafe or non-regular tracked file skipped: {path_text}")
            continue
        safe_candidates.append(candidate)

    ordered = sorted(safe_candidates, key=lambda item: (-item[2], item[0]))
    if len(ordered) > budget.max_related_files:
        truncation_reasons.append(
            f"related files truncated: {len(ordered)} > {budget.max_related_files}"
        )
        ordered = ordered[: budget.max_related_files]

    related_files: list[RelatedFile] = []
    context_chars = 0
    for path_text, reason, score in ordered:
        excerpt, file_truncated, readable = _read_excerpt(
            repo_root,
            path_text,
            tracked_modes,
            budget.max_file_excerpt_chars,
        )
        if not readable:
            truncation_reasons.append(f"unsafe or non-regular tracked file skipped: {path_text}")
            continue
        if file_truncated:
            truncation_reasons.append(f"file excerpt truncated: {path_text}")
        remaining = budget.max_context_chars - context_chars
        if remaining <= 0:
            truncation_reasons.append("context character budget exceeded")
            break
        if len(excerpt) > remaining:
            marker = "\n[context budget truncated]\n"
            if remaining <= len(marker):
                excerpt = marker[:remaining]
            else:
                excerpt = excerpt[: remaining - len(marker)] + marker
            truncation_reasons.append("context character budget exceeded")
        context_chars += len(excerpt)
        related_files.append(
            RelatedFile(path=path_text, reason=reason, score=score, excerpt=excerpt)
        )
        scanned_paths.add(path_text)

    return _build_manifest(
        mode=mode,
        changed_files=changed_files,
        related_files=related_files,
        scanned_paths=sorted(scanned_paths),
        commands=commands,
        budget=budget,
        diff=diff,
        truncation_reasons=truncation_reasons,
        budget_override_reason=context_budget_override_reason,
        diff_only_reason=diff_only_reason,
    )


def _merge_candidate(
    current: tuple[str, str, int] | None,
    candidate: tuple[str, str, int],
) -> tuple[str, str, int]:
    if current is None:
        return candidate
    path, reason, score = current
    _, new_reason, new_score = candidate
    reasons = reason.split("; ")
    if new_reason not in reasons:
        reasons.append(new_reason)
    return path, "; ".join(reasons), max(score, new_score)


def _build_manifest(
    *,
    mode: str,
    changed_files: list[str],
    related_files: list[RelatedFile],
    scanned_paths: list[str],
    commands: list[ContextCommand],
    budget: ReviewContextBudget,
    diff: str,
    truncation_reasons: list[str],
    budget_override_reason: str,
    diff_only_reason: str,
) -> ReviewContextManifest:
    fingerprint_input = {
        "mode": mode,
        "changed_files": changed_files,
        "related_files": [
            {"path": item.path, "reason": item.reason, "score": item.score, "excerpt": item.excerpt}
            for item in related_files
        ],
        "scanned_paths": scanned_paths,
        "commands_run": [item.to_dict() for item in commands],
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "budget": budget.to_dict(),
        "diff_only_reason": diff_only_reason,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    unique_reasons = sorted(set(reason for reason in truncation_reasons if reason))
    return ReviewContextManifest(
        repository_context_mode=mode,
        diff_only_reason=diff_only_reason,
        changed_files=changed_files,
        related_files=related_files,
        scanned_paths=scanned_paths,
        commands_run=commands,
        context_fingerprint=fingerprint,
        context_budget=budget,
        context_truncated=bool(unique_reasons),
        context_truncated_reason="; ".join(unique_reasons),
        context_budget_override_reason=budget_override_reason,
    )
