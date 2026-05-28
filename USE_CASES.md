# Use Cases — a guided tour for the reviewer

This document walks through the system's behavior across five categories of user input. Each case includes the input, the expected behavior, and what it demonstrates about the design.

Open the UI at <http://localhost:8000> and run the cases in order — they are ordered to surface the `explain_block_tool` design as the natural climax.

---

## Case 1 — Conversational onboarding

**Input:** `"Hola"`

**Expected behavior:** the agent responds with a brief, friendly acknowledgment plus a scope nudge:

> *"¡Hola! Soy un asistente especializado en la Revolución Mexicana (1910–1917). ¿En qué puedo ayudarte?"*

The response is direct from the model with `confidence_score: 1.0` — no retrieval, no guardrail block.

**What this demonstrates:** scope enforcement is layered, not binary. Greetings, capability questions, and acknowledgments are handled with explicit prompt rules so first contact is not met with a refusal. The bar for "out of scope" is intent, not topic.

**Other inputs that fall into this layer:** *"What can you do?"*, *"Gracias"*, *"Goodbye"*, *"Who are you?"*.

---

## Case 2 — In-scope factual question

**Input:** `"¿Quién fue Francisco Madero?"`

**Expected behavior:** the agent retrieves from the corpus, generates an answer with citations to specific pages, and emits `confidence_score: 1.0`. Response includes structured citations like `[{"source": "mexican_revolution.pdf", "page": 9}]`.

**What this demonstrates:**
- Parent / child retrieval: child chunks match precisely, parent chunks feed the model with surrounding context.
- Page-aware citations: every claim is traceable to a specific page.
- Strict JSON output enforced by the deterministic guardrail.

---

## Case 3 — Multi-turn follow-up

**Input (turn 1):** `"¿Quién fue Venustiano Carranza?"`
**Input (turn 2):** `"¿Y qué hizo durante la Revolución?"`

**Expected behavior:** turn 2 contains no proper noun, but the agent resolves the reference from conversation history. Internally, the model constructs a logically self-contained query (*"What did Venustiano Carranza do during the Mexican Revolution?"*) before invoking the retriever — visible in the server logs as `[agent] retriever query: '...'`.

**What this demonstrates:**
- LangGraph's `AsyncSqliteSaver` checkpointer hydrates full conversation state per turn.
- The model performs query reformulation at tool invocation, not in a separate rewriting node.
- The Chroma collection has no notion of conversation; the agent does.

---

## Case 4 — Out-of-scope question (the guardrail in action)

**Input:** `"¿Cuál es la capital de Japón?"`

**Expected behavior:** the agent does not answer. The model emits `confidence_score: 0.0` with an empty `answer`, the deterministic guardrail blocks the response, and the user sees a generic fallback message:

> *"I'd rather not guess. I only cover the Mexican Revolution (1910–1917). If your question fits, try wording it differently."*

Behind the scenes, `compliance_flag_tool` persists the blocked query, the model's raw output, and the retrieved context (if any) to a SQLite audit table.

**What this demonstrates:**
- Scope enforcement is a deterministic decision rule (Python guardrail) over the model's self-reported `confidence_score`. Here the model reports `0.0` for an out-of-corpus question and the rule blocks it, regardless of how the prose reads. The rule acts on what the model reports about itself: it catches answers the model marks low-confidence; an answer the model states confidently but that is not in the corpus would pass this threshold and is addressed upstream by the prompt and retrieval, not by it.
- Failure modes are observable: the block produces audit data, not a silent refusal.
- The threshold lives in code, not in the prompt — and the prompt instructs the model to emit `0.0` when context is insufficient.

---

## Case 5 — The extended tool: `explain_block_tool` *(the headline)*

**Input (turn 1):** `"¿Cuál es la capital de Japón?"` *(expected: blocked, as in Case 4)*
**Input (turn 2):** `"¿Por qué no pudiste responder?"` *(or:* `"What just happened?"`*)*

**Expected behavior:** the model invokes `explain_block_tool`. The tool reads the most recent audit entry for that conversation from the `flagged_queries` table and returns the structured block reason (category, confidence score, threshold, retrieved pages, blocked-at timestamp). The model articulates that structured data back to the user in natural language:

> *"En mi turno anterior, bloqueé la respuesta porque mi puntuación de confianza fue 0.00 — por debajo del umbral de 0.85. La pregunta sobre Japón está fuera del alcance de mis fuentes, que se limitan a la Revolución Mexicana. No recuperé ningún fragmento relevante del corpus."*

**Why this is the headline.** The challenge brief suggests examples like `conversation_summary`, `classification`, and `human_escalation_trigger`. None of those genuinely extends the model:

- **Summary and classification** are native LLM capabilities under prompting. Wrapping them as tools adds ceremony, not capability.
- **Human escalation** is a legitimate tool but has no plausible business meaning for questions about a historical corpus — it would be theater.

A tool, strictly defined, gives the model a capability it does not have natively. `explain_block_tool` does exactly that by giving the model access to two pieces of information that live outside its context:

1. **The threshold.** The model emits `confidence_score: 0.5` without knowing whether 0.5 is acceptable. The 0.85 threshold is a business decision that lives in Python, not in the model's context. Without the tool, the model could see its own past confidence in conversation history — but not the bar that score was measured against.
2. **The structured audit row.** When the guardrail blocks, only a generic fallback string ("*I'd rather not guess. I only cover the Mexican Revolution…*") reaches the conversation state. The `reason`, retrieved snippets, blocked-at timestamp, and category are written exclusively to the `flagged_queries` table. The state carries a refusal; the audit table carries the why.

The model could in principle say *"I blocked the previous answer"* from history alone. It could not say *"because confidence was 0.0 against an 0.85 threshold, and the snippets I retrieved did not cover the question"* — that structured answer requires both the threshold and the audit row, neither of which the model has.

**Why this matters beyond a historical corpus.** The pattern generalizes. Any system where automated decisions need to be explained back to a human — credit decisioning, fraud detection, KYC, content moderation — needs a structured audit trail outside the model's context window, and a way to query it. The Mexican Revolution corpus is a stand-in for the underlying shape: decision logged, threshold and reason structured, queryable by an explainer tool. Picking summary or classification as the extended tool would have demonstrated none of that.

---

## What to look at after running the cases

To see the trail each case produces:

| Where | What |
|---|---|
| Server logs (`make logs`) | retriever queries, model output pre-guardrail, guardrail verdict |
| `agent_db/flagged_queries.sqlite` (inside the rag container, `make shell`) | one row per block, with raw model output and retrieved context |
| `evals/results/*.json` | per-case behavior, citation validity, confidence scores |
| `tests/test_guardrail.py` (`make test`) | the nine enumerable block reasons the deterministic guardrail can return |

---

## Cases not covered here

These behaviors exist but are out of scope for this walkthrough:

- **Ambiguous queries with no antecedent** (e.g., *"¿y después qué?"* with no prior turn) — the model declines to invoke the retriever, the guardrail blocks at low confidence. Documented as a known edge case in `TUNING.md`.
- **Partial-coverage answers** (e.g., *"¿quién redactó el Plan de Ayala?"*) — the corpus mentions who proclaimed it but not who drafted it. The model self-rates `confidence: 0.50`, the guardrail blocks. Discussed in `TUNING.md` iteration 3 as a block-by-design boundary.

Both are visible in the eval suite output if you want to inspect them.
