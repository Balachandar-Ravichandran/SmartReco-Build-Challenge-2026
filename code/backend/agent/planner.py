"""Planner node (Section 6.2) — deterministic, zero external calls."""
import json

from backend.core import events
from backend.core.schemas import AgentCertificate, Fact, PlannerOutput
from backend.db.models import Product, Path
from backend.db.session import get_db
from backend.agent import scoring
from backend.agent.state import RecommendationState


def to_certificate(p: PlannerOutput) -> AgentCertificate:
    return AgentCertificate(
        stage="planner",
        success=True,
        fact_block=[
            Fact(key="scope", value=p.scope),
            Fact(key="tags", value=",".join(p.tags)),
            Fact(key="query_text", value=p.query_text),
            Fact(key="historical_top_tag", value=p.historical_top_tag),
            Fact(key="current_context_tag", value=p.current_context_tag),
            Fact(key="historical_tags", value=",".join(p.historical_tags)),
        ],
        summary=f"Planned a {p.scope} query over tags: {', '.join(p.tags)}.",
    )


def _blend_query_text(context_label: str, historical_top_tag: str, current_context_tag: str) -> str:
    """Phrases the query text to explicitly carry both signals when they
    differ — e.g. a user with a strong Agentic AI history browsing the
    Cybersecurity category should search on the blend of both, not just
    whichever tag happens to be listed first."""
    if not current_context_tag:
        return f"User's top interests: {historical_top_tag}." if historical_top_tag else "New user, general interests."
    if not historical_top_tag or historical_top_tag == current_context_tag:
        return f"{context_label}, tags: {current_context_tag}."
    return (
        f"{context_label} (tags: {current_context_tag}), with a strong prior interest "
        f"in {historical_top_tag} — blend both."
    )


async def run(state: RecommendationState) -> dict:
    scope = state["scope"]

    with get_db() as db:
        # Top-5 ranked historical tags, computed once — historical_top_tag is
        # just its first element; historical_tags (the full ranked list) is
        # what retrieval uses to boost matches without requiring an exact
        # match on the single #1 tag (Section 7.1's primary/boost split).
        historical_tags = scoring.top_interest_tags(db, state["user_id"], n=5)
        historical_top_tag = historical_tags[0] if historical_tags else ""

        if scope == "home":
            tags = historical_tags[:2]
            current_context_tag = ""
            query_text = _blend_query_text("", historical_top_tag, current_context_tag)
            course_context_id = None
        elif scope == "path":
            path_id = state["course_id"]
            path = db.get(Path, path_id)
            path_tags = json.loads(path.tags) if path else []
            tags = list(dict.fromkeys(path_tags + historical_tags[:1]))
            current_context_tag = path_tags[0] if path_tags else ""
            query_text = _blend_query_text(
                f"Viewing path '{path.title if path else path_id}'",
                historical_top_tag,
                current_context_tag,
            )
            course_context_id = path_id
        elif scope == "browse":
            # course_id doubles as the browsed topic string here (or None when
            # browsing the full, unfiltered catalog) — same "one generic
            # context id per scope" shape as course/path, just no DB row to
            # resolve since a topic IS the tag.
            topic = state["course_id"]
            tags = list(dict.fromkeys(([topic] if topic else []) + historical_tags[:1]))
            current_context_tag = topic or ""
            query_text = _blend_query_text(
                f"Browsing {topic or 'the full catalog'}", historical_top_tag, current_context_tag
            )
            course_context_id = topic
        else:
            course_id = state["course_id"]
            course = db.get(Product, course_id)
            course_tags = json.loads(course.tags) if course else []
            tags = list(dict.fromkeys(course_tags + historical_tags[:1]))
            current_context_tag = course_tags[0] if course_tags else ""
            query_text = _blend_query_text(
                f"Viewing course '{course.title if course else course_id}'",
                historical_top_tag,
                current_context_tag,
            )
            course_context_id = course_id

    output = PlannerOutput(
        scope=scope,
        tags=tags,
        query_text=query_text,
        course_context_id=course_context_id,
        historical_top_tag=historical_top_tag,
        current_context_tag=current_context_tag,
        historical_tags=historical_tags,
    )
    cert = to_certificate(output)

    events.plan_created(
        state["user_id"],
        fact_block=[f.model_dump() for f in cert.fact_block],
        summary=cert.summary,
    )

    return {"planner_cert": cert, "status": "embedding"}
