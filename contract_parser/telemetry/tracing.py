"""OpenTelemetry distributed tracing configuration with Google Cloud Trace integration.

Configures global TracerProvider, composite propagators (W3C traceparent +
Google Cloud X-Cloud-Trace-Context), serverless BatchSpanProcessor, and lifecycle
flushing to mitigate Cloud Run CPU freeze.
"""

import logging
import os
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.cloud_trace_propagator import CloudTraceFormatPropagator
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger(__name__)
_TRACER_PROVIDER: TracerProvider | None = None


def setup_telemetry(
    project_id: str = "",
    service_name: str = "contract-parser",
    service_version: str = "0.9.0",
    sample_rate: float = 1.0,
) -> TracerProvider:
    """Configures global OpenTelemetry TracerProvider with Cloud Trace export and composite propagators."""
    global _TRACER_PROVIDER
    if _TRACER_PROVIDER is not None:
        return _TRACER_PROVIDER

    # 1. Register Composite Propagators (W3C traceparent + Google Cloud Trace Context)
    set_global_textmap(
        CompositePropagator([
            TraceContextTextMapPropagator(),
            CloudTraceFormatPropagator(),
        ])
    )

    # 2. Resource Definition
    resource = Resource.create({
        "service.name": service_name,
        "service.version": service_version,
        "cloud.provider": "gcp",
        "cloud.platform": "gcp_cloud_run",
    })

    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(sample_rate),
    )

    # 3. Cloud Trace Exporter with Serverless-Tuned Batch Processor
    cloud_trace_enabled = os.getenv("CLOUD_TRACE_ENABLED", "true").lower() in (
        "true",
        "1",
        "yes",
    )

    if cloud_trace_enabled and project_id:
        try:
            cloud_exporter = CloudTraceSpanExporter(project_id=project_id)
            # Schedule delay tuned to 500ms to minimize buffering lag before container idle
            provider.add_span_processor(
                BatchSpanProcessor(
                    cloud_exporter,
                    schedule_delay_millis=500,
                    max_export_batch_size=64,
                )
            )
            logger.info(
                "OpenTelemetry CloudTraceSpanExporter successfully initialized for project: %s",
                project_id,
            )
        except Exception as e:
            logger.warning(
                "Cloud Trace exporter initialization failed: %s. Telemetry running with default provider.",
                e,
            )

    trace.set_tracer_provider(provider)
    _TRACER_PROVIDER = provider
    return provider


def get_tracer(name: str = "contract_parser") -> trace.Tracer:
    """Retrieves an application tracer instance."""
    return trace.get_tracer(name)


def flush_telemetry(timeout_millis: int = 2000) -> None:
    """Forces immediate export of all queued spans before Cloud Run CPU throttling."""
    global _TRACER_PROVIDER
    if _TRACER_PROVIDER is not None:
        try:
            _TRACER_PROVIDER.force_flush(timeout_millis=timeout_millis)
        except Exception as exc:
            logger.debug("force_flush non-fatal exception: %s", exc)


def shutdown_telemetry() -> None:
    """Gracefully shuts down the global TracerProvider on application termination."""
    global _TRACER_PROVIDER
    if _TRACER_PROVIDER is not None:
        try:
            _TRACER_PROVIDER.shutdown()
        except Exception as exc:
            logger.debug("shutdown_telemetry exception: %s", exc)
        _TRACER_PROVIDER = None
