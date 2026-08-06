"""Shared pytest fixtures — a fake LLM provider so tests never make real network
calls, and don't need real Mesh credentials to exercise the full pipeline."""
import atexit
import sys
import os
import shutil
import sqlite3
import tempfile
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "code"))

# Tests must never mutate the real seeded pathwise.db (repeated runs would hit
# stale state like duplicate ids, and it'd corrupt the demo dataset). Copy it
# into an isolated temp dir and point the app at that copy for this session.
_TEST_DIR = Path(tempfile.mkdtemp(prefix="pathwise_test_"))
shutil.copy(REPO_ROOT / "pathwise.db", _TEST_DIR / "pathwise.db")

# The real DB may carry a real (e.g. 3072-dim) cached query embedding from
# live testing against real Mesh — mixed with this session's 1536-dim
# FakeLLMProvider vectors, that's a Chroma dimension collision waiting to
# happen. Tests must never depend on whatever a prior live-testing session
# happened to leave cached, so clear it in the copy (never in the real DB).
with sqlite3.connect(_TEST_DIR / "pathwise.db") as _conn:
    _conn.execute("UPDATE user_onboarding SET query_embedding_cache = NULL")
    _conn.commit()

os.chdir(_TEST_DIR)
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DIR / 'pathwise.db').as_posix()}"
os.environ["CHROMA_PATH"] = str(_TEST_DIR / "chroma_data")
os.environ["EVENTS_LOG_DIR"] = str(_TEST_DIR / "logs")
atexit.register(shutil.rmtree, _TEST_DIR, ignore_errors=True)

import pytest


class FakeLLMProvider:
    """Deterministic stand-in for MeshClient. Never touches the network."""

    async def embed(self, text: str) -> list[float]:
        # Deterministic pseudo-embedding: hash text into a fixed-length vector so
        # identical inputs get identical vectors (useful for cache-hit assertions).
        seed = sum(ord(c) for c in text)
        return [((seed + i) % 100) / 100.0 for i in range(1536)]

    async def complete(self, system: str, user: str, response_schema=None) -> dict:
        # Extract the resolved facts the Solver embedded in its prompt and echo
        # them back in SolverOutput shape — good enough to prove the pipeline
        # plumbing works without a real model in the loop.
        import re

        shape_match = re.search(r"Decision shape: (\w+)", user)
        shape = shape_match.group(1) if shape_match else "single_course"

        path_tiles = []
        course_tiles = []

        path_match = re.search(r"'title': '([^']+)'.*?'price': ([\d.]+)", user)
        if "'path':" in user:
            title_match = re.search(r"'path': \{'title': '([^']+)', 'price': ([\d.]+)", user)
            if title_match:
                path_tiles.append(
                    {
                        "kicker": "Path · Top match",
                        "title": title_match.group(1),
                        "rating": None,
                        "timeline": "6 months",
                        "level": "Mixed",
                        "youget": "Bundled courses",
                        "price": float(title_match.group(2)),
                        "strike": None,
                        "cta": "Start the path",
                        "note": None,
                        "savingsNote": None,
                        "includes": [],
                    }
                )

        if "'course':" in user:
            title_match = re.search(r"'course': \{'title': '([^']+)', 'price': ([\d.]+)", user)
            if title_match:
                course_tiles.append(
                    {
                        "kicker": "Course · Top match",
                        "title": title_match.group(1),
                        "rating": None,
                        "timeline": "8 weeks",
                        "level": "Intermediate",
                        "youget": "Taught by an instructor",
                        "price": float(title_match.group(2)),
                        "strike": None,
                        "cta": "Start the course",
                        "note": None,
                        "savingsNote": None,
                        "includes": [],
                    }
                )

        return {
            "badge": {"text": "Fresh", "cls": "fresh"},
            "headline": f"Because of your recent activity ({shape})",
            "narrative": "This is a stubbed narrative for testing — no real LLM call was made.",
            "pathTiles": path_tiles,
            "courseTiles": course_tiles,
        }


@pytest.fixture(autouse=True)
def fake_llm_provider(monkeypatch):
    """Autouse: every test gets a stubbed LLM provider unless it opts out."""
    from backend.tools import llm_provider

    monkeypatch.setattr(llm_provider, "get_provider", lambda: FakeLLMProvider())
    monkeypatch.setattr(llm_provider, "get_embedding_provider", lambda: FakeLLMProvider())
    # Also patch the reference already imported into modules that did
    # `from backend.tools.llm_provider import get_provider/get_embedding_provider`
    # at import time.
    import backend.agent.embedder as embedder_mod
    import backend.agent.coldstart as coldstart_mod
    import backend.agent.solver as solver_mod
    import backend.api.catalog as catalog_mod
    import backend.scheduler.reconciliation as reconciliation_mod

    monkeypatch.setattr(embedder_mod, "get_embedding_provider", lambda: FakeLLMProvider())
    monkeypatch.setattr(coldstart_mod, "get_embedding_provider", lambda: FakeLLMProvider())
    monkeypatch.setattr(solver_mod, "get_provider", lambda: FakeLLMProvider())
    monkeypatch.setattr(catalog_mod, "get_embedding_provider", lambda: FakeLLMProvider())
    monkeypatch.setattr(reconciliation_mod, "get_embedding_provider", lambda: FakeLLMProvider())
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_session():
    from backend.db.session import get_db, init_db

    init_db()
    with get_db() as db:
        yield db
