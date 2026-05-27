# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

This is Stori's GenAI Challenge submission: an internal RAG assistant that answers questions about the Mexican Revolution (1910–1917) strictly from provided source PDFs. The design described in `README.md` is an **Agentic RAG** pattern with a single-pass self-evaluating guardrail — but only the **ingestion pipeline** is implemented today. The agent orchestrator, tools (`knowledge_retriever_tool`, `compliance_flag_tool`), guardrail layer, chat history store, and any API/UI surface still need to be built. Treat `README.md` as the target architecture spec, not as documentation of the current code.

## Commands

```bash
make install                # pip install -U pip && pip install -r requirements.txt
python ingestion.py         # Ingest raw_docs/mexican_revolution.pdf into ChromaDB + parent store
```

There is no test suite, linter config, or `docker compose` setup yet despite what `README.md` implies under "Local Deployment".

## Architecture notes

**Hierarchical Parent Document Retrieval** (`ingestion.py`) is the core retrieval strategy and the most important thing to preserve:
- Child chunks: 400 chars / 50 overlap — embedded into Chroma for high-precision vector match.
- Parent chunks: 2000 chars / 200 overlap — stored in `LocalFileStore` at `./parent_doc_store/`, returned to the LLM as full context when a child matches.
- Chroma collection name: `mexican_revolution_vt`, persisted at `./chroma_db/`.
- PDFs are loaded **page-by-page** as separate `Document`s so page numbers survive into chunk metadata (used for citations).

**RBAC metadata injection** — every ingested doc gets `source`, `ingested_at`, and `access_level: "internal_confidential"` stamped on. The planned compliance flow depends on this metadata being present, so don't strip it when adding new ingestion paths.

**SafeChroma wrapper** (`ingestion.py:21`) exists to work around a real bug: the Gemini embeddings provider occasionally returns fewer vectors than inputs, causing an `IndexError` deep inside Chroma. The wrapper pre-filters empty strings and falls back to one-at-a-time inserts on `IndexError`. Don't remove this unless the upstream issue is verified fixed.

**Embeddings provider.** Code uses `langchain-google-genai` with `GoogleGenerativeAIEmbeddings`. The model is configurable via the `EMBEDDING_MODEL` env var, defaulting to `gemini-embedding-2`. Validated fallback if the default ever 404s: `gemini-embedding-001`. `GOOGLE_API_KEY` is required.

**Idempotency guard** (`ingestion.py:154`) — `ingest_document` distinguishes three states *before* opening a Chroma client (opening one and then `rmtree`-ing the persist dir leaves a stale readonly SQLite handle that breaks the re-ingest path):
1. Completion marker (`./chroma_db/.ingest_complete`) present + Chroma persistence on disk → skip.
2. Chroma persistence on disk but no marker → previous run was interrupted mid-batch, wipe both stores and re-ingest.
3. Fresh → ingest, then write the marker with `{source, pages, chunks, ingested_at}`.

Don't replace the marker check with a naive `count() > 0` — a partial ingestion has positive count but is an incomplete corpus, and silently accepting it is worse than no guard at all.

## Design constraints from the spec

When implementing the remaining pieces, these are non-negotiable per `README.md`:
- **Single-pass guardrail, not LLM-as-judge.** The primary agent must emit a strict JSON payload `{answer, citations, confidence_score}`; a deterministic Python check blocks responses with `confidence_score < 0.85` and routes them through `compliance_flag_tool`. Do not introduce a second validator LLM — the cost/latency tradeoff is intentional.
- **Closed ingestion.** No public file-upload endpoint; ingestion runs at container startup against `raw_docs/`. Don't add user-facing upload flows.
- **Hexagonal architecture.** Keep the LLM/embedding provider behind a port so it can be swapped (OpenAI ↔ Google ↔ Anthropic) without touching agent logic.
- **Compliance tool logs, doesn't just apologize.** Out-of-scope and low-confidence queries must be persisted for audit, not silently rejected.
