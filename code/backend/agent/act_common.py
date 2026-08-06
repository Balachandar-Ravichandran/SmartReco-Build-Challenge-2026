"""Shared Act-worker logic — hybrid_search + certificate distillation (Section 7, 12.3).

act_path.py and act_course.py are thin wrappers around this so there's exactly
one place candidates are searched and exactly one place they're distilled.
"""
from typing import Literal

from backend.core import events
from backend.core.config import get_settings
from backend.core.schemas import AgentCertificate, Fact, SearchCandidate
from backend.db.session import get_db
from backend.tools import db_tool
from backend.tools.search import hybrid_search
from backend.agent.state import RecommendationState


def to_certificate(
    worker: Literal["path", "course"],
    candidates: list[SearchCandidate],
    retried: bool,
    retry_count: int,
) -> AgentCertificate:
    if not candidates:
        return AgentCertificate(
            stage=f"act_{worker}",
            success=False,
            fact_block=[Fact(key="candidate_count", value=0)],
            summary=f"{worker.title()} search returned no candidates.",
            reason="Empty result set — no catalog item cleared the keyword or semantic threshold.",
            retry=not retried,
            retry_count=retry_count,
        )

    top = candidates[0]
    fact_block = [
        Fact(key="candidate_count", value=len(candidates)),
        Fact(key="top_item_id", value=top.item_id),
        Fact(key="top_item_title", value=top.title),
        Fact(key="top_combined_score", value=top.combined_score),
        Fact(key="top_rerank_score", value=top.rerank_score),
        Fact(key="top_is_match", value=top.is_match),
    ]
    if worker == "path":
        fact_block += [
            Fact(key="top_discount_amount", value=top.discount_amount or 0.0),
            Fact(key="top_has_capstone", value=bool(top.has_capstone)),
        ]
    return AgentCertificate(
        stage=f"act_{worker}",
        success=True,
        fact_block=fact_block,
        summary=(
            f"Best {worker} match: '{top.title}' (score {top.combined_score:.2f}) "
            f"out of {len(candidates)} candidates."
        ),
        retry_count=retry_count,
    )


async def run_worker(
    state: RecommendationState,
    worker: Literal["path", "course"],
    widened: bool = False,
) -> AgentCertificate:
    settings = get_settings()
    collection = "path_embeddings" if worker == "path" else "course_embeddings"
    planner_facts = {f.key: f.value for f in state["planner_cert"].fact_block}
    tags = planner_facts["tags"].split(",") if planner_facts.get("tags") else []
    # current_context_tag is "" for scope="home" (no single page to anchor
    # to) — hybrid_search falls back to its original flat-overlap behavior
    # when primary_tag is None, so home is unaffected by the primary/boost split.
    primary_tag = planner_facts.get("current_context_tag") or None
    boost_tags = [t for t in (planner_facts.get("historical_tags") or "").split(",") if t]

    top_k = 10 if widened else 5

    if worker == "path":
        events.act_path_started(state["user_id"])
    else:
        events.act_course_started(state["user_id"])

    if state["query_vector"] is None:
        # Embedder failed upstream (Mesh outage/timeout) — no vector to
        # search with. Return an empty-result certificate so this flows
        # through the normal validate -> retry -> fallback path rather than
        # crashing on a None passed into hybrid_search().
        cert = to_certificate(worker, [], retried=widened, retry_count=state["retry_count"])
        fact_payload = [f.model_dump() for f in cert.fact_block]
        if worker == "path":
            events.act_path_result(state["user_id"], fact_block=fact_payload, summary=cert.summary)
        else:
            events.act_course_result(state["user_id"], fact_block=fact_payload, summary=cert.summary)
        return cert

    with get_db() as db:
        # Metadata filter (Section 7.1 polish): never re-recommend something
        # the user already bought.
        exclude_ids = db_tool.get_purchased_item_ids(db, state["user_id"], worker)
        candidates = hybrid_search(
            db, state["query_vector"], tags, collection, top_k=top_k,
            primary_tag=primary_tag, boost_tags=boost_tags, exclude_ids=exclude_ids,
        )

    if widened:
        # Retry's genuine parameter change (Section 8): lower threshold by 0.1
        # so the widened top_k can actually surface a looser match as is_match.
        lowered_semantic = settings.SEMANTIC_THRESHOLD - 0.1
        for c in candidates:
            c.is_match = (
                c.keyword_overlap_ratio > settings.KEYWORD_THRESHOLD
                or c.semantic_similarity > lowered_semantic
            )
        # is_match changed, not combined_score, so rerank_score (which is
        # derived from combined_score) is still valid — resort by it rather
        # than reverting to raw combined_score, or the retry would silently
        # discard the popularity re-rank applied inside hybrid_search().
        candidates = sorted(candidates, key=lambda c: c.rerank_score, reverse=True)

    cert = to_certificate(worker, candidates, retried=widened, retry_count=state["retry_count"])

    fact_payload = [f.model_dump() for f in cert.fact_block]
    if worker == "path":
        events.act_path_result(state["user_id"], fact_block=fact_payload, summary=cert.summary)
    else:
        events.act_course_result(state["user_id"], fact_block=fact_payload, summary=cert.summary)

    return cert
