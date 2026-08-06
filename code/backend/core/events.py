"""Structured event logging — JSONL, all 40 decision points (Section 11).

Single call site: core/events.py::emit(). Every event payload logged verbatim.
Denylist redaction: strips sensitive field names before writing.
"""
import json
import os
from datetime import datetime
from typing import Any
from pathlib import Path

from backend.core.config import get_settings

# Fields to redact from event payloads (never log these)
SENSITIVE_FIELDS = {
    "api_key",
    "password_hash",
    "mesh_api_key",
    "langchain_api_key",
    "token",
    "secret",
}


def _redact_payload(payload: dict) -> dict:
    """Recursively redact sensitive fields from payload."""
    redacted = {}
    for key, value in payload.items():
        key_lower = key.lower()
        if any(sensitive in key_lower for sensitive in SENSITIVE_FIELDS):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, dict):
            redacted[key] = _redact_payload(value)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            redacted[key] = [_redact_payload(item) if isinstance(item, dict) else item for item in value]
        else:
            redacted[key] = value
    return redacted


def emit(category: str, event: str, **payload) -> None:
    """Emit a structured event to JSONL log.

    Args:
        category: Event category (e.g., "trigger", "validation", "solver")
        event: Event name (e.g., "TRIGGER_RECEIVED", "VALIDATION_PASS_1_PASSED")
        **payload: Event-specific data (will be redacted of sensitive fields)

    Example:
        emit("validation", "VALIDATION_PASS_1_PASSED",
             worker="path", score=0.75, reason="top_combined_score clears threshold")
    """
    settings = get_settings()
    log_dir = settings.EVENTS_LOG_DIR
    os.makedirs(log_dir, exist_ok=True)

    # Redact payload
    safe_payload = _redact_payload(payload)

    # Construct event record
    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "category": category,
        "event": event,
        **safe_payload,
    }

    # Append to session log (JSONL format)
    log_path = Path(log_dir) / "events.jsonl"
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except IOError as e:
        # Log errors to stderr but don't fail the app
        print(f"Error writing event log: {e}", file=__import__("sys").stderr)


# Event catalogue (Section 11) — all 40 event names, by category

# ===== TRIGGER / COLD-START EVENTS =====
def trigger_received(user_id: str, scope: str, course_id: str | None = None):
    emit("trigger", "TRIGGER_RECEIVED", user_id=user_id, scope=scope, course_id=course_id)


def significance_check_passed(user_id: str, top_tag: str, score_delta: float, reason: str = "tag_change"):
    emit(
        "trigger",
        "SIGNIFICANCE_CHECK_PASSED",
        user_id=user_id,
        top_tag=top_tag,
        score_delta=score_delta,
        reason=reason,
    )


def cold_start_detected(user_id: str):
    emit("cold_start", "COLD_START_DETECTED", user_id=user_id)


def cold_start_hybrid_search_completed(user_id: str, candidate_count: int, top_item_title: str):
    emit(
        "cold_start",
        "COLD_START_HYBRID_SEARCH_COMPLETED",
        user_id=user_id,
        candidate_count=candidate_count,
        top_item_title=top_item_title,
    )


def cold_start_embed_cached(user_id: str):
    emit("cold_start", "COLD_START_EMBED_CACHED", user_id=user_id)


def cold_start_embed_computed(user_id: str):
    emit("cold_start", "COLD_START_EMBED_COMPUTED", user_id=user_id)


def cold_start_narrative_templated(user_id: str, narrative: str):
    emit("cold_start", "COLD_START_NARRATIVE_TEMPLATED", user_id=user_id, narrative=narrative)


# ===== PLANNER / EMBED EVENTS =====
def plan_created(user_id: str, fact_block: list, summary: str):
    emit("plan", "PLAN_CREATED", user_id=user_id, fact_block=fact_block, summary=summary)


def query_embedded(user_id: str, embedding_dim: int, latency_ms: float):
    emit("embed", "QUERY_EMBEDDED", user_id=user_id, embedding_dim=embedding_dim, latency_ms=latency_ms)


# ===== ACT / RETRY EVENTS =====
def act_path_started(user_id: str):
    emit("act", "ACT_PATH_STARTED", user_id=user_id)


def act_path_result(user_id: str, fact_block: list, summary: str):
    emit("act", "ACT_PATH_RESULT", user_id=user_id, fact_block=fact_block, summary=summary)


def act_course_started(user_id: str):
    emit("act", "ACT_COURSE_STARTED", user_id=user_id)


def act_course_result(user_id: str, fact_block: list, summary: str):
    emit("act", "ACT_COURSE_RESULT", user_id=user_id, fact_block=fact_block, summary=summary)


def act_retry_triggered(user_id: str, retry_workers: list[str]):
    emit("retry", "ACT_RETRY_TRIGGERED", user_id=user_id, retry_workers=retry_workers)


# ===== VALIDATOR EVENTS =====
def validation_pass_1_passed(user_id: str, worker: str, fact_block: list, summary: str):
    emit(
        "validate",
        "VALIDATION_PASS_1_PASSED",
        user_id=user_id,
        worker=worker,
        fact_block=fact_block,
        summary=summary,
    )


