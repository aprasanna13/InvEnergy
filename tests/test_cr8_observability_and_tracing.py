"""Automated Test Suite for CR-8: Enterprise Observability, Cloud Logging & Distributed Tracing (UT-1..UT-7)."""

from __future__ import annotations

import json
import logging
import os
import sys
from unittest.mock import MagicMock

import pytest
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from contract_parser.gemini_parser import (
    record_llm_response_telemetry,
    record_model_fallback_event,
)
from contract_parser.telemetry.logging_formatter import (
    CloudLoggingJsonFormatter,
    setup_structured_logging,
)
from contract_parser.telemetry.thread_propagation import (
    TracedThreadPoolExecutor,
    wrap_with_context,
)
from contract_parser.telemetry.tracing import (
    flush_telemetry,
    get_tracer,
    setup_telemetry,
    shutdown_telemetry,
)


def test_ut1_cloud_logging_json_formatter():
    """[UT-1] Asserts CloudLoggingJsonFormatter produces valid Google Cloud Logging schema JSON."""
    formatter = CloudLoggingJsonFormatter(
        gcp_project_id="pr-tftest",
        service_name="contract-parser",
        service_version="0.9.0",
    )

    # 1. Standard Info LogRecord
    record = logging.LogRecord(
        name="contract_parser.app",
        level=logging.INFO,
        pathname="/app/contract_parser/app.py",
        lineno=120,
        msg="Ingestion started for document",
        args=(),
        exc_info=None,
    )
    # Add domain metadata
    record.document_id = "doc_test123"
    record.project_id = "prj_cedar_lantern_wind"
    record.pass_name = "discovery"

    output = formatter.format(record)
    parsed = json.loads(output)

    assert parsed["severity"] == "INFO"
    assert "time" in parsed
    assert parsed["message"] == "Ingestion started for document"
    assert parsed["component"] == "contract_parser.app"
    assert parsed["serviceContext"] == {
        "service": "contract-parser",
        "version": "0.9.0",
    }
    assert parsed["logging.googleapis.com/sourceLocation"] == {
        "file": "/app/contract_parser/app.py",
        "line": 120,
        "function": "",
    }
    # Business domain isolation
    assert "contract" in parsed
    assert parsed["contract"]["document_id"] == "doc_test123"
    assert parsed["contract"]["project_id"] == "prj_cedar_lantern_wind"
    assert parsed["contract"]["pass_name"] == "discovery"

    # 2. Exception Handling for Error Reporting
    try:
        raise ValueError("Simulated parsing failure")
    except ValueError:
        exc_info = sys.exc_info()

    err_record = logging.LogRecord(
        name="contract_parser.app",
        level=logging.ERROR,
        pathname="/app/contract_parser/gemini_parser.py",
        lineno=250,
        msg="Fatal model crash",
        args=(),
        exc_info=exc_info,
    )
    err_output = formatter.format(err_record)
    err_parsed = json.loads(err_output)

    assert err_parsed["severity"] == "ERROR"
    # Stacktrace must be included in the message for Google Cloud Error Reporting
    assert "Fatal model crash" in err_parsed["message"]
    assert "Traceback (most recent call last):" in err_parsed["message"]
    assert "ValueError: Simulated parsing failure" in err_parsed["message"]

    # 3. Active OpenTelemetry Span Correlation
    setup_telemetry(project_id="pr-tftest")
    tracer = get_tracer("test_log_tracer")
    with tracer.start_as_current_span("test_span") as active_span:
        span_ctx = active_span.get_span_context()
        span_record = logging.LogRecord(
            name="contract_parser.app",
            level=logging.INFO,
            pathname="/app/contract_parser/app.py",
            lineno=130,
            msg="Processing within active span",
            args=(),
            exc_info=None,
        )
        span_output = formatter.format(span_record)
        span_parsed = json.loads(span_output)

        expected_trace_id = trace.format_trace_id(span_ctx.trace_id)
        expected_span_id = trace.format_span_id(span_ctx.span_id)
        assert span_parsed["logging.googleapis.com/trace"] == f"projects/pr-tftest/traces/{expected_trace_id}"
        assert span_parsed["logging.googleapis.com/spanId"] == expected_span_id
        assert "logging.googleapis.com/trace_sampled" in span_parsed


