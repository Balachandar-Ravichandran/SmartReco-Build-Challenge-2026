# Pathwise — Behavioral AI Recommendation Engine

A behavioral recommendation engine for online learning platforms. It ingests
user interests and browsing behavior and recommends personalized Learning
Paths and Courses with explanations.

## 1. Requirements

**Functional**
- Cold-start recommendations for new users from onboarding topics + goal only.
- Warm-path recommendations for returning users, driven by real-time
  behavioral events (views, clicks, purchases).
- A re-run trigger gate so recommendations only regenerate on a significant
  behavioral shift, not on every page view.
- Path-vs-course decisioning: serve a curated Learning Path when one matches,
  otherwise a single course or course combo.
- Every recommendation ships with an explanation ("why this").
- Admin catalog CRUD (courses/paths) with vector-store sync.
- Optional daily email digest of personalized recommendations.

**Non-functional**
- Runs fully locally: SQLite + an in-process Chroma vector store, no external
  services required beyond one LLM API.
- Single external LLM dependency (Mesh API, OpenAI-compatible) for both
  embeddings and completions — no vendor SDK.
- Deterministic, structured event logging for every decision point, so any
  recommendation can be replayed and audited after the fact.
- Bounded retry: a failed validation gate retries with widened search
  parameters exactly once, never loops.

Full detailed spec: [`PRD_Pathwise_v1_2.md`](PRD_Pathwise_v1_2.md).

## 2. Implementation

### Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in MESH_API_KEY / MESH_API_BASE
python -m data.seed         # creates pathwise.db + Chroma embeddings
uvicorn backend.app.main:app --reload --app-dir code
```

Server runs on `http://127.0.0.1:8000`.

### Configuration

All config is environment-driven (`.env`, loaded relative to the repo root).

