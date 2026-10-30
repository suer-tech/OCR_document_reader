from __future__ import annotations

# ruff: noqa: BLE001
# Optional telemetry SDK failures must not change pipeline outcomes.
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ocr_platform.observability.logging import get_logger

logger = get_logger(__name__)


class _SafeSpan:
    """Keep optional SDK updates outside the pipeline's failure semantics."""

    def __init__(self, observation: Any) -> None:
        self._observation = observation

    def update(self, **kwargs: Any) -> None:
        try:
            self._observation.update(**kwargs)
        except Exception as exc:
            logger.warning("langfuse_span_update_failed", error=str(exc))


class PipelineTracer:
    """Langfuse trace provider for a single pipeline run.
    Gracefully degrades to no-op when Langfuse keys are not configured.
    Creates a root trace (observation) with nested child spans.
    Uses Langfuse 4.x start_observation() API.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._trace: Any = None
        self._enabled = False
        self._try_init()

    def _try_init(self) -> None:
        pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
        sk = os.environ.get("LANGFUSE_SECRET_KEY")
        if not pk or not sk:
            self._enabled = False
            return
        try:
            from langfuse import Langfuse

            self._client = Langfuse()
            self._enabled = True
        except Exception as exc:
            logger.warning("langfuse_init_failed", error=str(exc))
            self._enabled = False

    def start_trace(self, name: str, **kwargs: Any) -> None:
        if not self._enabled or not self._client:
            return
        try:
            self._trace = self._client.start_observation(name=name, **kwargs)
        except Exception as exc:
            logger.warning("langfuse_trace_start_failed", error=str(exc))
            self._trace = None

    @contextmanager
    def span(self, name: str, **kwargs: Any) -> Iterator[_SafeSpan | None]:
        if not self._enabled or not self._trace:
            yield None
            return
        span = None
        try:
            span = self._trace.start_observation(name=name, **kwargs)
        except Exception as exc:
            logger.warning("langfuse_span_start_failed", error=str(exc))
        safe_span = _SafeSpan(span) if span is not None else None
        try:
            yield safe_span
        except BaseException as exc:
            if safe_span is not None:
                safe_span.update(level="ERROR", status_message=str(exc))
            # Preserve the original exception, including task cancellation.
            raise
        finally:
            if span is not None:
                try:
                    span.end()
                except Exception as exc:
                    logger.warning("langfuse_span_end_failed", error=str(exc))

    def update_trace(self, **kwargs: Any) -> None:
        if not self._enabled or not self._trace:
            return
        try:
            self._trace.update(**kwargs)
        except Exception as exc:
            logger.warning("langfuse_trace_update_failed", error=str(exc))

    def score(self, **kwargs: Any) -> None:
        if not self._enabled or not self._trace:
            return
        try:
            self._trace.score(**kwargs)
        except Exception as exc:
            logger.warning("langfuse_score_failed", error=str(exc))

    def flush(self) -> None:
        if not self._enabled or not self._client:
            return
        try:
            if self._trace is not None:
                self._trace.end()
            self._client.flush()
        except Exception as exc:
            logger.warning("langfuse_flush_failed", error=str(exc))


_tracer: PipelineTracer | None = None


def get_pipeline_tracer() -> PipelineTracer:
    global _tracer
    if _tracer is None:
        _tracer = PipelineTracer()
    return _tracer