def test_ut2_pii_and_validation_error_scrubbing():
    """[UT-2] Asserts that raw Pydantic validation dumps and sensitive keywords are redacted."""
    formatter = CloudLoggingJsonFormatter(gcp_project_id="pr-tftest")

    # Message with Pydantic dump and sensitive keys
    raw_message = (
        "Validation error occurred: 1 validation error for ClauseRow\n"
        "payment_schedule: $50,000 per megawatt annually\n"
        "grantor_name: Johnathan Doe\n"
        "account_number: 1234-5678-9012\n"
        "ssn: 000-12-3456\n"
        "tax_id: 99-8877665\n"
        "[input_value={'clause_text': 'Confidential contract terms and conditions...', 'secret': 123}]"
    )

    record = logging.LogRecord(
        name="contract_parser.schemas",
        level=logging.WARNING,
        pathname="/app/contract_parser/schemas.py",
        lineno=88,
        msg=raw_message,
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    msg = parsed["message"]

    # Verify redactions
    assert "[input_value=" not in msg
    assert "[INPUT_PAYLOAD_REDACTED]" in msg
    assert "payment_schedule=[REDACTED]" in msg
    assert "grantor_name=[REDACTED]" in msg
    assert "account_number=[REDACTED]" in msg
    assert "ssn=[REDACTED]" in msg
    assert "tax_id=[REDACTED]" in msg
    assert "Johnathan Doe" not in msg
    assert "1234-5678-9012" not in msg

    # Verify long message truncation (> 4096 characters)
    huge_message = "A" * 5000
    huge_record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg=huge_message,
        args=(),
        exc_info=None,
    )
    huge_formatted = formatter.format(huge_record)
    huge_parsed = json.loads(huge_formatted)
    assert len(huge_parsed["message"]) < 4200
    assert "... [TRUNCATED_AT_4KB]" in huge_parsed["message"]


def test_ut3_composite_trace_propagation():
    """[UT-3] Asserts CompositePropagator extracts trace context from W3C and GCP headers."""
    setup_telemetry(project_id="pr-tftest")

    # 1. Standard W3C traceparent header
    w3c_trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    w3c_span_id = "00f067aa0ba902b7"
    w3c_header = f"00-{w3c_trace_id}-{w3c_span_id}-01"

    carrier_w3c = {"traceparent": w3c_header}
    ctx_w3c = propagate.extract(carrier_w3c)
    span_w3c = trace.get_current_span(ctx_w3c)
    span_ctx_w3c = span_w3c.get_span_context()

    assert span_ctx_w3c.is_valid
    assert trace.format_trace_id(span_ctx_w3c.trace_id) == w3c_trace_id
    assert trace.format_span_id(span_ctx_w3c.span_id) == w3c_span_id

    # 2. Google Cloud X-Cloud-Trace-Context header
    # Format: TRACE_ID/SPAN_ID;o=TRACE_TRUE
    gcp_trace_id = "105445aa7843bc8bf206b12000100000"
    gcp_header = f"{gcp_trace_id}/12345;o=1"

    carrier_gcp = {"x-cloud-trace-context": gcp_header}
    ctx_gcp = propagate.extract(carrier_gcp)
    span_gcp = trace.get_current_span(ctx_gcp)
    span_ctx_gcp = span_gcp.get_span_context()

    assert span_ctx_gcp.is_valid
    assert trace.format_trace_id(span_ctx_gcp.trace_id) == gcp_trace_id


