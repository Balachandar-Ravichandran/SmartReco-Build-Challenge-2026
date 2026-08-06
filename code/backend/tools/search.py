"""hybrid_search() — the ONE shared retrieval function (Section 7.1).

Called by agent/act_path.py, agent/act_course.py, and agent/coldstart.py.
There is exactly one hybrid search implementation in the codebase.
"""
import math
from typing import Literal

from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.schemas import SearchCandidate
from backend.tools import db_tool, vector_tool


def hybrid_search(
    db: Session,
    query_vector: list[float],
    query_tags: list[str],
    collection: Literal["course_embeddings", "path_embeddings"],
    top_k: int = 5,
    primary_tag: str | None = None,
    boost_tags: list[str] | None = None,
    exclude_ids: set[str] | None = None,
) -> list[SearchCandidate]:
    settings = get_settings()
    collection_table = "paths" if collection == "path_embeddings" else "products"
    item_type = "path" if collection == "path_embeddings" else "course"

    semantic_hits = vector_tool.query(collection, query_vector, top_k=top_k)
    keyword_hits = db_tool.tag_overlap_query(
        db, collection_table, query_tags, primary_tag=primary_tag, boost_tags=boost_tags
    )

    # When a page context is given, the keyword lane has already been
    # engineered to mean something specific and reliable (0.65 baseline for
    # actually matching the page's own category — Section 7.1's primary/boost
    # split), whereas semantic similarity against a blended query text (page
    # category + historical interest) can legitimately be weak for a
    # perfectly on-topic item whose description just doesn't read as similar
    # to the historical tag (observed: a real Business & Finance path scored
    # semantic_similarity=0.0 and missed MIN_ACCEPT_SCORE despite being an
    # exact category match). Weighting keyword higher here means "matches the
    # page you're on" is enough to pass on its own; semantic/history then only
    # affect ranking among matches, never gate whether one is "relevant" at all.
    keyword_weight = 0.8 if primary_tag else 0.5
    merged = _merge_by_id(semantic_hits, keyword_hits, item_type, keyword_weight=keyword_weight)

    if exclude_ids:
        merged = [c for c in merged if c.item_id not in exclude_ids]

    for c in merged:
        c.is_match = (
            c.keyword_overlap_ratio > settings.KEYWORD_THRESHOLD
            or c.semantic_similarity > settings.SEMANTIC_THRESHOLD
        )

    _rerank(merged, weight=settings.RERANK_POPULARITY_WEIGHT)

    return sorted(merged, key=lambda c: c.rerank_score, reverse=True)


def _rerank(candidates: list[SearchCandidate], weight: float) -> None:
    """Second-pass re-rank (Section 7.1 polish): blends relevance
    (combined_score) with a popularity/quality signal (rating + learners_count)
    so that among several relevant matches, the better-regarded, more-enrolled
    one sorts first. Relevance gating (is_match, combined_score itself) is
    untouched — popularity only nudges final ordering, never decides whether
    an item is a match. Rating/learners_count are min-max/log normalized
    *within this result set* since raw values don't compare meaningfully
    across catalog scales; items with no popularity signal (e.g. paths, which
    carry no rating) get a neutral 0.5 so they're neither boosted nor
    penalized relative to items that do have one.
    """
    ratings = [c.rating for c in candidates if c.rating is not None]
    log_counts = [math.log1p(c.learners_count) for c in candidates if c.learners_count is not None]
    r_min, r_max = (min(ratings), max(ratings)) if ratings else (0.0, 0.0)
    c_min, c_max = (min(log_counts), max(log_counts)) if log_counts else (0.0, 0.0)

    for c in candidates:
        if c.rating is None and c.learners_count is None:
            popularity = 0.5
        else:
            r_norm = (
                (c.rating - r_min) / (r_max - r_min)
                if c.rating is not None and r_max > r_min
                else 0.5
            )
            cnt_norm = (
                (math.log1p(c.learners_count) - c_min) / (c_max - c_min)
                if c.learners_count is not None and c_max > c_min
                else 0.5
            )
            popularity = 0.5 * r_norm + 0.5 * cnt_norm
        c.rerank_score = (1 - weight) * c.combined_score + weight * popularity


def _merge_by_id(
    semantic_hits: list[dict], keyword_hits: list[dict], item_type: str, keyword_weight: float = 0.5
) -> list[SearchCandidate]:
    """Merge semantic (vector) and keyword (SQL) candidate lists by item id."""
    by_id: dict[str, dict] = {}

    for hit in keyword_hits:
        by_id[hit["item_id"]] = {
            "item_id": hit["item_id"],
            "title": hit["title"],
            "keyword_overlap_ratio": hit["keyword_overlap_ratio"],
            "semantic_similarity": 0.0,
            "discount_amount": hit.get("discount_amount"),
            "has_capstone": hit.get("has_capstone"),
            "rating": hit.get("rating"),
            "learners_count": hit.get("learners_count"),
        }

    for hit in semantic_hits:
        item_id = hit["item_id"]
        if item_id in by_id:
            by_id[item_id]["semantic_similarity"] = hit["semantic_similarity"]
        else:
            metadata = hit.get("metadata", {})
            by_id[item_id] = {
                "item_id": item_id,
                "title": metadata.get("title", item_id),
                "keyword_overlap_ratio": 0.0,
                "semantic_similarity": hit["semantic_similarity"],
                "discount_amount": metadata.get("discount_amount"),
                "has_capstone": metadata.get("has_capstone"),
                "rating": metadata.get("rating"),
                "learners_count": metadata.get("learners_count"),
            }

    candidates = []
    for entry in by_id.values():
        combined_score = (
            keyword_weight * entry["keyword_overlap_ratio"]
            + (1 - keyword_weight) * entry["semantic_similarity"]
        )
        candidates.append(
            SearchCandidate(
                item_type=item_type,
                item_id=entry["item_id"],
                title=entry["title"],
                keyword_overlap_ratio=entry["keyword_overlap_ratio"],
                semantic_similarity=entry["semantic_similarity"],
                combined_score=combined_score,
                is_match=False,  # set by caller after threshold check
                discount_amount=entry["discount_amount"],
                has_capstone=entry["has_capstone"],
                rating=entry.get("rating"),
                learners_count=entry.get("learners_count"),
            )
        )
    return candidates