def validation_pass_1_failed(user_id: str, worker: str, reason: str, fact_block: list):
    emit(
        "validate",
        "VALIDATION_PASS_1_FAILED",
        user_id=user_id,
        worker=worker,
        reason=reason,
        fact_block=fact_block,
    )


def validation_pass_2_passed(user_id: str, worker: str, fact_block: list, summary: str):
    emit(
        "validate",
        "VALIDATION_PASS_2_PASSED",
        user_id=user_id,
        worker=worker,
        fact_block=fact_block,
        summary=summary,
    )


def validation_pass_2_failed(user_id: str, worker: str, reason: str, fact_block: list):
    emit(
        "validate",
        "VALIDATION_PASS_2_FAILED",
        user_id=user_id,
        worker=worker,
        reason=reason,
        fact_block=fact_block,
    )


def retry_exhausted(user_id: str):
    emit("validate", "RETRY_EXHAUSTED", user_id=user_id)


# ===== SOLVER / WRITE / FALLBACK EVENTS =====
def solver_invoked(user_id: str, latency_ms: float):
    emit("solver", "SOLVER_INVOKED", user_id=user_id, latency_ms=latency_ms)


def solver_output_validated(user_id: str, fact_block: list, summary: str):
    emit("solver", "SOLVER_OUTPUT_VALIDATED", user_id=user_id, fact_block=fact_block, summary=summary)


def recommendations_written(user_id: str, recommendation_log_id: int, path_count: int, course_count: int):
    emit(
        "write",
        "RECOMMENDATIONS_WRITTEN",
        user_id=user_id,
        recommendation_log_id=recommendation_log_id,
        path_count=path_count,
        course_count=course_count,
    )


def fallback_served(user_id: str, fallback_type: str):
    emit("fallback", "FALLBACK_SERVED", user_id=user_id, fallback_type=fallback_type)


def proactive_refresh_triggered(user_id: str):
    emit("scheduler", "PROACTIVE_REFRESH_TRIGGERED", user_id=user_id)


def proactive_refresh_skipped(user_id: str, reason: str):
    emit("scheduler", "PROACTIVE_REFRESH_SKIPPED", user_id=user_id, reason=reason)


def digest_sent(user_id: str):
    emit("scheduler", "DIGEST_SENT", user_id=user_id)


def digest_stubbed(user_id: str):
    emit("scheduler", "DIGEST_STUBBED", user_id=user_id)


def digest_failed(user_id: str, reason: str):
    emit("scheduler", "DIGEST_FAILED", user_id=user_id, reason=reason)


# ===== CATALOG / DUAL-WRITE EVENTS =====
def product_created(product_id: str, title: str):
    emit("catalog", "PRODUCT_CREATED", product_id=product_id, title=title)


def product_updated(product_id: str, title: str, fields_changed: list[str]):
    emit("catalog", "PRODUCT_UPDATED", product_id=product_id, title=title, fields_changed=fields_changed)


def product_deleted(product_id: str, title: str):
    emit("catalog", "PRODUCT_DELETED", product_id=product_id, title=title)


def product_delete_blocked(product_id: str, blocked_by_path_id: str):
    emit("catalog", "PRODUCT_DELETE_BLOCKED", product_id=product_id, blocked_by_path_id=blocked_by_path_id)


def path_created(path_id: str, title: str, course_count: int):
    emit("catalog", "PATH_CREATED", path_id=path_id, title=title, course_count=course_count)


def path_deleted(path_id: str, title: str):
    emit("catalog", "PATH_DELETED", path_id=path_id, title=title)


def path_course_detached(path_id: str, course_id: str):
    emit("catalog", "PATH_COURSE_DETACHED", path_id=path_id, course_id=course_id)


def vector_upsert_succeeded(item_type: str, item_id: str, collection: str, latency_ms: float):
    emit(
        "dualwrite",
        "VECTOR_UPSERT_SUCCEEDED",
        item_type=item_type,
        item_id=item_id,
        collection=collection,
        latency_ms=latency_ms,
    )


def vector_upsert_failed(item_type: str, item_id: str, error: str):
    emit(
        "dualwrite",
        "VECTOR_UPSERT_FAILED",
        item_type=item_type,
        item_id=item_id,
        error=error,
    )


def vector_delete_succeeded(item_type: str, item_id: str):
    emit("dualwrite", "VECTOR_DELETE_SUCCEEDED", item_type=item_type, item_id=item_id)


def reconciliation_retry_attempted(vector_sync_log_id: int, item_type: str, item_id: str):
    emit(
        "scheduler",
        "RECONCILIATION_RETRY_ATTEMPTED",
        vector_sync_log_id=vector_sync_log_id,
        item_type=item_type,
        item_id=item_id,
    )


# ===== BEHAVIORAL EVENT EVENTS =====
def behavioral_event_ingested(user_id: str, event_type: str):
    emit("events", "BEHAVIORAL_EVENT_INGESTED", user_id=user_id, event_type=event_type)


def behavioral_event_rejected(user_id: str, reason: str):
    emit("events", "BEHAVIORAL_EVENT_REJECTED", user_id=user_id, reason=reason)
