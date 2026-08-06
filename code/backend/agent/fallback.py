"""Fallback (Section 10.2). Reached only when Validator Pass 2 is still
invalid/empty. Does NOT call Solver, does NOT write current_recommendations."""
from backend.core import events
from backend.db.session import get_db
from backend.tools import db_tool
from backend.agent import tiles
from backend.agent.state import RecommendationState


async def run(state: RecommendationState) -> dict:
    events.retry_exhausted(state["user_id"])
    scope = state["scope"]

    with get_db() as db:
        if scope == "home":
            # Home has no single page to stay "on topic" for — the user's
            # last confirmed, signal-driven pick is a reasonable fallback here.
            existing = db_tool.get_current_recommendations(db, state["user_id"])
            if existing:
                output = tiles.render_current_recommendations(
                    db,
                    existing,
                    reasoning=(
                        "We couldn't confidently retrieve a fresh match this time, so "
                        "we're showing your last confirmed recommendation instead of guessing."
                    ),
                )
                events.fallback_served(state["user_id"], fallback_type="stale_recommendations")
            else:
                output = tiles.render_popular(db)
                events.fallback_served(state["user_id"], fallback_type="popular_rail")
        else:
            # course/path/browse: NEVER fall back to `current_recommendations`
            # here — that row may have been computed for an entirely
            # different page (a different course, a different category), so
            # serving it would be visibly irrelevant, not just stale. Stay on
            # topic for the page the user is actually looking at instead.
            planner_cert = state.get("planner_cert")
            topic = None
            if planner_cert:
                topic = next(
                    (f.value for f in planner_cert.fact_block if f.key == "current_context_tag"),
                    None,
                ) or None
            output = tiles.render_popular(db, topic=topic)
            events.fallback_served(state["user_id"], fallback_type=f"popular_rail_topic:{topic or 'none'}")

    return {"solver_output": output.model_dump(), "status": "escalated_fallback"}
