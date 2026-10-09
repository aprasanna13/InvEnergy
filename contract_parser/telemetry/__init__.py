"""Telemetry foundation module for InvEnergy Contract Parser.

Provides Google Cloud Logging structured formatting, OpenTelemetry tracing with
GCP Cloud Trace exporter, and context-preserving multi-threaded execution.
"""

from contract_parser.telemetry.logging_formatter import (
    CloudLoggingJsonFormatter,
    setup_structured_logging,
)
from contract_parser.telemetry.tracing import (
    setup_telemetry,
    get_tracer,
    flush_telemetry,
    shutdown_telemetry,
)
from contract_parser.telemetry.thread_propagation import (
    TracedThreadPoolExecutor,
    wrap_with_context,
)

__all__ = [
    "CloudLoggingJsonFormatter",
    "setup_structured_logging",
    "setup_telemetry",
    "get_tracer",
    "flush_telemetry",
    "shutdown_telemetry",
    "TracedThreadPoolExecutor",
    "wrap_with_context",
]
