"""Solver node (Section 9, 12.6) — the ONE Mesh API completion call per warm run.

Reads only certificate fact_blocks (never raw SearchCandidate lists). Reconstructs
single-item pseudo-candidates from the final-pass certs and reuses the same
resolve_recommendation_shape() cold start calls with full lists — with only one
top item per worker available here, the reachable shapes are curated_path or
single_course; the combo shape needs >=2 matched courses, which only cold start's
full candidate list can supply. Same function, different cardinality of input,
consistent with Section 7.4's signature and Section 9's cert-only data access.
"""
import time
from pathlib import Path as FSPath

from backend.core import events
from backend.core.schemas import AgentCertificate, Fact, SearchCandidate, SolverOutput
from backend.db.models import Product, Path
from backend.db.session import get_db
from backend.tools import db_tool
from backend.tools.llm_provider import get_provider
from backend.agent.decision import resolve_recommendation_shape
from backend.agent import tiles
from backend.agent.state import RecommendationState

_PROMPT_PATH = FSPath(__file__).resolve().parents[3] / "prompts" / "solver_system.txt"


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text()
    return (
        "You are Pathwise's recommendation narrator. You will be given a decided "
        "shape (curated_path, single_course, or combo) and the real catalog facts "
        "for the winning item(s). Populate the SolverOutput schema exactly — do not "
        "invent course names, prices, or discounts beyond what is supplied."
    )


def _cert_to_candidate(cert: AgentCertificate, item_type: str) -> list[SearchCandidate]:
    if not cert.success:
        return []
    facts = {f.key: f.value for f in cert.fact_block}
    return [
        SearchCandidate(
            item_type=item_type,
            item_id=facts["top_item_id"],
            title=facts["top_item_title"],
            keyword_overlap_ratio=0.0,
            semantic_similarity=0.0,
            combined_score=facts["top_combined_score"],
            is_match=bool(facts.get("top_is_match", True)),
            discount_amount=facts.get("top_discount_amount"),
            has_capstone=facts.get("top_has_capstone"),
        )
    ]


def to_certificate(
    output: SolverOutput | None, valid: bool, reason: str | None
) -> AgentCertificate:
    if not valid:
        return AgentCertificate(
            stage="solver",
            success=False,
            fact_block=[],
            summary="Solver output failed structured-output validation.",
            reason=reason,
            retry=False,
        )
    return AgentCertificate(
        stage="solver",
        success=True,
        fact_block=[
            Fact(key="path_tile_count", value=len(output.pathTiles)),
            Fact(key="course_tile_count", value=len(output.courseTiles)),
            Fact(
                key="hero_path_title",
                value=output.pathTiles[0].title if output.pathTiles else "",
            ),
        ],
        summary=output.narrative,
    )


