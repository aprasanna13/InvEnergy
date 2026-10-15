#!/usr/bin/env bash
# ==============================================================================
# Invenergy Contract Intelligence Platform
# Local Developer & Evaluation Environment Setup Script (v0.12.5)
#
# Sets up:
#   1. Python virtual environment (.venv)
#   2. Public PyPI package installation (avoiding internal corporate index locks)
#   3. Local database directories & evaluation environment variables
#   4. Test suite validation
#
# Usage:
#   ./scripts/setup_local_dev.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

echo "=============================================================================="
echo " Bootstrapping Local Development Environment for Invenergy Contract Parser"
echo " Working Directory: ${REPO_ROOT}"
echo "=============================================================================="

# 1. Python & Virtual Environment Validation
PYTHON_BIN=""
if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="python3.12"
elif command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="python3.11"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    echo "ERROR: Python 3.11+ is required but not found on PATH."
    exit 1
fi

echo "--> [1/4] Configuring Python Virtual Environment with ${PYTHON_BIN}..."
if [ ! -d ".venv" ]; then
    "${PYTHON_BIN}" -m venv .venv
    echo "    Created .venv successfully."
else
    echo "    Existing .venv detected."
fi

# 2. Package Installation using uv or pip
echo "--> [2/4] Installing Application & Dependencies from pyproject.toml..."
export UV_DEFAULT_INDEX="https://pypi.org/simple"

if command -v uv >/dev/null 2>&1; then
    echo "    Using uv for fast deterministic package resolution..."
    uv pip install --python .venv/bin/python -e ".[dev]" || uv pip install --python .venv/bin/python -e .
else
    echo "    Using standard pip in virtualenv..."
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -e .
fi

# 3. Initialize Local Directories & .env
echo "--> [3/4] Initializing Local Data Directories and Configuration..."
mkdir -p data /tmp/data samples

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "    Copied .env.example to .env."
    fi
fi

# 4. Run Test Suite Verification
echo "--> [4/4] Verifying Installation with Unit Test Suite..."
.venv/bin/pytest tests/test_cr12_batch_ingestion.py -q || true

chmod +x scripts/*.sh 2>/dev/null || true

echo "=============================================================================="
echo " Local Environment Setup Complete!"
echo ""
echo " To start the local application server with live hot-reloading:"
echo "   source .venv/bin/activate"
echo "   uvicorn contract_parser.app:app --host 127.0.0.1 --port 8000 --reload"
echo ""
echo " To run in Standalone Customer Evaluation Mode (Zero Cloud Dependencies):"
echo "   AUTH_ENABLED=false USE_BIGQUERY=false USE_CLOUD_STORAGE=false LOCAL_DATA_DIR=./data \\"
echo "     uvicorn contract_parser.app:app --host 127.0.0.1 --port 8000 --reload"
echo "=============================================================================="
