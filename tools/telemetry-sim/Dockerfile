# Synthetic telemetry publisher. Build context: the repository root.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY packages/s2-common/pyproject.toml packages/s2-common/pyproject.toml
COPY services/ontology-engine/pyproject.toml services/ontology-engine/pyproject.toml
COPY services/ingestion-connectors/pyproject.toml services/ingestion-connectors/pyproject.toml
COPY tools/telemetry-sim tools/telemetry-sim
RUN uv sync --frozen --no-dev --package s2-telemetry-sim

# The service runs as a non-root user, so everything copied in must be world-readable
# whatever permissions the files had on the machine that built the image.
RUN chmod -R a+rX /app && useradd --system --uid 10001 --no-create-home s2
USER s2

ENV PATH="/app/.venv/bin:${PATH}"
CMD ["python", "-m", "s2_telemetry_sim"]
