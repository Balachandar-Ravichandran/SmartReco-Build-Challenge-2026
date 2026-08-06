"""Path-vs-Course decision (Section 7.4) — pure Python, zero Mesh API calls.

Not a graph node — a deterministic function called by the Solver node before it
drafts the narrative, and reused by cold start for the same shape decision.
Operates strictly on already-retrieved SearchCandidate-shaped data; never a DB read.
"""
from typing import Literal, TypedDict

from backend.core.config import get_settings
from backend.core.schemas import SearchCandidate


class ShapeDecision(TypedDict):
    shape: Literal["curated_path", "combo", "single_course"]
    top_path: SearchCandidate | None
    top_course: SearchCandidate | None
    combo_courses: list[SearchCandidate]  # populated only for "combo"
    matched_paths: list[SearchCandidate]  # all matched paths, ranked desc — for cold start's extra options
    matched_courses: list[SearchCandidate]  # all matched courses, ranked desc


def resolve_recommendation_shape(
    path_candidates: list[SearchCandidate],
    course_candidates: list[SearchCandidate],
) -> ShapeDecision:
    """Three-way decision tree (Section 7.4):

    1. Curated Path match (real discount + capstone) -> lead with the Path.
    2. No curated Path clears threshold, but >=2 individually-matched courses
       exist -> ad-hoc combo, price = literal sum, no invented discount.
    3. Only one course clears the threshold -> single dominant course only.
    """
    settings = get_settings()

    matched_paths = [c for c in path_candidates if c.is_match]
    matched_courses = [c for c in course_candidates if c.is_match]
    ranked_paths = sorted(matched_paths, key=lambda c: c.combined_score, reverse=True)
    ranked_courses = sorted(matched_courses, key=lambda c: c.combined_score, reverse=True)

    curated = [
        p for p in matched_paths if (p.discount_amount or 0) > 0 and p.has_capstone
    ]
    if curated:
        top_path = max(curated, key=lambda c: c.combined_score)
        top_course = matched_courses[0] if matched_courses else None
        return ShapeDecision(
            shape="curated_path",
            top_path=top_path,
            top_course=top_course,
            combo_courses=[],
            matched_paths=ranked_paths,
            matched_courses=ranked_courses,
        )

    if len(matched_courses) >= 2:
        ranked = sorted(matched_courses, key=lambda c: c.combined_score, reverse=True)
        return ShapeDecision(
            shape="combo",
            top_path=None,
            top_course=ranked[0],
            combo_courses=ranked[:2],
            matched_paths=ranked_paths,
            matched_courses=ranked_courses,
        )

    top_course = matched_courses[0] if matched_courses else None
    return ShapeDecision(
        shape="single_course",
        top_path=None,
        top_course=top_course,
        combo_courses=[],
        matched_paths=ranked_paths,
        matched_courses=ranked_courses,
    )
