"""Telemetry must neither hide pipeline failures nor break successful work."""
import asyncio
from unittest.mock import MagicMock

import pytest

from ocr_platform.observability.langfuse_tracing import PipelineTracer


@pytest.fixture
def tracer():
    tracer = object.__new__(PipelineTracer)
    tracer._enabled = True
    tracer._client = MagicMock()
    tracer._trace = MagicMock()
    return tracer


@pytest.mark.parametrize("update_fails,end_fails", [(False, False), (True, False), (False, True), (True, True)])
def test_original_extraction_error_survives_telemetry_failures(tracer, update_fails, end_fails):
    span = tracer._trace.start_observation.return_value
    if update_fails:
        span.update.side_effect = RuntimeError("Telemetry update failed")
    if end_fails:
        span.end.side_effect = RuntimeError("Telemetry end failed")
    original = ValueError("Passport response is invalid")
    with pytest.raises(ValueError) as caught, tracer.span("entity_extraction"):
        raise original
    assert caught.value is original
    span.end.assert_called_once()


def test_nested_spans_preserve_the_original_error(tracer):
    original = ValueError("Extraction failed")
    with pytest.raises(ValueError) as caught, tracer.span("outer"), tracer.span("inner"):
        raise original
    assert caught.value is original


@pytest.mark.parametrize("fails", ["start", "update", "end"])
def test_telemetry_failure_does_not_interrupt_successful_work(tracer, fails):
    if fails == "start":
        tracer._trace.start_observation.side_effect = RuntimeError("Telemetry unavailable")
    else:
        getattr(tracer._trace.start_observation.return_value, fails).side_effect = RuntimeError("Telemetry unavailable")
    work = []
    with tracer.span("entity_extraction") as span:
        work.append("extracted")
        if span is not None:
            span.update(output={"fields_count": 11})
    assert work == ["extracted"]


def test_failed_span_start_does_not_hide_body_error(tracer):
    tracer._trace.start_observation.side_effect = RuntimeError("Telemetry unavailable")
    original = ValueError("Extraction failed")
    with pytest.raises(ValueError) as caught, tracer.span("entity_extraction"):
        raise original
    assert caught.value is original


@pytest.mark.parametrize("mode", ["enabled", "disabled", "no_root"])
def test_cancellation_propagates(tracer, mode):
    if mode == "disabled":
        tracer._enabled = False
    elif mode == "no_root":
        tracer._trace = None
    original = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError) as caught, tracer.span("entity_extraction"):
        raise original
    assert caught.value is original


def test_success_records_output_and_ends_once(tracer):
    observation = tracer._trace.start_observation.return_value
    with tracer.span("entity_extraction") as span:
        span.update(output={"fields_count": 11})
    observation.update.assert_called_once_with(output={"fields_count": 11})
    observation.end.assert_called_once()
