"""Unit tests for solver.py's resolved-facts / bridging logic (pure Python,
no DB, no LLM) — the fix for the "reasoning claims a blend that isn't real"
bug: a single-course pick that only matches the current page category (not
the user's separate historical interest) must not be narrated as if it
bridges both signals.
"""
from types import SimpleNamespace

from backend.agent.solver import _build_resolved_facts


def _decision(shape="single_course"):
    return {"shape": shape}


def _course_row(tags):
    return SimpleNamespace(
        title="iOS Development with Swift",
        price=179.0,
        instructor="Ravi Menon",
        duration_weeks=7,
        level="Beginner",
    )


def test_bridges_both_false_when_winning_item_lacks_historical_tag():
    """iOS course tagged only Mobile Development, user's historical top tag is
    Python for AI — the course does not actually span both, so bridges_both
    must be False even though both signals were given."""
    facts = _build_resolved_facts(
        _decision("single_course"),
        path_row=None,
        course_row=_course_row(["Mobile Development"]),
        path_course_rows=[],
        historical_top_tag="Python for AI",
        current_context_tag="Mobile Development",
        winning_tags=["Mobile Development"],
    )
    assert facts["signals"]["bridges_both"] is False


def test_bridges_both_true_when_winning_item_carries_historical_tag():
    """A path/course tagged with the user's historical interest AND matching
    the current page category genuinely blends both — bridges_both is True."""
    facts = _build_resolved_facts(
        _decision("curated_path"),
        path_row=SimpleNamespace(
            title="Production AI Engineer Path", price=468.0,
            discount_amount=60.0, has_capstone=1, duration_months=4,
        ),
        course_row=None,
        path_course_rows=[],
        historical_top_tag="Python for AI",
        current_context_tag="Cybersecurity",
        winning_tags=["Agentic AI", "Cloud & DevOps", "Python for AI"],
    )
    assert facts["signals"]["bridges_both"] is True


def test_bridges_both_false_when_tags_are_identical():
    """historical_top_tag == current_context_tag isn't a "blend" of two
    distinct signals either — there's only one signal here."""
    facts = _build_resolved_facts(
        _decision("single_course"),
        path_row=None,
        course_row=_course_row(["Mobile Development"]),
        path_course_rows=[],
        historical_top_tag="Mobile Development",
        current_context_tag="Mobile Development",
        winning_tags=["Mobile Development"],
    )
    assert facts["signals"]["bridges_both"] is False


def test_no_signals_block_when_no_tags_present():
    facts = _build_resolved_facts(
        _decision("single_course"),
        path_row=None,
        course_row=_course_row(["Mobile Development"]),
        path_course_rows=[],
        historical_top_tag="",
        current_context_tag="",
        winning_tags=[],
    )
    assert "signals" not in facts
