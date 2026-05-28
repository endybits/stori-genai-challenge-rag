# Architecture Decisions

Record of non-trivial design decisions for the Stori GenAI Challenge.
Each entry captures **context** (what forced the decision) and **decision** (what we chose). Trade-offs and future-state thinking live in the README.

---

## Index

1. [ADR-001 — Closed ingestion (no public upload endpoint)](#adr-001--closed-ingestion-no-public-upload-endpoint)
2. [ADR-002 — Single-pass deterministic guardrail](#adr-002--single-pass-deterministic-guardrail)
3. [ADR-003 — Parent Document Retrieval over flat chunking](#adr-003--parent-document-retrieval-over-flat-chunking)
4. [ADR-004 — Extended tool: `explain_block_tool`](#adr-004--extended-tool-explain_block_tool)
5. [ADR-005 — Docker: ingestion as a separate job, root-for-fixups with gosu](#adr-005--docker-ingestion-as-a-separate-job-root-for-fixups-with-gosu)

---

## ADR-001 — Closed ingestion (no public upload endpoint)

**Context**
- Internal GenAI assistant over a single curated document.
- An open upload endpoint would expand the system's surface area (auth, file validation, malware scanning, rate limiting) for capabilities the use case doesn't require. For a curated corpus, ingestion is a deployment-time concern, not a runtime one; mixing them inflates attack surface and breaks separation of concerns.

**Decision**
- Ingestion runs as a one-off CLI / containerized job, never as an HTTP endpoint. The API exposes only chat and health.
- Re-ingestion is explicit and idempotent — a completion marker lets repeated runs short-circuit instead of re-embedding.

---

## ADR-002 — Single-pass deterministic guardrail

**Context**
- Output validation has two canonical patterns: (a) a second LLM as judge in the request path, (b) a deterministic validator enforcing structure and a confidence threshold.
- Pattern (a) doubles latency and token cost, makes the safety layer itself non-deterministic (the judge can hallucinate), and produces an opaque audit trail.
- The challenge constrains the agent to refuse out-of-scope questions — best enforced with enumerable rules, not semantic judgment.
- In fintech, this aligns with compliance expectations: every block reason is enumerable, persistable, and auditable — no opaque LLM "trust" calls in the request path.

**Decision**
- A single-pass deterministic guardrail validates the model's structured JSON output and blocks under enumerable, named reasons (parse failures, missing fields, confidence below threshold, structural malformations).
- LLM-as-judge exists only offline in the eval suite, never in the request path.
- 0.85 chosen conservatively. The current dataset is bimodal — scores cluster at 0.0 (out-of-scope) and 1.0 (well-grounded), with two mid-range observations at 0.50 (partial-coverage follow-ups). The threshold has not been calibrated against intermediate scores; closing that gap requires expanding the dataset with partial-coverage cases, listed under Improvements (README §1).
- Provider constraint: Gemini rejects function calling combined with forced JSON response mode (`400 INVALID_ARGUMENT`). Mitigated with a strict system prompt, `temperature=0`, and guardrail blocks on parse failure — JSON shape is enforced from the prompt side, not from the API.

**Structural complement.** This guardrail operates on a self-reported signal. The retriever upstream returns top-k by vector similarity without an independent relevance threshold, so the judgment that the retrieved context is "sufficient" is delegated to the LLM. A more robust RAG would add a similarity floor or a small re-ranker before the LLM sees the context, so an irrelevant retrieval is rejected without involving the model's confidence at all. That is the next layer of structural defense — listed under Improvements (README §1) — and is what the current single-pass guardrail does not provide on its own.

---

## ADR-003 — Parent Document Retrieval over flat chunking

**Context**
- Flat chunking forces a single chunk size to do two opposing jobs: small enough for vector precision, large enough for generation context.
- Small chunks (~300–500 chars) match precisely but starve the model of surrounding context. Large chunks (~1500–2500 chars) carry context but dilute the embedding signal. No middle ground satisfies both.

**Decision**
- Two-tier chunking: child chunks (~400 chars) embedded for vector search, parent chunks (~2000 chars) stored separately and returned to the model after a child match.
- The matcher works with tight chunks for precision; the model sees the wider parent for generation. Page numbers survive as metadata and feed the citation field.

---

## ADR-004 — Extended tool: `explain_block_tool`

**Context**
- Challenge requires at least one tool that extends utility, suggesting `conversation_summary`, `classification`, `human_escalation_trigger`.
- A tool, strictly defined, extends the model with capabilities it **lacks** natively: (1) external information access, (2) deterministic computation, (3) world side-effects, (4) access to internal system state.
- Summary and classification are native LLM capabilities under prompting — wrapping them as tools adds ceremony, not capability.
- Escalation is a legitimate tool but lacks domain fit here: the system serves curated historical questions, not a support workflow where a human adds downstream value. The escalation would be theater.
- Explainability of automated decisions, on the other hand, is a real product requirement in fintech — internal users hitting a block need to understand the limit, same logic that applies to credit decisioning, fraud detection, KYC.

**Decision**
- Implement `explain_block_tool`. When the user asks why a previous answer was blocked, the tool reads the latest audit entry for that conversation and returns the structured reason (category, confidence score, threshold, retrieved pages and snippets, timestamp). The model articulates it in natural language.
- Falls under category 4: the structured block reason lives in persistent storage, outside the model's context. The model cannot answer "why couldn't you respond before?" without it.

---

## ADR-005 — Docker: ingestion as a separate job, root-for-fixups with gosu

**Context**
- Two coupled questions: when do embeddings get generated, and how do non-root containers handle persistent volumes.
- Build-time ingestion bakes secrets into build args and ties the image to a corpus snapshot. Server-startup ingestion inflates cold-start to ~2 minutes on every container restart — unacceptable for horizontal scaling.
- Named volumes mount as `root:root`, shadowing the `chown` declared in the Dockerfile and breaking writes from non-root users.

**Decision**
- Ingestion runs as a separate compose service that exits on completion. The app starts only after (`depends_on: service_completed_successfully`). Cold-start is seconds, not minutes.
- Container starts as `root` solely to fix volume ownership, then drops to a non-root user via `gosu`. Same pattern as official `postgres`, `mysql`, and `redis` images.
- Vector store and parent document store persist in named volumes. The SQLite database stays ephemeral by design — in production it would move to managed services for state and audit, and persisting it locally would contradict that target architecture.
