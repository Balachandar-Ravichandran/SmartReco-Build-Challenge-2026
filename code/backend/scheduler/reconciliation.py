"""APScheduler interval job (every 5 min) — retries failed vector_sync_log rows
(Section 13.4)."""
from backend.core import events
from backend.db.session import get_db
from backend.db.models import Product, Path
from backend.tools import db_tool, vector_tool
from backend.tools.llm_provider import get_embedding_provider


async def run():
    with get_db() as db:
        failed = db_tool.get_failed_vector_syncs(db, limit=50)

        for row in failed:
            events.reconciliation_retry_attempted(
                row.id, "course" if row.product_id else "path", row.product_id or row.path_id
            )
            await _retry_one(db, row)


async def _retry_one(db, sync_row):
    item_type = "course" if sync_row.product_id else "path"
    item_id = sync_row.product_id or sync_row.path_id

    model = Product if item_type == "course" else Path
    catalog_row = db.get(model, item_id)
    if catalog_row is None or not catalog_row.is_active:
        return  # item deleted since — nothing to reconcile

    try:
        provider = get_embedding_provider()
        embedding = await provider.embed(catalog_row.description)

        import json
        tags = json.loads(catalog_row.tags)
        metadata = {"tags": tags, "price": catalog_row.price}

        if item_type == "course":
            vector_tool.upsert_course(item_id, catalog_row.description, embedding, metadata)
        else:
            metadata["has_capstone"] = bool(catalog_row.has_capstone)
            vector_tool.upsert_path(item_id, catalog_row.description, embedding, metadata)

        db_tool.mark_embedding_synced(db, item_type, item_id)
        sync_row.vector_status = "ok"
        sync_row.error_message = None
        db.flush()
        events.vector_upsert_succeeded(item_type, item_id, f"{item_type}_embeddings", latency_ms=0)
    except Exception as e:
        sync_row.error_message = str(e)
        db.flush()
        events.vector_upsert_failed(item_type, item_id, error=str(e))
