"""Environment configuration for the Gemini-First Contract Parser."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PipelineConfig:
    """Centralized configuration for GCP project, GCS, BigQuery, and Gemini 3.x."""

    google_cloud_project: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", "pr-tftest")
    )
    google_cloud_location: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    )
    use_enterprise: bool = field(
        default_factory=lambda: os.getenv("GOOGLE_GENAI_USE_ENTERPRISE", "true").lower()
        in ("true", "1", "yes")
    )
    gemini_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
    )
    gemini_fallback_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")
    )
    gcs_bucket_name: str = field(
        default_factory=lambda: os.getenv(
            "GCS_BUCKET_NAME", "pr-tftest-contract-intelligence"
        )
    )
    bq_dataset_id: str = field(
        default_factory=lambda: os.getenv("BQ_DATASET_ID", "contract_intelligence")
    )
    local_data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("LOCAL_DATA_DIR", "./data"))
    )
    use_cloud_storage: bool = field(
        default_factory=lambda: os.getenv("USE_CLOUD_STORAGE", "true").lower()
        in ("true", "1", "yes")
    )
    use_bigquery: bool = field(
        default_factory=lambda: os.getenv("USE_BIGQUERY", "true").lower()
        in ("true", "1", "yes")
    )
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("GEMINI_MAX_RETRIES", "3"))
    )
    initial_backoff_seconds: float = field(
        default_factory=lambda: float(os.getenv("GEMINI_INITIAL_BACKOFF", "2.0"))
    )
    max_output_tokens: int = field(
        default_factory=lambda: int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "65536"))
    )

    def ensure_genai_env(self) -> None:
        """Ensure environment variables required by google-genai are set."""
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.google_cloud_project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.google_cloud_location)
        if self.use_enterprise:
            os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "true")

    @property
    def bq_dataset_fqn(self) -> str:
        """Return fully-qualified BigQuery dataset ID (`project.dataset`)."""
        return f"{self.google_cloud_project}.{self.bq_dataset_id}"
