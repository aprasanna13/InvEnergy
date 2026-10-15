# Invenergy Contract Intelligence - Automation & Provisioning Scripts

This directory contains enterprise automation scripts for bootstrapping, deploying, and maintaining the Invenergy Contract Intelligence Platform across local development and Google Cloud environments.

---

## Directory Overview

```
scripts/
├── setup_gcp_environment.sh # Automated GCP Infrastructure Provisioning Script
├── deploy_cloud_run.sh      # Cloud Run Web Service & Cloud Run Job Deployment Script
├── setup_local_dev.sh       # Local Developer Environment Bootstrapping Script
├── cleanup_environment.sh   # Lakehouse & Cloud Storage Cleanup Utility
├── README.md                # This Documentation Guide
└── sql/                     # BigQuery Lakehouse DDL & Seed Scripts
    ├── 01_create_tables.sql     # 14 Base Lakehouse Tables with Partitioning & Clustering
    ├── 02_create_views.sql      # 15 Deduplication Views (QUALIFY ROW_NUMBER() = 1)
    └── 03_seed_erp_projects.sql # Baseline Renewable Energy Asset Seed Data
```

---

## 1. Automated GCP Infrastructure Setup (`setup_gcp_environment.sh`)

Provisions all required Google Cloud resources in an idempotent, single-command run:

### What It Provisions:
1. **9 Google Cloud APIs:** `run`, `cloudbuild`, `artifactregistry`, `aiplatform` (Vertex AI), `bigquery`, `storage`, `identitytoolkit`, `cloudtrace`, `logging`.
2. **Cloud Storage Bucket:** `gs://${PROJECT_ID}-contract-intelligence` with Uniform Bucket-Level Access and canonical directory structure:
   - `batch_inbox/`: Inbox folder for incoming PDF contracts.
   - `raw/`: Canonical immutable raw PDF storage partitioned by `raw/<document_id>/<filename>`.
   - `archive/`: Post-ingestion archive folder for processed contracts.
   - `exports/`: Per-document CSV extracts (`clauses.csv`, `defined_terms.csv`, `special_conditions.csv`, `dnd_checklist_signoffs.csv`, `exhibits_catalog.csv`).
3. **BigQuery Dataset & Lakehouse:** Creates dataset `contract_intelligence` and executes DDL scripts in `scripts/sql/`:
   - 14 base tables with time partitioning by `ingested_at` / `started_at` / `signed_at` and clustering.
   - 15 active-state analytical deduplication views.
   - 5 baseline renewable energy development asset projects.
4. **Least-Privilege Runtime Service Account:** Creates `contract-parser-sa@${PROJECT_ID}.iam.gserviceaccount.com` and binds 7 necessary roles:
   - `roles/bigquery.dataEditor`
   - `roles/bigquery.jobUser`
   - `roles/storage.objectAdmin`
   - `roles/aiplatform.user`
   - `roles/cloudtrace.agent`
   - `roles/logging.logWriter`
   - `roles/run.developer` (required to invoke Cloud Run Jobs from the web service)

### Usage:
```bash
export PROJECT_ID="your-project-id"
export REGION="us-central1"

chmod +x scripts/*.sh
./scripts/setup_gcp_environment.sh
```

---

## 2. Cloud Run Services & Batch Job Deployment (`deploy_cloud_run.sh`)

Builds container images and deploys both the web application and the asynchronous sliding-window batch ingestion worker pool:

### What It Deploys:
1. **Cloud Run Job (`contract-parser-batch`):**
   - Builds dedicated batch container image using `Dockerfile.batch`.
   - Provisions or updates Cloud Run Job `contract-parser-batch` in `us-central1` with 2 vCPU, 4GiB RAM, and 1-hour task timeout.
   - Configured with environment variables for Lakehouse BigQuery sync, Cloud Storage archiving, and telemetry.
2. **Cloud Run Web Service (`contract-parser`):**
   - Builds and deploys the FastAPI application container using `Dockerfile`.
   - Configures production environment variables, including `USE_LOCAL_BATCH_RUNNER=false` and `REGION=us-central1` to route batch extraction to the Cloud Run Job.
   - Verifies deployment with health check and prints public service URL.

### Usage:
```bash
export PROJECT_ID="your-project-id"
export REGION="us-central1"

./scripts/deploy_cloud_run.sh
```

---

## 3. Local Developer Setup (`setup_local_dev.sh`)

Bootstraps a fresh developer workstation for running the platform locally or in customer evaluation mode:

### What It Does:
1. Detects Python 3.11+ and creates a dedicated `.venv` virtual environment.
2. Installs package dependencies from `pyproject.toml` using `uv` (forcing public PyPI index `https://pypi.org/simple` to prevent private corporate mirror locks).
3. Prepares local SQLite directory `./data/` and copies `.env.example` to `.env` if missing.
4. Runs the automated test suite to verify installation integrity.

### Usage:
```bash
./scripts/setup_local_dev.sh
```

### Starting Local Server:
```bash
# Production mode with Cloud connection:
source .venv/bin/activate
uvicorn contract_parser.app:app --host 127.0.0.1 --port 8000 --reload

# Standalone Evaluation Mode (Zero Cloud Dependencies):
AUTH_ENABLED=false USE_BIGQUERY=false USE_CLOUD_STORAGE=false LOCAL_DATA_DIR=./data \
  uvicorn contract_parser.app:app --host 127.0.0.1 --port 8000 --reload
```

---

## 4. Lakehouse & Storage Cleanup Utility (`cleanup_environment.sh`)

Resets the Lakehouse and Cloud Storage state to a clean slate for repeatable batch processing and evaluations:

### What It Cleans:
1. Truncates all 14 BigQuery base tables.
2. Restores the 5 baseline renewable energy asset projects via `sql/03_seed_erp_projects.sql`.
3. Cleans GCS objects in `raw/`, `archive/`, and `exports/`, while **safely preserving contracts in `batch_inbox/`**.
4. Cleans the local SQLite database (`data/contract_intelligence.db`).

### Usage:
```bash
export PROJECT_ID="your-project-id"

./scripts/cleanup_environment.sh
```

---

## 5. SQL Reference (`scripts/sql/`)

| Script | Purpose |
| :--- | :--- |
| `01_create_tables.sql` | DDL for all 14 Lakehouse tables (`projects`, `landowners`, `documents`, `clauses`, `defined_terms`, `exhibits_catalog`, `special_conditions`, `dnd_checklist_signoffs`, `template_registry`, `template_clauses`, `contract_variances`, `clause_variance_details`, `batch_jobs`, `batch_job_items`). |
| `02_create_views.sql` | DDL for 15 deduplication views implementing `QUALIFY ROW_NUMBER() OVER (...) = 1` active-state resolution and joined `v_contract_intelligence_mart`. |
| `03_seed_erp_projects.sql` | Idempotent `MERGE` statement seeding 5 baseline utility-scale wind, solar, storage, transmission, and geothermal projects. |
