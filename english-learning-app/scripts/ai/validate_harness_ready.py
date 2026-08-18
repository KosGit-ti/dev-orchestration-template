#!/usr/bin/env python3
"""Provider-native AI Operating Model の準備状態を検証する。"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = [
    "ai/command-router.yml",
    "ai/capability-registry.yml",
    "ai/operation-policy.yml",
    "ai/context-index.yml",
    "ai/document-governance.yml",
    "ai/coherence-workflow.yml",
    "ai/sdd-policy.yml",
    "ai/pre-pr-review-policy.yml",
    "docs/ai/operating-model.md",
    "docs/ai/expectation-ledger.md",
    "docs/ai/execution-ledger.md",
    "docs/ai/decision-ledger.md",
    "docs/ai/human-required.md",
    "docs/specs/_template.md",
    "docs/lexicon/project-lexicon.yml",
    "scripts/ai/audit_document_inventory.py",
    "scripts/ai/validate_context_index.py",
    "scripts/ai/run_pre_pr_critical_review.py",
    "scripts/ai/acceptance_audit.py",
    "scripts/ai/review_report_gate.py",
    "scripts/ai/ci_final_gate.py",
    "scripts/ai/run_ci_fallback_review.py",
    "scripts/ai/codex_review_command.py",
    "scripts/ai/collect_review_context.py",
    ".github/workflows/ai-review-fallback.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/ci-final-gate.yml",
    "docs/ai/copilot-independent-review.md",
    "docs/ai/review-result.schema.json",
    "prompts/ai/repo-aware-review.md",
    ".github/CODEOWNERS",
    ".github/full-plan-execution.flag.example",
    "scripts/hooks/full_plan_completion.py",
    ".agents/skills/test-change/SKILL.md",
    ".agents/skills/review-change/SKILL.md",
    ".agents/skills/release-judgement/SKILL.md",
    ".claude/skills/test-change/SKILL.md",
    ".claude/skills/review-change/SKILL.md",
    ".claude/skills/release-judgement/SKILL.md",
]

CORE_POLICY_FORBIDDEN_TERMS = ["直近1か月の予測", "N-366-dashboard"]

CANONICAL_SKILLS = {
    "test-change": ".agents/skills/test-change/SKILL.md",
    "review-change": ".agents/skills/review-change/SKILL.md",
    "release-judgement": ".agents/skills/release-judgement/SKILL.md",
}

CLAUDE_SKILL_ADAPTERS = {name: f".claude/skills/{name}/SKILL.md" for name in CANONICAL_SKILLS}

LEGACY_ROSTER_PATHS = (
    ".github/agents/orchestrator.agent.md",
    ".github/agents/implementer.agent.md",
    ".github/agents/implementer-single-file.agent.md",
    ".github/agents/test-engineer.agent.md",
    ".github/agents/auditor-spec.agent.md",
    ".github/agents/auditor-security.agent.md",
    ".github/agents/auditor-reliability.agent.md",
    ".github/agents/release-manager.agent.md",
    ".github/agents/pre-pr-critical-reviewer.agent.md",
    "agents/orchestrator.agent.md",
    "agents/implementer.agent.md",
    "agents/implementer_single_file.agent.md",
    "agents/test_engineer.agent.md",
    "agents/auditor_spec.agent.md",
    "agents/auditor_security.agent.md",
    "agents/auditor_reliability.agent.md",
    "agents/release_manager.agent.md",
    "agents/pre_pr_critical_reviewer.agent.md",
    ".claude/agents/orchestrator.md",
    ".claude/agents/implementer.md",
    ".claude/agents/implementer-single-file.md",
    ".claude/agents/test-engineer.md",
    ".claude/agents/auditor-spec.md",
    ".claude/agents/auditor-security.md",
    ".claude/agents/auditor-reliability.md",
    ".claude/agents/release-manager.md",
    ".claude/agents/pre-pr-critical-reviewer.md",
)

ENTRYPOINTS = ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md")
ENTRYPOINT_MARKERS = (
    "ai/operation-policy.yml",
    "ai/capability-registry.yml",
    "reporter_communication",
    "subagent_delegation",
    "full_plan_delivery_pipeline",
    "docs/orchestration.md",
)

ORCHESTRATION_SCHEMA_FIELDS = (
    "`task_id`",
    "`status`",
    "`summary`",
    "`changed_files[]`",
    "`evidence[]`",
    "`next_actions[]`",
    "`risks[]`",
)

FULL_PLAN_EXECUTION_REQUIRED_STEPS = (
    "load_context_index",
    "load_execution_ledger",
    "select_current_queue_head",
    "load_task_spec",
    "reference_requirements_design_spec_completion_conditions",
    "verify_requirements_design_spec_completion_conditions_alignment",
    "implement",
    "run_targeted_tests",
    "run_static_checks",
    "propagate_to_related_docs_and_types",
    "run_runtime_smoke",
    "run_pre_pr_critical_review",
    "verify_completion_conditions_via_critical_review_and_independent_peer",
    "record_evidence",
    "update_execution_ledger",
    "classify_release_if_needed",
    "commit_changes",
    "push_branch",
    "create_pr_with_template",
    "request_ai_review_or_repo_aware_fallback",
    "monitor_ci_final_gate",
    "address_ci_and_review_findings",
    "record_and_run_eligible_review_extension_before_terminal_anchor",
    "commit_and_push_review_evidence_only_with_rebase_anchor",
    "monitor_final_anchor_head_ci_and_review_report",
    "verify_zero_unresolved_or_unanswered_review_threads",
    "run_release_judgement",
    "validate_rebase_merge_contract",
    "merge_pr_when_full_plan_mode",
    "fetch_and_verify_actual_merge_terminal",
    "pull_main_after_merge",
    "create_next_task_or_terminal_state_sync_branch",
    "write_task_checkpoint_after_merge",
    "update_execution_ledger_after_merge",
    "update_plan_after_merge",
    "validate_current_state_before_next_task_implementation",
    "proceed_to_next_task",
)

FULL_PLAN_COMPLETION_GATE_MARKERS = (
    "pr_required_before_task_done",
    "push_review_loop_required",
    "ci_final_gate_required",
    "release_judgement_required",
    "full_plan_mode_requires_merge_before_next_task",
)

FULL_PLAN_DELIVERY_STATE_FLAGS = (
    "changes_committed_and_pushed",
    "pr_created",
    "push_review_loop_completed",
    "review_evidence_anchor_pushed",
    "ci_final_gate_passed",
    "zero_unresolved_or_unanswered_threads",
    "release_manager_approved",
    "rebase_merge_contract_validated",
    "merged_to_main",
    "actual_merge_terminal_verified",
    "main_pulled_after_merge",
    "plan_updated_after_merge",
    "execution_ledger_updated_after_merge",
    "state_sync_completed_from_actual_merge_terminal",
)

FULL_PLAN_REVIEW_LOOP_STATE_DEFAULTS: dict[str, object] = {
    "default_rounds": 3,
    "current_round": 0,
    "extension_active": False,
    "extension_authorization_ref": None,
    "extension_records": [],
}

TERMINAL_STATE_SYNC_REQUIRED_CONTRACT_VALUES = {
    "reject_partial_signal": True,
    "review_fix_strategy": "許可path内の追加sync commit。squash/force-push不要",
}


def _read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _is_ordered_subsequence(expected: tuple[str, ...], actual: list[object]) -> bool:
    """expected が actual 内に同順で含まれるか検査する。"""
    start = 0
    actual_values = [str(item) for item in actual]
    for item in expected:
        try:
            found = actual_values.index(item, start)
        except ValueError:
            return False
        start = found + 1
    return True


def _extract_string_tuple_constant(path: Path, name: str) -> tuple[str, ...] | None:
    """Python source の top-level string sequence 定数を副作用なしで抽出する。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            target_name = target.id if isinstance(target, ast.Name) else None
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target_name = node.target.id if isinstance(node.target, ast.Name) else None
            value = node.value
        if target_name != name or value is None:
            continue
        try:
            raw = ast.literal_eval(value)
        except (ValueError, TypeError):
            return None
        if isinstance(raw, (tuple, list)) and all(isinstance(item, str) for item in raw):
            return tuple(raw)
        return None
    return None


