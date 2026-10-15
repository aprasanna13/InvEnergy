#!/usr/bin/env bash
# ==============================================================================
# Invenergy Contract Intelligence Platform
# Cloud Run Services & Batch Job Deployment Script (v0.12.5)
#
# Builds and deploys:
#   1. Cloud Run Job:         contract-parser-batch (using Dockerfile.batch)
#   2. Cloud Run Web Service: contract-parser       (using Dockerfile / source)
#
# Usage:
#   export PROJECT_ID="your-project-id"
#   export REGION="us-central1"
#   ./scripts/deploy_cloud_run.sh
# ==============================================================================

set -euo pipefail

# 1. Parameter Defaults & Validation
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
if [ -z "${PROJECT_ID}" ]; then
    echo "ERROR: PROJECT_ID environment variable is not set and no active gcloud project found."
    echo "Usage: PROJECT_ID=\"your-project-id\" [REGION=\"us-central1\"] $0"
    exit 1
fi

REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-contract-parser}"
JOB_NAME="${JOB_NAME:-contract-parser-batch}"
VERSION="${VERSION:-0.12.5}"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-contract-intelligence}"
BQ_DATASET_ID="${BQ_DATASET_ID:-contract_intelligence}"
SA_NAME="${SA_NAME:-contract-parser-sa}"
SA_EMAIL="${SA_EMAIL:-${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"

# Check if dedicated service account exists; fallback to default compute SA if not found
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    COMPUTE_NUM="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
    SA_EMAIL="${COMPUTE_NUM}-compute@developer.gserviceaccount.com"
    echo "Notice: ${SA_NAME} not found. Using default compute service account: ${SA_EMAIL}"
fi

# Load optional Firebase / Auth configs from .env if present
FIREBASE_API_KEY="${FIREBASE_API_KEY:-}"
FIREBASE_AUTH_DOMAIN="${FIREBASE_AUTH_DOMAIN:-${PROJECT_ID}.firebaseapp.com}"
ADMIN_EMAIL_WHITELIST="${ADMIN_EMAIL_WHITELIST:-aprasanna@google.com}"
BQ_DATA_AGENT_URN="${BQ_DATA_AGENT_URN:-}"

if [ -f .env ]; then
    echo "Loading existing configuration from .env..."
    # shellcheck disable=SC1091
    source <(grep -E '^(FIREBASE_API_KEY|FIREBASE_AUTH_DOMAIN|ADMIN_EMAIL_WHITELIST|BQ_DATA_AGENT_URN)=' .env || true)
fi

echo "=============================================================================="
echo " Deploying Invenergy Contract Intelligence (v${VERSION})"
echo " Project:          ${PROJECT_ID}"
echo " Region:           ${REGION}"
echo " Web Service:      ${SERVICE_NAME}"
echo " Batch Job:        ${JOB_NAME}"
echo " Service Account:  ${SA_EMAIL}"
echo " Storage Bucket:   gs://${BUCKET_NAME}"
echo " BigQuery Dataset: ${BQ_DATASET_ID}"
echo "=============================================================================="

# 2. Build & Deploy Batch Job (contract-parser-batch)
echo "--> [1/2] Building and Deploying Cloud Run Job: ${JOB_NAME}..."
JOB_IMAGE="gcr.io/${PROJECT_ID}/${JOB_NAME}:v${VERSION}"

echo "    Building container image ${JOB_IMAGE} using Dockerfile.batch..."
gcloud builds submit --project="${PROJECT_ID}" --tag="${JOB_IMAGE}" -f Dockerfile.batch .

