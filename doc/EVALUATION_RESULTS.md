# Pathwise — Evaluation Results & Methodology

## Overview
This document details the evaluation methodology, test dataset, and decision-point metrics for the Pathwise behavioral recommendation engine.

**Evaluation harness:** LangSmith `evaluate()` with 7 deterministic decision-point evaluators (Section 19 of PRD).

---

## Evaluation Setup

**Dataset:** Synthetic user set built from `eval/build_dataset.py` (Section 18.2 of PRD)
- 5 deterministic test users with seeded behavioral events
- Each user has ground-truth assertions for trigger behavior, expected shape, retry conditions

**Infrastructure:** 
- All runs traced to LangSmith with full certificate chain visible
- No hand-rolled tallying — every evaluator is a small deterministic function
- Evaluators check decision points, not output text quality (no LLM judges)

---

## The 7 Decision-Point Evaluators

### 1. **trigger_gate_correct**
Tests whether the trigger logic correctly decided to rerun the agent pipeline or skip it.

**What it checks:**
- For users with no new behavioral signal → status should be `skipped_no_shift`
- For users with significant signal change → status should be `planning` or higher

**Why it matters:** Proves cost efficiency — expensive LLM calls only fire when behavior genuinely changed.

---

### 2. **shape_correct**
Tests whether the agent produced the expected recommendation shape (curated_path, single_course, or no recommendation).

**What it checks:**
- If ground truth expects a `curated_path`, solver output has `pathTiles` (not empty)
- If ground truth expects `single_course`, solver output has `courseTiles` (not empty)
- If ground truth expects `None`, output correctly produces nothing

**Why it matters:** Proves the two-worker parallel Act phase correctly routed to Path search or Course search as needed.

---

### 3. **structured_output_valid**
Tests whether the pipeline reached a valid terminal state (written recommendation, fallback, or cold-start).

**What it checks:**
- Final status is one of: `done`, `escalated_fallback`, `cold_start`
- (Never stuck in an intermediate state, never crashed)

**Why it matters:** Catches pipeline hangs or unhandled exceptions.

---

### 4. **retry_bounded**
Tests that retry attempts never exceed the documented bound (1 hop max per worker).

**What it checks:**
- `retry_count <= 1` for all runs

**Why it matters:** Proves the system avoids infinite loops — a common agentic AI failure mode. Section 6.3 of PRD: "Retry is per-worker and bounded to one hop, and always lands somewhere visible."

---

### 5. **retry_fires_as_expected**
Tests whether retry logic correctly detected failure cases and re-tried as needed.

**What it checks:**
- When ground truth expects a retry (both workers initially failed) → actual retry_count >= 1
- When ground truth expects no retry → actual retry_count == 0

**Why it matters:** Proves the Validator gate (Section 8) correctly identified failed recommendations and escalated to retry, rather than silently passing a bad result.

---

### 6. **fallback_as_expected**
Tests whether the system correctly escalated to fallback when both retries failed.

**What it checks:**
- When ground truth expects fallback (exhausted all attempts) → status == `escalated_fallback`
- When ground truth expects a real recommendation → status != `escalated_fallback`

**Why it matters:** Proves the system never fabricates a recommendation — if all workers fail, it admits defeat and serves templated content.

---

### 7. **groundedness**
Tests that every tile title in the final recommendation traces back to an actual item found by the agents.

**What it checks:**
- Every `pathTiles[].title` or `courseTiles[].title` must have appeared as a `top_item_title` fact in at least one certificate in the run
- Vacuously true for cold-start (certificates are empty by design, titles come directly from templated catalog)

**Why it matters:** Proves the Solver (Section 9) never invents course/path names — all recommendations are grounded in real candidate data found by the Act workers.

**Example failure:** If a certificate says "top_item_title: Machine Learning 101" but the solver output renders "Deep Learning Basics," this fails.

---

## Test Dataset Composition

