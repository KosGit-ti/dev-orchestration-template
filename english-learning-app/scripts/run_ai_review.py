from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
_ROOT_TEXT = str(ROOT)
if _ROOT_TEXT not in sys.path:
    sys.path.insert(0, _ROOT_TEXT)

from scripts.ai.collect_review_context import (
    ReviewContextBudget,
    ReviewContextManifest,
    collect_review_context,
)
from scripts.ai.review_evidence import (
    REVIEW_REPORT_PREFIX,
    diff_fingerprint,
    merge_base,
)
from scripts.ai.review_evidence import (
    SCHEMA_VERSION as REVIEW_REPORT_SCHEMA_VERSION,
)

VALID_SEVERITIES = {"Must", "Should", "Nice"}
ENGINE_ORDER = ["copilot", "codex", "claude"]
DEFAULT_MAX_DIFF_CHARS = 120_000


@dataclass
class ReviewIssue:
    severity: str
    category: str
    file: str
    line: int
    rationale: str
    fix: str
    confidence: float


@dataclass
class ReviewContext:
    pr_number: int
    title: str
    body: str
    url: str
    base_ref: str
    head_ref: str
    head_sha: str
    diff: str
    diff_truncated: bool
    base_sha: str = ""
    repository_context_mode: str = "related_context"
    diff_only_reason: str = ""
    changed_files: list[str] = field(default_factory=list)
    related_files: list[dict[str, Any]] = field(default_factory=list)
    scanned_paths: list[str] = field(default_factory=list)
    commands_run: list[dict[str, Any]] = field(default_factory=list)
    context_fingerprint: str = ""
    context_budget: dict[str, int] = field(default_factory=dict)
    context_truncated: bool = False
    context_truncated_reason: str = ""
    context_budget_override_reason: str = ""


@dataclass
class ReviewResult:
    engine: str
    pr_number: int
    status: str
    failover_from: str | None
    generated_at: str
    summary: str
    base_ref: str
    base_sha: str
    head_ref: str
    reviewed_head_sha: str
    diff_truncated: bool
    repository_context_mode: str
    diff_only_reason: str
    changed_files: list[str]
    related_files: list[dict[str, Any]]
    scanned_paths: list[str]
    commands_run: list[dict[str, Any]]
    context_fingerprint: str
    context_budget: dict[str, int]
    context_truncated: bool
    context_truncated_reason: str
    context_budget_override_reason: str
    issues: list[ReviewIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "pr_number": self.pr_number,
            "status": self.status,
            "failover_from": self.failover_from,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "base_ref": self.base_ref,
            "base_sha": self.base_sha,
            "head_ref": self.head_ref,
            "reviewed_head_sha": self.reviewed_head_sha,
            "diff_truncated": self.diff_truncated,
            "repository_context_mode": self.repository_context_mode,
            "diff_only_reason": self.diff_only_reason,
            "changed_files": self.changed_files,
            "related_files": self.related_files,
            "scanned_paths": self.scanned_paths,
            "commands_run": self.commands_run,
            "context_fingerprint": self.context_fingerprint,
            "context_budget": self.context_budget,
            "context_truncated": self.context_truncated,
            "context_truncated_reason": self.context_truncated_reason,
            "context_budget_override_reason": self.context_budget_override_reason,
            "issues": [asdict(issue) for issue in self.issues],
        }


class ReviewExecutionError(RuntimeError):
    """AI review を安全に完了できない場合のエラー。"""


def _env_truthy(name: str) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _claude_api_billing_allowed() -> bool:
    return _env_truthy("AI_REVIEW_ALLOW_CLAUDE_API_BILLING")


def _engine_available(engine: str) -> bool:
    if engine == "copilot":
        return bool(os.getenv("COPILOT_REVIEW_COMMAND"))
    if engine == "codex":
        return bool(os.getenv("CODEX_REVIEW_COMMAND") or os.getenv("OPENAI_API_KEY"))
    if engine == "claude":
        return bool(
            os.getenv("CLAUDE_REVIEW_COMMAND")
            or (os.getenv("ANTHROPIC_API_KEY") and _claude_api_billing_allowed())
        )
    return False


