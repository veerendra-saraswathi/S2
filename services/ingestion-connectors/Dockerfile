# S2 ingestion connectors. Build context: the repository root.
#
#   docker build -f services/ingestion-connectors/Dockerfile -t s2/ingestion-connectors .
#
# One image, two processes:
#   python -m s2_ingest.mqtt_bridge       MQTT -> Kafka
#   python -m s2_ingest.timeseries_sink   Kafka -> TimescaleDB

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY services/ontology-engine/pyproject.toml services/ontology-engine/pyproject.toml
COPY services/agent-runtime/pyproject.toml services/agent-runtime/pyproject.toml
COPY services/pipeline-orchestrator/pyproject.toml services/pipeline-orchestrator/pyproject.toml
COPY services/action-executor/pyproject.toml services/action-executor/pyproject.toml
COPY tools/telemetry-sim/pyproject.toml tools/telemetry-sim/pyproject.toml
COPY packages/s2-common packages/s2-common
COPY services/ingestion-connectors services/ingestion-connectors
RUN uv sync --frozen --no-dev --package s2-ingest

# The service runs as a non-root user, so everything copied in must be world-readable
# whatever permissions the files had on the machine that built the image.
RUN chmod -R a+rX /app && useradd --system --uid 10001 --no-create-home s2
USER s2

ENV PATH="/app/.venv/bin:${PATH}"
CMD ["python", "-m", "s2_ingest.mqtt_bridge"]
