#!/usr/bin/env bash
#
# Lightweight entrypoint script to run the Stori RAG container.
#
# Modos:
#  - serve   -> uvicorn (default)
#  - ingest  -> python ingestion.py (job ingest in compose)

set -euo pipefail

# GOOGLE_API_KEY is required in both modes.
if [[ -z "${GOOGLE_API_KEY:-}" ]]; then
    echo "[entrypoint] ERROR: GOOGLE_API_KEY environment variable is not set."
    echo "[entrypoint] Please set GOOGLE_API_KEY to your .env file or environment variable."
    exit 1
fi

CMD="${1:-serve}"

case "$CMD" in
    serve)
        echo "[entrypoint] Starting Stori RAG server..."
        uvicorn app:app --host "${HOST}" --port "${PORT}"
        ;;
    ingest)
        echo "[entrypoint] Starting ingestion process..."
        exec python ingestion.py
        ;;
    *)
        echo "[entrypoint] ERROR: Unknown command '$CMD'."
        echo "[entrypoint] Usage: $0 [serve|ingest]"
        exec "$@"
        ;;
esac