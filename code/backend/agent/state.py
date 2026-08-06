"""RecommendationState — the LangGraph state schema (Section 12.2).

TypedDict, not Pydantic — this is what LangGraph nodes read/write directly.
Holds certificates, never raw objects. query_vector is the one exception
(a computed artifact, not a claim about the world).
"""
from typing import Literal, TypedDict

from backend.core.schemas import AgentCertificate


class RecommendationState(TypedDict):
    user_id: str
    scope: Literal["home", "course", "path", "browse"]
    course_id: str | None
    trigger_reason: Literal["page_change", "significant_shift"]
    planner_cert: AgentCertificate | None
    query_vector: list[float] | None
    act_path_cert: AgentCertificate | None
    act_course_cert: AgentCertificate | None
    validate_1_path_cert: AgentCertificate | None
    validate_1_course_cert: AgentCertificate | None
    validate_2_path_cert: AgentCertificate | None
    validate_2_course_cert: AgentCertificate | None
    solver_cert: AgentCertificate | None
    retry_count: int
    status: Literal[
        "planning",
        "embedding",
        "acting",
        "validating",
        "solving",
        "writing",
        "done",
        "escalated_fallback",
        "failed",
    ]
    # Working data not itself part of the certificate contract, but needed by
    # downstream nodes without re-deriving it — the Solver's final resolved
    # SolverOutput, kept off the certificate because certificates carry facts,
    # not full structured payloads (Section 12.6 wraps this in solver_cert too).
    solver_output: dict | None
