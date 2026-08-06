"""Read-only observability endpoints (Section 14.6, 14.7)."""
import json
from pathlib import Path as FSPath

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.core.config import get_settings
from backend.tools import db_tool
from backend.db.models import RecommendationLog

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/dual-write-status")
def dual_write_status(db: Session = Depends(get_db)):
    """Observability surface for the dual-write bonus requirement."""
    failed = db_tool.get_failed_vector_syncs(db, limit=50)
    return {
        "failed_count": len(failed),
        "rows": [
            {
                "id": row.id,
                "product_id": row.product_id,
                "path_id": row.path_id,
                "operation": row.operation,
                "error_message": row.error_message,
                "synced_at": row.synced_at.isoformat(),
            }
            for row in failed
        ],
    }


@router.get("/runs/{recommendation_log_id}/events")
def get_run_events(recommendation_log_id: int, db: Session = Depends(get_db)):
    """Filtered slice of the JSONL event log for one recommendation run."""
    log = db.get(RecommendationLog, recommendation_log_id)
    if not log:
        raise HTTPException(404, "Recommendation log not found")

    settings = get_settings()
    log_path = FSPath(settings.EVENTS_LOG_DIR) / "events.jsonl"
    if not log_path.exists():
        return {"events": []}

    matching = []
    with open(log_path) as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("user_id") == log.user_id:
                matching.append(record)

    return {
        "recommendation_log_id": recommendation_log_id,
        "user_id": log.user_id,
        "trigger_reason": log.trigger_reason,
        "events": matching,
    }
