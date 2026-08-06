"""Embed-query-once node (Section 6.3) — the single Mesh API embedding call per warm run."""
import time

from backend.core import events
from backend.tools.llm_provider import get_embedding_provider
from backend.agent.state import RecommendationState


async def run(state: RecommendationState) -> dict:
    query_text = next(
        f.value for f in state["planner_cert"].fact_block if f.key == "query_text"
    )

    provider = get_embedding_provider()
    start = time.monotonic()
    try:
        vector = await provider.embed(query_text)
    except (TimeoutError, ValueError) as e:
        # No node upstream of Act catches embed failures, so a transient Mesh
        # timeout here used to crash the whole request with a 500. Failing
        # to None instead lets act_common's existing "no query_vector -> act
        # certificate fails" check flow through the graph's normal
        # retry -> validate -> fallback machinery, with no new graph edges.
        latency_ms = (time.monotonic() - start) * 1000
        events.query_embedded(state["user_id"], embedding_dim=0, latency_ms=latency_ms)
        return {"query_vector": None, "status": "acting"}

    latency_ms = (time.monotonic() - start) * 1000
    events.query_embedded(state["user_id"], embedding_dim=len(vector), latency_ms=latency_ms)

    return {"query_vector": vector, "status": "acting"}
