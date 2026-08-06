"""Single retry node (Section 6.1's fix). Internally parallel over exactly the
worker(s) that need it; never touches a worker that already passed."""
import asyncio

from backend.core import events
from backend.agent import act_path, act_course
from backend.agent.state import RecommendationState


async def run(state: RecommendationState) -> dict:
    retry_workers = []
    tasks = {}

    if state["validate_1_path_cert"].retry:
        retry_workers.append("path")
        tasks["path"] = act_path.run(state, widened=True)
    if state["validate_1_course_cert"].retry:
        retry_workers.append("course")
        tasks["course"] = act_course.run(state, widened=True)

    events.act_retry_triggered(state["user_id"], retry_workers)

    results = await asyncio.gather(*tasks.values())
    update: dict = {"retry_count": state["retry_count"] + 1}
    for key, result_delta in zip(tasks.keys(), results):
        if key == "path":
            update["act_path_cert"] = result_delta["act_path_cert"]
        else:
            update["act_course_cert"] = result_delta["act_course_cert"]

    return update
