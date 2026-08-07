# Pathwise — Behavioral AI Recommendation Engine
## PRODUCT REQUIREMENTS DOCUMENT — v1.0
**SmartReco — Build a Behavioral AI Recommendation (career.krishnaik.in, `smartreco-build-challenge-2026`)**

Written to be implemented directly, file-by-file, by a coding agent. Every component below names its file path, its inputs/outputs as Pydantic models, and its logging obligations. Nothing in this document requires a design decision to be made at build time — every open question the team discussed (interest scoring, hybrid search, path-vs-course decisioning, agent architecture, retry mechanics, dual-write lifecycle, embedding efficiency) has already been settled and is recorded here as the final answer.

---

## Table of Contents

0. What this PRD assumes as already decided
1. Executive Summary
2. Problem Statement
3. User Personas & Roles
4. System Architecture
5. Feature Requirements — Trigger & Cold-Start Layer
6. Feature Requirements — Orchestrator (Planner / Embed / Act)
7. Feature Requirements — Act Workers (Path Search, Course Search)
8. Feature Requirements — Validator Gates
9. Feature Requirements — Solver
10. Feature Requirements — Write Path & Fallback
11. Event Catalogue
12. Data Models & Schemas (Agent Communication Contracts)
13. Database Schemas
14. API Contracts
15. Security & Guardrails
16. Non-Functional Requirements
17. Phased Delivery Plan
18. Synthetic Dataset Construction
19. Evaluation Methodology
20. Submission Folder Mapping

**NOTE: This is a summary reference. The full PRD is available in the project root as `PRD_Pathwise_v1_2.md`.**

---

## Key Highlights

### System Overview
Pathwise is a behavioral recommendation engine for online learning platforms. It watches real user behavior (views, dwell time, searches, clicks) and delivers two things on every page:
1. **A single Learning Path** (curated course bundle) with real discount & timeline
2. **A single Course** recommendation with plain-language reasoning

### Architecture
- **Cold Start** (new users): Direct hybrid search over onboarding answers, zero LLM calls
- **Warm Agent** (returning users): Planner → Embed-once → Parallel-Act → Bounded-Validator → Solver → Write
- **Fallback**: Retry exhausted → serve stale recommendation or Popular rail (never fabricate)

### Stack
✓ FastAPI backend
✓ Mesh API (embeddings + completions)
✓ Chroma vector store
✓ SQLite
✓ Jinja2 templates (server-rendered)
✓ LangGraph orchestration
✓ APScheduler for background jobs
✓ LangSmith tracing + dataset-driven evaluation

### Core Design Principles
1. **Certificate pattern**: Every inter-agent handoff passes structured facts + summary, never raw data
2. **Bounded retry**: One hop max per worker, always lands somewhere visible (fallback or success)
3. **Cost efficiency**: Embed and complete once per user-page interaction, only when signal truly shifts
4. **Grounded output**: Every recommendation title/discount must trace back to a Validator-cleared candidate

---

## Evaluation

See `EVALUATION_RESULTS.md` for the 7 deterministic decision-point evaluators and expected results.

---

*For the complete PRD, see the project root.*
