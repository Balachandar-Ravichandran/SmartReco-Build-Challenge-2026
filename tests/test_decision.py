"""Unit tests for decision.py's shape resolution — covers the fix for the
"a strong topical path match with no discount/capstone was silently
discarded in favor of a single course" bug.
"""
from backend.core.schemas import SearchCandidate
from backend.agent.decision import resolve_recommendation_shape


def _path(item_id, score, discount=0.0, has_capstone=False):
    return SearchCandidate(
        item_type="path", item_id=item_id, title=item_id,
        keyword_overlap_ratio=0.0, semantic_similarity=0.0,
        combined_score=score, is_match=True,
        discount_amount=discount, has_capstone=has_capstone,
    )


def _course(item_id, score):
    return SearchCandidate(
        item_type="course", item_id=item_id, title=item_id,
        keyword_overlap_ratio=0.0, semantic_similarity=0.0,
        combined_score=score, is_match=True,
    )


def test_uncurated_path_still_wins_over_single_course():
    """Mobile AI Developer Path-shaped case: a matched path with no discount
    and no capstone must still lead over a lone matched course, not be
    discarded down to single_course."""
    decision = resolve_recommendation_shape(
        path_candidates=[_path("path_mobile_ai", 0.8, discount=0.0, has_capstone=False)],
        course_candidates=[_course("crs_ios", 0.75)],
    )
    assert decision["shape"] == "curated_path"
    assert decision["top_path"].item_id == "path_mobile_ai"
    assert decision["top_course"].item_id == "crs_ios"


def test_curated_path_preferred_over_higher_scored_uncurated_path():
    decision = resolve_recommendation_shape(
        path_candidates=[
            _path("path_uncurated", 0.95, discount=0.0, has_capstone=False),
            _path("path_curated", 0.5, discount=60.0, has_capstone=True),
        ],
        course_candidates=[],
    )
    assert decision["top_path"].item_id == "path_curated"


def test_no_matched_path_falls_through_to_combo():
    decision = resolve_recommendation_shape(
        path_candidates=[],
        course_candidates=[_course("crs_a", 0.9), _course("crs_b", 0.8)],
    )
    assert decision["shape"] == "combo"


def test_no_matched_path_single_course_falls_through():
    decision = resolve_recommendation_shape(
        path_candidates=[],
        course_candidates=[_course("crs_a", 0.9)],
    )
    assert decision["shape"] == "single_course"
