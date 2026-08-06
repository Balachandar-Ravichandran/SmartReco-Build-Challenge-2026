"""SQL read/write helpers. Owns dual-write SQL-side steps (Section 13.4).

Keyword-overlap search here is the SQL half of hybrid_search() (Section 7.1) —
the semantic half lives in vector_tool.py, merged by tools/search.py.
"""
import json
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session
from sqlalchemy import select

from backend.db.models import (
    Product,
    Path,
    PathCourse,
    UserOnboarding,
    BehavioralEvent,
    RecommendationLog,
    CurrentRecommendation,
    VectorSyncLog,
)


# ==================== TAG OVERLAP (keyword lane of hybrid_search) ====================
def tag_overlap_query(
    db: Session,
    collection_table: Literal["products", "paths"],
    query_tags: list[str],
    primary_tag: str | None = None,
    boost_tags: list[str] | None = None,
) -> list[dict]:
    """Keyword-overlap search — the SQL lane of hybrid_search() (Section 7.1).

    Returns candidate dicts with `keyword_overlap_ratio` computed, one per active row.
    Rows with zero overlap are still returned (ratio=0.0) — filtering to `is_match`
    happens in hybrid_search(), not here, so keyword and semantic lanes merge cleanly.

    Two scoring modes:
    - `primary_tag` given (course/path/browse pages — a specific page context):
      ratio = 0.65 x (does the item carry the current page's own tag) + 0.35 x
      (how much of the user's top-5 historical tags it also carries). This is
      what actually blends "the page you're on" with "your history" — a flat
      `query_tags` overlap ratio dilutes as soon as unrelated historical tags
      are added to the denominator, which is exactly what was making
      cross-category matches (e.g. a Mobile+AI path for a user with an
      AI-heavy history browsing Mobile Development) narrowly miss the
      acceptance threshold and fall back to stale, off-topic data.
    - `primary_tag` absent (home — no single page to anchor to): original flat
      overlap-ratio-over-query_tags behavior, unchanged.
    """
    if not query_tags and not primary_tag:
        return []

    model = Product if collection_table == "products" else Path
    rows = db.execute(
        select(model).where(model.is_active == 1)
    ).scalars().all()

    query_set = set(query_tags)
    boost_set = set(boost_tags or [])
    candidates = []
    for row in rows:
        row_tags = set(json.loads(row.tags))
        if primary_tag:
            category_match = 1.0 if primary_tag in row_tags else 0.0
            boost_overlap = len(row_tags & boost_set) / len(boost_set) if boost_set else 0.0
            ratio = 0.65 * category_match + 0.35 * boost_overlap
        else:
            overlap = len(query_set & row_tags)
            ratio = overlap / len(query_set) if query_set else 0.0
        candidates.append(
            {
                "item_type": "path" if collection_table == "paths" else "course",
                "item_id": row.id,
                "title": row.title,
                "keyword_overlap_ratio": ratio,
                "discount_amount": getattr(row, "discount_amount", None),
                "has_capstone": bool(getattr(row, "has_capstone", None))
                if collection_table == "paths"
                else None,
                "rating": getattr(row, "rating", None),
                "learners_count": getattr(row, "learners_count", None),
            }
        )
    return candidates


# ==================== RE-RANK / METADATA FILTERING SUPPORT ====================
def get_purchased_item_ids(
    db: Session, user_id: str, item_type: Literal["path", "course"]
) -> set[str]:
    """Item ids the user has already purchased (Section 7.1 polish) — used to
    metadata-filter them out of retrieval so a hybrid_search() result never
    re-recommends something already bought."""
    id_col = BehavioralEvent.path_id if item_type == "path" else BehavioralEvent.product_id
    rows = db.execute(
        select(id_col).where(
            BehavioralEvent.user_id == user_id,
            BehavioralEvent.event_type == "purchase",
            id_col.isnot(None),
        )
    ).scalars()
    return set(rows)


# ==================== CATALOG READS ====================
def get_by_id(db: Session, item_type: Literal["path", "course"], item_id: str):
    """Resolve an item_id back to its full catalog row (Solver's one exception, Section 9 step 2)."""
    model = Path if item_type == "path" else Product
    return db.get(model, item_id)


