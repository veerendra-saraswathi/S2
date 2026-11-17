# S2 ontology engine. Build context: the repository root.
#
#   docker build -f services/ontology-engine/Dockerfile -t s2/ontology-engine .
#
# The same image runs the API (default command) and the migrations
# (`alembic upgrade head`, from this WORKDIR).

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

# The workspace definition and lock file, then only what this service needs. uv has to
# see every workspace member's pyproject.toml, but builds only the selected package.
COPY pyproject.toml uv.lock .python-version ./
COPY services/ingestion-connectors/pyproject.toml services/ingestion-connectors/pyproject.toml
COPY services/agent-runtime/pyproject.toml services/agent-runtime/pyproject.toml
COPY services/action-executor/pyproject.toml services/action-executor/pyproject.toml
COPY services/pipeline-orchestrator/pyproject.toml services/pipeline-orchestrator/pyproject.toml
COPY tools/telemetry-sim/pyproject.toml tools/telemetry-sim/pyproject.toml
COPY packages/s2-common packages/s2-common
COPY services/ontology-engine services/ontology-engine
RUN uv sync --frozen --no-dev --package s2-ontology

# The shipped ontology packs, so `s2ctl apply-pack` works inside the container.
COPY ontologies ontologies

# The service runs as a non-root user, so everything copied in must be world-readable
# whatever permissions the files had on the machine that built the image.
RUN chmod -R a+rX /app && useradd --system --uid 10001 --no-create-home s2
USER s2

ENV PATH="/app/.venv/bin:${PATH}"
WORKDIR /app/services/ontology-engine
EXPOSE 8000
CMD ["uvicorn", "s2_ontology.main:app", "--host", "0.0.0.0", "--port", "8000"]
