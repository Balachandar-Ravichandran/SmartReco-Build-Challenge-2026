"""Cold-start hybrid search + templated narrative (Section 5.2).

Runs entirely outside the LangGraph pipeline — no Planner/Act/Validator/Solver
nodes execute. Reuses the exact same hybrid_search() Act uses (Section 7.1).
"""
import json
import time

from sqlalchemy.orm import Session

from backend.core import events
from backend.core.schemas import SolverOutput
from backend.db.models import Product, Path
from backend.tools import db_tool
from backend.tools.search import hybrid_search
from backend.tools.llm_provider import get_embedding_provider
from backend.agent.decision import resolve_recommendation_shape
from backend.agent import tiles

MAX_PATH_TILES = 3
MAX_COURSE_TILES = 2


async def run_cold_start(
    db: Session, user_id: str, scope: str = "home", course_id: str | None = None
) -> SolverOutput:
    events.cold_start_detected(user_id)

    onboarding = db_tool.get_latest_onboarding(db, user_id)
    if onboarding is None:
        # A user_id with no onboarding row (unrecognized, or signed up but
        # never completed onboarding) has zero personalization signal —
        # same "always land somewhere visible" treatment as the Mesh-outage
        # branch below, rather than a raw 500 (Section 0.6).
        events.fallback_served(user_id, fallback_type="cold_start_no_onboarding")
        output = tiles.render_popular(db)
        _write_cold_start_result(db, user_id, [], [], output, scope, course_id)
        return output

    topics = json.loads(onboarding.selected_topics)
    goal = onboarding.goal
    query_text = f"Interested in {', '.join(topics)}. Goal: {goal}."

    try:
        query_vector = await _get_or_compute_embedding(db, onboarding, query_text, user_id)
    except (TimeoutError, ValueError) as e:
        # Mesh unreachable — cold start has no retry/validator gate of its own
        # (Section 5.2 bypasses the whole graph), so an embed failure here
        # gets the same "always lands somewhere visible" treatment Section
        # 0.6 requires everywhere else: serve the Popular rail, don't 500.
        events.fallback_served(user_id, fallback_type=f"cold_start_mesh_outage: {e}")
        output = tiles.render_popular(db)
        _write_cold_start_result(db, user_id, [], [], output, scope, course_id)
        return output

    path_candidates = hybrid_search(db, query_vector, topics, "path_embeddings", top_k=5)
    course_candidates = hybrid_search(db, query_vector, topics, "course_embeddings", top_k=5)

    decision = resolve_recommendation_shape(path_candidates, course_candidates)

    top_title = None
    if decision["shape"] == "curated_path" and decision["top_path"]:
        top_title = decision["top_path"].title
    elif decision["top_course"]:
        top_title = decision["top_course"].title

    events.cold_start_hybrid_search_completed(
        user_id,
        candidate_count=len(path_candidates) + len(course_candidates),
        top_item_title=top_title or "none",
    )

    narrative = template_narrative(topics, goal, top_title)
    events.cold_start_narrative_templated(user_id, narrative)

    path_tiles, course_tiles, path_items, course_items = _build_tiles(db, decision)

    output = SolverOutput(
        badge={"text": "Fresh", "cls": "fresh"},
        headline=f"Because you're exploring {', '.join(topics[:2])}",
        reasoning=(
            f"You told us at signup you're interested in {', '.join(topics[:2])}, "
            f"aiming to {goal} — no browsing history yet, so this is a straight match "
            f"on what you selected, not something we inferred."
        ),
        narrative=narrative,
        highlights=_template_highlights(topics, goal, top_title),
        pathTiles=path_tiles,
        courseTiles=course_tiles,
    )

    _write_cold_start_result(db, user_id, path_items, course_items, output, scope, course_id)

    return output


async def _get_or_compute_embedding(
    db: Session, onboarding, query_text: str, user_id: str
) -> list[float]:
    """Embedding cache check (Section 5.2) — cache hit/miss determines which event fires."""
    if onboarding.query_embedding_cache:
        events.cold_start_embed_cached(user_id)
        return json.loads(onboarding.query_embedding_cache)

    provider = get_embedding_provider()
    embedding = await provider.embed(query_text)
    db_tool.cache_query_embedding(db, onboarding.id, embedding)
    events.cold_start_embed_computed(user_id)
    return embedding


def template_narrative(topics: list[str], goal: str, top_match_title: str | None) -> str:
    """Fill-in-the-blank sentence. NOT an LLM call — plain Python string formatting."""
    if not top_match_title:
        return (
            f"Most people who pick {', '.join(topics[:2])}, aiming to {goal}, "
            f"haven't found a strong match yet — check back as our catalog grows."
        )
    topic_phrase = topics[0] if len(topics) < 2 else f"{topics[0]} + {topics[1]}"
    return (
        f"Most people who pick {topic_phrase}, aiming to {goal}, "
        f"start with the same move — {top_match_title}."
    )


