"""Configuration management — all settings from environment variables."""
import os
from pathlib import Path
from functools import lru_cache

from dotenv import load_dotenv

# Load .env from the repo root (three levels up from this file:
# code/backend/core/config.py -> code/backend/core -> code/backend -> code -> repo root)
_REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_REPO_ROOT / ".env")


def _anchor_path(path: str) -> str:
    """Resolve a relative path against the repo root, not the process cwd."""
    p = Path(path)
    return str(p if p.is_absolute() else _REPO_ROOT / p)


def _anchor_sqlite_url(url: str) -> str:
    """Same anchoring for a `sqlite:///relative/path` URL — leaves absolute
    paths (sqlite:////abs/path, or non-sqlite URLs) untouched."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    raw_path = url[len(prefix):]
    if raw_path.startswith("/") or (len(raw_path) > 1 and raw_path[1] == ":"):
        return url  # already absolute (posix or Windows drive letter)
    return prefix + str(_REPO_ROOT / raw_path).replace("\\", "/")


class Settings:
    """Load all config from env vars. Defaults provided where appropriate."""

    # ===== LLM PROVIDER (Mesh API only) =====
    MESH_API_KEY: str = os.getenv("MESH_API_KEY", "")
    MESH_API_BASE: str = os.getenv("MESH_API_BASE", "")
    MESH_EMBED_TIMEOUT_SEC: float = float(os.getenv("MESH_EMBED_TIMEOUT_SEC", "3"))
    MESH_COMPLETE_TIMEOUT_SEC: float = float(os.getenv("MESH_COMPLETE_TIMEOUT_SEC", "6"))
    # Mesh is a multi-provider aggregator — model ids need a provider prefix
    # (e.g. "vertex/gemini-embedding-001"), confirmed against its /v1/models
    # catalog rather than assumed.
    MESH_EMBED_MODEL: str = os.getenv("MESH_EMBED_MODEL", "vertex/gemini-embedding-001")
    MESH_COMPLETION_MODEL: str = os.getenv("MESH_COMPLETION_MODEL", "google/gemini-3.5-flash-lite")

    # ===== DATABASE =====
    # Relative paths are anchored to the repo root, not the process's cwd —
    # otherwise `uvicorn` launched from a different working directory silently
    # creates a fresh, empty sqlite file instead of finding the seeded one.
    DATABASE_URL: str = _anchor_sqlite_url(os.getenv("DATABASE_URL", "sqlite:///pathwise.db"))

    # ===== VECTOR STORE =====
    CHROMA_PATH: str = _anchor_path(os.getenv("CHROMA_PATH", "./chroma_data"))

    # ===== RECOMMENDATION THRESHOLDS =====
    # Tuned against synthetic dataset (Section 18-19)
    SEMANTIC_THRESHOLD: float = float(os.getenv("SEMANTIC_THRESHOLD", "0.65"))
    KEYWORD_THRESHOLD: float = float(os.getenv("KEYWORD_THRESHOLD", "0.3"))
    MIN_ACCEPT_SCORE: float = float(os.getenv("MIN_ACCEPT_SCORE", "0.5"))
    # Re-rank pass (Section 7.1 polish): final ordering blends relevance
    # (combined_score) with a popularity/quality signal (rating + learners_count).
    # Kept low so it only tie-breaks among already-relevant matches — is_match
    # gating always stays on combined_score alone, never on popularity.
    RERANK_POPULARITY_WEIGHT: float = float(os.getenv("RERANK_POPULARITY_WEIGHT", "0.15"))

    # ===== SCHEDULER =====
    # Detached for the competition submission — proactive-refresh is a bonus
    # feature (Section 6.5), not required for the core recommendation flow,
    # and an always-on 6h background job adds nothing to grading while adding
    # a moving part. The job code is untouched; flip this on to re-enable it.
    ENABLE_PROACTIVE_REFRESH: bool = os.getenv("ENABLE_PROACTIVE_REFRESH", "false").lower() == "true"
    PROACTIVE_REFRESH_INTERVAL_MINUTES: int = int(
        os.getenv("PROACTIVE_REFRESH_INTERVAL_MINUTES", "360")
    )
    RECONCILIATION_INTERVAL_MINUTES: int = int(
        os.getenv("RECONCILIATION_INTERVAL_MINUTES", "5")
    )

    # ===== DAILY DIGEST (bonus) =====
    # Cron-style daily send (not an interval) — same "detached for competition
    # by default" reasoning as proactive-refresh above. Flip on once SMTP
    # creds are set; the job itself works today via email_client's log-only
    # stub when SMTP_HOST is unset.
    ENABLE_DAILY_DIGEST: bool = os.getenv("ENABLE_DAILY_DIGEST", "false").lower() == "true"
    DIGEST_HOUR: int = int(os.getenv("DIGEST_HOUR", "8"))
    DIGEST_MINUTE: int = int(os.getenv("DIGEST_MINUTE", "0"))

    # ===== SMTP (daily digest delivery) =====
    # Left unset by default — email_client.send() logs the rendered digest
    # instead of sending when SMTP_HOST is empty, so the pipeline is testable
    # before real credentials exist.
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "")
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

    # ===== EVENT LOGGING =====
    EVENTS_LOG_DIR: str = _anchor_path(os.getenv("EVENTS_LOG_DIR", "./logs"))
    EVENTS_MAX_PER_MINUTE: int = int(os.getenv("EVENTS_MAX_PER_MINUTE", "120"))

    # ===== LANGSMITH (Optional observability bonus) =====
    LANGCHAIN_TRACING_V2: bool = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
    LANGCHAIN_API_KEY: str = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT: str = os.getenv("LANGCHAIN_PROJECT", "pathwise-hackathon")

    # ===== FASTAPI =====
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))
    WORKERS: int = int(os.getenv("WORKERS", "1"))

    # ===== TOPIC VOCABULARY =====
    # Controlled enum: topic tags must be from this list (Section 5.3)
    _DEFAULT_TOPICS = (
        "Agentic AI,Machine Learning,Data Engineering,Generative AI,"
        "Cloud & DevOps,Cybersecurity,Product & Design,Business & Finance,"
        "Mobile Development,Career Skills,MLOps,Python for AI"
    )
    TOPIC_VOCABULARY: list[str] = [
        t.strip() for t in os.getenv("TOPIC_VOCABULARY", _DEFAULT_TOPICS).split(",")
    ]

    # ===== GOAL VOCABULARY =====
    # Closed enum matching the wireframe's 4 onboarding goal cards exactly —
    # same "controlled vocabulary" reasoning as TOPIC_VOCABULARY (Section 5.3):
    # a free-text goal would fragment narrative templating with no way to
    # normalize wording back to one of the wireframe's 4 supported outcomes.
    _DEFAULT_GOALS = (
        "Get hired / switch careers,Upskill in my current role,"
        "Build a project,Get certified"
    )
    GOAL_VOCABULARY: list[str] = [
        g.strip() for g in os.getenv("GOAL_VOCABULARY", _DEFAULT_GOALS).split(",")
    ]

    def validate(self):
        """Validate required config is present."""
        if not self.MESH_API_KEY or not self.MESH_API_BASE:
            raise ValueError(
                "MESH_API_KEY and MESH_API_BASE are required. Set them in .env."
            )

        # Ensure log directory exists
        os.makedirs(self.EVENTS_LOG_DIR, exist_ok=True)
        os.makedirs(self.CHROMA_PATH, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()
    settings.validate()
    return settings
