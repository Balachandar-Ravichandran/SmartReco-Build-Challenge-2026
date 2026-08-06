"""Runs `pathwise-decision-points` through the real pipeline via
`langsmith.evaluate()` (Section 6.6, 19) — not a hand-rolled tally.

Every evaluator here is a small deterministic Python function with a
ground-truth answer in the synthetic set (Section 18.2), never an LLM judge.

Run directly: `python -m eval.run_eval` (needs LANGCHAIN_API_KEY set, and the
seeded dev DB — `python -m data.seed` — since these are real user_ids).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from langsmith import Client, evaluate

from backend.agent import coldstart, scoring
from backend.agent.graph import build_graph
from backend.core.tracing import get_tracer_config
from backend.db.session import get_db
from backend.tools import db_tool
from eval.build_dataset import DATASET_NAME, build_dataset

_CERT_KEYS = [
    "planner_cert", "act_path_cert", "act_course_cert",
    "validate_1_path_cert", "validate_1_course_cert",
    "validate_2_path_cert", "validate_2_course_cert", "solver_cert",
]


def _infer_shape(solver_output: dict | None) -> str | None:
    """Warm-path shapes are only ever curated_path or single_course — the
    combo shape needs >=2 individually-matched courses, which only cold
    start's full candidate list can supply (solver.py's own documented
    invariant, Section 7.4/9)."""
    if not solver_output:
        return None
    if solver_output.get("pathTiles"):
        return "curated_path"
    if solver_output.get("courseTiles"):
        return "single_course"
    return None


async def _run_example(user_id: str, trigger_reason: str, scope: str, course_id: str | None) -> dict:
    with get_db() as db:
        is_cold = not db_tool.has_any_events(db, user_id)
        if is_cold:
            output = await coldstart.run_cold_start(db, user_id, scope=scope, course_id=course_id)
            return {
                "status": "cold_start",
                "retry_count": 0,
                "resolved_shape": None,  # cold start's decision dict never leaves coldstart.py
                "solver_output": output.model_dump(),
                "all_certs": [],
            }
        scoring.should_rerun(db, user_id, scope, course_id)

    graph = build_graph()
    initial_state = {
        "user_id": user_id,
        "scope": scope,
        "course_id": course_id,
        "trigger_reason": trigger_reason,
        "planner_cert": None,
        "query_vector": None,
        "act_path_cert": None,
        "act_course_cert": None,
        "validate_1_path_cert": None,
        "validate_1_course_cert": None,
        "validate_2_path_cert": None,
        "validate_2_course_cert": None,
        "solver_cert": None,
        "retry_count": 0,
        "status": "planning",
        "solver_output": None,
    }
    config = get_tracer_config(user_id, trigger_reason, scope)
    final_state = await graph.ainvoke(initial_state, config=config)

    return {
        "status": final_state["status"],
        "retry_count": final_state["retry_count"],
        "resolved_shape": _infer_shape(final_state.get("solver_output")),
        "solver_output": final_state.get("solver_output"),
        "all_certs": [c for k in _CERT_KEYS if (c := final_state.get(k)) is not None],
    }


def target(inputs: dict) -> dict:
    return asyncio.run(
        _run_example(
            inputs["user_id"], inputs["trigger_reason"], inputs["scope"], inputs.get("course_id")
        )
    )


# ==================== EVALUATORS ====================
def trigger_gate_correct(run, example) -> dict:
    actual = run.outputs["status"] != "skipped_no_shift"
    return {"key": "trigger_gate_correct", "score": actual == example.outputs["expected_rerun"]}


def shape_correct(run, example) -> dict:
    expected = example.outputs["expected_shape"]
    if expected is None:
        return {"key": "shape_correct", "score": True}  # nothing asserted for this user
    return {"key": "shape_correct", "score": run.outputs.get("resolved_shape") == expected}


def structured_output_valid(run, example) -> dict:
    # Pass iff the run reached a written recommendation OR correctly routed
    # to fallback/cold_start — all three are valid, non-buggy terminal states.
    return {
        "key": "structured_output_valid",
        "score": run.outputs["status"] in ("done", "escalated_fallback", "cold_start"),
    }


def retry_bounded(run, example) -> dict:
    return {"key": "retry_bounded", "score": run.outputs["retry_count"] <= 1}


def retry_fires_as_expected(run, example) -> dict:
    actual = run.outputs["retry_count"] >= 1
    return {"key": "retry_fires_as_expected", "score": actual == example.outputs["expected_retry_fires"]}


def fallback_as_expected(run, example) -> dict:
    actual = run.outputs["status"] == "escalated_fallback"
    return {"key": "fallback_as_expected", "score": actual == example.outputs["expected_fallback"]}


def groundedness(run, example) -> dict:
    # Every tile title in SolverOutput must trace back to a top_item_title
    # Fact some certificate in this run actually produced — never a name the
    # Solver invented. Vacuously true for cold start (all_certs is empty by
    # construction there) since coldstart.py templates titles directly from
    # the same catalog rows it renders tiles from, never an LLM call.
    output = run.outputs.get("solver_output") or {}
    tile_titles = [t["title"] for t in output.get("pathTiles", []) + output.get("courseTiles", [])]
    if not tile_titles:
        return {"key": "groundedness", "score": True}
    if not run.outputs.get("all_certs"):
        return {"key": "groundedness", "score": True}
    known_titles = {
        fact.value
        for cert in run.outputs["all_certs"]
        for fact in cert.fact_block
        if fact.key == "top_item_title"
    }
    return {"key": "groundedness", "score": all(title in known_titles for title in tile_titles)}


EVALUATORS = [
    trigger_gate_correct,
    shape_correct,
    structured_output_valid,
    retry_bounded,
    retry_fires_as_expected,
    fallback_as_expected,
    groundedness,
]


if __name__ == "__main__":
    build_dataset()
    client = Client()
    results = evaluate(
        target,
        data=DATASET_NAME,
        evaluators=EVALUATORS,
        experiment_prefix="pathwise-decision-points",
        client=client,
        # Sequential — target() calls asyncio.run() per example, and running
        # those concurrently across evaluate()'s worker threads silently
        # dropped 2 of 5 runs (no error, no logged run) in testing.
        max_concurrency=1,
    )
    # The background trace-submission queue hasn't necessarily drained by the
    # time evaluate() returns — without this, the last example's run was
    # silently lost (never reached LangSmith, no error surfaced) in testing.
    client.flush()
    print(results)
