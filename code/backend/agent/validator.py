"""Validator gates — certificate-in, certificate-out (Section 8, 12.5).

One reusable validate() function, called independently per worker at both
checkpoints. Pure Python — zero Mesh API calls. Never touches raw candidate
data, only the Act certificate's fact_block.
"""
from typing import Literal

from backend.core import events
from backend.core.config import get_settings
from backend.core.schemas import AgentCertificate, Fact
from backend.agent.state import RecommendationState


_CERT_STAGE = {"act_pass1": "validate_1", "act_pass2": "validate_2"}


def validate(
    stage: Literal["act_pass1", "act_pass2"],
    worker_cert: AgentCertificate,
    context: dict,
) -> AgentCertificate:
    # `stage` names which pass is checking ("act_pass1"/"act_pass2"); the
    # certificate's own `stage` field uses the pipeline-stage vocabulary
    # ("validate_1"/"validate_2") per Section 12.0's Literal type — these are
    # two different vocabularies that the PRD's own pseudocode conflated.
    cert_stage = _CERT_STAGE[stage]
    top_score = next(
        (f.value for f in worker_cert.fact_block if f.key == "top_combined_score"), None
    )
    if not worker_cert.success or top_score is None or top_score < context["MIN_ACCEPT_SCORE"]:
        return AgentCertificate(
            stage=cert_stage,
            success=False,
            fact_block=[
                Fact(key="checked_worker", value=worker_cert.stage),
                Fact(key="top_score_seen", value=top_score or 0.0),
            ],
            summary=f"{worker_cert.stage} did not clear the acceptance threshold.",
            reason=f"top_combined_score={top_score} < MIN_ACCEPT_SCORE={context['MIN_ACCEPT_SCORE']}",
            retry=(stage == "act_pass1"),
            retry_count=context.get("retry_count", 0),
        )
    return AgentCertificate(
        stage=cert_stage,
        success=True,
        fact_block=[
            Fact(key="checked_worker", value=worker_cert.stage),
            Fact(key="top_score_seen", value=top_score),
        ],
        summary=f"{worker_cert.stage} passed: top score {top_score:.2f} clears the threshold.",
    )


def _emit_result(user_id: str, worker: str, pass_num: int, cert: AgentCertificate):
    fact_payload = [f.model_dump() for f in cert.fact_block]
    if cert.success:
        if pass_num == 1:
            events.validation_pass_1_passed(user_id, worker, fact_payload, cert.summary)
        else:
            events.validation_pass_2_passed(user_id, worker, fact_payload, cert.summary)
    else:
        if pass_num == 1:
            events.validation_pass_1_failed(user_id, worker, cert.reason, fact_payload)
        else:
            events.validation_pass_2_failed(user_id, worker, cert.reason, fact_payload)


async def run_pass_1(state: RecommendationState) -> dict:
    settings = get_settings()
    context = {"MIN_ACCEPT_SCORE": settings.MIN_ACCEPT_SCORE, "retry_count": state["retry_count"]}

    path_cert = validate("act_pass1", state["act_path_cert"], context)
    course_cert = validate("act_pass1", state["act_course_cert"], context)

    _emit_result(state["user_id"], "path", 1, path_cert)
    _emit_result(state["user_id"], "course", 1, course_cert)

    return {
        "validate_1_path_cert": path_cert,
        "validate_1_course_cert": course_cert,
        "status": "validating",
    }


async def run_pass_2(state: RecommendationState) -> dict:
    settings = get_settings()
    context = {"MIN_ACCEPT_SCORE": settings.MIN_ACCEPT_SCORE, "retry_count": state["retry_count"]}

    if state["validate_1_path_cert"].retry:
        path_cert = validate("act_pass2", state["act_path_cert"], context)
        _emit_result(state["user_id"], "path", 2, path_cert)
    else:
        path_cert = state["validate_1_path_cert"]

    if state["validate_1_course_cert"].retry:
        course_cert = validate("act_pass2", state["act_course_cert"], context)
        _emit_result(state["user_id"], "course", 2, course_cert)
    else:
        course_cert = state["validate_1_course_cert"]

    return {"validate_2_path_cert": path_cert, "validate_2_course_cert": course_cert}
