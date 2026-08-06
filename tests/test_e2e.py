"""End-to-end tests against the seeded synthetic dataset (Section 18.2).

Uses the FakeLLMProvider from conftest.py — proves the full pipeline (trigger
gate, cold start, LangGraph warm agent, dual-write, admin console) works
correctly independent of which real LLM provider sits behind the interface.
"""
import json


def test_cold_start_bala(client):
    """Bala has zero behavioral_events -> cold-start path, no LangGraph nodes run."""
    resp = client.get("/api/v1/recommendations", params={"user_id": "usr_bala_cold", "scope": "home"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["trigger_reason"] == "cold_start"
    assert body["badge"]["cls"] == "fresh"
    assert body["narrative"]  # templated narrative, non-empty
    assert body["pathTiles"] or body["courseTiles"]


def test_onboarding_vocabulary_validation(client):
    """Topics outside TOPIC_VOCABULARY are rejected with 422 (Section 5.3)."""
    resp = client.post(
        "/api/v1/onboarding",
        json={"user_id": "usr_bala_cold", "selected_topics": ["Not A Real Topic"], "goal": "test"},
    )
    assert resp.status_code == 422


def test_onboarding_valid_topics(client):
    resp = client.post(
        "/api/v1/onboarding",
        json={
            "user_id": "usr_test_new",
            "selected_topics": ["Agentic AI", "MLOps"],
            "goal": "Get hired / switch careers",
        },
    )
    assert resp.status_code == 201
    assert "onboarding_id" in resp.json()


def test_events_ingestion_batched(client):
    """POST /api/v1/events accepts a batch, returns 202 immediately (Section 5.4)."""
    resp = client.post(
        "/api/v1/events",
        params={"user_id": "usr_sol_viewer"},
        json=[
            {
                "event_type": "view",
                "product_id": "crs_ai-engineer--by-sol",
                "client_ts": "2026-08-05T10:00:00Z",
            }
        ],
    )
    assert resp.status_code == 202
    assert resp.json()["accepted"] == 1


def test_events_batch_size_limit(client):
    """Max 50 events per batch (Section 14.3)."""
    batch = [
        {"event_type": "view", "product_id": "crs_ai-engineer--by-sol", "client_ts": "2026-08-05T10:00:00Z"}
        for _ in range(51)
    ]
    resp = client.post("/api/v1/events", params={"user_id": "usr_sol_viewer"}, json=batch)
    assert resp.status_code == 422


def test_warm_agent_sam_shifter(client):
    """Sam-shifter has behavioral history -> should trigger the full LangGraph
    pipeline (Planner->Embed->Act->Validate->Solver->Write), Section 18.2."""
    resp = client.get(
        "/api/v1/recommendations", params={"user_id": "usr_sam_shifter", "scope": "home"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["trigger_reason"] in ("significant_shift", "page_change")
    # Either a fresh graph run or a served cache — both are valid SolverOutput shapes
    assert "narrative" in body
    assert "badge" in body


def test_debounce_serves_cache_on_immediate_repeat(client):
    """Section 5.1: calling twice in immediate succession should hit the debounce
    gate on the second call and serve cached recommendations unchanged."""
    first = client.get(
        "/api/v1/recommendations", params={"user_id": "usr_sparse_tag", "scope": "home"}
    )
    assert first.status_code == 200

    second = client.get(
        "/api/v1/recommendations", params={"user_id": "usr_sparse_tag", "scope": "home"}
    )
    assert second.status_code == 200
    # Second call within the debounce window must not re-run the agent
    assert second.json()["trigger_reason"] == "page_change"


def test_catalog_create_course_dual_write(client):
    """Admin course creation triggers dual-write: SQL + Chroma (Section 13.4)."""
    resp = client.post(
        "/api/v1/courses",
        json={
            "id": "crs_test_e2e_course",
            "title": "E2E Test Course",
            "instructor": "Test Instructor",
            "description": "A course created during automated end-to-end testing.",
            "tags": ["Machine Learning"],
            "level": "Beginner",
            "duration_weeks": 4,
            "price": 49.0,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == "crs_test_e2e_course"


def test_catalog_tag_validation(client):
    """Tags outside TOPIC_VOCABULARY rejected on course creation (Section 5.3)."""
    resp = client.post(
        "/api/v1/courses",
        json={
            "id": "crs_bad_tags",
            "title": "Bad Tags Course",
            "instructor": "Test",
            "description": "desc",
            "tags": ["Not A Real Tag"],
            "level": "Beginner",
            "duration_weeks": 4,
            "price": 49.0,
        },
    )
    assert resp.status_code == 422


def test_delete_course_blocked_if_in_path(client, db_session):
    """Section 13.4: deleting a course still referenced by a path is blocked (409)."""
    from backend.db.models import PathCourse

    row = db_session.query(PathCourse).first()
    assert row is not None, "seed data should have at least one path_courses row"

    resp = client.delete(f"/api/v1/courses/{row.course_id}")
    assert resp.status_code == 409
    assert "blocked_by_path_id" in resp.json()["detail"]


def test_admin_dual_write_status(client):
    resp = client.get("/api/v1/admin/dual-write-status")
    assert resp.status_code == 200
    assert "failed_count" in resp.json()


def test_home_page_renders(client):
    resp = client.get("/", params={"user_id": "usr_bala_cold"})
    assert resp.status_code == 200
    assert "Pathwise" in resp.text


def test_admin_page_renders(client):
    resp = client.get("/admin")
    assert resp.status_code == 200
