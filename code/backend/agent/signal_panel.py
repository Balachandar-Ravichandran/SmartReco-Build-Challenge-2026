"""Signal panel data — the wireframe's "Your Signal" sidebar: a live ticker of
recent behavioral events, interest score bars, and a reasoning trace for why
the current recommendation looks the way it does.

Not part of the recommendation pipeline itself — this is read-only display
data assembled fresh per page load, over data the pipeline already produces
(behavioral_events, agent/scoring.py's interest scores, recommendation_log's
trigger_reason). No certificate, no LLM call.
"""
import json
from datetime import datetime

from sqlalchemy.orm import Session

from backend.db.models import BehavioralEvent, RecommendationLog
from backend.tools import db_tool
from backend.agent import scoring

EVENT_LABELS = {
    "view": "Viewed",
    "search": "Searched",
    "click": "Clicked",
    "add_to_cart": "Added to cart",
    "purchase": "Purchased",
}


def build_ticker(db: Session, user_id: str, limit: int = 12) -> list[dict]:
    """Recent behavioral events as display strings, most recent first.
    Events since the last recommendation run are flagged `new` — matching the
    wireframe's ticker treatment of "new" (purple-highlighted) entries."""
    events = db_tool.get_recent_events(db, user_id, limit=limit)
    if not events:
        return []

    last_log = (
        db.query(RecommendationLog)
        .filter(RecommendationLog.user_id == user_id)
        .order_by(RecommendationLog.created_at.desc())
        .first()
    )
    cutoff = last_log.created_at if last_log else None

    ticker = []
    for e in events:
        label = EVENT_LABELS.get(e.event_type, e.event_type.title())
        target_title = _resolve_target_title(db, e)

        if e.event_type == "dwell":
            text = f"Spent {e.dwell_seconds or 0}s on <b>{target_title}</b>"
        elif e.event_type == "search":
            text = f'{label} &middot; "{e.query_text}"'
        elif target_title:
            text = f"{label} &middot; <b>{target_title}</b>"
        else:
            text = label

        ticker.append({"text": text, "new": cutoff is None or e.created_at > cutoff})

    return ticker


def _resolve_target_title(db: Session, event: BehavioralEvent) -> str | None:
    if event.product_id:
        from backend.db.models import Product
        row = db.get(Product, event.product_id)
        return row.title if row else event.product_id
    if event.path_id:
        from backend.db.models import Path
        row = db.get(Path, event.path_id)
        return row.title if row else event.path_id
    return event.target


def build_scores(db: Session, user_id: str, limit: int = 5) -> list[dict]:
    """Interest scores per tag, 0-1 normalized -> {label, v: 0-100} for bars."""
    raw = scoring.compute_interest_scores(db, user_id)
    ranked = sorted(raw.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"label": tag, "v": round(score * 100)} for tag, score in ranked]


def build_reason(
    trigger_reason: str,
    has_any_events: bool,
    top_tag: str | None = None,
    top_score: int | None = None,
) -> str:
    """Reasoning trace text — mirrors the wireframe's per-state `reason` field.
    Names the actual top interest tag/score driving the decision (read off the
    same scores this text sits below) instead of a generic templated sentence."""
    if not has_any_events:
        return (
            "We don't have any browsing activity from you yet, so this pick comes "
            "straight from what you chose at signup — not a generic \"most popular\" list."
        )

    tag_clause = (
        f"your top interest is {top_tag} at {top_score}% strength"
        if top_tag is not None
        else "your interest scores"
    )
    # Plain str.capitalize() also lowercases the rest of the string, which
    # mangles topic names with internal caps (e.g. "Python for AI" -> "python
    # for ai") — only the leading letter should change case.
    tag_clause_leading = tag_clause[0].upper() + tag_clause[1:]
    if trigger_reason == "significant_shift":
        return (
            f"{tag_clause_leading} — recalculated fresh from your full signal "
            f"history just now, not a cached pick."
        )
    if trigger_reason == "page_change":
        return (
            f"{tag_clause_leading}, same as your last visit — no new activity "
            f"since then, so we're showing the same confirmed pick instead of "
            f"spending another AI call to recompute an identical answer."
        )
    return f"This recommendation reflects your most recent browsing activity — {tag_clause}."
