# Stori GenAI Challenge — Makefile


.DEFAULT_GOAL := help
.PHONY: help install build ingest up down restart logs status clean eval test shell

help:  ## All available commands.
	@echo "Stori GenAI Challenge — All available commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Quick start:"
	@echo "  cp .env.example .env   # then edit .env with your GOOGLE_API_KEY"
	@echo "  make up                # builds + ingests + serves on :8000"
	@echo ""


install:
	pip install -U pip && \
	pip install -r requirements.txt


# === Docker workflows ===

build:  ## Builds the Docker image
	docker compose build

ingest:  ## Runs the ingestion as an ephemeral job (idempotent)
	docker compose run --rm ingest

up:  ## Starts the complete system (ingestion + server)
	docker compose up -d
	@echo ""
	@echo "✓ Stori RAG running at http://localhost:8000"
	@echo "  Health:  curl http://localhost:8000/health"
	@echo "  Logs:    make logs"
	@echo "  Stop:    make down"

down:  ## Stops the containers (PRESERVES the index in volumes)
	docker compose down

restart:  ## Restarts only the server (without re-ingesting)
	docker compose restart rag

logs:  ## Follows the server logs in real-time
	docker compose logs -f rag

status:  ## Shows the status of containers and volumes
	@echo "── Containers ──"
	@docker compose ps
	@echo ""
	@echo "── Volumes ──"
	@docker volume ls --filter "name=stori"

clean:  ## RESET TOTAL: deletes containers AND volumes (will force re-ingestion)
	docker compose down -v
	@echo "✓ Clean state. The next 'make up' will re-ingest from scratch."

# === Quality ===
eval:  ## Runs the offline evaluation suite
	docker compose run --rm rag python -m evals.run

test:  ## Runs unit tests (when available)
	docker compose run --rm rag pytest tests/ -v

shell:  ## Opens a shell inside the app container
	docker compose exec rag /bin/bash