def resolve_engine(primary: str, allow_failover: bool) -> tuple[str, str | None]:
    if _engine_available(primary):
        return primary, None
    if not allow_failover:
        raise ReviewExecutionError(f"primary engine unavailable: {primary}")

    for candidate in ENGINE_ORDER:
        if candidate != primary and _engine_available(candidate):
            return candidate, primary
    raise ReviewExecutionError("no review engine available")


def _run_command(
    args: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        args,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=env,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise ReviewExecutionError(f"{' '.join(args)} failed: {stderr}")
    return result.stdout


def _max_diff_chars() -> int:
    raw_value = os.getenv("AI_REVIEW_MAX_DIFF_CHARS", str(DEFAULT_MAX_DIFF_CHARS))
    try:
        return max(10_000, int(raw_value))
    except ValueError:
        return DEFAULT_MAX_DIFF_CHARS


def _provider_timeout_seconds() -> int:
    raw_value = os.getenv("AI_REVIEW_PROVIDER_TIMEOUT_SEC", "180")
    try:
        return max(60, int(raw_value))
    except ValueError:
        return 180


def _current_head_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise ReviewExecutionError(f"git rev-parse HEAD failed: {stderr}")
    return result.stdout.strip()


def _local_pr_diff(
    *,
    base_sha: str,
    base_ref: str,
    head_sha: str,
    original_error: ReviewExecutionError,
) -> str:
    candidates = [base_sha]
    if base_ref:
        candidates.extend([f"origin/{base_ref}", base_ref])

    errors = [str(original_error)]
    for candidate in [item for item in candidates if item]:
        try:
            merge_base = _run_command(
                ["git", "merge-base", candidate, head_sha],
                timeout=30,
            ).strip()
            if not merge_base:
                raise ReviewExecutionError(f"git merge-base {candidate} {head_sha} returned empty")
            return _run_command(
                ["git", "diff", "--binary", merge_base, head_sha],
                timeout=120,
            )
        except ReviewExecutionError as exc:
            errors.append(str(exc))

    raise ReviewExecutionError(
        "gh pr diff が失敗し、ローカル git diff fallback も失敗しました: " + " | ".join(errors)
    )


def _pr_diff_with_local_fallback(
    *,
    pr_number: int,
    base_sha: str,
    base_ref: str,
    head_sha: str,
) -> str:
    try:
        return _run_command(["gh", "pr", "diff", str(pr_number), "--patch"], timeout=60)
    except ReviewExecutionError as exc:
        return _local_pr_diff(
            base_sha=base_sha,
            base_ref=base_ref,
            head_sha=head_sha,
            original_error=exc,
        )


def _strip_git_diff_path(path: str) -> str:
    normalized = path.strip().strip('"').replace("\\", "/")
    if normalized.startswith(("a/", "b/")):
        return normalized[2:]
    return normalized


def _diff_header_paths(line: str) -> list[str]:
    parts = line.strip().split()
    if len(parts) < 4:
        return []
    return [_strip_git_diff_path(parts[2]), _strip_git_diff_path(parts[3])]


def _strip_review_report_diff(diff: str) -> str:
    """レビュー証跡 JSON 自身を provider prompt / context から除外する。"""

    chunks: list[str] = []
    current: list[str] = []
    keep = True
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if keep and current:
                chunks.extend(current)
            current = [line]
            paths = _diff_header_paths(line)
            keep = not paths or not all(path.startswith(REVIEW_REPORT_PREFIX) for path in paths)
            continue
        current.append(line)
    if keep and current:
        chunks.extend(current)
    return "".join(chunks)


def _context_mode() -> str:
    return os.getenv("AI_REVIEW_CONTEXT_MODE", "related_context").strip() or "related_context"


def _context_budget_override_reason() -> str:
    return os.getenv("AI_REVIEW_CONTEXT_BUDGET_OVERRIDE_REASON", "").strip()


def _manifest_to_context_fields(manifest: ReviewContextManifest) -> dict[str, Any]:
    data = manifest.to_dict()
    return {
        "repository_context_mode": data["repository_context_mode"],
        "diff_only_reason": data["diff_only_reason"],
        "changed_files": data["changed_files"],
        "related_files": data["related_files"],
        "scanned_paths": data["scanned_paths"],
        "commands_run": data["commands_run"],
        "context_fingerprint": data["context_fingerprint"],
        "context_budget": data["context_budget"],
        "context_truncated": data["context_truncated"],
        "context_truncated_reason": data["context_truncated_reason"],
        "context_budget_override_reason": data["context_budget_override_reason"],
    }


def collect_pr_context(
    pr_number: int,
    *,
    repository_context_mode: str | None = None,
    context_budget_override_reason: str | None = None,
    diff_only_reason: str = "",
) -> ReviewContext:
    raw_view = _run_command(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "title,body,url,baseRefName,baseRefOid,headRefName,headRefOid",
        ],
        timeout=30,
    )
    try:
        view = json.loads(raw_view)
    except json.JSONDecodeError as exc:
        raise ReviewExecutionError(f"PR metadata JSON parse failed: {exc}") from exc
    if not isinstance(view, dict):
        raise ReviewExecutionError("PR metadata must be a JSON object")

    head_sha = str(view.get("headRefOid") or "")
    base_ref = str(view.get("baseRefName") or "")
    base_sha = str(view.get("baseRefOid") or "")
    current_head = _current_head_sha()
    if head_sha and current_head != head_sha and not _env_truthy("AI_REVIEW_ALLOW_HEAD_MISMATCH"):
        raise ReviewExecutionError(
            "fallback review は PR head checkout 上で実行する必要があります。"
            f" current={current_head} expected={head_sha}"
        )

    raw_full_diff = _pr_diff_with_local_fallback(
        pr_number=pr_number,
        base_sha=base_sha,
        base_ref=base_ref,
        head_sha=head_sha,
    )
    full_diff = _strip_review_report_diff(raw_full_diff)
    max_chars = _max_diff_chars()
    diff_truncated = len(full_diff) > max_chars
    diff = full_diff
    if diff_truncated:
        diff = full_diff[:max_chars] + "\n\n[diff truncated by AI_REVIEW_MAX_DIFF_CHARS]\n"

    manifest = collect_review_context(
        repo_root=Path.cwd(),
        diff=full_diff,
        mode=repository_context_mode or _context_mode(),
        budget=ReviewContextBudget.from_env(),
        context_budget_override_reason=(
            context_budget_override_reason
            if context_budget_override_reason is not None
            else _context_budget_override_reason()
        ),
        diff_only_reason=diff_only_reason,
    )

    return ReviewContext(
        pr_number=pr_number,
        title=str(view.get("title") or ""),
        body=str(view.get("body") or ""),
        url=str(view.get("url") or ""),
        base_ref=base_ref,
        base_sha=base_sha,
        head_ref=str(view.get("headRefName") or ""),
        head_sha=head_sha,
        diff=diff,
        diff_truncated=diff_truncated,
        **_manifest_to_context_fields(manifest),
    )