echo "    Configuring Cloud Run Job: ${JOB_NAME}..."
if gcloud run jobs describe "${JOB_NAME}" --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud run jobs update "${JOB_NAME}" \
        --image="${JOB_IMAGE}" \
        --region="${REGION}" \
        --project="${PROJECT_ID}" \
        --service-account="${SA_EMAIL}" \
        --cpu=2 \
        --memory=4Gi \
        --max-retries=0 \
        --task-timeout=3600 \
        --update-env-vars="\
GOOGLE_CLOUD_PROJECT=${PROJECT_ID},\
GOOGLE_CLOUD_LOCATION=global,\
USE_BIGQUERY=true,\
BQ_DATASET_ID=${BQ_DATASET_ID},\
GCS_BUCKET_NAME=${BUCKET_NAME},\
AUTH_ENABLED=true,\
FIREBASE_API_KEY=${FIREBASE_API_KEY},\
FIREBASE_AUTH_DOMAIN=${FIREBASE_AUTH_DOMAIN},\
ADMIN_EMAIL_WHITELIST=${ADMIN_EMAIL_WHITELIST}"
    echo "    Updated Cloud Run Job: ${JOB_NAME}"
else
    gcloud run jobs create "${JOB_NAME}" \
        --image="${JOB_IMAGE}" \
        --region="${REGION}" \
        --project="${PROJECT_ID}" \
        --service-account="${SA_EMAIL}" \
        --cpu=2 \
        --memory=4Gi \
        --max-retries=0 \
        --task-timeout=3600 \
        --set-env-vars="\
GOOGLE_CLOUD_PROJECT=${PROJECT_ID},\
GOOGLE_CLOUD_LOCATION=global,\
USE_BIGQUERY=true,\
BQ_DATASET_ID=${BQ_DATASET_ID},\
GCS_BUCKET_NAME=${BUCKET_NAME},\
AUTH_ENABLED=true,\
FIREBASE_API_KEY=${FIREBASE_API_KEY},\
FIREBASE_AUTH_DOMAIN=${FIREBASE_AUTH_DOMAIN},\
ADMIN_EMAIL_WHITELIST=${ADMIN_EMAIL_WHITELIST}"
    echo "    Created Cloud Run Job: ${JOB_NAME}"
fi

# 3. Deploy Cloud Run Web Service (contract-parser)
echo "--> [2/2] Deploying Cloud Run Web Service: ${SERVICE_NAME}..."
gcloud run deploy "${SERVICE_NAME}" \
    --source . \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --service-account="${SA_EMAIL}" \
    --memory=2Gi \
    --cpu=2 \
    --timeout=600 \
    --update-env-vars="\
GOOGLE_CLOUD_PROJECT=${PROJECT_ID},\
GOOGLE_CLOUD_LOCATION=global,\
GOOGLE_GENAI_USE_ENTERPRISE=true,\
REGION=${REGION},\
USE_BIGQUERY=true,\
BQ_DATASET_ID=${BQ_DATASET_ID},\
GCS_BUCKET_NAME=${BUCKET_NAME},\
AUTH_ENABLED=true,\
FIREBASE_API_KEY=${FIREBASE_API_KEY},\
FIREBASE_AUTH_DOMAIN=${FIREBASE_AUTH_DOMAIN},\
ADMIN_EMAIL_WHITELIST=${ADMIN_EMAIL_WHITELIST},\
USE_LOCAL_BATCH_RUNNER=false,\
FAST_DISCOVERY_MODEL=gemini-3.8-flash,\
GEMINI_MODEL=gemini-3.1-pro-preview,\
GEMINI_FALLBACK_MODEL=gemini-3.8-flash,\
MULTI_PASS_ENABLED=true,\
PASS_TIMEOUT_SECONDS=480.0,\
GEMINI_MAX_OUTPUT_TOKENS=65536,\
LOCAL_DATA_DIR=/tmp/data,\
LOG_FORMAT=json,\
LOG_LEVEL=INFO,\
CLOUD_TRACE_ENABLED=true,\
CLOUD_TRACE_SCHEDULE_DELAY_MS=500,\
TELEMETRY_SERVICE_NAME=${SERVICE_NAME},\
TELEMETRY_SERVICE_VERSION=${VERSION},\
TELEMETRY_SAMPLE_RATE=1.0,\
BQ_DATA_AGENT_URN=${BQ_DATA_AGENT_URN}" \
    --allow-unauthenticated \
    --quiet

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)')"

echo "=============================================================================="
echo " Deployment Successfully Completed!"
echo " Web Service URL: ${SERVICE_URL}"
echo " Cloud Run Job:   ${JOB_NAME} (${REGION})"
echo "=============================================================================="
