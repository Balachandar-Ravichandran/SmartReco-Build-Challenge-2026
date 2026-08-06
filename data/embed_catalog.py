"""One-time Chroma population pass — dual-write's vector side for seeded data.

data/seed.py only writes the relational side (pathwise.db). Chroma is a
separate store (Section 13.1), so seeded courses/paths need to be embedded and
upserted once after the SQL catalog exists. This mirrors what api/catalog.py's
create endpoints do per-row at write time, just run in bulk over the seed set.

Requires MESH_API_KEY / MESH_API_BASE configured in .env — this is the one
step in the whole seed pipeline that makes real network calls.

Usage (from code/ as working directory, so `backend.*` imports resolve):
    cd code
    python -m data.embed_catalog     # or: python ../data/embed_catalog.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

# Windows console default codepage (cp1252) can't encode some seeded titles
# (e.g. "ML → MLOps Path"). Force UTF-8 stdout so printing never crashes the run.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from backend.core import events
from backend.db.session import get_db, init_db
from backend.db.models import Product, Path as PathModel
from backend.tools import db_tool, vector_tool
from backend.tools.llm_provider import get_embedding_provider


async def embed_all():
    init_db()
    provider = get_embedding_provider()

    with get_db() as db:
        products = db.query(Product).filter(Product.is_active == 1).all()
        paths = db.query(PathModel).filter(PathModel.is_active == 1).all()

        print(f"Embedding {len(products)} courses...")
        for p in products:
            if vector_tool.exists("course_embeddings", p.id):
                continue  # idempotent — skip rows already in Chroma
            try:
                embedding = await provider.embed(p.description)
                tags = json.loads(p.tags)
                vector_tool.upsert_course(
                    p.id, p.description, embedding,
                    {"tags": tags, "price": p.price, "level": p.level, "is_active": True},
                )
                db_tool.mark_embedding_synced(db, "course", p.id)
                db_tool.log_vector_sync(db, "course", p.id, "insert", "ok", "ok")
                events.vector_upsert_succeeded("course", p.id, "course_embeddings", latency_ms=0)
                print(f"  OK  {p.id}  {p.title}")
            except Exception as e:
                db_tool.log_vector_sync(db, "course", p.id, "insert", "ok", "failed", str(e))
                events.vector_upsert_failed("course", p.id, error=str(e))
                print(f"  FAIL {p.id}  {p.title}: {e}")

        print(f"Embedding {len(paths)} paths...")
        for path in paths:
            if vector_tool.exists("path_embeddings", path.id):
                continue  # idempotent — skip rows already in Chroma
            try:
                embedding = await provider.embed(path.description)
                tags = json.loads(path.tags)
                vector_tool.upsert_path(
                    path.id, path.description, embedding,
                    {"tags": tags, "price": path.price, "has_capstone": bool(path.has_capstone), "is_active": True},
                )
                db_tool.mark_embedding_synced(db, "path", path.id)
                db_tool.log_vector_sync(db, "path", path.id, "insert", "ok", "ok")
                events.vector_upsert_succeeded("path", path.id, "path_embeddings", latency_ms=0)
                print(f"  OK  {path.id}  {path.title}")
            except Exception as e:
                db_tool.log_vector_sync(db, "path", path.id, "insert", "ok", "failed", str(e))
                events.vector_upsert_failed("path", path.id, error=str(e))
                print(f"  FAIL {path.id}  {path.title}: {e}")

    print("Done. Re-run this script any time — already-synced rows are skipped.")


if __name__ == "__main__":
    asyncio.run(embed_all())