def build_review_prompt(context: ReviewContext) -> str:
    context_manifest_json = json.dumps(
        {
            "changed_files": context.changed_files,
            "related_files": context.related_files,
            "scanned_paths": context.scanned_paths,
            "commands_run": context.commands_run,
        },
        ensure_ascii=False,
        indent=2,
    )
    return f"""あなたは本リポジトリの独立批判レビュー担当です。
PR 差分を読み、spec / security / reliability / testability の観点で
Must / Should / Nice を分類してください。
必ず実際の差分に基づく指摘だけを返し、固定文面や一般論だけの指摘は禁止です。

出力は次の JSON object のみです。Markdown や説明文を前後に付けないでください。
{{
  "summary": "レビュー要約",
  "repository_context_mode": "{context.repository_context_mode}",
  "diff_only_reason": "{context.diff_only_reason}",
  "issues": [
    {{
      "severity": "Must | Should | Nice",
      "category": "spec_consistency | security | reliability | test_reliability | maintainability",
      "file": "path/to/file",
      "line": 1,
      "rationale": "差分に基づく根拠",
      "fix": "具体的な修正案",
      "confidence": 0.0
    }}
  ]
}}

Must は秘密情報、fail-open、安全制約違反、CI/必須ゲート不能、仕様不整合など即時ブロッカーです。
Should は今回対応が望ましい非ブロッカーです。Nice は任意改善です。
問題がなければ issues は空配列にしてください。
既存の docs/ai/reviews/*.json はこの実行後に再生成される自己参照成果物です。
既存レビュー証跡の stale head_sha / diff_fingerprint / open finding は指摘対象にせず、
レビュー証跡ファイル以外の実差分だけを評価してください。

PR: #{context.pr_number} {context.title}
URL: {context.url}
base: {context.base_ref}
head: {context.head_ref}
head_sha: {context.head_sha}
diff_truncated: {context.diff_truncated}
repository_context_mode: {context.repository_context_mode}
diff_only_reason: {context.diff_only_reason}
context_fingerprint: {context.context_fingerprint}
context_truncated: {context.context_truncated}
context_truncated_reason: {context.context_truncated_reason}
context_budget: {json.dumps(context.context_budget, ensure_ascii=False)}

PR body:
{context.body}

Repo-aware context manifest:
{context_manifest_json}

Diff:
{context.diff}
"""