async def run(state: RecommendationState) -> dict:
    path_cert = state.get("validate_2_path_cert") or state["validate_1_path_cert"]
    course_cert = state.get("validate_2_course_cert") or state["validate_1_course_cert"]

    path_candidates = _cert_to_candidate(state["act_path_cert"], "path") if path_cert.success else []
    course_candidates = _cert_to_candidate(state["act_course_cert"], "course") if course_cert.success else []

    decision = resolve_recommendation_shape(path_candidates, course_candidates)

    planner_facts = {f.key: f.value for f in state["planner_cert"].fact_block}
    historical_top_tag = planner_facts.get("historical_top_tag", "")
    current_context_tag = planner_facts.get("current_context_tag", "")

    with get_db() as db:
        path_row = db.get(Path, decision["top_path"].item_id) if decision["top_path"] else None
        course_row = db.get(Product, decision["top_course"].item_id) if decision["top_course"] else None
        path_course_rows = [pc.course for pc in path_row.courses] if path_row else []
        # Build resolved_facts INSIDE the session — path_row/course_row are ORM
        # objects whose attributes expire once this session commits and closes,
        # so all attribute reads must happen before the `with` block exits.
        resolved_facts = _build_resolved_facts(
            decision, path_row, course_row, path_course_rows, historical_top_tag, current_context_tag
        )
        path_id = path_row.id if path_row else None
        course_id = course_row.id if course_row else None

    system_prompt = _load_system_prompt()
    user_prompt = (
        f"Decision shape: {decision['shape']}.\n"
        f"Resolved facts: {resolved_facts}\n"
        f"Produce a SolverOutput populating badge, headline, reasoning, narrative, highlights, "
        f"pathTiles, courseTiles using ONLY the item names/prices/discounts given above. The "
        f"`reasoning` field must explicitly name the signals in resolved_facts['signals'] (if "
        f"present) — the user's historical interest and, when given, the category of the page "
        f"they're currently on — and state plainly how this pick bridges them. `highlights` "
        f"must be 3-5 short standalone bullet points, not a paragraph."
    )

    provider = get_provider()
    # One retry on a transient completion failure (timeout, or a schema
    # rejection the model corrects on a second attempt) before giving up to
    # fallback — retrieval already found and validated a real match by this
    # point, so a solver-only hiccup shouldn't throw that match away. Real
    # observed failure: a single Mesh response with `courseTiles.0.includes:
    # null` sent a perfectly good match to fallback with zero recourse.
    last_error: Exception | None = None
    for attempt in range(2):
        start = time.monotonic()
        try:
            raw = await provider.complete(system_prompt, user_prompt, response_schema=SolverOutput)
            latency_ms = (time.monotonic() - start) * 1000
            events.solver_invoked(state["user_id"], latency_ms=latency_ms)

            output = SolverOutput(**raw)
            # Stamp the real catalog id/kind onto whatever the LLM produced —
            # never trust an LLM-emitted id (Section 9's "no invented facts").
            if output.pathTiles and path_id:
                output.pathTiles[0].id = path_id
                output.pathTiles[0].kind = "path"
            if output.courseTiles and course_id:
                output.courseTiles[0].id = course_id
                output.courseTiles[0].kind = "course"
            cert = to_certificate(output, valid=True, reason=None)
            events.solver_output_validated(
                state["user_id"],
                fact_block=[f.model_dump() for f in cert.fact_block],
                summary=cert.summary,
            )
            return {"solver_cert": cert, "solver_output": output.model_dump(), "status": "writing"}
        except (TimeoutError, ValueError) as e:
            latency_ms = (time.monotonic() - start) * 1000
            # Emit on every attempt, failure or success — SOLVER_INVOKED's
            # trigger condition (Section 11.5) is "a Mesh API completion call
            # happened", not conditioned on success. Without this, a real API
            # failure here (bad model name, schema rejection, network error)
            # was silently swallowed with no trace in events.jsonl — the
            # certificate's own `reason` field carries the actual cause.
            events.solver_invoked(state["user_id"], latency_ms=latency_ms)
            last_error = e

    cert = to_certificate(None, valid=False, reason=str(last_error))
    events.solver_output_validated(
        state["user_id"], fact_block=[], summary=f"FAILED: {cert.reason}"
    )
    return {"solver_cert": cert, "solver_output": None, "status": "escalated_fallback"}


def _build_resolved_facts(
    decision, path_row, course_row, path_course_rows,
    historical_top_tag: str = "", current_context_tag: str = "",
) -> dict:
    facts: dict = {"shape": decision["shape"]}
    if historical_top_tag or current_context_tag:
        facts["signals"] = {
            "historical_interest": historical_top_tag or None,
            "current_page_category": current_context_tag or None,
        }
    if path_row:
        facts["path"] = {
            "title": path_row.title,
            "price": path_row.price,
            "discount_amount": path_row.discount_amount,
            "has_capstone": bool(path_row.has_capstone),
            "duration_months": path_row.duration_months,
            "includes": [c.title for c in path_course_rows],
        }
    if course_row:
        facts["course"] = {
            "title": course_row.title,
            "price": course_row.price,
            "instructor": course_row.instructor,
            "duration_weeks": course_row.duration_weeks,
            "level": course_row.level,
        }
    return facts