def test_ut4_opentelemetry_span_hierarchy_and_tokens():
    """[UT-4] Asserts OpenTelemetry span nesting and in-loop Gemini token usage recording."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test-service"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test_tracer")

    with tracer.start_as_current_span("root_pipeline") as root_span:
        root_span.set_attribute("contract.document_id", "doc_test_tokens")

        with tracer.start_as_current_span("pass_2_body_extraction") as child_span:
            # Simulate Gemini response with usage_metadata
            mock_usage = MagicMock()
            mock_usage.prompt_token_count = 15400
            mock_usage.candidates_token_count = 2300
            mock_usage.total_token_count = 17700
            mock_usage.cached_content_token_count = 4000

            mock_response = MagicMock()
            mock_response.model_version = "gemini-3.1-pro-preview-001"
            mock_response.usage_metadata = mock_usage

            record_llm_response_telemetry(child_span, mock_response, "gemini-3.1-pro-preview")

    spans = exporter.get_finished_spans()
    assert len(spans) == 2

    child = next(s for s in spans if s.name == "pass_2_body_extraction")
    root = next(s for s in spans if s.name == "root_pipeline")

    # Hierarchy verification
    assert child.parent.span_id == root.context.span_id
    assert child.context.trace_id == root.context.trace_id

    # Token and GenAI attributes
    attrs = child.attributes
    assert attrs["gen_ai.system"] == "vertex_ai"
    assert attrs["gen_ai.response.model"] == "gemini-3.1-pro-preview-001"
    assert attrs["gen_ai.usage.prompt_tokens"] == 15400
    assert attrs["gen_ai.usage.completion_tokens"] == 2300
    assert attrs["gen_ai.usage.total_tokens"] == 17700
    assert attrs["gen_ai.usage.cached_tokens"] == 4000


def test_ut5_model_fallback_span_events():
    """[UT-5] Asserts that model_fallback_transition span event is recorded with diagnostic attributes."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test_fallback_tracer")

    with tracer.start_as_current_span("pass_extraction_with_fallback") as span:
        err = RuntimeError("429 Quota exhausted for model gemini-3.1-pro-preview")
        record_model_fallback_event(
            span=span,
            failed_model="gemini-3.1-pro-preview",
            fallback_model="gemini-3.8-flash",
            attempt=1,
            error=err,
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span_data = spans[0]

    events = span_data.events
    assert len(events) == 1
    event = events[0]
    assert event.name == "model_fallback_transition"

    event_attrs = event.attributes
    assert event_attrs["gen_ai.fallback.failed_model"] == "gemini-3.1-pro-preview"
    assert event_attrs["gen_ai.fallback.target_model"] == "gemini-3.8-flash"
    assert event_attrs["gen_ai.fallback.attempt"] == 1
    assert event_attrs["gen_ai.fallback.error_type"] == "RuntimeError"
    assert "429 Quota exhausted" in event_attrs["gen_ai.fallback.error_message"]


def test_ut6_traced_thread_pool_executor():
    """[UT-6] Asserts that worker threads inherit active OpenTelemetry trace context via TracedThreadPoolExecutor."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test_threading_tracer")

    parent_trace_id = None
    child_trace_ids: list[int] = []

    with tracer.start_as_current_span("parent_work") as parent:
        parent_trace_id = parent.get_span_context().trace_id

        def worker_task(index: int) -> int:
            current = trace.get_current_span()
            ctx = current.get_span_context()
            if ctx and ctx.is_valid:
                child_trace_ids.append(ctx.trace_id)
            return index * 2

        with TracedThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(worker_task, 1)
            fut2 = executor.submit(worker_task, 2)
            results = list(executor.map(worker_task, [3, 4]))

        res1 = fut1.result()
        res2 = fut2.result()

    assert res1 == 2
    assert res2 == 4
    assert results == [6, 8]
    assert len(child_trace_ids) == 4
    # All tasks running in worker threads must inherit the exact parent trace_id
    for tid in child_trace_ids:
        assert tid == parent_trace_id


def test_ut7_telemetry_force_flush():
    """[UT-7] Asserts that flush_telemetry and shutdown_telemetry execute gracefully."""
    # Test uninitialized or initialized states
    flush_telemetry(timeout_millis=500)

    # Initialize telemetry
    provider = setup_telemetry(project_id="")
    assert provider is not None

    # Test flush on active provider
    flush_telemetry(timeout_millis=500)

    # Test clean shutdown
    shutdown_telemetry()