def _extract_string_constant(path: Path, name: str) -> str | None:
    """Python source の top-level string 定数を副作用なしで抽出する。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            target_name = target.id if isinstance(target, ast.Name) else None
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target_name = node.target.id if isinstance(node.target, ast.Name) else None
            value = node.value
        if target_name != name or value is None:
            continue
        try:
            raw = ast.literal_eval(value)
        except (ValueError, TypeError):
            return None
        return raw if isinstance(raw, str) else None
    return None


def validate_core_files() -> list[str]:
    """AI Operating Model の必須ファイルと禁止語を検査する。"""
    issues: list[str] = []
    missing = [p for p in REQUIRED if not (ROOT / p).exists()]
    if missing:
        issues.append("必須ファイルが存在しません: " + ", ".join(missing))
        return issues

    control_text = "\n".join(
        _read_text(p) for p in REQUIRED if p.startswith("ai/") and (ROOT / p).exists()
    )
    hits = [s for s in CORE_POLICY_FORBIDDEN_TERMS if s in control_text]
    if hits:
        issues.append("core control files に特定機能の語彙があります: " + ", ".join(hits))
    return issues


def validate_harness_symmetry() -> list[str]:
    """Provider-native skill、入口、provider role の整合を検査する。

    関数名は既存 CI / test の互換性のため維持する。N-613 以後は同一 roster ではなく、
    provider-neutral capability と harness adapter の整合を意味する。
    """
    issues: list[str] = []
    direct_read_paths = [
        "docs/orchestration.md",
        "ai/operation-policy.yml",
        "ai/capability-registry.yml",
        *ENTRYPOINTS,
        *CANONICAL_SKILLS.values(),
        *CLAUDE_SKILL_ADAPTERS.values(),
    ]
    missing_direct_reads = [path for path in direct_read_paths if not (ROOT / path).exists()]
    if missing_direct_reads:
        issues.append("必須ファイルが存在しません: " + ", ".join(missing_direct_reads))
        return issues

    for legacy_path in LEGACY_ROSTER_PATHS:
        if (ROOT / legacy_path).exists():
            issues.append(f"廃止済み fixed roster が残っています: {legacy_path}")

    for name, canonical_path in CANONICAL_SKILLS.items():
        canonical_text = _read_text(canonical_path)
        if not canonical_text.startswith("---\n"):
            issues.append(f"{canonical_path} に YAML frontmatter がありません")
        if f"name: {name}" not in canonical_text:
            issues.append(f"{canonical_path} の name が {name} ではありません")

        adapter_path = CLAUDE_SKILL_ADAPTERS[name]
        adapter_text = _read_text(adapter_path)
        expected_reference = f"../../../{canonical_path}"
        if expected_reference not in adapter_text:
            issues.append(
                f"{adapter_path} が canonical skill {expected_reference} を参照していません"
            )
        if "model:" in adapter_text:
            issues.append(f"{adapter_path} に model 固定が残っています")

    operation_policy = _read_text("ai/operation-policy.yml")
    for marker in (
        "reporter_communication:",
        "subagent_delegation:",
        "full_plan_delivery_pipeline:",
    ):
        if marker not in operation_policy:
            issues.append(f"ai/operation-policy.yml に {marker} がありません")

    orchestration = _read_text("docs/orchestration.md")
    if "## 応答契約" not in orchestration:
        issues.append("docs/orchestration.md に応答契約がありません")
    for field in ORCHESTRATION_SCHEMA_FIELDS:
        if field not in orchestration:
            issues.append(f"docs/orchestration.md の応答契約に {field} がありません")

    registry = yaml.safe_load(_read_text("ai/capability-registry.yml")) or {}
    provider_roles = registry.get("provider_roles")
    if not isinstance(provider_roles, dict):
        issues.append("ai/capability-registry.yml に provider_roles がありません")
    else:
        if provider_roles.get("implementation_harnesses") != ["claude_code", "codex"]:
            issues.append("implementation_harnesses が claude_code / codex ではありません")
        copilot = provider_roles.get("copilot")
        if not isinstance(copilot, dict) or copilot.get("roles") != ["pr_review"]:
            issues.append("Copilot の role が pr_review に限定されていません")
        review = provider_roles.get("review")
        if not isinstance(review, dict):
            issues.append("provider_roles.review がありません")
        else:
            if review.get("accepted_review_providers") != ["copilot", "claude", "codex"]:
                issues.append(
                    "accepted_review_providers が copilot / claude / codex ではありません"
                )
            if review.get("auto_trigger_pr_review_providers") != ["copilot"]:
                issues.append("auto_trigger_pr_review_providers が Copilot だけではありません")
            if review.get("fallback_review_providers") != ["claude"]:
                issues.append("fallback_review_providers が Claude だけではありません")

    for path in ENTRYPOINTS:
        text = _read_text(path)
        for marker in ENTRYPOINT_MARKERS:
            if marker not in text:
                issues.append(f"{path} が端的報告/委譲正本を参照していません: {marker}")

    return issues


def validate_review_provider_routing(root: Path = ROOT) -> list[str]:
    """provider registry と実 executor / gate / workflow の経路を照合する。"""
    issues: list[str] = []
    registry_path = root / "ai" / "capability-registry.yml"
    try:
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        review = registry["provider_roles"]["review"]
        accepted = tuple(review["accepted_review_providers"])
        auto_trigger = tuple(review["auto_trigger_pr_review_providers"])
        fallback = tuple(review["fallback_review_providers"])
    except (OSError, KeyError, TypeError, UnicodeDecodeError, yaml.YAMLError):
        return ["provider routing の正本を解析できません"]

    expected_accepted = ("copilot", "claude", "codex")
    if accepted != expected_accepted:
        issues.append("runtime routing の accepted provider 正本が不正です")
    expected_executor_order = auto_trigger + fallback
    engine_order = _extract_string_tuple_constant(
        root / "scripts" / "run_ai_review.py", "ENGINE_ORDER"
    )
    if engine_order != expected_executor_order:
        issues.append(
            "scripts/run_ai_review.py の ENGINE_ORDER が auto-trigger / fallback と不一致です"
        )
    supported_engines = _extract_string_tuple_constant(
        root / "scripts" / "run_ai_review.py", "SUPPORTED_REVIEW_ENGINES"
    )
    if supported_engines != accepted:
        issues.append(
            "scripts/run_ai_review.py の明示 engine 集合が accepted provider と不一致です"
        )

    expected_csv = ",".join(accepted)
    gate_default = _extract_string_constant(
        root / "scripts" / "ai" / "review_report_gate.py",
        "DEFAULT_ACCEPTED_REVIEW_PROVIDERS",
    )
    if gate_default != expected_csv:
        issues.append("review_report_gate の accepted provider 既定値が正本と不一致です")

    fallback_default = _extract_string_constant(
        root / "scripts" / "ai" / "run_ci_fallback_review.py",
        "DEFAULT_REVIEW_REPORT_ACCEPTED_PROVIDERS",
    )
    if fallback_default != expected_csv:
        issues.append("run_ci_fallback_review の accepted provider 既定値が正本と不一致です")

    workflow_path = root / ".github" / "workflows" / "ci.yml"
    try:
        workflow = workflow_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        issues.append("ci.yml の review provider 設定を読み込めません")
    else:
        if "vars.AI_REVIEW_ACCEPTED_PROVIDERS" not in workflow:
            issues.append("ci.yml が AI_REVIEW_ACCEPTED_PROVIDERS を参照していません")
        if f"'{expected_csv}'" not in workflow:
            issues.append("ci.yml の accepted provider fallback が正本と不一致です")

    return issues


def validate_full_plan_delivery_workflow() -> list[str]:
    """execute_current_queue が PR delivery loop まで含むことを検査する。"""
    issues: list[str] = []

    workflow_path = ROOT / "ai" / "coherence-workflow.yml"
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}
    workflows = data.get("workflows")
    if not isinstance(workflows, dict):
        return ["ai/coherence-workflow.yml に workflows がありません"]
    execute_workflow = workflows.get("execute_current_queue")
    if not isinstance(execute_workflow, dict):
        return ["ai/coherence-workflow.yml に execute_current_queue がありません"]

    steps = execute_workflow.get("steps")
    if not isinstance(steps, list):
        issues.append("execute_current_queue.steps が list ではありません")
    elif not _is_ordered_subsequence(FULL_PLAN_EXECUTION_REQUIRED_STEPS, steps):
        missing_steps = [step for step in FULL_PLAN_EXECUTION_REQUIRED_STEPS if step not in steps]
        issues.append(
            "execute_current_queue.steps に full-plan delivery loop が不足しています: "
            + ", ".join(missing_steps)
        )

    completion_gate = execute_workflow.get("completion_gate")
    if not isinstance(completion_gate, dict):
        issues.append("execute_current_queue.completion_gate がありません")
    else:
        for marker in FULL_PLAN_COMPLETION_GATE_MARKERS:
            if completion_gate.get(marker) is not True:
                issues.append(
                    f"execute_current_queue.completion_gate.{marker} が true ではありません"
                )

    delivery_loop = execute_workflow.get("full_plan_delivery_loop")
    if not isinstance(delivery_loop, dict):
        issues.append("execute_current_queue.full_plan_delivery_loop がありません")
    else:
        if delivery_loop.get("pr_body_source") != ".github/PULL_REQUEST_TEMPLATE.md":
            issues.append("full_plan_delivery_loop.pr_body_source が PR template を指していません")
        if (
            delivery_loop.get("review_loop_source")
            != ".github/instructions/review-loop.instructions.md"
        ):
            issues.append(
                "full_plan_delivery_loop.review_loop_source が "
                "review-loop instructions を指していません"
            )
        if delivery_loop.get("merge_after_release_judgement_approval") is not True:
            issues.append(
                "full_plan_delivery_loop.merge_after_release_judgement_approval が "
                "true ではありません"
            )
        rebase_contract = delivery_loop.get("rebase_merge_contract")
        if not isinstance(rebase_contract, dict):
            issues.append("full_plan_delivery_loop.rebase_merge_contract がありません")
        else:
            required_contract_values = {
                "anchor_commit_allowed_paths": "docs/ai/reviews/*.json only",
                "anchor_evidence_requirement": "final treeで通常file・非空・JSON parse可能",
                "required_base_ref": "main",
                "require_first_commit_parent_equals_base_ref_oid": True,
                "require_local_contiguous_one_parent_nonempty_chain": True,
                "require_main_status_checks_strict": True,
            }
            for key, expected in required_contract_values.items():
                if rebase_contract.get(key) != expected:
                    issues.append(
                        "full_plan_delivery_loop.rebase_merge_contract."
                        f"{key} が {expected!r} ではありません"
                    )
            terminal_contract = rebase_contract.get("terminal_state_sync")
            if not isinstance(terminal_contract, dict):
                issues.append(
                    "full_plan_delivery_loop.rebase_merge_contract.terminal_state_sync がありません"
                )
            else:
                for key, expected in TERMINAL_STATE_SYNC_REQUIRED_CONTRACT_VALUES.items():
                    if terminal_contract.get(key) != expected:
                        issues.append(
                            "full_plan_delivery_loop.rebase_merge_contract."
                            f"terminal_state_sync.{key} が {expected!r} ではありません"
                        )

    flag_example_path = ROOT / ".github" / "full-plan-execution.flag.example"
    try:
        flag_example = json.loads(flag_example_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(f"full-plan execution flag example を解析できません: {exc}")
    else:
        delivery = flag_example.get("delivery") if isinstance(flag_example, dict) else None
        if not isinstance(delivery, dict):
            issues.append("full-plan execution flag example に delivery object がありません")
        else:
            for field in FULL_PLAN_DELIVERY_STATE_FLAGS:
                if not isinstance(delivery.get(field), bool):
                    issues.append(
                        "full-plan execution flag example の delivery."
                        f"{field} が bool ではありません"
                    )

        review_loop = flag_example.get("review_loop") if isinstance(flag_example, dict) else None
        if not isinstance(review_loop, dict):
            issues.append("full-plan execution flag example に review_loop object がありません")
        else:
            for field, expected in FULL_PLAN_REVIEW_LOOP_STATE_DEFAULTS.items():
                value = review_loop.get(field)
                if value != expected or type(value) is not type(expected):
                    issues.append(
                        "full-plan execution flag example の review_loop."
                        f"{field} が既定値 {expected!r} ではありません"
                    )

    hook_flags = _extract_string_tuple_constant(
        ROOT / "scripts" / "hooks" / "full_plan_completion.py",
        "REQUIRED_DELIVERY_STATE_FLAGS",
    )
    if hook_flags is None:
        issues.append("full_plan_completion の delivery flag 定数を解析できません")
    elif hook_flags != FULL_PLAN_DELIVERY_STATE_FLAGS:
        issues.append(
            "full_plan_completion と harness validator の delivery flag 順序・集合が不一致です"
        )

    return issues


# NFR-072: active policy / entrypoint / skill に provider 固有 model 名を書かない。
MODEL_NAME_BAN_STATIC_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    ".github/copilot-instructions.md",
    "scripts/run_ai_review.py",
    "scripts/ai/codex_review_command.py",
    "docs/orchestration.md",
)

FORBIDDEN_MODEL_SELECTOR_KEYS = frozenset(
    {
        "model",
        "model_id",
        "model_name",
        "model_selector",
        "review_model",
        "default_subagent_model",
        "selector",
        "current_resolution",
    }
)

CONFIG_MODEL_SELECTOR_RE = re.compile(
    r"(?:^|\s)(?:--config|-c)(?:=|\s+)\s*['\"]?model\s*=",
    re.IGNORECASE,
)
MODEL_ASSIGNMENT_ARG_RE = re.compile(r"^\s*model\s*=", re.IGNORECASE)


def _contains_forbidden_model_selector(value: object) -> bool:
    """YAML mapping に固定 model 解決用の構造があるかを返す。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in FORBIDDEN_MODEL_SELECTOR_KEYS:
                return True
            if _contains_forbidden_model_selector(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_model_selector(item) for item in value)
    return False


