#!/usr/bin/env python3
"""GitHub CI infra unavailable 時の代替 CI 証跡を生成する。"""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_ROOT_TEXT = str(ROOT)
if _ROOT_TEXT not in sys.path:
    sys.path.insert(0, _ROOT_TEXT)

from scripts.ai.collect_review_context import ReviewContextBudget, collect_review_context
from scripts.ai.review_report_gate import diff_fingerprint

SCHEMA_VERSION = "1.0"
DEFAULT_OUTPUT_JSON = Path("docs/ai/ci-fallback-review.json")
DEFAULT_OUTPUT_MD = Path("docs/ai/ci-fallback-review.md")
# accepted と auto-trigger は別契約。Codex は受理するが自動 executor へ追加しない。
DEFAULT_REVIEW_REPORT_ACCEPTED_PROVIDERS = "copilot,claude,codex"


def review_report_required_providers() -> str:
    configured = os.getenv("AI_REVIEW_ACCEPTED_PROVIDERS", "").strip()
    if not configured:
        configured = os.getenv("AI_REVIEW_REQUIRED_PROVIDERS", "").strip()
    return configured or DEFAULT_REVIEW_REPORT_ACCEPTED_PROVIDERS


@dataclass(frozen=True)
class FallbackCheck:
    name: str
    command: str
    exit_code: int
    duration_seconds: float
    log_excerpt: str
    tool_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_command(
    command: str,
    *,
    cwd: Path,
    timeout: int,
    dry_run: bool,
) -> FallbackCheck:
    started = time.monotonic()
    if dry_run:
        return FallbackCheck(
            name=command.split()[0] if command.split() else "unknown",
            command=command,
            exit_code=0,
            duration_seconds=0.0,
            log_excerpt="[dry-run] command not executed",
            tool_version="dry-run",
        )

    result = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    duration = time.monotonic() - started
    output = (result.stdout + "\n" + result.stderr).strip()
    return FallbackCheck(
        name=command.split()[0] if command.split() else "unknown",
        command=command,
        exit_code=result.returncode,
        duration_seconds=round(duration, 3),
        log_excerpt=output[-4000:],
        tool_version="not-collected",
    )


def _run_git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        msg = f"git {' '.join(args)} failed: {stderr}"
        raise RuntimeError(msg)
    return result.stdout.strip()


def resolve_sha(root: Path, ref_or_sha: str) -> str:
    if not ref_or_sha:
        return ""
    return _run_git(["rev-parse", "--verify", ref_or_sha], cwd=root)


def fetch_pr_metadata(repo: str, pr_number: int, root: Path) -> dict[str, str]:
    # runner の gh（2.45.0 相当）は `gh pr view --json baseRefOid` を
    # `Unknown JSON field` で拒否する（`baseRefOid` は gh 2.63.0 で追加・Issue #1023。
    # `headRefOid` は 2.45.0 にも存在する）。そのため REST API を `gh api` で直接叩く
    # （scripts/run_ai_review.py の collect_pr_context・.github/workflows/
    # ai-review-fallback.yml の Resolve PR metadata step と同型の脆弱性を同一手法で修正）。
    # 本経路は workflow から呼ばれず N-384 のローカル gate 専用だが、P-065 fix-on-discovery
    # により同一 PR で修正する（devcontainer の gh 2.92.0 では従来実装でも動作し実害は無い）。
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr_number}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        msg = f"gh api pulls failed: {stderr}"
        raise RuntimeError(msg)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = f"gh api pulls output JSON parse failed: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(data, dict):
        raise RuntimeError("gh api pulls output must be object")
    base_raw = data.get("base")
    head_raw = data.get("head")
    base_obj: dict[str, Any] = base_raw if isinstance(base_raw, dict) else {}
    head_obj: dict[str, Any] = head_raw if isinstance(head_raw, dict) else {}
    return {
        "base_ref": str(base_obj.get("ref") or ""),
        "head_ref": str(head_obj.get("ref") or ""),
        "base_sha": str(base_obj.get("sha") or ""),
        "head_sha": str(head_obj.get("sha") or ""),
    }


def fetch_pr_body(repo: str, pr_number: int, root: Path) -> str:
    result = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", "body"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        msg = f"gh pr view body failed: {stderr}"
        raise RuntimeError(msg)
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("gh pr view body output must be object")
    return str(data.get("body") or "")


