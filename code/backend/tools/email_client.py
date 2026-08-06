"""Email delivery for the scheduled daily digest (Section 6.5 bonus).

Plain smtplib — no vendor SDK, since the competition-safety rules (PRD
Section 0) already restrict which third-party clients ship in `backend/`.
When SMTP_HOST is unset, send() logs the rendered email instead of dispatching
it, so the digest job (scheduler/daily_digest.py) is fully exercisable before
real SMTP credentials exist.
"""
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from backend.core.config import get_settings

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_MAP = {"&middot;": "-", "&mdash;": "--", "&nbsp;": " "}


def _to_plain_text(html_fragment: str) -> str:
    """Strip the small set of tags/entities signal_panel.build_ticker emits
    (meant for `|safe` HTML rendering) down to plain text for the email's
    text/plain part."""
    text = _TAG_RE.sub("", html_fragment)
    for entity, replacement in _ENTITY_MAP.items():
        text = text.replace(entity, replacement)
    return text


def render_digest(
    rec: dict, ticker: list[dict] | None = None, scores: list[dict] | None = None
) -> tuple[str, str, str]:
    """Build (subject, html_body, text_body) from:
    - rec: a RecommendationResponse dict (same shape home.html renders —
      badge/headline/reasoning/narrative/highlights/pathTiles/courseTiles)
    - ticker/scores: this user's "today's activity" (agent/signal_panel.py's
      build_ticker/build_scores — the same real behavioral data the home
      page's Signal sidebar shows, not a separate summary)
    """
    headline = rec.get("headline") or "Your recommendations for today"
    narrative = rec.get("narrative") or ""
    reasoning = rec.get("reasoning") or ""
    highlights = rec.get("highlights") or []
    tiles = (rec.get("pathTiles") or []) + (rec.get("courseTiles") or [])
    ticker = ticker or []
    scores = scores or []

    subject = f"Pathwise Daily Digest: {headline}"

    text_lines = [headline, "", narrative]
    if reasoning:
        text_lines += ["", reasoning]
    if highlights:
        text_lines += [""] + [f"- {h}" for h in highlights]
    if tiles:
        text_lines += ["", "Picked for you:"]
        text_lines += [f"* {t.get('title')} ({t.get('timeline', '')})" for t in tiles]
    if ticker:
        text_lines += ["", "Your activity today:"]
        text_lines += [f"- {_to_plain_text(t['text'])}" for t in ticker if t.get("new")]
    if scores:
        text_lines += ["", "Your top interests:"]
        text_lines += [f"- {s['label']}: {s['v']}%" for s in scores]
    body_text = "\n".join(text_lines)

    highlights_html = "".join(f"<li>{h}</li>" for h in highlights)
    tiles_html = "".join(
        f"<li><strong>{t.get('title')}</strong>"
        + (f" &mdash; {t.get('timeline')}" if t.get("timeline") else "")
        + "</li>"
        for t in tiles
    )
    new_ticker_items = [t for t in ticker if t.get("new")]
    ticker_html = "".join(f"<li>{t['text']}</li>" for t in new_ticker_items)
    scores_html = "".join(
        f"<li>{s['label']}: {s['v']}%</li>" for s in scores
    )

    body_html = f"""
    <div style="font-family:sans-serif;max-width:600px;color:#1a1a1a;">
      <h2 style="margin-bottom:4px;">{headline}</h2>
      <p>{narrative}</p>
      {f'<p style="color:#666;font-size:13px;">{reasoning}</p>' if reasoning else ''}
      {f'<ul>{highlights_html}</ul>' if highlights_html else ''}
      {f'<h3 style="margin-top:20px;">Picked for you</h3><ul>{tiles_html}</ul>' if tiles_html else ''}
      {f'<h3 style="margin-top:20px;">Your activity today</h3><ul>{ticker_html}</ul>' if ticker_html else ''}
      {f'<h3 style="margin-top:20px;">Your top interests</h3><ul>{scores_html}</ul>' if scores_html else ''}
    </div>
    """
    return subject, body_html, body_text


def send(to_email: str, subject: str, body_html: str, body_text: str) -> bool:
    """Sends via SMTP; returns True if actually dispatched. Falls back to a
    log-only stub (returns False) when SMTP_HOST is unset."""
    settings = get_settings()
    if not settings.SMTP_HOST:
        print(f"[digest stub] would send to {to_email!r}: {subject!r}\n{body_text}\n")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USER
    msg["To"] = to_email
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
        if settings.SMTP_USE_TLS:
            server.starttls()
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(msg["From"], [to_email], msg.as_string())
    return True
