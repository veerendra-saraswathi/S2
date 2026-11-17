# S2 pipelines: the lakehouse CLI and Dagster. Build context: the repository root.
#
#   docker build -f services/pipeline-orchestrator/Dockerfile -t s2/pipelines .
#
#   dagster dev -h 0.0.0.0 -p 3000 -m s2_pipelines.definitions    (default)
#   python -m s2_pipelines <command>                                (the CLI)

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    DAGSTER_HOME=/var/lib/dagster

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY services/ontology-engine/pyproject.toml services/ontology-engine/pyproject.toml
COPY services/agent-runtime/pyproject.toml services/agent-runtime/pyproject.toml
COPY services/ingestion-connectors/pyproject.toml services/ingestion-connectors/pyproject.toml
COPY services/action-executor/pyproject.toml services/action-executor/pyproject.toml
COPY tools/telemetry-sim/pyproject.toml tools/telemetry-sim/pyproject.toml
COPY packages/s2-common packages/s2-common
COPY services/pipeline-orchestrator services/pipeline-orchestrator
RUN uv sync --frozen --no-dev --package s2-pipelines

# The service runs as a non-root user, so everything copied in must be world-readable
# whatever permissions the files had on the machine that built the image. Dagster keeps
# its run history in DAGSTER_HOME, which that user must be able to write.
RUN chmod -R a+rX /app && useradd --system --uid 10001 --no-create-home s2 \
    && mkdir -p "$DAGSTER_HOME" && touch "$DAGSTER_HOME/dagster.yaml" && chown -R s2 "$DAGSTER_HOME"
USER s2

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 3000
CMD ["dagster", "dev", "-h", "0.0.0.0", "-p", "3000", "-m", "s2_pipelines.definitions"]
