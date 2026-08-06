"""LangSmith tracer config (Section 6.6) — no-op fallback if unconfigured."""
import os


def get_tracer_config(
    user_id: str, trigger_reason: str, scope: str, retry_count: int = 0
) -> dict:
    if not os.getenv("LANGCHAIN_API_KEY"):
        return {"callbacks": [], "metadata": {}, "tags": ["no_tracing"]}

    from langchain_core.tracers import LangChainTracer

    tracer = LangChainTracer(
        project_name=os.getenv("LANGCHAIN_PROJECT", "pathwise-hackathon")
    )
    return {
        "callbacks": [tracer],
        "metadata": {
            "user_id": user_id,
            "trigger_reason": trigger_reason,
            "scope": scope,
            "retry_count": retry_count,
        },
        "tags": [trigger_reason, scope, "warm_agent"],
        "run_name": f"pathwise_run_{trigger_reason}",
    }
