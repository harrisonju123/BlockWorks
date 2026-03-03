FROM python:3.12-slim AS base

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# Dev target: includes dev dependencies, mounts source for hot reload
FROM base AS dev
RUN pip install --no-cache-dir ".[dev]"

# Prod target: minimal image
FROM base AS prod
EXPOSE 8100
CMD ["uvicorn", "agentproof.api.app:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "4"]
