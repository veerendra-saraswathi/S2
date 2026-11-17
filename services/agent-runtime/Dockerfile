# S2 agent. Build context: the repository root.
#
#   docker build -f services/agent-runtime/Dockerfile -t s2/agent .
#
#   python -m s2_agents loop    (default)
#   python -m s2_agents once

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
COPY services/ingestion-connectors/pyproject.toml services/ingestion-connectors/pyproject.toml
COPY services/action-executor/pyproject.toml services/action-executor/pyproject.toml
COPY services/pipeline-orchestrator/pyproject.toml services/pipeline-orchestrator/pyproject.toml
COPY tools/telemetry-sim/pyproject.toml tools/telemetry-sim/pyproject.toml
COPY packages/s2-common packages/s2-common
COPY services/agent-runtime services/agent-runtime
RUN uv sync --frozen --no-dev --package s2-agents

RUN chmod -R a+rX /app && useradd --system --uid 10001 --no-create-home s2
USER s2

ENV PATH="/app/.venv/bin:${PATH}"
CMD ["python", "-m", "s2_agents", "loop"]
