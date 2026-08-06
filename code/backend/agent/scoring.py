"""Live RFM-style interest scoring + trigger-layer significance check (Section 5.1, 0.1).

Interest scoring is computed live from behavioral_events — recency-decay x
frequency x intensity(dwell/event-type), pure SQL/Python aggregation, no LLM,
no separate scores table.
"""
import json
import math
from datetime import datetime

from sqlalchemy.orm import Session

from backend.core import events
from backend.db.models import BehavioralEvent, Product, Path
from backend.tools import db_tool

# Intensity weight per event type — purchase/add_to_cart signal strongest interest
EVENT_INTENSITY = {
    "view": 1.0,
    "dwell": 1.5,
    "search": 1.5,
    "click": 1.2,
    "add_to_cart": 2.5,
    "purchase": 4.0,
}

RECENCY_HALF_LIFE_DAYS = 7.0


def _aggregate_scores(
    db: Session, rows: list[BehavioralEvent], reference_time: datetime
) -> dict[str, float]:
    """Recency-decay x frequency x intensity, aggregated per tag category,
    relative to `reference_time`. Unnormalized — callers decide whether raw
    magnitude or a 0-1-normalized share is appropriate."""
    scores: dict[str, float] = {}

    for row in rows:
        tags = _tags_for_event(db, row)
        if not tags:
            continue

        age_days = max((reference_time - row.created_at).total_seconds() / 86400.0, 0.0)
        recency_weight = math.exp(-age_days / RECENCY_HALF_LIFE_DAYS * math.log(2))
        intensity = EVENT_INTENSITY.get(row.event_type, 1.0)
        if row.event_type == "dwell" and row.dwell_seconds:
            intensity *= min(row.dwell_seconds / 60.0, 3.0)  # cap dwell bonus at 3x

        weight = recency_weight * intensity
        for tag in tags:
            scores[tag] = scores.get(tag, 0.0) + weight

    return scores


def compute_raw_interest_scores(db: Session, user_id: str) -> dict[str, float]:
    """Unnormalized weighted engagement per tag — the actual accumulated
    signal. Used by should_rerun() for significance comparisons, since a
    magnitude that grows with real engagement (repeat searches, longer dwell,
    a cart-add) is what "significant shift" needs to detect, even when the
    same tag stays on top."""
    rows = db_tool.get_recent_events(db, user_id, limit=500)
    if not rows:
        return {}
    return _aggregate_scores(db, rows, datetime.utcnow())


def compute_interest_scores(db: Session, user_id: str) -> dict[str, float]:
    """0-1-normalized (by max) per tag category — for ranking/display only
    (top_interest_tags, the signal-panel bars). Do NOT use this for
    significance-shift magnitude comparisons: normalizing by max forces the
    top tag's own score to always read as exactly 1.0, which makes any
    same-tag score-delta check trivially zero. See compute_raw_interest_scores.
    """
    raw = compute_raw_interest_scores(db, user_id)
    if not raw:
        return {}

    max_score = max(raw.values())
    return {tag: score / max_score for tag, score in raw.items()}


def _tags_for_event(db: Session, event: BehavioralEvent) -> list[str]:
    if event.product_id:
        product = db.get(Product, event.product_id)
        return json.loads(product.tags) if product else []
    if event.path_id:
        path = db.get(Path, event.path_id)
        return json.loads(path.tags) if path else []
    return []


def top_interest_tags(db: Session, user_id: str, n: int = 2) -> list[str]:
    scores = compute_interest_scores(db, user_id)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [tag for tag, _ in ranked[:n]]


def should_rerun(
    db: Session, user_id: str, scope: str, course_id: str | None = None
):
    """Trigger gate (Section 5.1), keyed per exact page: (user, scope, context_id).

    Earlier designs tried a debounce/event-floor/significance-shift gate over
    "the last run for this user" — that compares against the wrong reference
    point whenever the user has visited a different page since (cross-scope
    contamination, see feature_context_aware_recommendations memory, Rounds
    1-5) and was eventually removed entirely (Round 6), trading all caching
    away for correctness.

    Both of those designs conflated two different questions: "has anything
    changed" and "is this the same page as last time." This version asks only
    the first, at the narrowest possible scope: is there a confirmed prior run
    for this *exact* (scope, context_id), and has *any* new behavioral event
    landed since then? Any event invalidates — interest scores blend full
    history, so any signal could shift the picture — which means this can
    never serve a recommendation that's stale relative to the user's actual
    behavior. It only skips the redundant recompute when nothing happened at
    all (a reload, a duplicate request, a revisit with zero new signal).

    Returns (rerun: bool, cached_log: RecommendationLog | None) — the caller
    serves `cached_log.solver_output_json` verbatim when rerun is False.
    """
    last_log = db_tool.get_latest_recommendation_log(db, user_id, scope, course_id)

    if last_log is None or not last_log.solver_output_json:
        events.significance_check_passed(
            user_id, top_tag=f"(page_context:{scope})", score_delta=1.0,
            reason="no_confirmed_run_for_this_page",
        )
        return True, last_log

    if db_tool.has_events_since(db, user_id, last_log.created_at):
        events.significance_check_passed(
            user_id, top_tag=f"(page_context:{scope})", score_delta=1.0,
            reason="new_signal_since_last_run",
        )
        return True, last_log

    events.significance_check_passed(
        user_id, top_tag=f"(page_context:{scope})", score_delta=0.0,
        reason="no_new_signal_since_last_run",
    )
    return False, last_log
