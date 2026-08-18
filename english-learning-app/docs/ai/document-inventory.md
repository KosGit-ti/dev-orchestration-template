# 文書棚卸し

`scripts/ai/audit_document_inventory.py` により生成。

| path | classification | action | reason | read_by_default |
| --- | --- | --- | --- | --- |
| .agents/skills/agmsg/ARCHITECTURE.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/agmsg/CHANGELOG.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/agmsg/PRIVACY.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/agmsg/README.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/agmsg/SKILL.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/agmsg/templates/cmd.antigravity.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/agmsg/templates/cmd.claude-code.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/agmsg/templates/cmd.codex.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/agmsg/templates/cmd.copilot.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/agmsg/templates/cmd.gemini.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/agmsg/templates/cmd.opencode.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/stop-ai-slop-jp/CHANGELOG.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/stop-ai-slop-jp/README.en.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/stop-ai-slop-jp/README.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/stop-ai-slop-jp/SKILL.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/stop-ai-slop-jp/references/examples.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/stop-ai-slop-jp/references/phrases.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .agents/skills/stop-ai-slop-jp/references/structures.md | AGENT_SPECIFIC | keep | Skills allowlist（共通正本） | no |
| .claude/agents/auditor-reliability.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .claude/agents/auditor-security.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .claude/agents/auditor-spec.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .claude/agents/implementer-single-file.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .claude/agents/implementer.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .claude/agents/orchestrator.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .claude/agents/pre-pr-critical-reviewer.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .claude/agents/release-manager.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .claude/agents/test-engineer.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .claude/output-styles/orchestrator-behavior.md | AGENT_SPECIFIC | keep | 行動仕様の Claude Code 写像（output style） | no |
| .github/PULL_REQUEST_TEMPLATE.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| .github/agents/auditor-reliability.agent.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .github/agents/auditor-security.agent.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .github/agents/auditor-spec.agent.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .github/agents/implementer-single-file.agent.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .github/agents/implementer.agent.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .github/agents/orchestrator.agent.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .github/agents/pre-pr-critical-reviewer.agent.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .github/agents/release-manager.agent.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .github/agents/test-engineer.agent.md | AGENT_SPECIFIC | keep | エージェント定義 | no |
| .github/copilot-code-review-instructions.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| .github/copilot-instructions.md | AGENT_SPECIFIC | revise | ツール別入口。薄く保つ | yes |
| .github/instructions/docs.instructions.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| .github/instructions/review-loop.instructions.md | AGENT_SPECIFIC | revise | ツール別入口。薄く保つ | yes |
| .github/instructions/security.instructions.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| .github/instructions/template-sync.instructions.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| .github/instructions/tests.instructions.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| .github/prompts/AUDIT_RELIABILITY.prompt.md | ARCHIVE | move_or_keep_out_of_default_context | 一回限りプロンプト素材 | no |
| .github/prompts/AUDIT_SECURITY.prompt.md | ARCHIVE | move_or_keep_out_of_default_context | 一回限りプロンプト素材 | no |
| .github/prompts/AUDIT_SPEC.prompt.md | ARCHIVE | move_or_keep_out_of_default_context | 一回限りプロンプト素材 | no |
| .github/prompts/EXECUTE.prompt.md | ARCHIVE | move_or_keep_out_of_default_context | 一回限りプロンプト素材 | no |
| .github/prompts/FINAL_REVIEW.prompt.md | ARCHIVE | move_or_keep_out_of_default_context | 一回限りプロンプト素材 | no |
| AGENTS.md | AGENT_SPECIFIC | revise | ツール別入口。薄く保つ | yes |
| CLAUDE.md | AGENT_SPECIFIC | revise | ツール別入口。薄く保つ | yes |
| ai/capability-registry.yml | CORE_CONTROL | keep | AI 制御面 | yes |
| ai/coherence-workflow.yml | CORE_CONTROL | keep | AI 制御面 | yes |
| ai/command-router.yml | CORE_CONTROL | keep | AI 制御面 | yes |
| ai/context-index.yml | CORE_CONTROL | keep | AI 制御面 | yes |
| ai/document-governance.yml | CORE_CONTROL | keep | AI 制御面 | yes |
| ai/operation-policy.yml | CORE_CONTROL | keep | AI 制御面 | yes |
| ai/pre-pr-review-policy.yml | CORE_CONTROL | keep | AI 制御面 | yes |
| ai/sdd-policy.yml | CORE_CONTROL | keep | AI 制御面 | yes |
| docs/GETTING_STARTED.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/MODE_GUIDE.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/TEMPLATE_CHANGELOG.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/UPGRADE_GUIDE.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/adr/ADR-TEMPLATE.md | REFERENCE | keep | 参照資料 | no |
| docs/agent-skills-integration.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/ai/change-log.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/ai/decision-ledger.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/ai/document-inventory.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/ai/execution-ledger.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/ai/expectation-ledger.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/ai/human-required.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/ai/operating-model.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/ai/shogun-decomposition-examples.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/ai/shogun-multi-session-protocol.md | DOMAIN_SSOT | keep | Shogun 複数セッション層プロトコル正本（任意層・単一セッションでは不使用） | no |
| docs/ai/shogun-operating-model.md | DOMAIN_SSOT | keep | Shogun 運用モデル正本（shogun_dispatch モードで読込） | no |
| docs/ai/shogun-safety-boundary.md | DOMAIN_SSOT | keep | Shogun 安全境界正本（既存安全床への参照のみ・shogun_dispatch モードで読込） | no |
| docs/architecture.md | DOMAIN_SSOT | keep | ドメイン正本 | no |
| docs/constraints.md | DOMAIN_SSOT | keep | ドメイン正本 | no |
| docs/hooks-guide.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/observability-guide.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/orchestration.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/plan.md | DOMAIN_SSOT | keep | ドメイン正本 | no |
| docs/policies.md | DOMAIN_SSOT | keep | ドメイン正本 | no |
| docs/quality-guide.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
| docs/requirements.md | DOMAIN_SSOT | keep | ドメイン正本 | no |
| docs/runbook.md | DOMAIN_SSOT | keep | ドメイン正本 | no |
| docs/security-policy-template.md | REFERENCE | review | 手動または AI による分類精査が必要 | no |
