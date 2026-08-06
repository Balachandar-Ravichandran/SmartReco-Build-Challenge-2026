"""GET /api/v1/recommendations (Section 4.2, 4.3, 14.2) — the single entry point.

Runs the Trigger layer, then either the cold-start path, a cache-serve of the
last confirmed run for this exact page, or the full agent graph. A cache-serve
only happens when zero new behavioral events have landed since that run — see
agent/scoring.py::should_rerun() for why that can never go stale relative to
the user's actual behavior (Section 5.1).
"""
import json
from pathlib import Path as _Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.core import events
from backend.core.schemas import RecommendationResponse
from backend.core.tracing import get_tracer_config
from backend.tools import db_tool
from backend.agent import coldstart, scoring, tiles
from backend.agent.graph import build_graph

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])

# Same templates directory as app/main.py's `templates` — a separate instance
# because this router doesn't have access to the app's, and cart-companions
# is the only endpoint here that needs to render HTML instead of JSON.
_TEMPLATES_DIR = _Path(__file__).resolve().parents[3] / "code" / "frontend" / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("", response_model=RecommendationResponse)
async def get_recommendations(
    user_id: str = Query(...),
    scope: Literal["home", "course", "path", "browse"] = Query("home"),
    course_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    if scope in ("course", "path") and not course_id:
        raise HTTPException(422, f"course_id is required when scope={scope}")

    events.trigger_received(user_id, scope, course_id)

    if not db_tool.has_any_events(db, user_id):
        output = await coldstart.run_cold_start(db, user_id, scope=scope, course_id=course_id)
        return RecommendationResponse(trigger_reason="cold_start", **output.model_dump())

    # Decision Point 0 (Section 5.1) — cache gate, scoped to this exact page.
    rerun, cached_log = scoring.should_rerun(db, user_id, scope, course_id)
    if not rerun:
        cached = json.loads(cached_log.solver_output_json)
        cached["badge"] = {"text": "Cached", "cls": "cached"}
        return RecommendationResponse(trigger_reason="page_change", **cached)

    trigger_reason = "significant_shift"
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

    return RecommendationResponse(
        trigger_reason=trigger_reason, **final_state["solver_output"]
    )


@router.get("/cart-companions")
async def get_cart_companions(
    items: str = Query("", description="Comma-separated kind:id pairs, e.g. course:c1,path:p2"),
    db: Session = Depends(get_db),
):
    """Cart page's "Complete your learning" rail — the cosmetic cart
    (Section 2) has no server-side record, so the client sends its
    localStorage contents on every load. Catalog-adjacent tag overlap (see
    db_tool.get_cart_recommendations), not an agent/LLM call — but rendered
    through the SAME agent_recommended() macro as the real Agent Recommended
    rail (headline, badge, narrative, highlights, then the tile grid) so it
    reads as one consistent recommendation component, not a bespoke one-off.

    Returns rendered HTML rather than raw JSON since the client just needs
    to drop it into the page.
    """
    parsed: list[tuple[str, str]] = []
    for pair in items.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        kind, _, item_id = pair.partition(":")
        if kind in ("course", "path") and item_id:
            parsed.append((kind, item_id))

    if not parsed:
        return {"html": ""}

    recs, top_tag, cart_tags = db_tool.get_cart_recommendations(db, parsed)
    if not recs:
        return {"html": ""}

    path_tiles, course_tiles, highlights = [], [], []
    for kind, row in recs:
        row_tags = set(json.loads(row.tags))
        shared = sorted(cart_tags & row_tags)
        fresh_ground = sorted(row_tags - cart_tags)
        highlight = f"{row.title} builds on your {', '.join(shared) or top_tag} focus"
        highlight += f", adding {', '.join(fresh_ground)}." if fresh_ground else "."
        highlights.append(highlight)

        if kind == "course":
            course_tiles.append(tiles.build_course_tile(row, "Completes your cart"))
        else:
            course_rows = [pc.course for pc in row.courses]
            path_tiles.append(tiles.build_path_tile(row, course_rows, "Completes your cart"))

    rec = {
        "trigger_reason": "significant_shift",
        "badge": {"text": "Fresh", "cls": "fresh"},
        "reasoning": (
            f"These pair well with what's already in your cart — adding one rounds "
            f"out a complete {top_tag} skill set instead of leaving a gap."
        ),
        "narrative": f"Here's what completes the {top_tag} skill set you're already building in your cart.",
        "highlights": highlights,
        "pathTiles": [t.model_dump() for t in path_tiles],
        "courseTiles": [t.model_dump() for t in course_tiles],
    }

    html = _templates.env.get_template("_cart_companions.html").render(rec=rec, top_topic=top_tag)
    return {"html": html}
