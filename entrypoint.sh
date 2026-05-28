#!/usr/bin/env bash
#
# Lightweight entrypoint for the Stori RAG container.
#
# Modes:
#   serve   -> uvicorn (default)
#   ingest  -> python ingestion.py (used by the ingest service in compose)

set -euo pipefail

if [[ "$(id -u)" == "0" ]]; then
    chown -R app:app /app/chroma_db /app/parent_doc_store /app/agent_db /app/evals/results 2>/dev/null || true
    exec gosu app "$0" "$@"
fi

if [[ -z "${GOOGLE_API_KEY:-}" ]]; then
    echo "[entrypoint] GOOGLE_API_KEY is not set." >&2
    exit 1
fi

CMD="${1:-serve}"

case "$CMD" in
    serve)
        echo "[entrypoint] Starting Stori RAG server..."
        exec uvicorn app:app --host "${HOST}" --port "${PORT}"
        ;;
    ingest)
        echo "[entrypoint] Starting ingestion process..."
        exec python ingestion.py
        ;;
    *)
        exec "$@"
        ;;
esac