def write_temp_pr_body(body: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix="-pr-body.md",
        delete=False,
    ) as handle:
        handle.write(body)
        return Path(handle.name)


def fallback_commands(
    *,
    base_sha: str,
    head_sha: str,
    pr_body_file: Path | None = None,
    require_pr_body: bool = True,
    trusted_gate_dir: Path | None = None,
    required_review_providers: str | None = None,
) -> list[tuple[str, str, int]]:
    if require_pr_body and pr_body_file is None:
        msg = "fallback CI の acceptance_audit には PR body が必要です。"
        raise ValueError(msg)

    base = shlex.quote(base_sha)
    head = shlex.quote(head_sha)
    root_arg = "--root ."
    acceptance_script = "scripts/ai/acceptance_audit.py"
    review_report_script = "scripts/ai/review_report_gate.py"
    if trusted_gate_dir is not None:
        acceptance_script = str(trusted_gate_dir / "scripts" / "ai" / "acceptance_audit.py")
        review_report_script = str(trusted_gate_dir / "scripts" / "ai" / "review_report_gate.py")
    # trusted base script は N-613 より前の版も実行するため、互換 alias を使う。
    review_provider_flag = (
        "--required-providers" if trusted_gate_dir is not None else "--accepted-providers"
    )
    acceptance_script = shlex.quote(acceptance_script)
    review_report_script = shlex.quote(review_report_script)
    review_providers = shlex.quote(required_review_providers or review_report_required_providers())
    acceptance_body_args = "--no-require-pr-body"
    if pr_body_file is not None:
        acceptance_body_args = f"--body-file {shlex.quote(str(pr_body_file))}"
    return [
        ("uv_sync", "uv sync --all-extras --dev --frozen --reinstall", 1800),
        ("policy_check", "python ci/policy_check.py", 300),
        ("ruff_check", "uv run ruff check .", 300),
        ("ruff_format", "uv run ruff format --check .", 300),
        ("mypy", "uv run mypy --no-incremental src/ tests/ scripts/ ci/", 900),
        ("pytest", "uv run pytest -q --tb=short", 1200),
        (
            "pip-audit",
            # --ignore-vuln CVE-2025-3000（torch.jit.script ローカルメモリ破損・severity LOW）:
            # 2026-06-10 の advisory 更新（GHSA-rrmf-rvhw-rf47 / PYSEC-2025-194）で torch 全リリース
            # （〜2.12.0）が affected・fix version なしとなり upgrade では解消不能。攻撃前提は
            # 細工された TorchScript のローカル読込であり、本リポジトリは自前生成モデルのみを
            # 扱うため脅威モデル外。ユーザー個別認可（2026-06-11・PR #515）で ignore。
            # 見直し条件: advisory に fixed version が追加されたら ignore を外し torch を更新。
            #
            # G3 掃除（BACKLOG-N545-CI-YML-PIP-AUDIT-SYNC / BACKLOG-N545-DEP-UPDATE・2026-07-10）:
            # aiohttp 3.14.0〜3.14.1 系（CVE-2026-54273〜54280）・cryptography 48.0.0 系
            # （GHSA-537c-gmf6-5ccf）・starlette 1.1.0 系（CVE-2026-54282/54283）の一時 ignore は、
            # 依存が既に修正済みバージョン（aiohttp 3.14.1・cryptography 49.0.0・starlette 1.3.1）
            # へ更新済みのため対象外＝除去した（現状照合は本 PR で pip-audit clean 再実測）。
            'bash -lc \'uv pip install "pip-audit==2.10.0" && '
            'uv pip freeze | grep -v "^\\\\-e" | grep -vi "english-learning-app" '
            "> /tmp/requirements-frozen.txt && "
            "uv run pip-audit -r /tmp/requirements-frozen.txt --strict --progress-spinner=off "
            "--ignore-vuln CVE-2025-3000'",
            600,
        ),
        (
            "gitleaks",
            "bash -lc '"
            "if ! command -v gitleaks >/dev/null 2>&1; then "
            'GITLEAKS_VERSION="8.30.1"; '
            'GITLEAKS_SHA256="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"; '
            "curl -sSfL -o /tmp/gitleaks.tar.gz "
            '"https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/'
            'gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"; '
            'echo "${GITLEAKS_SHA256}  /tmp/gitleaks.tar.gz" | sha256sum -c -; '
            "tar -xzf /tmp/gitleaks.tar.gz -C /tmp gitleaks; "
            "export PATH=/tmp:$PATH; "
            "fi; "
            "gitleaks version && "
            "gitleaks git --verbose --report-format=sarif --report-path=gitleaks-report.sarif ."
            "'",
            600,
        ),
        (
            "acceptance_audit",
            f"uv run python {acceptance_script} "
            f"{root_arg} --base-ref {base} --head-ref {head} "
            f"--allow-should "
            f"{acceptance_body_args}",
            300,
        ),
        (
            "review_report_gate",
            f"uv run python {review_report_script} "
            f"{root_arg} --base-ref {base} --head-ref {head} "
            f"--changed-only {review_provider_flag} {review_providers}",
            300,
        ),
    ]


