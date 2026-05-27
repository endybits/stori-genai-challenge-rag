# syntax=docker/dockerfile:1.6
#
# Stori GenAI Challenge — RAG sobre la Revolución Mexicana
#
# Build:    docker build -t stori-rag .
# Ingest:   docker compose run --rm ingest      (one-time, via volume)
# Serve:    docker compose up -d                (instant: index already on volume)
# Or just:  make up

FROM python:3.11-slim as base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# No root user
RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app

# Dependencies layer (cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

# Application layer
COPY --chown=app:app . .

# Entrypoint permissions
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /app/chroma_db /app/parent_doc_store \
    && chown -R app:app /app/chroma_db /app/parent_doc_store

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    HOST=0.0.0.0

USER app

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["serve"]
