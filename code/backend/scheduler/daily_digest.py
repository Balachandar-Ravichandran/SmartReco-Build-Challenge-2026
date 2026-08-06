"""APScheduler cron job (Section 6.5 bonus) — sends a daily personalized-
recommendation email to users who opted in via `users.digest_enabled`.

Reuses the same `get_recommendations` entry point the home page calls, so the
digest content is exactly what the user would see if they logged in — no
separate recommendation logic to keep in sync.
"""
from backend.core import events
from backend.db.session import get_db
from backend.db.models import User
from backend.tools import email_client


async def run():
    with get_db() as db:
        recipients = [
            (u.id, u.email)
            for u in db.query(User).filter(User.digest_enabled.is_(True)).all()
        ]

    for user_id, email in recipients:
        await _send_one(user_id, email)


async def _send_one(user_id: str, email: str):
    from backend.api.recommendations import get_recommendations
    from backend.agent import signal_panel

    try:
        with get_db() as db:
            result = await get_recommendations(
                user_id=user_id, scope="home", course_id=None, db=db
            )
            ticker = signal_panel.build_ticker(db, user_id)
            scores = signal_panel.build_scores(db, user_id)
    except Exception as e:
        events.digest_failed(user_id, reason=str(e))
        return

    subject, body_html, body_text = email_client.render_digest(
        result.model_dump(), ticker=ticker, scores=scores
    )
    sent = email_client.send(email, subject, body_html, body_text)
    if sent:
        events.digest_sent(user_id)
    else:
        events.digest_stubbed(user_id)