def _markdown_has_forbidden_model_selector(text: str) -> bool:
    """Markdown frontmatter または command の固定 model selector を検出する。"""
    if text.startswith("---\n"):
        closing = text.find("\n---\n", 4)
        if closing >= 0:
            try:
                metadata = yaml.safe_load(text[4:closing]) or {}
            except yaml.YAMLError:
                return True
            if _contains_forbidden_model_selector(metadata):
                return True
    if re.search(r"(?:^|\s)--model(?:=|\s+)[^\s`]+", text) is not None:
        return True
    return CONFIG_MODEL_SELECTOR_RE.search(text) is not None


def _python_has_forbidden_model_selector(text: str) -> bool:
    """Python の固定 model 定数または CLI model selector を検出する。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literal = node.value
            if literal == "--model" or literal.startswith("--model="):
                return True
            if CONFIG_MODEL_SELECTOR_RE.search(literal):
                return True
            # subprocess argv の `--config`, `model=...` / `-c`, `model=...`
            # という分割表現も、model 名の語彙を列挙せず検出する。
            if MODEL_ASSIGNMENT_ARG_RE.match(literal):
                return True
        targets: list[ast.expr]
        assigned_value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets = node.targets
            assigned_value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            assigned_value = node.value
        else:
            continue
        if not isinstance(assigned_value, ast.Constant) or not isinstance(
            assigned_value.value, str
        ):
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            normalized = target.id.lower()
            if normalized in FORBIDDEN_MODEL_SELECTOR_KEYS or normalized.endswith("_model"):
                return True
    return False


def _active_file_has_forbidden_model_selector(path: Path) -> bool:
    """拡張子に応じ、名前列挙に依存せず model selector を検査する。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yml", ".yaml"}:
        try:
            return _contains_forbidden_model_selector(yaml.safe_load(text) or {})
        except yaml.YAMLError:
            return True
    if path.suffix == ".py":
        return _python_has_forbidden_model_selector(text)
    if path.suffix == ".md":
        return _markdown_has_forbidden_model_selector(text)
    return False


