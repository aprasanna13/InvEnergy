#!/usr/bin/env bash
# ==============================================================================
# Invenergy Contract Intelligence Platform
# Lakehouse & Cloud Storage Cleanup Utility (v0.12.5)
#
# Cleans up:
#   1. BigQuery 14 base tables (truncates parsed contract records)
#   2. Restores baseline ERP development asset projects (03_seed_erp_projects.sql)
#   3. Cloud Storage bucket (purges raw/, archive/, exports/ while preserving batch_inbox/)
#   4. Local SQLite development database (data/contract_intelligence.db)
#
# Usage:
#   export PROJECT_ID="your-project-id"
#   ./scripts/cleanup_environment.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
if [ -z "${PROJECT_ID}" ]; then
    echo "ERROR: PROJECT_ID environment variable is not set and no active gcloud project found."
    echo "Usage: PROJECT_ID=\"your-project-id\" $0"
    exit 1
fi

BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-contract-intelligence}"
BQ_DATASET_ID="${BQ_DATASET_ID:-contract_intelligence}"

echo "=============================================================================="
echo " Starting Lakehouse & Storage Cleanup"
echo " Project:          ${PROJECT_ID}"
echo " Storage Bucket:   gs://${BUCKET_NAME}"
echo " BigQuery Dataset: ${PROJECT_ID}:${BQ_DATASET_ID}"
echo " Preserved Folder: gs://${BUCKET_NAME}/batch_inbox/"
echo "=============================================================================="

# 1. BigQuery Tables Truncation
echo "--> [1/4] Truncating BigQuery Lakehouse Base Tables..."
bq query --use_legacy_sql=false --project_id="${PROJECT_ID}" "
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.batch_job_items\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.batch_jobs\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.clauses\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.clause_variance_details\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.contract_variances\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.defined_terms\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.dnd_checklist_signoffs\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.documents\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.exhibits_catalog\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.landowners\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.special_conditions\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.template_clauses\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.template_registry\`;
TRUNCATE TABLE \`${PROJECT_ID}.${BQ_DATASET_ID}.projects\`;
"

# 2. Re-seed Baseline ERP Portfolio Projects
echo "--> [2/4] Restoring Baseline ERP Portfolio Projects..."
sed "s/contract_intelligence/${BQ_DATASET_ID}/g" "${SCRIPT_DIR}/sql/03_seed_erp_projects.sql" | \
    bq query --use_legacy_sql=false --project_id="${PROJECT_ID}"

# 3. Clean Cloud Storage Objects (excluding batch_inbox/)
echo "--> [3/4] Purging Cloud Storage Objects in raw/, archive/, and exports/..."
for folder in "raw" "archive" "exports"; do
    if gcloud storage ls "gs://${BUCKET_NAME}/${folder}/**" >/dev/null 2>&1; then
        echo "    Removing gs://${BUCKET_NAME}/${folder}/..."
        gcloud storage rm --recursive "gs://${BUCKET_NAME}/${folder}/" || true
    else
        echo "    Folder gs://${BUCKET_NAME}/${folder}/ is already empty."
    fi
done

# 4. Clean Local SQLite Database
echo "--> [4/4] Cleaning Local SQLite Database..."
LOCAL_DB="${REPO_ROOT}/data/contract_intelligence.db"
if [ -f "${LOCAL_DB}" ]; then
    python3 -c "
import sqlite3, os
p = '${LOCAL_DB}'
if os.path.exists(p):
    conn = sqlite3.connect(p)
    cur = conn.cursor()
    cur.execute(\"SELECT name FROM sqlite_master WHERE type='table';\")
    tables = [r[0] for r in cur.fetchall() if not r[0].startswith('sqlite_')]
    for t in tables:
        cur.execute(f'DELETE FROM {t};')
    conn.commit()
    conn.close()
    print('    Local SQLite tables cleared.')
" || true
fi

echo "=============================================================================="
echo " Cleanup Complete! Environment is fresh and ready for batch extraction runs."
echo "=============================================================================="