def prepare_trusted_gate_scripts(
    *,
    root: Path,
    base_sha: str,
    allow_bootstrap_target_gate_scripts: bool,
) -> tuple[Path, str]:
    trusted_dir = Path(tempfile.mkdtemp(prefix="ci-fallback-trusted-gates-"))
    scripts = (
        "scripts/ai/acceptance_audit.py",
        "scripts/ai/review_report_gate.py",
    )
    source = f"trusted_base:{base_sha}"
    for rel_path in scripts:
        output = trusted_dir / rel_path
        output.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "show", f"{base_sha}:{rel_path}"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            output.write_text(result.stdout, encoding="utf-8")
            continue
        if not allow_bootstrap_target_gate_scripts:
            msg = (
                "trusted gate script が base に存在しません: "
                f"{rel_path}; bootstrap 例外なしでは fallback CI を実行できません。"
            )
            raise RuntimeError(msg)
        source = f"bootstrap_target:{base_sha}"
        output.write_text((root / rel_path).read_text(encoding="utf-8"), encoding="utf-8")
    return trusted_dir, source


def _check_to_named_dict(name: str, check: FallbackCheck) -> dict[str, Any]:
    data = check.to_dict()
    data["name"] = name
    return data


def build_fallback_report(
    *,
    root: Path,
    base_ref: str,
    head_ref: str,
    base_sha: str,
    head_sha: str,
    risk_tier: str,
    github_ci_unavailable_reason: str,
    repository_context_mode: str,
    context_budget_override_reason: str,
    dry_run: bool,
    pr_body_file: Path | None = None,
    require_pr_body: bool = True,
    human_override: bool = False,
    rollback_plan: str = "",
    allow_bootstrap_target_gate_scripts: bool = False,
) -> dict[str, Any]:
    if not base_sha or not head_sha:
        msg = "base_sha と head_sha は必須です。"
        raise ValueError(msg)
    current_head = resolve_sha(root, "HEAD")
    if current_head != head_sha:
        msg = (
            "fallback CI は PR head checkout 上で実行する必要があります。"
            f" current={current_head} expected={head_sha}"
        )
        raise ValueError(msg)

    merge_base = _run_git(["merge-base", base_sha, head_sha], cwd=root)
    diff_text = _run_git(["diff", "--binary", merge_base, head_sha], cwd=root)
    manifest = collect_review_context(
        repo_root=root,
        diff=diff_text,
        mode=repository_context_mode,
        budget=ReviewContextBudget.from_env(),
        context_budget_override_reason=context_budget_override_reason,
    )
    trusted_gate_dir, trusted_gate_source = prepare_trusted_gate_scripts(
        root=root,
        base_sha=base_sha,
        allow_bootstrap_target_gate_scripts=allow_bootstrap_target_gate_scripts,
    )
    checks: list[dict[str, Any]] = []
    for name, command, timeout in fallback_commands(
        base_sha=base_sha,
        head_sha=head_sha,
        pr_body_file=pr_body_file,
        require_pr_body=require_pr_body,
        trusted_gate_dir=trusted_gate_dir,
    ):
        check = _run_command(command, cwd=root, timeout=timeout, dry_run=dry_run)
        checks.append(_check_to_named_dict(name, check))

    checks_pass = all(check["exit_code"] == 0 for check in checks)
    red_blocked = risk_tier.lower() == "red" and (not human_override or not rollback_plan.strip())
    result = "pass" if checks_pass and not red_blocked else "fail"
    merge_policy_result = "fallback_allowed" if result == "pass" else "fallback_blocked"

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "fallback_ci_pass_as_equivalent" if result == "pass" else "failure",
        "base_ref": base_ref,
        "base_sha": base_sha,
        "head_ref": head_ref,
        "head_sha": head_sha,
        "diff_fingerprint": diff_fingerprint(root, base_sha, head_sha),
        "risk_tier": risk_tier,
        "github_ci_state": "github_ci_infra_unavailable",
        "github_ci_unavailable_reason": github_ci_unavailable_reason,
        "ai_review_provider": "claude",
        "ai_review_runtime": "repo-aware fallback manifest",
        "trusted_gate_source": trusted_gate_source,
        "pr_body_source": "provided" if pr_body_file is not None else "not_required",
        **manifest.to_dict(),
        "checks": checks,
        "result": result,
        "merge_policy_result": merge_policy_result,
        "human_override": human_override,
        "rollback_plan": rollback_plan,
        "generated_at": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
    }