def _iter_model_name_ban_files(root: Path) -> tuple[Path, ...]:
    """Active policy / entrypoint / repo 管理 skill を漏れなく列挙する。"""
    paths = {root / rel for rel in MODEL_NAME_BAN_STATIC_FILES}
    paths.update(root.glob("ai/*.yml"))
    paths.update(root.glob("ai/*.yaml"))
    for skill_root in (root / ".agents" / "skills", root / ".claude" / "skills"):
        if skill_root.exists():
            paths.update(skill_root.glob("*/SKILL.md"))
    return tuple(
        sorted((path for path in paths if path.is_file()), key=lambda path: path.as_posix())
    )


def validate_model_tier_consistency(root: Path = ROOT) -> list[str]:
    """Model abstraction と旧 model 更新経路の不在を検証する。

    関数名は CI / test の互換性のため維持する。N-613 以後は tier literal の一致ではなく、
    active policy への固定 model selector 再混入を、将来の model 名を列挙せず拒否する。
    """
    issues: list[str] = []
    config_path = root / "project-config.yml"
    if not config_path.exists():
        return ["project-config.yml が存在しません"]
    project_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if "ai_models" in project_config:
        issues.append("project-config.yml に廃止済み ai_models が残っています")

    if (root / "scripts/update_agent_models.sh").exists():
        issues.append("廃止済み scripts/update_agent_models.sh が残っています")

    for rel in LEGACY_ROSTER_PATHS:
        if (root / rel).exists():
            issues.append(f"廃止済み fixed roster が残っています: {rel}")

    for path in _iter_model_name_ban_files(root):
        rel = path.relative_to(root).as_posix()
        if _active_file_has_forbidden_model_selector(path):
            issues.append(
                f"{rel} に固定 model selector が残っています"
                "（NFR-072: active model abstraction 違反）"
            )
    return issues


def main() -> int:
    issues = validate_core_files()
    if not issues:
        issues.extend(validate_harness_symmetry())
        issues.extend(validate_review_provider_routing())
        issues.extend(validate_full_plan_delivery_workflow())
        issues.extend(validate_model_tier_consistency())
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1

    print("AI operating model ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