def get_all_products(db: Session, include_inactive: bool = False) -> list[Product]:
    """Admin catalog table listing."""
    stmt = select(Product).order_by(Product.created_at.desc())
    if not include_inactive:
        stmt = stmt.where(Product.is_active == 1)
    return list(db.execute(stmt).scalars())


def get_all_paths(db: Session, include_inactive: bool = False) -> list[Path]:
    """Admin catalog table listing."""
    stmt = select(Path).order_by(Path.created_at.desc())
    if not include_inactive:
        stmt = stmt.where(Path.is_active == 1)
    return list(db.execute(stmt).scalars())


def get_path_course_titles(db: Session, path_id: str) -> list[str]:
    rows = (
        db.query(PathCourse)
        .filter(PathCourse.path_id == path_id)
        .order_by(PathCourse.sequence_order)
        .all()
    )
    return [pc.course.title for pc in rows]


def get_dual_write_status(
    db: Session, item_type: Literal["path", "course"], item_id: str
) -> str:
    """"ok" | "failed" | "pending" — for the admin table's sync badges."""
    model = Path if item_type == "path" else Product
    row = db.get(model, item_id)
    if row and row.embedding_synced_at:
        return "ok"

    filter_col = VectorSyncLog.path_id if item_type == "path" else VectorSyncLog.product_id
    last_failure = (
        db.query(VectorSyncLog)
        .filter(filter_col == item_id, VectorSyncLog.vector_status == "failed")
        .order_by(VectorSyncLog.synced_at.desc())
        .first()
    )
    return "failed" if last_failure else "pending"


def get_popular_courses(db: Session, limit: int = 6, topic: str | None = None) -> list[Product]:
    """Home page "Popular in {topic}" rail — most-learners-first, optionally
    filtered to the user's top interest tag so the section title matches its
    contents. Falls back to unfiltered popularity if the topic has no matches
    (e.g. a niche tag with an empty catalog slice)."""
    rows = list(
        db.execute(
            select(Product)
            .where(Product.is_active == 1)
            .order_by(Product.learners_count.desc())
        ).scalars()
    )
    if topic:
        filtered = [r for r in rows if topic in json.loads(r.tags)]
        if filtered:
            return filtered[:limit]
    return rows[:limit]


def get_extra_paths(
    db: Session, limit: int = 2, topic: str | None = None, exclude_ids: set[str] | None = None
) -> list[Path]:
    """Supplementary paths for the home page's Agent Recommended rail when the
    Solver's single decided shape yields fewer than 2 paths. Display padding
    only — never touches the persisted recommendation decision/certificate."""
    exclude_ids = exclude_ids or set()
    rows = [
        r for r in db.execute(
            select(Path).order_by(Path.has_capstone.desc(), Path.discount_amount.desc())
        ).scalars()
        if r.id not in exclude_ids
    ]
    if topic:
        filtered = [r for r in rows if topic in json.loads(r.tags)]
        if filtered:
            return filtered[:limit]
    return rows[:limit]


def get_recently_viewed(db: Session, user_id: str, limit: int = 6) -> list[dict]:
    """Home page "Recently viewed" rail — distinct courses/paths the user has
    viewed, most-recent first (repeat views of the same item collapse to its
    latest occurrence)."""
    events = list(
        db.execute(
            select(BehavioralEvent)
            .where(BehavioralEvent.user_id == user_id, BehavioralEvent.event_type == "view")
            .order_by(BehavioralEvent.created_at.desc())
            .limit(200)
        ).scalars()
    )

    seen: set[str] = set()
    items: list[dict] = []
    for e in events:
        if len(items) >= limit:
            break
        if e.product_id:
            key = f"course:{e.product_id}"
            if key in seen:
                continue
            seen.add(key)
            p = db.get(Product, e.product_id)
            if p:
                items.append({
                    "id": p.id, "kind": "course", "title": p.title,
                    "description": p.description, "tags": json.loads(p.tags),
                    "price": p.price, "rating": p.rating, "learners_count": p.learners_count,
                })
        elif e.path_id:
            key = f"path:{e.path_id}"
            if key in seen:
                continue
            seen.add(key)
            p = db.get(Path, e.path_id)
            if p:
                items.append({
                    "id": p.id, "kind": "path", "title": p.title,
                    "description": p.description, "tags": json.loads(p.tags),
                    "price": p.price, "rating": None, "learners_count": None,
                })
    return items


