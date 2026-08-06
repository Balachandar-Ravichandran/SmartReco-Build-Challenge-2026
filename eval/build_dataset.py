"""Idempotent LangSmith Dataset builder (Section 6.6, 18.2, 19).

Creates (or reuses) the `pathwise-decision-points` dataset with one example
per Section 18.2 synthetic user. `expected_shape` is left `None` wherever the
PRD doesn't document a specific shape outcome for that user (retrieval-
dependent, not a fixed decision-point claim) — `run_eval.py`'s
`shape_correct` evaluator treats `None` as "not asserted" rather than
guessing at a value nothing in the spec pins down.

Run directly: `python -m eval.build_dataset` (needs LANGCHAIN_API_KEY set).
"""
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

# Standalone entry point — nothing here imports `backend`, so unlike the app
# (whose .env load is a side effect of importing backend.core.config) this
# needs its own load_dotenv() or LANGCHAIN_API_KEY never reaches os.environ.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DATASET_NAME = "pathwise-decision-points"

EXAMPLES = [
    {
        "inputs": {"user_id": "usr_bala_cold", "trigger_reason": "cold_start", "scope": "home", "course_id": None},
        "outputs": {
            "expected_rerun": True,  # cold start bypasses the gate but still serves a fresh pick, not a skip
            "expected_shape": None,  # DP0 exercises the bypass itself, not a specific shape
            "expected_retry_fires": False,
            "expected_fallback": False,
        },
    },
    {
        "inputs": {"user_id": "usr_sol_viewer", "trigger_reason": "significant_shift", "scope": "home", "course_id": None},
        "outputs": {
            "expected_rerun": True,  # first warm trigger — event floor met, significance shift
            "expected_shape": None,
            "expected_retry_fires": False,
            "expected_fallback": False,
        },
    },
    {
        "inputs": {"user_id": "usr_freed_viewer", "trigger_reason": "significant_shift", "scope": "home", "course_id": None},
        "outputs": {
            "expected_rerun": True,  # should_rerun() always returns True (Section 5.1) — every scope recomputes fresh
            "expected_shape": None,
            "expected_retry_fires": False,
            "expected_fallback": False,
        },
    },
    {
        "inputs": {"user_id": "usr_sam_shifter", "trigger_reason": "significant_shift", "scope": "home", "course_id": None},
        "outputs": {
            "expected_rerun": True,  # new category (MLOps) — significance shift
            "expected_shape": "curated_path",  # Section 7.4 resolves to the curated Path since one exists
            "expected_retry_fires": False,
            "expected_fallback": False,
        },
    },
    {
        "inputs": {"user_id": "usr_sparse_tag", "trigger_reason": "significant_shift", "scope": "home", "course_id": None},
        "outputs": {
            "expected_rerun": True,
            "expected_shape": None,
            "expected_retry_fires": True,  # near-zero keyword overlap forces Validator Pass 1 retry (Section 8)
            "expected_fallback": False,  # retry's widened top_k/lowered threshold should still recover a match
        },
    },
]


def build_dataset() -> str:
    client = Client()

    existing = list(client.list_datasets(dataset_name=DATASET_NAME))
    if existing:
        dataset = existing[0]
    else:
        dataset = client.create_dataset(
            dataset_name=DATASET_NAME,
            description=(
                "Section 18.2's 5 synthetic users, one example per documented "
                "decision point (trigger gate, shape resolution, retry bound, "
                "fallback escalation) — Section 6.6/19."
            ),
        )

    already_covered = {
        ex.inputs.get("user_id") for ex in client.list_examples(dataset_id=dataset.id)
    }
    for example in EXAMPLES:
        if example["inputs"]["user_id"] in already_covered:
            continue
        client.create_example(
            inputs=example["inputs"],
            outputs=example["outputs"],
            dataset_id=dataset.id,
        )

    return dataset.id


if __name__ == "__main__":
    dataset_id = build_dataset()
    print(f"Dataset '{DATASET_NAME}' ready: {dataset_id}")
