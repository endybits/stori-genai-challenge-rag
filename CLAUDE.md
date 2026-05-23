# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

This is Stori's GenAI Challenge submission: an internal RAG assistant that answers questions about the Mexican Revolution (1910–1917) strictly from provided source PDFs. The design described in `README_TMP.md` is an **Agentic RAG** pattern with a single-pass self-evaluating guardrail — but only the **ingestion pipeline** is implemented today. The agent orchestrator, tools (`knowledge_retriever_tool`, `compliance_flag_tool`), guardrail layer, chat history store, and any API/UI surface still need to be built. Treat `README_TMP.md` as the target architecture spec, not as documentation of the current code.

## Commands

```bash
make install                # pip install -U pip && pip install -r requirements.txt
python ingest.py            # Ingest raw_docs/Sr AI Eng_Challenge_Doc.pdf into ChromaDB + parent store
```

There is no test suite, linter config, or `docker compose` setup yet despite what `README_TMP.md` implies under "Local Deployment Guide".

## Architecture notes

**Hierarchical Parent Document Retrieval** (`ingest.py`) is the core retrieval strategy and the most important thing to preserve:
- Child chunks: 400 chars / 50 overlap — embedded into Chroma for high-precision vector match.
- Parent chunks: 2000 chars / 200 overlap — stored in `LocalFileStore` at `./parent_doc_store/`, returned to the LLM as full context when a child matches.
- Chroma collection name: `mexican_revolution_vt`, persisted at `./croma_db/` (note: misspelled "croma" — keep consistent if you rename).
- PDFs are loaded **page-by-page** as separate `Document`s so page numbers survive into chunk metadata (used for citations).

**RBAC metadata injection** — every ingested doc gets `source`, `ingested_at`, and `access_level: "internal_confidential"` stamped on. The planned compliance flow depends on this metadata being present, so don't strip it when adding new ingestion paths.

**SafeChroma wrapper** (`ingest.py:18`) exists to work around a real bug: the Gemini embeddings provider occasionally returns fewer vectors than inputs, causing an `IndexError` deep inside Chroma. The wrapper pre-filters empty strings and falls back to one-at-a-time inserts on `IndexError`. Don't remove this unless the upstream issue is verified fixed.

**Embeddings provider mismatch** — `requirements.txt` and code use `langchain-google-genai` with `GoogleGenerativeAIEmbeddings` (model `gemini-embedding-2`, env var `GOOGLE_API_KEY`), but `README_TMP.md` says `OPENAI_API_KEY`. The code is the source of truth; the README is aspirational.

**Known issue in requirements.txt:** `python-dotenvv==1.2.2` is a typo — the actual import in code is `python-dotenv`. Fix this before any clean install will succeed.

## Design constraints from the spec

When implementing the remaining pieces, these are non-negotiable per `README_TMP.md`:
- **Single-pass guardrail, not LLM-as-judge.** The primary agent must emit a strict JSON payload `{answer, citations, confidence_score}`; a deterministic Python check blocks responses with `confidence_score < 0.85` and routes them through `compliance_flag_tool`. Do not introduce a second validator LLM — the cost/latency tradeoff is intentional.
- **Closed ingestion.** No public file-upload endpoint; ingestion runs at container startup against `raw_docs/`. Don't add user-facing upload flows.
- **Hexagonal architecture.** Keep the LLM/embedding provider behind a port so it can be swapped (OpenAI ↔ Google ↔ Anthropic) without touching agent logic.
- **Compliance tool logs, doesn't just apologize.** Out-of-scope and low-confidence queries must be persisted for audit, not silently rejected.
