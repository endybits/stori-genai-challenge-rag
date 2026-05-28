# Tuning Log

Iteration record of the Stori RAG agent. Each entry: what changed, what the evidence showed, what was decided.

---

## Iteration 1 — Index corruption surfaced defensive behavior

While debugging Docker volume permissions, an interrupted ingestion left `chroma_db/` with only the SQLite catalog and no collection UUID. A subsequent eval run returned **0/10 behavior matches** — every query blocked with confidence 0.00 and empty payload.

This was not a regression. With the retriever returning no results, the agent correctly emitted an empty answer at zero confidence and the guardrail blocked. No hallucinations leaked through under corruption — the defensive properties of the design held.

Archived as `evals/results/.archive/20260528T034905Z.json` to keep the evidence of correct behavior under data corruption.

---

## Iteration 2 — Clean re-ingestion

Re-ran `python ingestion.py` against a healthy filesystem. 20 pages, 77 child chunks, 20 parent documents indexed. Eval: **8/10 behavior matches**. All factual, interpretative, out-of-scope, and `ambiguous_02` cases pass.

Result: `evals/results/20260528T035813Z.json`.

---

## Iteration 3 — The remaining 2/10 are blocks by design

`multiturn_01` (Carranza's post-presidency actions) and `multiturn_02` (the drafter of the Plan de Ayala) both self-rated at confidence **0.50** — distinct from the 0.00 of true out-of-scope. Inspecting the raw outputs shows the model produced factually-correct partial answers ("the documents do not specify who drafted it, but they were proclaimed by Zapata") with valid page citations.

The guardrail blocks at 0.85, so these honest-but-incomplete answers are withheld.

**Decision: do not lower the threshold.** 0.50 is the model's truthful signal of partial coverage, not high-confidence hallucination. Blocking conservatively preserves the safety property at the cost of two edge cases that fall in a known boundary. 8/10 with intact safety is preferable to 10/10 obtained by lowering the bar.

The two cases stay in the dataset as ongoing instrumentation for that boundary: if a future corpus update fills the gap, they should naturally promote to confidence ≥ 0.85 and pass without code changes.

---

## Iteration 4 — Test suite caught a latent bypass in the guardrail

While writing unit tests for `agent/guardrail.py`, the suite surfaced a
defensive-layer bug: a JSON payload with `answer: null` and high confidence
passed through the guardrail because `str(None).strip()` evaluates to the
truthy string `"None"`, bypassing the empty-answer check. In practice the
model never emits `null` answer because the prompt forbids it, but the
defensive layer should not rely on that.

Fix: `agent/guardrail.py` now treats `None` as empty alongside `""` and
whitespace-only strings.

This is a small but instructive find: the test suite immediately did the
job it's supposed to do — catch behaviors the request path would have
missed in production.

---

## Metrics in the suite

`behavior_match`, `tool_match`, `confidence_score`, `citation_validity`, `citations_nonempty_match`, `faithfulness` (offline LLM judge over retrieved-vs-claimed grounding).

**Known gap**: no direct Answer Relevance metric (semantic question↔answer alignment). The suite scores grounding and structural correctness but not semantic responsiveness. Listed in README "Improvements with more time."
