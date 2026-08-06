"""Course Search Worker (Section 7.3)."""
from backend.agent import act_common
from backend.agent.state import RecommendationState


async def run(state: RecommendationState, widened: bool = False) -> dict:
    cert = await act_common.run_worker(state, "course", widened=widened)
    return {"act_course_cert": cert}
