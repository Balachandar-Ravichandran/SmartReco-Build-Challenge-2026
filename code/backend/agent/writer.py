"""Write node (Section 10.1) — delete-then-insert current_recommendations + audit log."""
import json

from backend.core import events
from backend.db.session import get_db
from backend.tools import db_tool
from backend.agent.state import RecommendationState


async def run(state: RecommendationState) -> dict:
    output = state["solver_output"]

    with get_db() as db:
        log = db_tool.create_recommendation_log(
            db,
            user_id=state["user_id"],
            trigger_reason=state["trigger_reason"],
            act_path_candidates=[f.model_dump() for f in state["act_path_cert"].fact_block],
            act_course_candidates=[f.model_dump() for f in state["act_course_cert"].fact_block],
            validator_status="retried" if state["retry_count"] > 0 else "pass",
            retry_count=state["retry_count"],
            solver_narrative=output["narrative"],
            solver_output_json=json.dumps(output),
            latency_ms=None,
            scope=state["scope"],
            context_id=state["course_id"],
        )

        path_items, course_items = _resolve_written_items(db, output)
        db_tool.write_recommendations(db, state["user_id"], log.id, path_items, course_items)
        log_id = log.id  # read before the session below closes and expires it

    events.recommendations_written(
        state["user_id"],
        recommendation_log_id=log_id,
        path_count=len(path_items),
        course_count=len(course_items),
    )

    return {"status": "done"}


def _resolve_written_items(db, output: dict) -> tuple[list[dict], list[dict]]:
    """Match tiles back to real catalog ids.

    Prefers `tile["id"]` — solver.py stamps this from the actual path/course row
    it fed the LLM, never from LLM output, so it's exact. Falls back to matching
    on title (Solver only ever names real, validated titles — Section 9.1's
    grounding guarantee) for any tile built before that field existed, e.g. a
    stale cached solver_output."""
    from backend.db.models import Product, Path

    path_items = []
    for rank, tile in enumerate(output.get("pathTiles", []), start=1):
        row = (
            db.get(Path, tile["id"]) if tile.get("id")
            else db.query(Path).filter(Path.title == tile["title"]).first()
        )
        if row:
            path_items.append({"path_id": row.id, "rank": rank, "is_hero": rank == 1})

    course_items = []
    for rank, tile in enumerate(output.get("courseTiles", []), start=1):
        row = (
            db.get(Product, tile["id"]) if tile.get("id")
            else db.query(Product).filter(Product.title == tile["title"]).first()
        )
        if row:
            course_items.append({"product_id": row.id, "rank": rank, "is_hero": rank == 1})

    return path_items, course_items
