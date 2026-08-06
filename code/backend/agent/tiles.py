"""Build OptionTile rows from real catalog data.

Used by coldstart.py (no LLM narrative) and fallback.py (serving stale/Popular).
The warm-agent Solver builds its own tiles via the LLM's structured output —
this module is for the two paths that render tiles without an LLM call.
"""
import json

from backend.db.models import Product, Path
from backend.core.schemas import OptionTile, IncludesRow, SolverOutput
from backend.tools import db_tool


def build_path_tile(path: Path, courses: list[Product], kicker: str) -> OptionTile:
    strike = path.price + path.discount_amount if path.discount_amount else None
    return OptionTile(
        kicker=kicker,
        title=path.title,
        rating=None,
        timeline=f"{path.duration_months} months",
        level=path.level_range,
        youget=f"{len(courses)} courses" + (" + capstone" if path.has_capstone else ""),
        price=path.price,
        strike=strike,
        cta="Start the path",
        note=None,
        savingsNote=(
            f"Save ${path.discount_amount:.0f}" if path.discount_amount else None
        ),
        includes=[
            IncludesRow(name=c.title, level=c.level, dur=f"{c.duration_weeks}w")
            for c in courses
        ],
        id=path.id,
        kind="path",
    )


def build_course_tile(course: Product, kicker: str) -> OptionTile:
    rating = (
        f"{course.rating} · {course.learners_count} learners" if course.rating else None
    )
    return OptionTile(
        kicker=kicker,
        title=course.title,
        rating=rating,
        timeline=f"{course.duration_weeks} weeks",
        level=course.level,
        youget=f"Taught by {course.instructor}",
        price=course.price,
        strike=None,
        cta="Start the course",
        note=None,
        savingsNote=None,
        includes=[],
        id=course.id,
        kind="course",
    )


def render_current_recommendations(
    db,
    existing,
    badge_text: str = "Cached",
    kicker_suffix: str = "Saved",
    reasoning: str = (
        "Nothing about your interests or the page you're on has changed enough "
        "since last time to recompute this — so we're showing you the same pick."
    ),
) -> SolverOutput:
    """Convert stored current_recommendations rows back into SolverOutput-shaped
    tiles. Shared by the trigger-gate cache-serve path (default reasoning is
    accurate there) and fallback.py (passes its own reasoning — serving stale
    isn't "nothing changed," it's "the agent couldn't confirm a fresh pick")."""
    path_tiles, course_tiles = [], []
    for row in existing:
        if row.item_type == "path" and row.path_id:
            path_row = db.get(Path, row.path_id)
            if path_row:
                course_rows = [pc.course for pc in path_row.courses]
                path_tiles.append(
                    build_path_tile(path_row, course_rows, f"Path · {kicker_suffix}")
                )
        elif row.item_type == "course" and row.product_id:
            course_row = db.get(Product, row.product_id)
            if course_row:
                course_tiles.append(
                    build_course_tile(course_row, f"Course · {kicker_suffix}")
                )

    return SolverOutput(
        badge={"text": badge_text, "cls": "cached"},
        headline="Your saved recommendations",
        reasoning=reasoning,
        narrative="Showing your most recent recommendations.",
        pathTiles=path_tiles,
        courseTiles=course_tiles,
    )


def render_popular(db, topic: str | None = None) -> SolverOutput:
    """Zero-personalization rail — used whenever nothing else can be served
    (Section 10.2), including cold start's Mesh-outage fallback.

    `topic` filters to the CURRENT page's own category (fallback.py passes
    this for course/path/browse scopes) — this is the fix for a real
    correctness bug: without it, this rail (and the plain stale-current-
    recommendations fallback it replaced for non-home scopes) could serve a
    pick totally unrelated to the page the user is actually looking at."""
    popular = db_tool.get_popular_courses(db, limit=6, topic=topic)
    course_tiles = [build_course_tile(c, "Popular") for c in popular]

    return SolverOutput(
        badge={"text": "Cached", "cls": "cached"},
        headline=f"Popular in {topic}" if topic else "Popular right now",
        reasoning=(
            f"We couldn't confidently match your interests to a specific {topic} pick "
            f"right now, so here's what's popular in {topic} instead."
        ) if topic else "No personalization signal to reason over yet, so this isn't tailored to you.",
        narrative=(
            f"Here's what's popular in {topic} right now."
            if topic else "We don't have enough signal yet — here's what's popular across the catalog."
        ),
        pathTiles=[],
        courseTiles=course_tiles,
    )


def build_combo_tile(courses: list[Product], kicker: str) -> OptionTile:
    total_price = sum(c.price for c in courses)
    return OptionTile(
        kicker=kicker,
        title=" + ".join(c.title for c in courses),
        rating=None,
        timeline=f"{sum(c.duration_weeks for c in courses)} weeks combined",
        level="Mixed",
        youget="Suggested combo",
        price=total_price,
        strike=None,
        cta="Start this combo",
        note="Suggested combo",
        savingsNote=None,
        includes=[
            IncludesRow(name=c.title, level=c.level, dur=f"{c.duration_weeks}w")
            for c in courses
        ],
    )
