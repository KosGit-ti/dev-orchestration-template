"""シェルコマンドからレビューループ関連の操作を検出する。"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterator

_CONTROL_OPERATORS: frozenset[str] = frozenset({"&&", "||", ";", "|", "&", "(", ")"})
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_COPILOT_REVIEWERS: frozenset[str] = frozenset(
    {
        "copilot-pull-request-reviewer",
        "copilot-pull-request-reviewer[bot]",
        "Copilot",
    }
)
_AI_REVIEW_REQUEST_COMMANDS: frozenset[str] = frozenset(
    {
        "request_ai_review",
        "request_copilot_review",
        "request_codex_review",
        "request_claude_review",
    }
)
_AI_REVIEW_MENTIONS: frozenset[str] = frozenset(
    {
        "@codex review",
        "@claude review",
    }
)
_GIT_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "-c",
        "-C",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--super-prefix",
        "--work-tree",
    }
)
_GH_OPTIONS_WITH_VALUE: frozenset[str] = frozenset({"-R", "--repo", "-h", "--hostname"})
_GH_API_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "-X",
        "--method",
        "-f",
        "--field",
        "-F",
        "--raw-field",
        "-H",
        "--header",
        "--hostname",
        "--input",
        "--jq",
        "--preview",
        "--template",
        "--cache",
    }
)
_GH_API_REVIEWER_REQUEST_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH"})
_GH_WORKFLOW_RUN_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {"-r", "--ref", "-f", "--field", "-F", "--raw-field"}
)
_AI_REVIEW_FALLBACK_WORKFLOWS: frozenset[str] = frozenset({"ai-review-fallback.yml"})
_SUDO_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "-C",
        "-D",
        "-g",
        "-h",
        "-p",
        "-r",
        "-t",
        "-u",
        "--chdir",
        "--close-from",
        "--group",
        "--host",
        "--prompt",
        "--role",
        "--type",
        "--user",
    }
)
_ENV_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {"-C", "-S", "-u", "--chdir", "--split-string", "--unset"}
)


class ReviewLoopCommandDetection(NamedTuple):
    """レビューループ関連操作の検出結果。"""

    is_git_push: bool
    is_reviewer_request: bool


def detect_review_loop_actions(command: str) -> ReviewLoopCommandDetection:
    """コマンド文字列から git push / reviewer request を検出する。"""
    if not command.strip():
        return ReviewLoopCommandDetection(False, False)

    is_git_push = False
    is_reviewer_request = False

    try:
        for segment in _iter_command_segments(command):
            normalized = _normalize_command(segment)
            if normalized is None:
                continue

            executable, args = normalized
            if executable == "git" and _git_subcommand(args) == "push":
                is_git_push = True
            elif executable in _AI_REVIEW_REQUEST_COMMANDS or (
                executable == "gh" and _is_gh_review_request(args)
            ):
                is_reviewer_request = True

            if is_git_push and is_reviewer_request:
                break
    except ValueError:
        return ReviewLoopCommandDetection(False, False)

    return ReviewLoopCommandDetection(is_git_push, is_reviewer_request)


def _iter_command_segments(command: str) -> Iterator[list[str]]:
    lexer = shlex.shlex(
        _normalize_command_separators(command),
        posix=True,
        punctuation_chars=";&|()",
    )
    lexer.whitespace_split = True
    lexer.commenters = ""

    current: list[str] = []
    for token in lexer:
        if token in _CONTROL_OPERATORS:
            if current:
                yield current
                current = []
            continue
        current.append(token)

    if current:
        yield current


def _normalize_command_separators(command: str) -> str:
    """非引用の改行を Bash のコマンド区切りとして扱う。"""
    normalized: list[str] = []
    quote: str | None = None
    escaped = False

    for char in command:
        if escaped:
            normalized.append(char)
            escaped = False
            continue
        if char == "\\":
            normalized.append(char)
            escaped = quote != "'"
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            normalized.append(char)
            continue
        if char in {"\n", "\r"} and quote is None:
            normalized.append(";")
            continue
        normalized.append(char)

    return "".join(normalized)


def _normalize_command(tokens: list[str]) -> tuple[str, list[str]] | None:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _is_assignment(token):
            index += 1
            continue

        executable = os.path.basename(token)
        if executable == "command":
            index = _skip_command_wrapper(tokens, index + 1)
            continue
        if executable == "sudo":
            index = _skip_wrapper_with_options(tokens, index + 1, _SUDO_OPTIONS_WITH_VALUE)
            continue
        if executable == "env":
            index = _skip_env_wrapper(tokens, index + 1)
            continue

        return executable, tokens[index + 1 :]

    return None


def _is_assignment(token: str) -> bool:
    return bool(_ASSIGNMENT_RE.fullmatch(token))


def _skip_command_wrapper(tokens: list[str], index: int) -> int:
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1
        if not token.startswith("-") or token == "-":
            return index
        index += 1
    return index


def _skip_wrapper_with_options(
    tokens: list[str],
    index: int,
    options_with_value: frozenset[str],
) -> int:
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1
        if not token.startswith("-") or token == "-":
            return index
        if token in options_with_value and index + 1 < len(tokens):
            index += 2
            continue
        if _option_with_inline_value(token, options_with_value):
            index += 1
            continue
        index += 1
    return index


def _skip_env_wrapper(tokens: list[str], index: int) -> int:
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1
        if _is_assignment(token):
            index += 1
            continue
        if not token.startswith("-") or token == "-":
            return index
        if token in _ENV_OPTIONS_WITH_VALUE and index + 1 < len(tokens):
            index += 2
            continue
        if _option_with_inline_value(token, _ENV_OPTIONS_WITH_VALUE):
            index += 1
            continue
        index += 1
    return index


def _option_with_inline_value(token: str, options_with_value: frozenset[str]) -> bool:
    return any(
        token.startswith(f"{option}=") for option in options_with_value if option.startswith("--")
    )


def _git_subcommand(args: list[str]) -> str | None:
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            break
        if not token.startswith("-") or token == "-":
            return token
        if token in _GIT_OPTIONS_WITH_VALUE and index + 1 < len(args):
            index += 2
            continue
        if _option_with_inline_value(token, _GIT_OPTIONS_WITH_VALUE):
            index += 1
            continue
        index += 1
    if index < len(args):
        return args[index]
    return None


def _is_gh_review_request(args: list[str]) -> bool:
    subcommand_tokens = _skip_gh_global_options(args)
    if not subcommand_tokens:
        return False

    if subcommand_tokens[0] == "api":
        return _is_gh_api_reviewer_request(subcommand_tokens[1:])

    if (
        len(subcommand_tokens) >= 2
        and subcommand_tokens[0] == "workflow"
        and subcommand_tokens[1] == "run"
    ):
        return _gh_workflow_run_requests_ai_review(subcommand_tokens[2:])

    if (
        len(subcommand_tokens) >= 2
        and subcommand_tokens[0] == "pr"
        and subcommand_tokens[1] == "edit"
    ):
        return _gh_pr_edit_adds_copilot_reviewer(subcommand_tokens[2:])

    if (
        len(subcommand_tokens) >= 2
        and subcommand_tokens[0] == "pr"
        and subcommand_tokens[1] == "comment"
    ):
        return _gh_pr_comment_requests_ai_review(subcommand_tokens[2:])

    return False


def _is_gh_api_reviewer_request(args: list[str]) -> bool:
    method = _gh_api_method(args)
    endpoint = _gh_api_endpoint(args)
    return method in _GH_API_REVIEWER_REQUEST_METHODS and _is_requested_reviewers_endpoint(endpoint)


def _gh_api_method(args: list[str]) -> str:
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"-X", "--method"}:
            if index + 1 < len(args):
                return args[index + 1].upper()
            return "GET"
        if token.startswith("--method="):
            return token.partition("=")[2].upper()
        if token.startswith("-X") and len(token) > 2:
            return token[2:].upper()
        index += 1
    return "GET"


def _gh_api_endpoint(args: list[str]) -> str | None:
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            if index + 1 < len(args):
                return args[index + 1]
            return None
        if not token.startswith("-") or token == "-":
            return token
        if token in _GH_API_OPTIONS_WITH_VALUE and index + 1 < len(args):
            index += 2
            continue
        if _option_with_inline_value(token, _GH_API_OPTIONS_WITH_VALUE):
            index += 1
            continue
        if token.startswith("-X") and len(token) > 2:
            index += 1
            continue
        index += 1
    return None


def _is_requested_reviewers_endpoint(endpoint: str | None) -> bool:
    if endpoint is None:
        return False
    path = endpoint.partition("?")[0].strip("/")
    return path == "requested_reviewers" or path.endswith("/requested_reviewers")


def _gh_workflow_run_requests_ai_review(args: list[str]) -> bool:
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            continue
        if not token.startswith("-") or token == "-":
            return os.path.basename(token).lower() in _AI_REVIEW_FALLBACK_WORKFLOWS
        if token in _GH_WORKFLOW_RUN_OPTIONS_WITH_VALUE and index + 1 < len(args):
            index += 2
            continue
        if _option_with_inline_value(token, _GH_WORKFLOW_RUN_OPTIONS_WITH_VALUE):
            index += 1
            continue
        index += 1
    return False


def _skip_gh_global_options(args: list[str]) -> list[str]:
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            return args[index + 1 :]
        if not token.startswith("-") or token == "-":
            return args[index:]
        if token in _GH_OPTIONS_WITH_VALUE and index + 1 < len(args):
            index += 2
            continue
        if _option_with_inline_value(token, _GH_OPTIONS_WITH_VALUE):
            index += 1
            continue
        index += 1
    return []


def _gh_pr_edit_adds_copilot_reviewer(args: list[str]) -> bool:
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--add-reviewer":
            if index + 1 < len(args) and _contains_copilot_reviewer(args[index + 1]):
                return True
            index += 2
            continue
        if token.startswith("--add-reviewer=") and _contains_copilot_reviewer(
            token.partition("=")[2]
        ):
            return True
        index += 1
    return False


def _contains_copilot_reviewer(value: str) -> bool:
    return any(part.strip() in _COPILOT_REVIEWERS for part in value.split(","))


def _gh_pr_comment_requests_ai_review(args: list[str]) -> bool:
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"--body", "-b"}:
            if index + 1 < len(args) and _contains_ai_review_mention(args[index + 1]):
                return True
            index += 2
            continue
        if token.startswith("--body=") and _contains_ai_review_mention(token.partition("=")[2]):
            return True
        if token in {"--body-file", "-F"}:
            if index + 1 < len(args) and _body_file_contains_ai_review_mention(args[index + 1]):
                return True
            index += 2
            continue
        if token.startswith("--body-file=") and _body_file_contains_ai_review_mention(
            token.partition("=")[2]
        ):
            return True
        index += 1
    return False


def _contains_ai_review_mention(value: str) -> bool:
    normalized = " ".join(value.strip().lower().split())
    return any(marker in normalized for marker in _AI_REVIEW_MENTIONS)


def _body_file_contains_ai_review_mention(path_text: str) -> bool:
    if path_text == "-":
        return False
    try:
        body = Path(path_text).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _contains_ai_review_mention(body)
