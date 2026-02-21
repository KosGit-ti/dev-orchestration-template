"""OpenTelemetry GenAI Semantic Conventions に準拠した計装モジュール。

このモジュールをプロジェクトの LLM 呼び出し・ツール実行・エージェント操作に
適用することで、マルチエージェントワークフロー全体のトレースを取得できる。

OTel SDK がインストールされていない場合、全デコレータはパススルー（no-op）
として動作し、既存コードに影響を与えない。

注意: OpenTelemetry GenAI Semantic Conventions は 2026年2月時点で
"Development" ステータスであり、属性名が将来変更される可能性がある。
最新仕様は以下を参照すること:
https://opentelemetry.io/docs/specs/semconv/gen-ai/

デコレータ:
    trace_agent_operation: エージェント操作のトレース
    trace_tool_execution:  ツール実行のトレース
    trace_llm_call:        LLM 呼び出しのトレース
"""

import functools
import logging
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 型変数（ParamSpec + TypeVar で mypy strict / Pylance 互換）
# ---------------------------------------------------------------------------

P = ParamSpec("P")
R = TypeVar("R")

# ---------------------------------------------------------------------------
# デフォルトのサービス名・トレーサー名
# 最上位パッケージ名をサービス名とし、「<package>.observability」を
# トレーサー識別子として利用する。必要に応じて init_tracer(service_name=...)
# でサービス名を上書きすること。
# ---------------------------------------------------------------------------

SERVICE_NAME = __name__.split(".")[0]
_TRACER_NAME = f"{SERVICE_NAME}.observability"

# ---------------------------------------------------------------------------
# OTel SDK のオプショナルインポート
# ---------------------------------------------------------------------------

_HAS_OTEL = False

# fmt: off
try:
    from opentelemetry import trace  # type: ignore[import-not-found]
    from opentelemetry.sdk.resources import Resource  # type: ignore[import-not-found]
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter  # type: ignore[import-not-found]

    _HAS_OTEL = True
except ImportError:
    pass
# fmt: on


# ---------------------------------------------------------------------------
# TracerProvider 初期化
# ---------------------------------------------------------------------------


def init_tracer(
    service_name: str = SERVICE_NAME,
    *,
    enable_console_export: bool = False,
) -> None:
    """TracerProvider を初期化する。

    OTel SDK がインストールされていない場合は何もしない。
    現状はコンソール出力（``enable_console_export=True`` 時）のみ対応。
    OTLP 送信が必要な場合は ``BatchSpanProcessor`` + ``OTLPSpanExporter``
    を追加する拡張が必要。

    Args:
        service_name: サービス名（リソース属性に設定）。
        enable_console_export: True の場合、コンソールへもスパンを出力する。
    """
    if not _HAS_OTEL:
        logger.info("OpenTelemetry SDK 未インストール — トレーシング無効")
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if enable_console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    # OTLP エクスポータは未設定。プロジェクトの要件に応じて拡張すること。
    trace.set_tracer_provider(provider)
    logger.info("TracerProvider 初期化完了: service=%s", service_name)


def get_tracer() -> Any:
    """トレーサーのインスタンスを取得する。

    OTel SDK 未導入時は ``None`` を返す。

    Returns:
        trace.Tracer または None。
    """
    if not _HAS_OTEL:
        return None
    return trace.get_tracer(_TRACER_NAME)


# ---------------------------------------------------------------------------
# デコレータ: エージェント操作
# ---------------------------------------------------------------------------


def trace_agent_operation(
    operation_name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """エージェント操作をトレースするデコレータ。

    GenAI Semantic Conventions の ``gen_ai.agent.*`` 属性を付与する。
    OTel SDK がインストールされていない場合はパススルー。

    記録する属性:
        - gen_ai.agent.operation: 操作名
        - gen_ai.agent.name: エージェント名（設定時）
        - gen_ai.agent.id: エージェント ID（設定時）
        - gen_ai.conversation.id: 会話 ID（設定時）

    Args:
        operation_name: スパン名。省略時は関数の修飾名を使用する。

    Returns:
        デコレートされた関数。

    使用方法::

        @trace_agent_operation("orchestrator.plan")
        def plan_task(task: str) -> str:
            ...
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        tracer = get_tracer()
        if tracer is None:
            return func

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            name = operation_name or func.__qualname__
            with tracer.start_as_current_span(
                name,
                attributes={
                    "gen_ai.agent.operation": name,
                    "gen_ai.system": SERVICE_NAME,
                },
            ) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("agent.status", "success")
                    return result
                except Exception as exc:
                    span.set_attribute("agent.status", "error")
                    span.record_exception(exc)
                    raise

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# デコレータ: ツール実行
# ---------------------------------------------------------------------------


def trace_tool_execution(
    tool_name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """ツール実行をトレースするデコレータ。

    ``tool.name`` / ``tool.status`` 属性を付与する。
    例外発生時は ``tool.status=error`` を記録し、例外を再送出する。
    OTel SDK がインストールされていない場合はパススルー。

    記録する属性:
        - tool.name: ツール名
        - tool.status: 実行結果（"success" / "error"）
        - ツールの入力パラメータ（機密情報・認証情報は除外すること）

    Args:
        tool_name: スパン名。省略時は関数の修飾名を使用する。

    Returns:
        デコレートされた関数。

    使用方法::

        @trace_tool_execution("shell.run_command")
        def run_shell_command(cmd: str) -> str:
            ...
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        tracer = get_tracer()
        if tracer is None:
            return func

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            name = tool_name or func.__qualname__
            with tracer.start_as_current_span(
                name,
                attributes={
                    "tool.name": name,
                    "gen_ai.system": SERVICE_NAME,
                },
            ) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("tool.status", "success")
                    return result
                except Exception as exc:
                    span.set_attribute("tool.status", "error")
                    span.record_exception(exc)
                    raise

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# デコレータ: LLM 呼び出し
# ---------------------------------------------------------------------------


def trace_llm_call(
    model_name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """LLM 呼び出しをトレースするデコレータ。

    GenAI Semantic Conventions に準拠した属性を付与する。

    記録する属性 (OTel GenAI Semantic Conventions 準拠):
        - gen_ai.operation.name: 操作種別（"chat"）
        - gen_ai.request.model: リクエスト時のモデル名
        - gen_ai.response.model: レスポンス時のモデル名（設定時）
        - gen_ai.usage.input_tokens: 入力トークン数（設定時）
        - gen_ai.usage.output_tokens: 出力トークン数（設定時）

    Args:
        model_name: LLM モデル名。省略時は ``"unknown"``。

    Returns:
        デコレートされた関数。

    使用方法::

        @trace_llm_call("claude-3-opus")
        def call_claude(prompt: str) -> str:
            ...
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        tracer = get_tracer()
        if tracer is None:
            return func

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            name = model_name or "unknown"
            with tracer.start_as_current_span(
                f"gen_ai.chat.{name}",
                attributes={
                    "gen_ai.request.model": name,
                    "gen_ai.system": SERVICE_NAME,
                    "gen_ai.operation.name": "chat",
                },
            ) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("gen_ai.response.finish_reason", "stop")
                    return result
                except Exception as exc:
                    span.set_attribute("gen_ai.response.finish_reason", "error")
                    span.record_exception(exc)
                    raise

        return wrapper

    return decorator