def get_similar_courses(db: Session, course_id: str, limit: int = 3) -> list[Product]:
    """Course detail page's "students who explored this also looked at" grid.

    Deliberately lightweight (shared-tag lookup, ordered by popularity) rather
    than a full hybrid_search/agent call — the wireframe's own framing is
    "students who explored this also looked at", i.e. category adjacency, not
    a personalized recommendation (that's what the reccard above it is for).
    """
    course = db.get(Product, course_id)
    if course is None:
        return []
    tags = set(json.loads(course.tags))
    if not tags:
        return []

    candidates = db.execute(
        select(Product).where(Product.is_active == 1, Product.id != course_id)
    ).scalars().all()

    matches = [c for c in candidates if tags & set(json.loads(c.tags))]
    matches.sort(key=lambda c: c.learners_count, reverse=True)
    return matches[:limit]


def get_cart_recommendations(
    db: Session, items: list[tuple[str, str]], limit: int = 3
) -> tuple[list[tuple[str, Product | Path]], str | None, set[str]]:
    """Cart page's "Complete your learning" rail. The cosmetic cart has no
    server-side record (Section 2 — contents live in localStorage only), so
    the caller sends the cart's (kind, id) pairs on every request. Same
    tag-overlap lane as get_similar_courses, just seeded from every item in
    the cart at once instead of one product page — this is catalog
    adjacency, not a personalized agent pick.

    Returns ((kind, row) pairs, top_tag, cart_tags) — raw ORM rows rather
    than dicts so the caller can feed them straight into agent/tiles.py's
    build_course_tile / build_path_tile and render the exact same
    OptionTile card the Agent Recommended rail uses. top_tag is the tag
    most represented across the cart and cart_tags is the full set, both
    used to phrase the "why" explanation and per-tile highlights.
    """
    cart_ids = {item_id for _, item_id in items}
    tag_counts: dict[str, int] = {}
    for kind, item_id in items:
        model = Path if kind == "path" else Product
        row = db.get(model, item_id)
        if row is None:
            continue
        for tag in json.loads(row.tags):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    if not tag_counts:
        return [], None, set()

    cart_tags = set(tag_counts)
    top_tag = max(tag_counts, key=tag_counts.get)

    candidates: list[tuple[str, Product | Path, int, float]] = []
    for row in db.execute(select(Product).where(Product.is_active == 1)).scalars():
        if row.id in cart_ids:
            continue
        overlap = cart_tags & set(json.loads(row.tags))
        if overlap:
            candidates.append(("course", row, len(overlap), row.learners_count))
    for row in db.execute(select(Path).where(Path.is_active == 1)).scalars():
        if row.id in cart_ids:
            continue
        overlap = cart_tags & set(json.loads(row.tags))
        if overlap:
            candidates.append(("path", row, len(overlap), row.discount_amount or 0))

    candidates.sort(key=lambda c: (c[2], c[3]), reverse=True)
    top = [(kind, row) for kind, row, _, _ in candidates[:limit]]
    return top, top_tag, cart_tags


# ==================== ONBOARDING ====================
def get_latest_onboarding(db: Session, user_id: str) -> UserOnboarding | None:
    return db.execute(
        select(UserOnboarding)
        .where(UserOnboarding.user_id == user_id)
        .order_by(UserOnboarding.created_at.desc())
        .limit(1)
    ).scalars().first()


def cache_query_embedding(db: Session, onboarding_id: int, embedding: list[float]):
    onboarding = db.get(UserOnboarding, onboarding_id)
    onboarding.query_embedding_cache = json.dumps(embedding)
    db.flush()


