"""Offline eval pipeline for the Mexican Revolution RAG agent.

Loads `evals/dataset.yaml`, replays each query against the compiled graph using
an ephemeral checkpointer (kept out of `agent_db/` so the production audit DB
is not contaminated), and scores four metrics:

  1. behavior_match      — did the guardrail block/answer as expected?
  2. tool_call           — was knowledge_retriever_tool invoked?
  3. citation_validity   — are reported citations a subset of retrieved pages?
                            Subset, not equality: the model may legitimately
                            cite the same page for multiple claims.
  4. confidence_score    — raw score, reported per query (calibration signal).

Faithfulness as semantic grounding (LLM-as-judge) is intentionally NOT measured
here — see README §3. The citation_validity metric only verifies citations are
reachable, not that the prose itself is grounded.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

# Make the project root importable when run as `python evals/run.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.graph import build_graph  # noqa: E402
from evals.judge import score_faithfulness  # noqa: E402

logging.getLogger("agent").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


DATASET_PATH = Path(__file__).parent / "dataset.yaml"
RESULTS_DIR = Path(__file__).parent / "results"


def _parse_tool_header(block: str) -> tuple[str, int] | None:
    """Pull (source, page) out of a `[source=..., page=N]` header line.

    Returns None on malformed input — the eval treats those as no-citation.
    """
    if not block.startswith("[") or "]" not in block:
        return None
    header = block[1 : block.index("]")]
    fields = {}
    for part in header.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        fields[k.strip()] = v.strip()
    src = fields.get("source")
    page_raw = fields.get("page")
    if src is None or page_raw is None:
        return None
    try:
        return src, int(page_raw)
    except ValueError:
        return None


def _retrieved_pages_from_tool_messages(messages: list) -> set[tuple[str, int]]:
    """Collect every (source, page) the retriever surfaced across this turn.

    knowledge_retriever_tool joins blocks with `\\n---\\n` and prefixes each
    with a `[source=..., page=...]` header; we reverse that to get the set
    of citation targets the model had access to.
    """
    pages: set[tuple[str, int]] = set()
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        content = m.content if isinstance(m.content, str) else str(m.content)
        if content == "NO_RESULTS":
            continue
        for block in content.split("\n---\n"):
            parsed = _parse_tool_header(block)
            if parsed is not None:
                pages.add(parsed)
    return pages


def _tool_was_called(messages: list) -> bool:
    """Heuristic: any ToolMessage in the trace means a tool ran.

    `knowledge_retriever_tool` is the only tool bound to the LLM today, so a
    ToolMessage uniquely identifies it. Update this if more tools are bound.
    """
    return any(isinstance(m, ToolMessage) for m in messages)


def _retrieved_context_from_tool_messages(messages: list) -> str:
    """Concatenate every tool output into a single string for the judge."""
    blocks = []
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        content = m.content if isinstance(m.content, str) else str(m.content)
        if content and content != "NO_RESULTS":
            blocks.append(content)
    return "\n---\n".join(blocks)


def _eval_query(state: dict, expected: dict, run_judge: bool) -> dict:
    messages = state.get("messages", [])
    blocked = bool(state.get("blocked", False))
    answer = state.get("answer", "") or ""
    citations = state.get("citations", []) or []
    confidence = float(state.get("confidence_score", 0.0))

    actual_behavior = "block" if blocked else "answer"
    behavior_match = actual_behavior == expected["behavior"]

    tool_called = _tool_was_called(messages)
    tool_match = tool_called == expected["tool_call"]

    reported = {(c["source"], int(c["page"])) for c in citations}
    retrieved = _retrieved_pages_from_tool_messages(messages)
    # citation_validity is N/A when the agent blocked (no citations expected)
    # or when no tool ran at all.
    if blocked or not retrieved:
        citation_validity = None
    else:
        citation_validity = reported.issubset(retrieved) if reported else False

    citations_nonempty_expected = expected.get("citations_nonempty")
    if citations_nonempty_expected is None:
        citations_nonempty_match = None
    else:
        citations_nonempty_match = bool(reported) == citations_nonempty_expected

    # Faithfulness via LLM-as-judge — offline only. Skip when the agent
    # blocked (no answer to grade) or when the judge is disabled.
    if run_judge and not blocked and answer:
        context = _retrieved_context_from_tool_messages(messages)
        faithfulness = score_faithfulness(answer, context)
    else:
        faithfulness = {
            "claims": [],
            "score": None,
            "n_claims": 0,
            "n_supported": 0,
            "error": None,
        }

    return {
        "actual_behavior": actual_behavior,
        "behavior_match": behavior_match,
        "tool_called": tool_called,
        "tool_match": tool_match,
        "confidence_score": confidence,
        "answer": answer,
        "citations_reported": sorted(reported),
        "citations_retrieved": sorted(retrieved),
        "citation_validity": citation_validity,
        "citations_nonempty_match": citations_nonempty_match,
        "faithfulness": faithfulness,
    }


async def _run_dataset(dataset: dict, run_judge: bool) -> list[dict]:
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="eval_ckpt_") as tmpdir:
        ckpt_path = os.path.join(tmpdir, "checkpointer.sqlite")
        async with AsyncSqliteSaver.from_conn_string(ckpt_path) as checkpointer:
            await checkpointer.setup()
            graph = build_graph(checkpointer)

            for item in dataset["queries"]:
                qid = item["id"]
                conversation_id = f"eval-{qid}"
                config = {
                    "configurable": {"thread_id": conversation_id},
                    "recursion_limit": 12,
                }
                final_state = None
                for turn_idx, user_text in enumerate(item["turns"]):
                    final_state = await graph.ainvoke(
                        {
                            "messages": [HumanMessage(content=user_text)],
                            "conversation_id": conversation_id,
                        },
                        config=config,
                    )
                    print(
                        f"  · {qid} turn{turn_idx + 1}: "
                        f"blocked={final_state.get('blocked')} "
                        f"score={final_state.get('confidence_score'):.2f}"
                    )

                metrics = _eval_query(final_state, item["expected"], run_judge)
                results.append(
                    {
                        "id": qid,
                        "category": item["category"],
                        "turns": item["turns"],
                        "expected": item["expected"],
                        **metrics,
                    }
                )
    return results


def _render_table(results: list[dict]) -> str:
    rows = []
    header = (
        f"{'id':<18}{'category':<26}{'beh':<5}{'tool':<6}"
        f"{'cite':<6}{'faith':<14}{'conf':<6}"
    )
    rows.append(header)
    rows.append("-" * len(header))
    for r in results:
        beh = "OK" if r["behavior_match"] else "FAIL"
        tool = "OK" if r["tool_match"] else "FAIL"
        cv = r["citation_validity"]
        cite = "n/a" if cv is None else ("OK" if cv else "FAIL")
        f_score = r["faithfulness"]["score"]
        if f_score is None:
            faith = "n/a"
        else:
            faith = f"{f_score:.2f} ({r['faithfulness']['n_supported']}/{r['faithfulness']['n_claims']})"
        conf = f"{r['confidence_score']:.2f}"
        rows.append(
            f"{r['id']:<18}{r['category']:<26}{beh:<5}{tool:<6}{cite:<6}{faith:<14}{conf:<6}"
        )
    return "\n".join(rows)


def _aggregate(results: list[dict]) -> dict:
    by_cat: dict[str, dict] = {}
    for r in results:
        cat = r["category"]
        b = by_cat.setdefault(
            cat,
            {
                "n": 0,
                "behavior_pass": 0,
                "tool_pass": 0,
                "cite_pass": 0,
                "cite_n": 0,
                "faith_sum": 0.0,
                "faith_n": 0,
            },
        )
        b["n"] += 1
        if r["behavior_match"]:
            b["behavior_pass"] += 1
        if r["tool_match"]:
            b["tool_pass"] += 1
        if r["citation_validity"] is not None:
            b["cite_n"] += 1
            if r["citation_validity"]:
                b["cite_pass"] += 1
        f_score = r["faithfulness"]["score"]
        if f_score is not None:
            b["faith_sum"] += f_score
            b["faith_n"] += 1
    return by_cat


def _render_aggregate(agg: dict) -> str:
    rows = ["", "Aggregate by category:"]
    rows.append(
        f"{'category':<26}{'n':<4}{'behavior':<12}{'tool':<10}"
        f"{'citations':<12}{'faithfulness':<16}"
    )
    rows.append("-" * 80)
    for cat, b in sorted(agg.items()):
        beh = f"{b['behavior_pass']}/{b['n']}"
        tool = f"{b['tool_pass']}/{b['n']}"
        if b["cite_n"]:
            cite = f"{b['cite_pass']}/{b['cite_n']}"
        else:
            cite = "n/a"
        if b["faith_n"]:
            faith = f"{b['faith_sum'] / b['faith_n']:.2f} (n={b['faith_n']})"
        else:
            faith = "n/a"
        rows.append(
            f"{cat:<26}{b['n']:<4}{beh:<12}{tool:<10}{cite:<12}{faith:<16}"
        )
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DATASET_PATH,
        help="Path to dataset.yaml",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=RESULTS_DIR,
        help="Directory to write the JSON dump",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip the LLM-as-judge faithfulness scoring (faster).",
    )
    args = parser.parse_args()

    if not os.getenv("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY is not set", file=sys.stderr)
        return 2

    with open(args.dataset) as f:
        dataset = yaml.safe_load(f)

    run_judge = not args.no_judge
    judge_note = "with judge" if run_judge else "without judge"
    print(f"Running {len(dataset['queries'])} queries ({judge_note})...")
    results = asyncio.run(_run_dataset(dataset, run_judge))

    print()
    print(_render_table(results))
    print(_render_aggregate(_aggregate(results)))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = args.output_dir / f"{ts}.json"
    with open(out_path, "w") as f:
        json.dump(
            {"timestamp": ts, "dataset": str(args.dataset), "results": results},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
