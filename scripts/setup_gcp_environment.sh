#!/usr/bin/env bash
# ==============================================================================
# Invenergy Contract Intelligence Platform
# Customer Environment GCP Infrastructure Setup Script
#
# Usage:
#   export PROJECT_ID="sophia-gcp-project-id"
#   export REGION="us-central1"
#   ./scripts/setup_gcp_environment.sh
# ==============================================================================

set -euo pipefail

# 1. Parameter Validation & Defaults
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
if [ -z "${PROJECT_ID}" ]; then
    echo "ERROR: PROJECT_ID environment variable is not set and no active gcloud project found."
    echo "Usage: PROJECT_ID=\"your-project-id\" [REGION=\"us-central1\"] $0"
    exit 1
fi

REGION="${REGION:-us-central1}"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-contract-intelligence}"
BQ_DATASET_ID="${BQ_DATASET_ID:-contract_intelligence}"
SA_NAME="${SA_NAME:-contract-parser-sa}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Set attribution prefix
export CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity"

echo "=============================================================================="
echo " Starting GCP Provisioning for Invenergy Contract Intelligence"
echo " Project ID:     ${PROJECT_ID}"
echo " Region:         ${REGION}"
echo " Storage Bucket: gs://${BUCKET_NAME}"
echo " BigQuery Set:   ${PROJECT_ID}:${BQ_DATASET_ID}"
echo " Service Acct:   ${SA_EMAIL}"
echo "=============================================================================="

# 2. Enable Required GCP APIs
echo "--> [1/5] Enabling Required Google Cloud APIs..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    aiplatform.googleapis.com \
    bigquery.googleapis.com \
    storage.googleapis.com \
    identitytoolkit.googleapis.com \
    cloudtrace.googleapis.com \
    logging.googleapis.com \
    --project="${PROJECT_ID}"

# 3. Create Cloud Storage Bucket
echo "--> [2/5] Creating Cloud Storage Bucket gs://${BUCKET_NAME}..."
if gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "    Bucket gs://${BUCKET_NAME} already exists. Skipping creation."
else
    gcloud storage buckets create "gs://${BUCKET_NAME}" \
        --project="${PROJECT_ID}" \
        --location="${REGION}" \
        --uniform-bucket-level-access
    echo "    Bucket gs://${BUCKET_NAME} successfully created with Uniform Bucket-Level Access."
fi

# 4. Provision BigQuery Dataset & Run DDL Scripts
echo "--> [3/5] Provisioning BigQuery Dataset ${BQ_DATASET_ID}..."
if bq show --project_id="${PROJECT_ID}" "${BQ_DATASET_ID}" >/dev/null 2>&1; then
    echo "    Dataset ${BQ_DATASET_ID} already exists."
else
    bq --location="${REGION}" mk \
        --project_id="${PROJECT_ID}" \
        --dataset \
        --description="Invenergy Contract Intelligence Lakehouse" \
        "${BQ_DATASET_ID}"
    echo "    Dataset ${BQ_DATASET_ID} created."
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_DIR="${SCRIPT_DIR}/sql"

echo "--> [4/5] Executing BigQuery DDL, Deduplication Views, and ERP Seed Scripts..."
echo "    Executing 01_create_tables.sql..."
sed "s/contract_intelligence/${BQ_DATASET_ID}/g" "${SQL_DIR}/01_create_tables.sql" | \
    bq query --use_legacy_sql=false --project_id="${PROJECT_ID}"

echo "    Executing 02_create_views.sql..."
sed "s/contract_intelligence/${BQ_DATASET_ID}/g" "${SQL_DIR}/02_create_views.sql" | \
    bq query --use_legacy_sql=false --project_id="${PROJECT_ID}"

echo "    Executing 03_seed_erp_projects.sql..."
sed "s/contract_intelligence/${BQ_DATASET_ID}/g" "${SQL_DIR}/03_seed_erp_projects.sql" | \
    bq query --use_legacy_sql=false --project_id="${PROJECT_ID}"

# 5. Service Account & IAM Least-Privilege Role Bindings
echo "--> [5/5] Provisioning Dedicated Runtime Service Account & IAM Roles..."
if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "    Service Account ${SA_EMAIL} already exists."
else
    gcloud iam service-accounts create "${SA_NAME}" \
        --project="${PROJECT_ID}" \
        --display-name="Contract Intelligence Runtime SA" \
        --description="Least-privilege execution identity for Contract Intelligence Cloud Run"
    echo "    Created Service Account: ${SA_EMAIL}"
fi

ROLES=(
    "roles/bigquery.dataEditor"
    "roles/bigquery.jobUser"
    "roles/storage.objectAdmin"
    "roles/aiplatform.user"
    "roles/cloudtrace.agent"
    "roles/logging.logWriter"
)

for role in "${ROLES[@]}"; do
    echo "    Binding role: ${role} to ${SA_EMAIL}..."
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${role}" \
        --condition=None >/dev/null
done

echo "=============================================================================="
echo " GCP Infrastructure Provisioning Complete!"
echo " Next Steps:"
echo " 1. Configure Firebase Authentication / Identity Platform Web App credentials."
echo " 2. (Optional) Set up BigQuery Data Analytics Agent URN in BigQuery Studio."
echo " 3. Copy .env.example to .env or deploy directly to Cloud Run:"
echo "    gcloud run deploy contract-parser \\"
echo "      --source . \\"
echo "      --region ${REGION} \\"
echo "      --project ${PROJECT_ID} \\"
echo "      --service-account ${SA_EMAIL} \\"
echo "      --set-env-vars \"GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GCS_BUCKET_NAME=${BUCKET_NAME},BQ_DATASET_ID=${BQ_DATASET_ID}\" \\"
echo "      --allow-unauthenticated"
echo "=============================================================================="