def _request_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int = 120,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ReviewExecutionError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ReviewExecutionError(f"request to {url} failed: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewExecutionError(f"response JSON parse failed: {exc}") from exc
    if not isinstance(data, dict):
        raise ReviewExecutionError("provider response must be a JSON object")
    return data


def _response_text_from_openai(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    texts: list[str] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    texts.append(text)
    if texts:
        return "\n".join(texts)
    raise ReviewExecutionError("OpenAI response did not contain output text")


def _response_text_from_anthropic(data: dict[str, Any]) -> str:
    content = data.get("content")
    if not isinstance(content, list):
        raise ReviewExecutionError("Anthropic response did not contain content")
    texts = [
        part.get("text")
        for part in content
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    if not texts:
        raise ReviewExecutionError("Anthropic response did not contain text")
    return "\n".join(cast("list[str]", texts))


def _run_external_review_command(command: str, prompt: str) -> str:
    args = shlex.split(command)
    if not args:
        raise ReviewExecutionError("review command is empty")
    return _run_command(
        args,
        input_text=prompt,
        timeout=_provider_timeout_seconds(),
        env=_external_review_env(),
    )


def _external_review_env() -> dict[str, str]:
    blocked = {
        "AI_REVIEW_FALLBACK_PUSH_TOKEN",
        "AUTOMATION_PR_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "PUSH_TOKEN",
    }
    if not _env_truthy("AI_REVIEW_ALLOW_EXTERNAL_PROVIDER_API_TOKENS"):
        blocked.add("OPENAI_API_KEY")
        blocked.add("ANTHROPIC_API_KEY")
    elif not _claude_api_billing_allowed():
        blocked.add("ANTHROPIC_API_KEY")
    return {key: value for key, value in os.environ.items() if key not in blocked}


def _run_openai_review(prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ReviewExecutionError("OPENAI_API_KEY is required for codex review")
    model = os.getenv("OPENAI_REVIEW_MODEL", "gpt-5")
    data = _request_json(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}"},
        payload={
            "model": model,
            "instructions": "You are a senior code reviewer. Return only valid JSON.",
            "input": prompt,
        },
    )
    return _response_text_from_openai(data)


def _run_anthropic_review(prompt: str) -> str:
    if not _claude_api_billing_allowed():
        raise ReviewExecutionError(
            "Claude API review は Anthropic API 課金が発生するため既定では無効です。"
            "利用する場合だけ AI_REVIEW_ALLOW_CLAUDE_API_BILLING=true を明示してください。"
        )
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ReviewExecutionError("ANTHROPIC_API_KEY is required for claude review")
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    data = _request_json(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        payload={
            "model": model,
            "max_tokens": int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096")),
            "system": "You are a senior code reviewer. Return only valid JSON.",
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return _response_text_from_anthropic(data)


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_review_payload(text: str) -> dict[str, Any]:
    candidate = _strip_json_fence(text)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ReviewExecutionError(f"review output must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReviewExecutionError("review output must be a JSON object")
    return payload


def _normalize_issue(raw_issue: dict[str, Any]) -> ReviewIssue:
    severity = str(raw_issue.get("severity", "")).strip().title()
    if severity not in VALID_SEVERITIES:
        raise ReviewExecutionError(f"invalid severity: {severity}")
    category = str(raw_issue.get("category") or "maintainability").strip()
    file_path = str(raw_issue.get("file") or "PR").strip()
    rationale = str(raw_issue.get("rationale") or raw_issue.get("evidence") or "").strip()
    fix = str(raw_issue.get("fix") or raw_issue.get("recommendation") or "").strip()
    if not rationale or not fix:
        raise ReviewExecutionError("review issue requires rationale and fix")
    try:
        line = int(raw_issue.get("line") or 1)
    except (TypeError, ValueError):
        line = 1
    try:
        confidence = float(raw_issue.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    return ReviewIssue(
        severity=severity,
        category=category,
        file=file_path,
        line=max(1, line),
        rationale=rationale,
        fix=fix,
        confidence=min(1.0, max(0.0, confidence)),
    )


def _issues_from_payload(payload: dict[str, Any]) -> tuple[str, list[ReviewIssue]]:
    summary = str(payload.get("summary") or "AI review completed.").strip()
    raw_issues = payload.get("issues", [])
    if not isinstance(raw_issues, list):
        raise ReviewExecutionError("review output issues must be a list")
    issues: list[ReviewIssue] = []
    for raw_issue in raw_issues:
        if not isinstance(raw_issue, dict):
            raise ReviewExecutionError("each review issue must be an object")
        issues.append(_normalize_issue(raw_issue))
    _validate_issues(issues)
    return summary, issues


def _validate_issues(issues: list[ReviewIssue]) -> None:
    for issue in issues:
        if issue.severity not in VALID_SEVERITIES:
            raise ValueError(f"invalid severity: {issue.severity}")
        if not (0.0 <= issue.confidence <= 1.0):
            raise ValueError(f"invalid confidence: {issue.confidence}")
        if issue.line < 1:
            raise ValueError(f"invalid line: {issue.line}")


def _run_provider(engine: str, prompt: str) -> str:
    if engine == "copilot":
        command = os.getenv("COPILOT_REVIEW_COMMAND")
        if not command:
            raise ReviewExecutionError("copilot cannot be executed by GITHUB_TOKEN alone")
        return _run_external_review_command(command, prompt)
    if engine == "codex":
        command = os.getenv("CODEX_REVIEW_COMMAND")
        if command:
            return _run_external_review_command(command, prompt)
        return _run_openai_review(prompt)
    if engine == "claude":
        command = os.getenv("CLAUDE_REVIEW_COMMAND")
        if command:
            return _run_external_review_command(command, prompt)
        if os.getenv("ANTHROPIC_API_KEY"):
            return _run_anthropic_review(prompt)
        raise ReviewExecutionError(
            "claude review requires CLAUDE_REVIEW_COMMAND, or explicit "
            "AI_REVIEW_ALLOW_CLAUDE_API_BILLING=true with ANTHROPIC_API_KEY"
        )
    raise ReviewExecutionError(f"unsupported review engine: {engine}")


def run_review(
    engine: str,
    pr_number: int,
    failover_from: str | None = None,
    *,
    context: ReviewContext | None = None,
) -> ReviewResult:
    review_context = context or collect_pr_context(pr_number)
    prompt = build_review_prompt(review_context)
    raw_output = _run_provider(engine, prompt)
    summary, issues = _issues_from_payload(_parse_review_payload(raw_output))
    return ReviewResult(
        engine=engine,
        pr_number=pr_number,
        status="completed",
        failover_from=failover_from,
        generated_at=datetime.now(UTC).isoformat(),
        summary=summary,
        base_ref=review_context.base_ref,
        base_sha=review_context.base_sha,
        head_ref=review_context.head_ref,
        reviewed_head_sha=review_context.head_sha,
        diff_truncated=review_context.diff_truncated,
        repository_context_mode=review_context.repository_context_mode,
        diff_only_reason=review_context.diff_only_reason,
        changed_files=review_context.changed_files,
        related_files=review_context.related_files,
        scanned_paths=review_context.scanned_paths,
        commands_run=review_context.commands_run,
        context_fingerprint=review_context.context_fingerprint,
        context_budget=review_context.context_budget,
        context_truncated=review_context.context_truncated,
        context_truncated_reason=review_context.context_truncated_reason,
        context_budget_override_reason=review_context.context_budget_override_reason,
        issues=issues,
    )


def write_result(path: Path, result: ReviewResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _report_ref_for_base(result: ReviewResult) -> str:
    return result.base_sha or result.base_ref


def _report_ref_for_head(result: ReviewResult) -> str:
    return result.reviewed_head_sha or result.head_ref


def _gate_finding(
    provider: str,
    issue: ReviewIssue,
    index: int,
    ac_mapping: list[str],
) -> dict[str, Any]:
    severity = issue.severity.lower()
    return {
        "id": f"{provider.upper()}-{index:03d}",
        "severity": severity,
        "status": "open",
        "file": issue.file,
        "line": issue.line,
        "title": issue.rationale.splitlines()[0][:80],
        "evidence": issue.rationale,
        "recommendation": issue.fix,
        "ac_mapping": ac_mapping,
        "confidence": issue.confidence,
    }


def build_review_report(
    result: ReviewResult,
    *,
    root: Path,
    provider: str | None = None,
    ac_mapping: list[str] | None = None,
    review_report_path: Path | None = None,
) -> dict[str, Any]:
    """review-report-gate が読む provider 証跡 JSON を生成する。"""

    report_provider = (provider or result.engine).strip().lower()
    if report_provider not in {"codex", "claude"}:
        msg = f"review report provider must be codex or claude: {report_provider}"
        raise ReviewExecutionError(msg)
    base_ref = _report_ref_for_base(result)
    head_ref = _report_ref_for_head(result)
    if not base_ref or not head_ref:
        raise ReviewExecutionError("review report requires base/head refs")
    mapped_ac = ac_mapping or ["AC-020"]
    report_base_sha = merge_base(root, base_ref, head_ref)
    issues = [
        _gate_finding(report_provider, issue, index, mapped_ac)
        for index, issue in enumerate(result.issues, start=1)
    ]
    report_result = "pass"
    if any(issue.severity in {"Must", "Should"} for issue in result.issues) or result.issues:
        report_result = "partial"
    changed_files = list(result.changed_files)
    scanned_paths = list(result.scanned_paths)
    if review_report_path is not None:
        try:
            report_relative_path = (
                review_report_path.resolve().relative_to(root.resolve()).as_posix()
            )
        except ValueError:
            report_relative_path = ""
        if report_relative_path.startswith(REVIEW_REPORT_PREFIX) and report_relative_path.endswith(
            ".json"
        ):
            if report_relative_path not in changed_files:
                changed_files.append(report_relative_path)
            if report_relative_path not in scanned_paths:
                scanned_paths.append(report_relative_path)
    return {
        "schema_version": REVIEW_REPORT_SCHEMA_VERSION,
        "provider": report_provider,
        "role": "spec-security-reliability",
        "base_ref": result.base_ref,
        "base_sha": report_base_sha,
        "head_ref": result.head_ref,
        "head_sha": result.reviewed_head_sha,
        "diff_fingerprint": diff_fingerprint(root, base_ref, head_ref),
        "generated_at": result.generated_at,
        "result": report_result,
        "summary": result.summary,
        "lenses": ["spec", "security", "reliability"],
        "repository_context_mode": result.repository_context_mode,
        "diff_only_reason": result.diff_only_reason,
        "changed_files": changed_files,
        "related_files": result.related_files,
        "scanned_paths": scanned_paths,
        "commands_run": result.commands_run,
        "context_fingerprint": result.context_fingerprint,
        "context_budget": result.context_budget,
        "context_truncated": result.context_truncated,
        "context_truncated_reason": result.context_truncated_reason,
        "context_budget_override_reason": result.context_budget_override_reason,
        "findings": issues,
    }


def write_review_report(
    path: Path,
    result: ReviewResult,
    *,
    root: Path | None = None,
    provider: str | None = None,
    ac_mapping: list[str] | None = None,
) -> None:
    report = build_review_report(
        result,
        root=(root or Path.cwd()).resolve(),
        provider=provider,
        ac_mapping=ac_mapping,
        review_report_path=path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_daily_log(log_dir: Path, result: ReviewResult) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    output_path = log_dir / f"ai_review_{datetime.now(UTC).date().isoformat()}.json"
    payload: dict[str, Any] = {"runs": []}
    if output_path.exists():
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"runs": []}
    payload.setdefault("runs", []).append(result.to_dict())
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def build_review_markdown(result: ReviewResult) -> str:
    severity_counts = {
        severity: sum(1 for issue in result.issues if issue.severity == severity)
        for severity in ("Must", "Should", "Nice")
    }
    lines = [
        "## AI レビュー結果",
        "",
        f"- engine: `{result.engine}`",
        f"- failover_from: `{result.failover_from}`",
        f"- reviewed_head_sha: `{result.reviewed_head_sha}`",
        f"- diff_truncated: `{result.diff_truncated}`",
        f"- repository_context_mode: `{result.repository_context_mode}`",
        f"- diff_only_reason: `{result.diff_only_reason}`",
        f"- context_fingerprint: `{result.context_fingerprint}`",
        f"- context_truncated: `{result.context_truncated}`",
        f"- related_files: {len(result.related_files)}",
        f"- context_commands: {len(result.commands_run)}",
        f"- generated_at: `{result.generated_at}`",
        f"- Must: {severity_counts['Must']}",
        f"- Should: {severity_counts['Should']}",
        f"- Nice: {severity_counts['Nice']}",
        f"- summary: {result.summary}",
        "",
        "### 指摘",
    ]
    if not result.issues:
        lines.append("- 指摘なし")
    for issue in result.issues:
        lines.extend(
            [
                f"- **{issue.severity}** `{issue.category}` `{issue.file}:{issue.line}`",
                f"  - 根拠: {issue.rationale}",
                f"  - 修正案: {issue.fix}",
                f"  - confidence: {issue.confidence:.2f}",
            ]
        )
    return "\n".join(lines)


def post_review_to_github(pr_number: int, body: str) -> None:
    result = subprocess.run(
        ["gh", "pr", "review", str(pr_number), "--comment", "--body", body],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ReviewExecutionError(f"failed to post review: {result.stderr.strip()}")


def blocking_issue_count(result: ReviewResult) -> int:
    return sum(1 for issue in result.issues if issue.severity in {"Must", "Should"})


def main() -> int:
    parser = argparse.ArgumentParser(description="AI レビューフェイルオーバー実行")
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--engine", choices=ENGINE_ORDER, default="copilot")
    parser.add_argument("--allow-failover", action="store_true")
    parser.add_argument("--output", default="outputs/ai/review_result.json")
    parser.add_argument(
        "--review-report-output",
        help="review-report-gate 用 docs/ai/reviews/*.json も生成する",
    )
    parser.add_argument(
        "--review-report-provider",
        choices=["codex", "claude"],
        help="証跡 provider 名。未指定時は実行 engine を使う",
    )
    parser.add_argument(
        "--review-report-ac",
        action="append",
        default=None,
        help="finding の ac_mapping。複数指定可。未指定時は AC-020",
    )
    parser.add_argument("--daily-log-dir", default="data/ai_review_log")
    parser.add_argument("--post-to-github", action="store_true")
    parser.add_argument(
        "--repository-context-mode",
        choices=["diff_only", "related_context", "full_repo_agentic"],
        default=os.getenv("AI_REVIEW_CONTEXT_MODE", "related_context"),
    )
    parser.add_argument("--context-budget-override-reason", default="")
    parser.add_argument("--diff-only-reason", default="")
    args = parser.parse_args()

    engine, failover_from = resolve_engine(args.engine, args.allow_failover)
    context = collect_pr_context(
        args.pr_number,
        repository_context_mode=args.repository_context_mode,
        context_budget_override_reason=args.context_budget_override_reason,
        diff_only_reason=args.diff_only_reason,
    )
    result = run_review(engine, args.pr_number, failover_from, context=context)
    write_result(Path(args.output), result)
    review_report_written = False
    if args.review_report_output:
        report_provider = (args.review_report_provider or result.engine).strip().lower()
        if report_provider in {"codex", "claude"}:
            write_review_report(
                Path(args.review_report_output),
                result,
                provider=args.review_report_provider,
                ac_mapping=args.review_report_ac,
            )
            review_report_written = True
    log_path = append_daily_log(Path(args.daily_log_dir), result)

    if args.post_to_github:
        post_review_to_github(args.pr_number, build_review_markdown(result))

    print(f"review completed by {engine} (failover_from={failover_from})")
    print(f"result: {args.output}")
    if review_report_written:
        print(f"review report: {args.review_report_output}")
    print(f"daily log: {log_path}")
    blocking = blocking_issue_count(result)
    if blocking:
        print(f"blocking findings: {blocking}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
