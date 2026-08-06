"""Admin CRUD for courses and Paths — owns dual-write orchestration call sites
(Section 13.4, 14.4, 14.5)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.core import events
from backend.core.config import get_settings
from backend.core.schemas import CreateCourseRequest, CreatePathRequest
from backend.tools import db_tool, vector_tool
from backend.tools.llm_provider import get_embedding_provider

router = APIRouter(prefix="/api/v1", tags=["catalog"])


def _validate_tags(tags: list[str]):
    settings = get_settings()
    invalid = set(tags) - set(settings.TOPIC_VOCABULARY)
    if invalid:
        raise HTTPException(422, f"Tags outside controlled vocabulary: {sorted(invalid)}")


# ==================== SEARCH (learner-facing nav typeahead) ====================
@router.get("/search")
async def search_catalog(q: str = "", db: Session = Depends(get_db)):
    import json

    needle = q.strip().lower()
    if not needle:
        return {"courses": [], "paths": []}

    def _matches(title: str, description: str, tags: list[str]) -> bool:
        if needle in title.lower() or needle in description.lower():
            return True
        return any(needle in t.lower() for t in tags)

    courses = []
    for c in db_tool.get_all_products(db):
        tags = json.loads(c.tags)
        if _matches(c.title, c.description, tags):
            courses.append({"id": c.id, "kind": "course", "title": c.title, "price": c.price, "tags": tags})

    paths = []
    for p in db_tool.get_all_paths(db):
        tags = json.loads(p.tags)
        if _matches(p.title, p.description, tags):
            paths.append({"id": p.id, "kind": "path", "title": p.title, "price": p.price, "tags": tags})

    return {"courses": courses[:6], "paths": paths[:6]}


# ==================== ADMIN LISTINGS ====================
@router.get("/courses")
async def list_courses(db: Session = Depends(get_db)):
    import json

    rows = db_tool.get_all_products(db)
    return [
        {
            "id": r.id, "title": r.title, "instructor": r.instructor, "level": r.level,
            "price": r.price, "tags": json.loads(r.tags),
            "dual_write_status": db_tool.get_dual_write_status(db, "course", r.id),
        }
        for r in rows
    ]


@router.get("/paths")
async def list_paths(db: Session = Depends(get_db)):
    rows = db_tool.get_all_paths(db)
    return [
        {
            "id": r.id, "title": r.title, "duration_months": r.duration_months,
            "price": r.price, "discount_amount": r.discount_amount,
            "has_capstone": bool(r.has_capstone),
            "courses": db_tool.get_path_course_titles(db, r.id),
            "dual_write_status": db_tool.get_dual_write_status(db, "path", r.id),
        }
        for r in rows
    ]


# ==================== COURSES ====================
@router.post("/courses", status_code=201)
async def create_course(body: CreateCourseRequest, db: Session = Depends(get_db)):
    _validate_tags(body.tags)

    try:
        row = db_tool.create_product(db, body.model_dump())
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"A course with id '{body.id}' already exists")
    events.product_created(row.id, row.title)

    await _sync_vector_upsert(db, "course", row.id, row.description, body.tags, {
        "price": row.price, "level": row.level, "is_active": True
    })

    return {"id": row.id}


@router.put("/courses/{course_id}")
async def update_course(course_id: str, body: dict, db: Session = Depends(get_db)):
    if "tags" in body:
        _validate_tags(body["tags"])

    row = db_tool.update_product(db, course_id, body)
    events.product_updated(row.id, row.title, fields_changed=list(body.keys()))

    if "description" in body or "tags" in body:
        import json
        tags = body.get("tags") or json.loads(row.tags)
        await _sync_vector_upsert(db, "course", row.id, row.description, tags, {
            "price": row.price, "level": row.level, "is_active": bool(row.is_active)
        })
    elif any(k in body for k in ("price", "level")):
        vector_tool.patch_metadata(
            "course_embeddings", row.id, {"price": row.price, "level": row.level}
        )

    return {"id": row.id}


@router.delete("/courses/{course_id}", status_code=200)
async def delete_course(course_id: str, db: Session = Depends(get_db)):
    blocking_path = db_tool.is_product_in_path(db, course_id)
    if blocking_path:
        events.product_delete_blocked(course_id, blocking_path)
        raise HTTPException(409, detail={"blocked_by_path_id": blocking_path})

    db_tool.soft_delete_product(db, course_id)
    events.product_deleted(course_id, title=course_id)
    vector_tool.delete_by_id("course_embeddings", course_id)
    events.vector_delete_succeeded("course", course_id)

    return {"deleted": course_id}


# ==================== PATHS ====================
@router.post("/paths", status_code=201)
async def create_path(body: CreatePathRequest, db: Session = Depends(get_db)):
    _validate_tags(body.tags)

    data = body.model_dump()
    course_ids = data.pop("course_ids")
    data["has_capstone"] = int(data["has_capstone"])
    try:
        row = db_tool.create_path(db, data, course_ids)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"A path with id '{body.id}' already exists")
    events.path_created(row.id, row.title, course_count=len(course_ids))

    await _sync_vector_upsert(db, "path", row.id, row.description, body.tags, {
        "price": row.price, "has_capstone": bool(row.has_capstone), "is_active": True
    })

    return {"id": row.id}


@router.delete("/paths/{path_id}", status_code=200)
async def delete_path_endpoint(path_id: str, db: Session = Depends(get_db)):
    db_tool.delete_path(db, path_id)
    events.path_deleted(path_id, title=path_id)
    vector_tool.delete_by_id("path_embeddings", path_id)
    events.vector_delete_succeeded("path", path_id)

    return {"deleted": path_id}


@router.delete("/paths/{path_id}/courses/{course_id}")
async def detach_course(path_id: str, course_id: str, db: Session = Depends(get_db)):
    db_tool.detach_course_from_path(db, path_id, course_id)
    events.path_course_detached(path_id, course_id)
    return {"detached": course_id}


# ==================== DUAL-WRITE HELPER ====================
async def _sync_vector_upsert(
    db: Session,
    item_type: str,
    item_id: str,
    description: str,
    tags: list[str],
    metadata: dict,
):
    """SQL write already committed by caller. Vector write is separate — a
    failure here does not roll back the SQL write (Section 13.4)."""
    metadata = {**metadata, "tags": tags}
    try:
        provider = get_embedding_provider()
        embedding = await provider.embed(description)

        if item_type == "course":
            vector_tool.upsert_course(item_id, description, embedding, metadata)
        else:
            vector_tool.upsert_path(item_id, description, embedding, metadata)

        db_tool.mark_embedding_synced(db, item_type, item_id)
        db_tool.log_vector_sync(db, item_type, item_id, "insert", "ok", "ok")
        events.vector_upsert_succeeded(item_type, item_id, f"{item_type}_embeddings", latency_ms=0)
    except Exception as e:
        db_tool.log_vector_sync(db, item_type, item_id, "insert", "ok", "failed", error_message=str(e))
        events.vector_upsert_failed(item_type, item_id, error=str(e))
