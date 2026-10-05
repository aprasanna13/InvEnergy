FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    LOCAL_DATA_DIR=/tmp/data

WORKDIR /app

# Copy project manifest, design doc required by hatchling build, and package source
COPY pyproject.toml .
COPY Design/ Design/
COPY contract_parser/ contract_parser/
COPY samples/ samples/

# Install the application and dependencies
RUN pip install --no-cache-dir .

# Create writable tmp data directory for ephemeral SQLite and exports
RUN mkdir -p /tmp/data

# Cloud Run injects $PORT (defaulting to 8080) and listens on 0.0.0.0
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn contract_parser.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