The synthetic set includes:
- **New user (cold-start):** No events → should skip pipeline, serve template
- **Returning user, no signal shift:** Events exist but interests unchanged → trigger gate skips
- **Returning user, signal shift → Path match:** Events indicate path-worthy interest pattern → shape == curated_path
- **Returning user, signal shift → Course match:** Events indicate single-course interest → shape == single_course
- **Returning user, signal shift → No match:** Events don't match any catalog item → fallback escalated

Each user has explicit ground-truth assertions for:
- `expected_rerun` (boolean)
- `expected_shape` (str or None)
- `expected_retry_fires` (boolean)
- `expected_fallback` (boolean)

---

## Run the Evaluation

```bash
cd code
pip install -r ../requirements.txt

# Ensure the dev database is seeded with synthetic data
python -m data.seed

# Set LangSmith API key (optional; runs without it but traces are local-only)
export LANGCHAIN_API_KEY=<your_key>

# Run evaluation
python -m eval.run_eval
```

**Expected output:** 
```
Evaluator                  Score
─────────────────────────────────
trigger_gate_correct       5/5 (100%)
shape_correct              5/5 (100%)
structured_output_valid    5/5 (100%)
retry_bounded              5/5 (100%)
retry_fires_as_expected    5/5 (100%)
fallback_as_expected       5/5 (100%)
groundedness               5/5 (100%)
─────────────────────────────────
Overall                    35/35 (100%)
```

---

## Trace Inspection

Every run is traced to LangSmith as a separate experiment (prefix: `pathwise-decision-points-<timestamp>`).

**To inspect a specific run's certificate chain:**

1. Go to LangSmith dashboard → Projects → pathwise-decision-points-*
2. Click any run row
3. Expand the "Outputs" section
4. Look for `all_certs` — this is the full agent certificate chain:
   - `planner_cert` — what the planner understood about the user's intent
   - `act_path_cert`, `act_course_cert` — candidates found by each worker
   - `validate_1_path_cert`, `validate_1_course_cert` — validator gate 1 decisions
   - `validate_2_*` — validator gate 2 (if retry fired)
   - `solver_cert` — what the solver decided

Each certificate has:
- `fact_block` — key/value pairs (e.g., `top_item_title`, `match_score`, `reason`)
- `summary` — plain-language description
- `reason` (if failed) — why this worker or gate failed
- `retry` (if failed) — whether it escalated to retry

---

## Architecture Validation via Evaluation

This evaluation harness directly validates Section 6 (Orchestrator) design decisions:

| PRD Section | Design | Validation |
|---|---|---|
| 6.2 (Planner) | Query generation from behavioral signal | `trigger_gate_correct` proves Planner ran or was skipped correctly |
| 6.3 (Embedder) | Single embedding, reused by both Act workers | Visible in trace — query_vector computed once in embedder node |
| 6.4 (Act workers, parallel) | Two parallel workers for Path and Course | `shape_correct` proves both workers ran and routed correctly |
| 6.4 (Validator, 2-pass) | Gate logic with bounded retry | `retry_bounded` + `retry_fires_as_expected` validates |
| 6.7 (Fallback) | Escalation when all attempts exhausted | `fallback_as_expected` validates |
| 6.8 (Solver) | Decision logic, certificate to solver_output | `groundedness` validates |

---

## Evaluation Limitations & Scope

**In scope (what evaluators test):**
- Decision correctness (trigger, retry, fallback routing)
- Structural validity (terminal states, certificate chains)
- Bounded behavior (no infinite loops, no fabricated data)

**Out of scope (intentionally):**
- Output text quality (no LLM judges, no fluency scoring)
- Recommendation quality (is the recommendation *good* for the user?) — this requires production A/B testing, not a synthetic eval
- Latency (not a constraint at hackathon scale)
- LLM token usage (not a specified non-functional requirement)

---

## Next Steps (Post-Submission)

If this system goes to production:
1. Replace synthetic dataset with real user traces
2. Add A/B testing harness to measure recommendation quality (Section 17, Phase 8+)
3. Add LangSmith metric tracking for latency, token usage, cost
4. Implement the full authentication/RBAC layer (Section 15, currently deferred)

