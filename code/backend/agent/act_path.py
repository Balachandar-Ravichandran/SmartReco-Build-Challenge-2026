"""Path Search Worker (Section 7.2)."""
from backend.agent import act_common
from backend.agent.state import RecommendationState


async def run(state: RecommendationState, widened: bool = False) -> dict:
    cert = await act_common.run_worker(state, "path", widened=widened)
    return {"act_path_cert": cert}