| Variable | Required | Purpose |
|---|---|---|
| `MESH_API_KEY`, `MESH_API_BASE` | Yes | Mesh API credentials — every embed/completion call goes through Mesh |
| `MESH_EMBED_MODEL`, `MESH_COMPLETION_MODEL` | No | Model IDs (provider-prefixed, e.g. `vertex/gemini-embedding-001`) |
| `SEMANTIC_THRESHOLD`, `KEYWORD_THRESHOLD`, `MIN_ACCEPT_SCORE` | No | Hybrid-search match/accept thresholds, tuned against the synthetic dataset |
| `RECONCILIATION_INTERVAL_MINUTES` | No | How often the scheduler retries failed vector writes |
| `ENABLE_PROACTIVE_REFRESH`, `PROACTIVE_REFRESH_INTERVAL_MINUTES` | No | Background job that refreshes stale recommendations |
| `ENABLE_DAILY_DIGEST`, `DIGEST_HOUR`, `DIGEST_MINUTE` | No | See [Mail](#6-mail) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` | No | See [Mail](#6-mail) |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | No | See [Observability](#4-observability) |

### Key endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | Home page (recommendations) |
| `GET /course/{id}` | Course detail with context-aware recommendations |
| `POST /api/v1/onboarding` | Set user interests/goal at signup |
| `POST /api/v1/events` | Ingest behavioral events (batched, non-blocking) |
| `GET /api/v1/recommendations` | Main recommendation entry point |
| `POST /api/v1/admin/courses`, `/paths` | Catalog CRUD (dual-write) |
| `/admin` | Admin console (catalog CRUD + observability) |

### Project structure

```
code/
  backend/
    app/        FastAPI app factory, startup sequence
    api/        route handlers (auth, onboarding, catalog, events, recommendations, admin_console)
    agent/      LangGraph nodes — see Section 3
    core/       config, schemas, structured event logging, LangSmith tracer config
    db/         SQLAlchemy models + schema.sql
    tools/      llm_provider.py, mesh_client.py, db_tool.py, vector_tool.py, email_client.py
    scheduler/  reconciliation, proactive refresh, daily digest (APScheduler jobs)
  frontend/
    templates/  Jinja2 (home, course, profile, admin)
    static/     tracking.js — client-side event batching/POSTing
data/           seed.py + csv/ (synthetic dataset) + db/schema.sql
tests/          deterministic unit + e2e tests (fake LLM provider, no network calls)
eval/           build_dataset.py, run_eval.py — LangSmith evaluation harness
prompts/        solver_system.txt — the one LLM prompt in the system
logs/           events.jsonl, generated at runtime
chroma_data/    Chroma vector store, generated at runtime
```

### Running tests

```bash
pytest tests/ -v --cov=code --cov-report=html
```

Tests run against a temp copy of the seeded database with a deterministic
fake LLM provider — no network calls, no real credentials needed.

## 3. Agent

### Architecture

```
Planner (deterministic query builder)
  ↓
Embedder (1 shared query embedding)
  ↓
Act (parallel): Path Search ∥ Course Search
  ↓
Validator Pass 1 (independent per-worker gates)
  ↓
Act-Retry (bounded, widened search — failed workers only)
  ↓
Validator Pass 2 (final gates; hard fail → fallback)
  ↓
Solver (1 LLM completion call, structured output)
  ↓
Writer (dual-write: SQL + Chroma)
```

### Algorithm

1. **Trigger gate.** A recommendation only recomputes on `should_rerun()` —
   an RFM-style interest-score shift, not on every page load.
2. **Cold start bypasses the graph entirely.** New users with no behavior
   history get a direct hybrid search over onboarding topics + goal, with a
   templated (non-LLM) narrative — no LangGraph, no completion call.
3. **Warm path runs the full graph above.** The Planner deterministically
   builds a query from recent behavioral signal. The Embedder computes one
   query embedding, shared by both Act workers (previously two separate
   calls — now one).
4. **Two Act workers run in parallel**: Path Search and Course Search, each
   doing hybrid (semantic + keyword) retrieval against Chroma/SQL.
5. **Validator Pass 1** checks each worker's result against
   `SEMANTIC_THRESHOLD` / `KEYWORD_THRESHOLD` / `MIN_ACCEPT_SCORE`, purely in
   Python — no LLM involved. A failing worker sets a retry flag on its
   certificate.
6. **Act-Retry** is a single node, internally parallel via `asyncio.gather`,
   bounded to exactly one hop. It widens the failed worker's search
   parameters (e.g. `top_k` 5→10, threshold −0.1) rather than re-running the
   identical query.
7. **Validator Pass 2** is the final gate — a hard fail here (both workers
   still failing) escalates to fallback; otherwise both workers hand
   certificates to the Solver.
8. **Decision logic** picks curated-path vs. single-course vs. course-combo
   shape based on what Validator Pass 2 actually produced.
9. **Solver** makes exactly one LLM completion call (Mesh), given only the
   distilled certificate facts (never raw search candidates), and returns
   structured output validated against a Pydantic schema.
10. **Fallback** serves stale recommendations or a Popular rail if retry is
    exhausted.
11. **Writer** dual-writes the result: SQL first (transactional), then
    Chroma; a Chroma failure never rolls back the SQL write — it's logged to
    `vector_sync_log` and retried by the reconciliation job.

### Certificate pattern

Every inter-node handoff is one `AgentCertificate` — a distilled
`fact_block` + summary, never a raw candidate list or DB row:

```python
AgentCertificate(
    stage="planner",
    success=True,
    fact_block=[Fact(key="tags", value="AI,ML")],
    summary="Planned a home query over tags: AI, ML.",
)
```

This keeps every stage's reasoning auditable in the event log and keeps
failure/retry signals explicit rather than inferred from raw data.

## 4. Observability

- **Structured event log** (`core/events.py`): every decision point is
  logged as JSONL to `logs/events.jsonl`, with a denylist that redacts
  sensitive fields (API keys, tokens, secrets) before anything is written.
- **Per-run trace**: `GET /api/v1/admin/runs/{recommendation_log_id}/events`
  returns the exact event slice for one recommendation run — trigger
  decision, certificates, retry, final write.
- **Dual-write health**: `GET /api/v1/admin/dual-write-status` surfaces any
  Chroma writes that failed and are pending reconciliation.
- **LangSmith tracing** (optional): set `LANGCHAIN_TRACING_V2=true` plus
  `LANGCHAIN_API_KEY`/`LANGCHAIN_PROJECT` to get the full LangGraph trace
  tree (per-node inputs/outputs, retry branches) in LangSmith. Leave unset
  and the app runs identically, just untraced (`get_tracer_config()` no-ops).

## 5. Evaluation

`eval/` builds a LangSmith **Dataset** (`pathwise-decision-points`, one
example per synthetic user covering a specific decision point: trigger gate,
shape resolution, retry firing, fallback escalation) and runs it through the
real pipeline via `langsmith.evaluate()`.

```bash
python -m eval.build_dataset    # create/reuse the dataset
python -m eval.run_eval          # run the pipeline through it, print results
```

All 7 evaluators are deterministic Python functions checked against a known
ground truth in the synthetic set — never an LLM-as-judge:

| Evaluator | Checks |
|---|---|
| `trigger_gate_correct` | Re-run fires iff a significant behavioral shift occurred |
| `shape_correct` | Resolved recommendation shape matches the expected one, where asserted |
| `structured_output_valid` | Run reached a valid terminal state (`done`, `escalated_fallback`, or `cold_start`) |
| `retry_bounded` | Retry never fires more than once |
| `retry_fires_as_expected` | Retry fires exactly when the synthetic case expects it to |
| `fallback_as_expected` | Fallback escalation matches the expected outcome |
| `groundedness` | Every recommended tile title traces back to a fact an upstream certificate actually produced, never an invented title |

Results (per-example scores and the full trace tree behind any failing
example) are viewable in the linked LangSmith project after running
`eval/run_eval.py` — the experiment isn't checked into the repo since it's
tied to a live LangSmith run.

## 6. Mail

An optional daily digest (`backend/scheduler/daily_digest.py`, APScheduler
cron job) emails each opted-in user (`users.digest_enabled`) the same
personalized recommendations they'd see on their home page — it reuses the
`get_recommendations` entry point directly, so there's no separate digest
logic to keep in sync.

Delivery is plain `smtplib` (`backend/tools/email_client.py`), no vendor
SDK. Configure via `.env`:

```
ENABLE_DAILY_DIGEST=true
DIGEST_HOUR=8
DIGEST_MINUTE=0
SMTP_HOST=...
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
SMTP_FROM_EMAIL=...
```

Leaving `SMTP_HOST` empty runs the job in log-only stub mode — it renders
and logs the digest instead of sending, so the pipeline is testable before
real SMTP credentials exist.

---

Built for the AI Engineering Hackathon 2026.
