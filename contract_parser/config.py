"""Environment configuration for the Gemini-First Contract Parser."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BQ_DATA_AGENT_URN = (
    "urn:agent:projects-255093976233:projects:255093976233:"
    "locations:us:geminidataanalytics:dataAgents:"
    "agent_f8454b44-a4aa-4c94-accf-245b5e6b1f11"
)


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
        default_factory=lambda: os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.8-flash")
    )
    fast_discovery_model: str = field(
        default_factory=lambda: os.getenv("FAST_DISCOVERY_MODEL", "gemini-3.8-flash")
    )
    multi_pass_enabled: bool = field(
        default_factory=lambda: os.getenv("MULTI_PASS_ENABLED", "true").lower()
        in ("true", "1", "yes")
    )
    gcs_bucket_name: str = field(
        default_factory=lambda: os.getenv(
            "GCS_BUCKET_NAME", "pr-tftest-contract-intelligence"
        )
    )
    bq_dataset_id: str = field(
        default_factory=lambda: os.getenv("BQ_DATASET_ID", "contract_intelligence")
    )
    bq_data_agent_urn: str = field(
        default_factory=lambda: os.getenv(
            "BQ_DATA_AGENT_URN", DEFAULT_BQ_DATA_AGENT_URN
        )
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
    pass_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("PASS_TIMEOUT_SECONDS", "480.0"))
    )
    auth_enabled: bool = field(
        default_factory=lambda: os.getenv("AUTH_ENABLED", "true").lower()
        in ("true", "1", "yes")
    )
    firebase_api_key: str = field(
        default_factory=lambda: os.getenv("FIREBASE_API_KEY", "")
    )
    firebase_auth_domain: str = field(
        default_factory=lambda: os.getenv(
            "FIREBASE_AUTH_DOMAIN", "pr-tftest.firebaseapp.com"
        )
    )
    admin_email_whitelist: set[str] = field(
        default_factory=lambda: {
            e.strip().lower()
            for e in os.getenv(
                "ADMIN_EMAIL_WHITELIST", "lead.evaluator@invenergy.com"
            ).split(",")
            if e.strip()
        }
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

    def resolve_data_agent_resource(self) -> tuple[str, str, str]:
        """Resolve `bq_data_agent_urn` into `(project_id_or_number, location, data_agent_resource)`."""
        raw = (self.bq_data_agent_urn or DEFAULT_BQ_DATA_AGENT_URN).strip()
        urn_match = re.search(
            r"projects:([^:]+):locations:([^:]+):geminidataanalytics:dataAgents:([^:\s]+)",
            raw,
        )
        if urn_match:
            proj, loc, agent_id = urn_match.group(1), urn_match.group(2), urn_match.group(3)
            return proj, loc, f"projects/{proj}/locations/{loc}/dataAgents/{agent_id}"

        res_match = re.search(
            r"projects/([^/]+)/locations/([^/]+)/dataAgents/([^/\s]+)",
            raw,
        )
        if res_match:
            proj, loc, agent_id = res_match.group(1), res_match.group(2), res_match.group(3)
            return proj, loc, f"projects/{proj}/locations/{loc}/dataAgents/{agent_id}"

        proj = self.google_cloud_project or "255093976233"
        loc = "us"
        return proj, loc, f"projects/{proj}/locations/{loc}/dataAgents/{raw}"