def write_report(output_json: Path, output_md: Path, report: dict[str, Any]) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    checks = report.get("checks", [])
    failed = [
        check
        for check in checks
        if (
            isinstance(check, dict)
            and isinstance(check.get("exit_code"), int)
            and check["exit_code"]
        )
    ]
    lines = [
        "# CI fallback review",
        "",
        f"- mode: `{report.get('mode')}`",
        f"- result: `{report.get('result')}`",
        f"- base_sha: `{report.get('base_sha')}`",
        f"- head_sha: `{report.get('head_sha')}`",
        f"- diff_fingerprint: `{report.get('diff_fingerprint')}`",
        f"- repository_context_mode: `{report.get('repository_context_mode')}`",
        f"- context_fingerprint: `{report.get('context_fingerprint')}`",
        f"- checks: {len(checks)}",
        f"- failed_checks: {len(failed)}",
        "",
        "## Checks",
    ]
    for check in checks:
        if not isinstance(check, dict):
            continue
        check_name = check.get("name")
        exit_code = check.get("exit_code")
        command = check.get("command")
        lines.append(f"- `{check_name}` exit={exit_code} command=`{command}`")
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fallback CI review")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repo")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--head-ref", default="")
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--risk-tier", default="green")
    parser.add_argument("--github-ci-unavailable-reason", default="manual fallback")
    parser.add_argument(
        "--repository-context-mode",
        choices=["diff_only", "related_context", "full_repo_agentic"],
        default="related_context",
    )
    parser.add_argument("--context-budget-override-reason", default="")
    parser.add_argument("--pr-body-file", type=Path)
    parser.add_argument("--no-require-pr-body", action="store_true")
    parser.add_argument("--human-override", action="store_true")
    parser.add_argument("--allow-bootstrap-target-gate-scripts", action="store_true")
    parser.add_argument("--rollback-plan", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    args = parser.parse_args()

    root = args.root.resolve()
    base_ref = args.base_ref
    head_ref = args.head_ref
    base_sha = args.base_sha
    head_sha = args.head_sha
    if args.repo and args.pr and (not base_sha or not head_sha):
        metadata = fetch_pr_metadata(args.repo, args.pr, root)
        base_ref = base_ref or metadata["base_ref"]
        head_ref = head_ref or metadata["head_ref"]
        base_sha = base_sha or metadata["base_sha"]
        head_sha = head_sha or metadata["head_sha"]
    if base_ref and not base_sha:
        base_sha = resolve_sha(root, base_ref)
    if head_ref and not head_sha:
        head_sha = resolve_sha(root, head_ref)

    pr_body_file = args.pr_body_file
    if pr_body_file is None and args.repo and args.pr:
        pr_body_file = write_temp_pr_body(fetch_pr_body(args.repo, args.pr, root))

    report = build_fallback_report(
        root=root,
        base_ref=base_ref,
        head_ref=head_ref,
        base_sha=base_sha,
        head_sha=head_sha,
        risk_tier=args.risk_tier,
        github_ci_unavailable_reason=args.github_ci_unavailable_reason,
        repository_context_mode=args.repository_context_mode,
        context_budget_override_reason=args.context_budget_override_reason,
        pr_body_file=pr_body_file,
        require_pr_body=not args.no_require_pr_body,
        dry_run=args.dry_run,
        human_override=args.human_override,
        rollback_plan=args.rollback_plan,
        allow_bootstrap_target_gate_scripts=args.allow_bootstrap_target_gate_scripts,
    )

    output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
    output_md = args.output_md if args.output_md.is_absolute() else root / args.output_md
    write_report(output_json, output_md, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