def _template_highlights(topics: list[str], goal: str, top_match_title: str | None) -> list[str]:
    """Scannable pointers for cold start — not an LLM call, mirrors the
    Solver's `highlights` field so first-time and returning users see the
    same panel shape."""
    points = [f"Selected interests: {', '.join(topics[:2])}" if topics else "No topics selected yet"]
    points.append(f"Goal: {goal}")
    if top_match_title:
        points.append(f"Top match for this profile: {top_match_title}")
    return points


def _rank_path_tiles(db: Session, decision) -> tuple[list, list]:
    """Hero path (decision["top_path"]) plus up to MAX_PATH_TILES-1 more matched
    paths, ranked by combined_score. Degrades gracefully to fewer tiles if the
    catalog doesn't have enough matches."""
    path_tiles, path_items, seen_ids = [], [], set()
    kickers = ["Path 1 · Top match", "Path 2", "Path 3"]

    candidates = [decision["top_path"]] + [
        c for c in decision["matched_paths"] if c.item_id != decision["top_path"].item_id
    ]
    for cand in candidates:
        if len(path_tiles) >= MAX_PATH_TILES or cand.item_id in seen_ids:
            continue
        path_row = db.get(Path, cand.item_id)
        if not path_row:
            continue
        course_rows = [pc.course for pc in path_row.courses]
        rank = len(path_tiles) + 1
        path_tiles.append(tiles.build_path_tile(path_row, course_rows, kickers[rank - 1]))
        path_items.append({"path_id": path_row.id, "rank": rank, "is_hero": rank == 1})
        seen_ids.add(cand.item_id)

    return path_tiles, path_items


def _rank_course_tiles(db: Session, decision) -> tuple[list, list]:
    """Hero course (decision["top_course"]) plus up to MAX_COURSE_TILES-1 more
    matched courses, ranked by combined_score. Degrades gracefully to fewer
    tiles if the catalog doesn't have enough matches."""
    course_tiles, course_items, seen_ids = [], [], set()
    kickers = ["Option 1 · Recommended", "Option 2 · Alternative"]

    candidates = [decision["top_course"]] + [
        c for c in decision["matched_courses"] if c.item_id != decision["top_course"].item_id
    ]
    for cand in candidates:
        if len(course_tiles) >= MAX_COURSE_TILES or cand.item_id in seen_ids:
            continue
        course_row = db.get(Product, cand.item_id)
        if not course_row:
            continue
        rank = len(course_tiles) + 1
        course_tiles.append(tiles.build_course_tile(course_row, kickers[rank - 1]))
        course_items.append({"product_id": course_row.id, "rank": rank, "is_hero": rank == 1})
        seen_ids.add(cand.item_id)

    return course_tiles, course_items


def _build_tiles(db: Session, decision) -> tuple[list, list, list, list]:
    path_tiles, course_tiles, path_items, course_items = [], [], [], []

    if decision["shape"] == "curated_path" and decision["top_path"]:
        path_tiles, path_items = _rank_path_tiles(db, decision)
        if decision["top_course"]:
            course_tiles, course_items = _rank_course_tiles(db, decision)

    elif decision["shape"] == "combo":
        course_rows = [db.get(Product, c.item_id) for c in decision["combo_courses"]]
        course_tiles.append(tiles.build_combo_tile(course_rows, "Suggested combo"))
        course_items = [
            {"product_id": c.item_id, "rank": i + 1, "is_hero": i == 0}
            for i, c in enumerate(decision["combo_courses"])
        ]

    elif decision["shape"] == "single_course" and decision["top_course"]:
        course_tiles, course_items = _rank_course_tiles(db, decision)

    return path_tiles, course_tiles, path_items, course_items


def _write_cold_start_result(
    db: Session,
    user_id: str,
    path_items: list,
    course_items: list,
    output: SolverOutput,
    scope: str = "home",
    course_id: str | None = None,
):
    log = db_tool.create_recommendation_log(
        db,
        user_id=user_id,
        trigger_reason="cold_start",
        act_path_candidates=None,
        act_course_candidates=None,
        validator_status="pass",
        retry_count=0,
        solver_narrative=output.narrative,
        solver_output_json=json.dumps(output.model_dump()),
        latency_ms=None,
        scope=scope,
        context_id=course_id,
    )
    db_tool.write_recommendations(db, user_id, log.id, path_items, course_items)
