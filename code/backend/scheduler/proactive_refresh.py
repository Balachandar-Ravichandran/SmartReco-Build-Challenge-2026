"""APScheduler interval job (every 6h, configurable) — bonus feature (Section 6.5).

Proactively refreshes current_recommendations for users with significant new
signal since their last run, without waiting for their next page load.
"""
from datetime import datetime, timedelta

from backend.core import events
from backend.core.config import get_settings
from backend.core.tracing import get_tracer_config
from backend.db.session import get_db
from backend.db.models import BehavioralEvent
from backend.agent import scoring
from backend.agent.graph import build_graph


async def run():
    settings = get_settings()
    with get_db() as db:
        cutoff = datetime.utcnow() - timedelta(
            minutes=settings.PROACTIVE_REFRESH_INTERVAL_MINUTES
        )
        user_ids = [
            row[0]
            for row in db.query(BehavioralEvent.user_id)
            .filter(BehavioralEvent.created_at > cutoff)
            .distinct()
            .all()
        ]

    for user_id in user_ids:
        await _check_and_refresh(user_id)


async def _check_and_refresh(user_id: str):
    with get_db() as db:
        should_run = scoring.should_rerun(db, user_id, scope="home")

    if not should_run:
        events.proactive_refresh_skipped(user_id, reason="no significant shift")
        return

    events.proactive_refresh_triggered(user_id)

    graph = build_graph()
    initial_state = {
        "user_id": user_id,
        "scope": "home",
        "course_id": None,
        "trigger_reason": "significant_shift",
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
    config = get_tracer_config(user_id, "significant_shift", "home")
    await graph.ainvoke(initial_state, config=config)
