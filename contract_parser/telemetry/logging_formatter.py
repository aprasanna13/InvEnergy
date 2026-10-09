"""Google Cloud Logging structured JSON formatter with automated PII sanitization.

Outputs single-line JSON adhering to GCP Cloud Logging schema (severity, time,
logging.googleapis.com/trace, logging.googleapis.com/spanId, serviceContext)
for native Cloud Run log ingestion and Cloud Error Reporting grouping.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any
from opentelemetry import trace

# Pattern to detect raw Pydantic validation dumps or large input payloads
_PYDANTIC_DUMP_REGEX = re.compile(r"(\[input_value=\{.*?\}\])", re.DOTALL)
# Sensitive keyword pattern to mask confidential financial and identity values
_SENSITIVE_KEY_REGEX = re.compile(
    r"(payment_schedule|grantor_name|account_number|ssn|tax_id)[\s:=]+([^\s,]+)",
    re.IGNORECASE,
)


class CloudLoggingJsonFormatter(logging.Formatter):
    """Formats Python logging LogRecord objects into Google Cloud Logging structured JSON."""

    def __init__(
        self,
        gcp_project_id: str | None = None,
        service_name: str = "contract-parser",
        service_version: str = "0.9.0",
    ):
        super().__init__()
        self.gcp_project_id = (
            gcp_project_id
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCP_PROJECT")
            or ""
        )
        self.service_name = service_name
        self.service_version = service_version

    def _sanitize_message(self, message: str) -> str:
        """Strip raw JSON dumps, Pydantic validation payloads, and sensitive tokens from log messages."""
        if not message:
            return ""
        # Redact raw Pydantic input dumps
        cleaned = _PYDANTIC_DUMP_REGEX.sub("[INPUT_PAYLOAD_REDACTED]", message)
        # Redact sensitive keys
        cleaned = _SENSITIVE_KEY_REGEX.sub(r"\1=[REDACTED]", cleaned)
        # Cap message length to prevent giant stdout frames
        if len(cleaned) > 4096:
            return cleaned[:4096] + "... [TRUNCATED_AT_4KB]"
        return cleaned

    def format(self, record: logging.LogRecord) -> str:
        severity_map = {
            "DEBUG": "DEBUG",
            "INFO": "INFO",
            "WARNING": "WARNING",
            "ERROR": "ERROR",
            "CRITICAL": "CRITICAL",
        }
        severity = severity_map.get(record.levelname, "DEFAULT")

        # Format message and sanitize
        raw_msg = record.getMessage()
        sanitized_msg = self._sanitize_message(raw_msg)

        # Handle exception information for Google Cloud Error Reporting
        if record.exc_info:
            formatted_exc = self.formatException(record.exc_info)
            formatted_exc = self._sanitize_message(formatted_exc)
            # Cloud Error Reporting expects the traceback in the message field
            sanitized_msg = f"{sanitized_msg}\n{formatted_exc}"

        payload: dict[str, Any] = {
            "severity": severity,
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "message": sanitized_msg,
            "component": record.name,
            "logging.googleapis.com/sourceLocation": {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName or "",
            },
            "serviceContext": {
                "service": self.service_name,
                "version": self.service_version,
            },
        }

        # Extract current OpenTelemetry trace & span context if active
        try:
            current_span = trace.get_current_span()
            span_context = current_span.get_span_context() if current_span else None

            if span_context and span_context.is_valid:
                trace_id_hex = trace.format_trace_id(span_context.trace_id)
                span_id_hex = trace.format_span_id(span_context.span_id)

                if self.gcp_project_id:
                    payload["logging.googleapis.com/trace"] = (
                        f"projects/{self.gcp_project_id}/traces/{trace_id_hex}"
                    )
                else:
                    payload["logging.googleapis.com/trace"] = trace_id_hex

                payload["logging.googleapis.com/spanId"] = span_id_hex
                payload["logging.googleapis.com/trace_sampled"] = (
                    span_context.trace_flags.sampled
                )
        except Exception:
            # Telemetry context extraction must never crash the logging pipeline
            pass

        # Isolate InvEnergy ERP domain metadata under 'contract' sub-object
        contract_meta: dict[str, Any] = {}
        for field in (
            "document_id",
            "project_id",
            "landowner_id",
            "pass_name",
            "model",
            "latency_ms",
            "tokens",
        ):
            val = getattr(record, field, None)
            if val is not None:
                contract_meta[field] = val
        if contract_meta:
            payload["contract"] = contract_meta

        # Pass HTTP request telemetry if present
        http_req = getattr(record, "httpRequest", None)
        if http_req and isinstance(http_req, dict):
            payload["httpRequest"] = http_req

        return json.dumps(payload, default=str)


def setup_structured_logging(
    gcp_project_id: str | None = None,
    log_level: str = "INFO",
    service_name: str = "contract-parser",
    service_version: str = "0.9.0",
) -> None:
    """Configures structured JSON logging for root logger and Uvicorn loggers."""
    formatter = CloudLoggingJsonFormatter(
        gcp_project_id=gcp_project_id,
        service_name=service_name,
        service_version=service_version,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Intercept Uvicorn loggers to maintain uniform JSON stdout in Cloud Run
    for uvicorn_logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        u_log = logging.getLogger(uvicorn_logger_name)
        u_log.handlers.clear()
        u_log.addHandler(handler)
        u_log.propagate = False
