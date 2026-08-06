"""POST /api/v1/events (Section 5.4, 14.3) — batched / throttled / non-blocking."""
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import ValidationError

from backend.core import events as event_log
from backend.core.config import get_settings
from backend.core.schemas import BehavioralEventInput
from backend.db.models import Session as SessionModel
from backend.db.session import get_db

router = APIRouter(prefix="/api/v1/events", tags=["events"])

# In-memory sliding window rate limiter: user_id -> deque[timestamps]
_rate_windows: dict[str, deque] = defaultdict(deque)


def _check_rate_limit(user_id: str) -> tuple[bool, float]:
    settings = get_settings()
    now = time.monotonic()
    window = _rate_windows[user_id]

    while window and now - window[0] > 60:
        window.popleft()

    if len(window) >= settings.EVENTS_MAX_PER_MINUTE:
        retry_after = 60 - (now - window[0])
        return False, retry_after

    window.append(now)
    return True, 0.0


@router.post("", status_code=202)
async def ingest_events(
    body: list[BehavioralEventInput],
    background_tasks: BackgroundTasks,
    response: Response,
    user_id: str,
):
    if len(body) > 50:
        raise HTTPException(422, "Max 50 events per batch")

    allowed, retry_after = _check_rate_limit(user_id)
    if not allowed:
        response.headers["Retry-After"] = str(int(retry_after) + 1)
        raise HTTPException(429, "Rate limit exceeded")

    background_tasks.add_task(_process_batch, user_id, body)
    return {"accepted": len(body)}


def _process_batch(user_id: str, events_batch: list[BehavioralEventInput]):
    from backend.tools import db_tool

    with get_db() as db:
        session_id = _get_or_create_session(db, user_id)

        for event in events_batch:
            try:
                event.validate_exclusive()
                db_tool.insert_behavioral_event(
                    db,
                    {
                        "user_id": user_id,
                        "session_id": session_id,
                        "event_type": event.event_type,
                        "target": event.target,
                        "product_id": event.product_id,
                        "path_id": event.path_id,
                        "query_text": event.query_text,
                        "dwell_seconds": event.dwell_seconds,
                    },
                )
                event_log.behavioral_event_ingested(user_id, event.event_type)
            except (ValidationError, ValueError) as e:
                event_log.behavioral_event_rejected(user_id, reason=str(e))


def _get_or_create_session(db, user_id: str) -> str:
    active = (
        db.query(SessionModel)
        .filter(SessionModel.user_id == user_id, SessionModel.ended_at.is_(None))
        .order_by(SessionModel.started_at.desc())
        .first()
    )
    if active:
        return active.id

    session = SessionModel(id=str(uuid.uuid4()), user_id=user_id)
    db.add(session)
    db.flush()
    return session.id
