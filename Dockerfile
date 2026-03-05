FROM python:3.12-slim AS base

# slim images strip ca-certificates; needed for TLS to upstream APIs (e.g. api.anthropic.com)
# Also inject corporate Zscaler root CA so TLS inspection doesn't break outbound connections
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY zscaler-root-ca.pem /usr/local/share/ca-certificates/zscaler-root-ca.crt
RUN update-ca-certificates

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# httpx uses certifi's bundle, not the OS store — append Zscaler CA so it's trusted there too
RUN python -c "import certifi; open(certifi.where(), 'a').write(open('/usr/local/share/ca-certificates/zscaler-root-ca.crt').read())"

# Dev target: editable install so volume-mounted src/ is used directly
FROM base AS dev
RUN pip install --no-cache-dir -e ".[dev,blockchain]"

# Prod target: minimal image
FROM base AS prod
EXPOSE 8100
CMD ["uvicorn", "blockthrough.api.app:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "4"]
