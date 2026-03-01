"""OpenTelemetry 計装パッケージ。

OTel SDK 未インストール時は no-op として動作する。
"""

__all__: list[str] = [
    "init_tracer",
    "get_tracer",
    "trace_agent_operation",
    "trace_tool_execution",
    "trace_llm_call",
]

from observability.tracing import (
    get_tracer,
    init_tracer,
    trace_agent_operation,
    trace_llm_call,
    trace_tool_execution,
)