# ==================== BEHAVIORAL EVENTS / SCORING SUPPORT ====================
def has_any_events(db: Session, user_id: str) -> bool:
    """Cold-start check (Section 5.2): count(*) behavioral_events for user = 0."""
    row = db.execute(
        select(BehavioralEvent.id).where(BehavioralEvent.user_id == user_id).limit(1)
    ).first()
    return row is not None


def get_recent_events(db: Session, user_id: str, limit: int = 200) -> list[BehavioralEvent]:
    """Feed for live RFM interest scoring (agent/scoring.py)."""
    return list(
        db.execute(
            select(BehavioralEvent)
            .where(BehavioralEvent.user_id == user_id)
            .order_by(BehavioralEvent.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def has_events_since(db: Session, user_id: str, since: datetime) -> bool:
    """Any new tracked signal for this user after `since`? Used by the
    per-page cache gate (agent/scoring.py::should_rerun) — a single new event
    of any kind invalidates a cached recommendation, since interest scores
    blend full history and any signal could shift the picture."""
    row = db.execute(
        select(BehavioralEvent.id)
        .where(BehavioralEvent.user_id == user_id, BehavioralEvent.created_at > since)
        .limit(1)
    ).first()
    return row is not None


def get_latest_recommendation_log(
    db: Session, user_id: str, scope: str, context_id: str | None = None
) -> RecommendationLog | None:
    """Last confirmed (Solver-validated, Writer-persisted) run for this exact
    page — never a fallback run, since fallback.py never writes a log row.
    Scoped to (scope, context_id) so a cache hit can never replay a different
    page's content."""
    return (
        db.query(RecommendationLog)
        .filter(
            RecommendationLog.user_id == user_id,
            RecommendationLog.scope == scope,
            RecommendationLog.context_id == context_id,
        )
        .order_by(RecommendationLog.created_at.desc())
        .first()
    )


def insert_behavioral_event(db: Session, event_data: dict) -> BehavioralEvent:
    row = BehavioralEvent(**event_data)
    db.add(row)
    db.flush()
    return row


# ==================== RECOMMENDATION WRITE (Section 10.1) ====================
def create_recommendation_log(
    db: Session,
    user_id: str,
    trigger_reason: str,
    act_path_candidates: list | None,
    act_course_candidates: list | None,
    validator_status: str,
    retry_count: int,
    solver_narrative: str | None,
    latency_ms: int | None,
    scope: str | None = None,
    context_id: str | None = None,
    solver_output_json: str | None = None,
) -> RecommendationLog:
    log = RecommendationLog(
        user_id=user_id,
        trigger_reason=trigger_reason,
        scope=scope,
        context_id=context_id,
        act_path_candidates=json.dumps(act_path_candidates) if act_path_candidates else None,
        act_course_candidates=json.dumps(act_course_candidates) if act_course_candidates else None,
        validator_status=validator_status,
        retry_count=retry_count,
        solver_narrative=solver_narrative,
        solver_output_json=solver_output_json,
        latency_ms=latency_ms,
    )
    db.add(log)
    db.flush()
    return log


def write_recommendations(
    db: Session,
    user_id: str,
    recommendation_log_id: int,
    path_items: list[dict],  # [{"path_id": ..., "rank": int, "is_hero": bool}]
    course_items: list[dict],  # [{"product_id": ..., "rank": int, "is_hero": bool}]
):
    """Delete-then-insert current_recommendations for user (Section 10.1)."""
    db.query(CurrentRecommendation).filter(
        CurrentRecommendation.user_id == user_id
    ).delete()

    for item in path_items:
        db.add(
            CurrentRecommendation(
                user_id=user_id,
                recommendation_log_id=recommendation_log_id,
                item_type="path",
                path_id=item["path_id"],
                rank=item["rank"],
                is_hero=int(item["is_hero"]),
            )
        )
    for item in course_items:
        db.add(
            CurrentRecommendation(
                user_id=user_id,
                recommendation_log_id=recommendation_log_id,
                item_type="course",
                product_id=item["product_id"],
                rank=item["rank"],
                is_hero=int(item["is_hero"]),
            )
        )
    db.flush()


def get_current_recommendations(db: Session, user_id: str) -> list[CurrentRecommendation]:
    return list(
        db.execute(
            select(CurrentRecommendation)
            .where(CurrentRecommendation.user_id == user_id)
            .order_by(CurrentRecommendation.item_type, CurrentRecommendation.rank)
        ).scalars()
    )


# ==================== DUAL-WRITE SQL SIDE (Section 13.4) ====================
def create_product(db: Session, product_data: dict) -> Product:
    product_data = dict(product_data)
    product_data["tags"] = json.dumps(product_data["tags"])
    row = Product(**product_data)
    db.add(row)
    db.flush()
    return row


def update_product(db: Session, product_id: str, updates: dict) -> Product:
    row = db.get(Product, product_id)
    if "tags" in updates:
        updates["tags"] = json.dumps(updates["tags"])
    for key, value in updates.items():
        setattr(row, key, value)
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def is_product_in_path(db: Session, product_id: str) -> str | None:
    """Returns blocking path_id if product is still referenced, else None."""
    row = db.execute(
        select(PathCourse.path_id).where(PathCourse.course_id == product_id).limit(1)
    ).first()
    return row[0] if row else None


def soft_delete_product(db: Session, product_id: str):
    row = db.get(Product, product_id)
    row.is_active = 0
    row.deleted_at = datetime.utcnow()
    db.flush()


def create_path(db: Session, path_data: dict, course_ids: list[str]) -> Path:
    path_data = dict(path_data)
    path_data["tags"] = json.dumps(path_data["tags"])
    row = Path(**path_data)
    db.add(row)
    db.flush()
    for order, course_id in enumerate(course_ids):
        db.add(PathCourse(path_id=row.id, course_id=course_id, sequence_order=order))
    db.flush()
    return row


def delete_path(db: Session, path_id: str):
    """Deletes path_courses join rows explicitly, then the path itself.

    schema.sql declares path_courses.path_id ON DELETE CASCADE, but SQLite
    only enforces FK actions when PRAGMA foreign_keys=ON is set per-connection
    (not this app's default), and separately the ORM's own dependency
    tracking doesn't know to defer to a DB-level cascade it isn't configured
    to trust (passive_deletes) — either alone left path_courses as orphaned
    rows or raised trying to null out a NOT NULL FK column. Explicit two-step
    delete sidesteps both: linked courses (products) are never touched.
    """
    db.query(PathCourse).filter(PathCourse.path_id == path_id).delete()
    row = db.get(Path, path_id)
    db.delete(row)
    db.flush()


def detach_course_from_path(db: Session, path_id: str, course_id: str):
    db.query(PathCourse).filter(
        PathCourse.path_id == path_id, PathCourse.course_id == course_id
    ).delete()
    db.flush()


def mark_embedding_synced(db: Session, item_type: Literal["path", "course"], item_id: str):
    model = Path if item_type == "path" else Product
    row = db.get(model, item_id)
    row.embedding_synced_at = datetime.utcnow()
    db.flush()


def log_vector_sync(
    db: Session,
    item_type: Literal["path", "course"],
    item_id: str,
    operation: Literal["insert", "update", "delete"],
    sql_status: str,
    vector_status: str,
    error_message: str | None = None,
) -> VectorSyncLog:
    log = VectorSyncLog(
        product_id=item_id if item_type == "course" else None,
        path_id=item_id if item_type == "path" else None,
        operation=operation,
        sql_status=sql_status,
        vector_status=vector_status,
        error_message=error_message,
    )
    db.add(log)
    db.flush()
    return log


def get_failed_vector_syncs(db: Session, limit: int = 50) -> list[VectorSyncLog]:
    """Reconciliation job's read (scheduler/reconciliation.py)."""
    return list(
        db.execute(
            select(VectorSyncLog)
            .where(VectorSyncLog.vector_status == "failed")
            .order_by(VectorSyncLog.synced_at.asc())
            .limit(limit)
        ).scalars()
    )
