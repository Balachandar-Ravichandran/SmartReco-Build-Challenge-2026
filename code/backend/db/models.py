"""SQLAlchemy ORM models — 11 tables from Section 13.2 of PRD."""
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, ForeignKey, Text, Boolean,
    Index, CheckConstraint, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class User(Base):
    """Users table — learners and admins."""

    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)  # 'learner' or 'admin'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Opt-in for the scheduled daily digest email (Section 6.5 bonus) — off by
    # default, flipped from /profile.
    digest_enabled = Column(Boolean, default=False, nullable=False)

    # Relationships
    onboardings = relationship("UserOnboarding", back_populates="user")
    behavioral_events = relationship("BehavioralEvent", back_populates="user")
    sessions = relationship("Session", back_populates="user")
    purchases = relationship("Purchase", back_populates="user")
    recommendations_log = relationship("RecommendationLog", back_populates="user")
    current_recommendations = relationship(
        "CurrentRecommendation", back_populates="user"
    )

    __table_args__ = (
        CheckConstraint("role IN ('learner','admin')"),
    )


class UserOnboarding(Base):
    """User interests & goals at signup."""

    __tablename__ = "user_onboarding"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    selected_topics = Column(String, nullable=False)  # JSON array
    goal = Column(String, nullable=False)
    query_embedding_cache = Column(String, nullable=True)  # JSON array (floats)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="onboardings")

    __table_args__ = (
        Index("idx_onboarding_user_latest", "user_id", "created_at"),
    )


class Product(Base):
    """Courses catalog."""

    __tablename__ = "products"

    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    instructor = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    tags = Column(String, nullable=False)  # JSON array
    level = Column(String, nullable=False)
    duration_weeks = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    rating = Column(Float, nullable=True)
    learners_count = Column(Integer, default=0)
    is_active = Column(Integer, default=1, nullable=False)  # SQLite lacks BOOLEAN
    deleted_at = Column(DateTime, nullable=True)
    embedding_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    path_courses_as_course = relationship("PathCourse", back_populates="course")
    purchases = relationship("Purchase", back_populates="product")
    behavioral_events = relationship("BehavioralEvent", back_populates="product")


class Path(Base):
    """Learning paths — curated course bundles."""

    __tablename__ = "paths"

    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    tags = Column(String, nullable=False)  # JSON array
    level_range = Column(String, nullable=False)
    duration_months = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    discount_amount = Column(Float, default=0, nullable=False)
    has_capstone = Column(Integer, default=0, nullable=False)
    is_active = Column(Integer, default=1, nullable=False)
    embedding_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    courses = relationship("PathCourse", back_populates="path")
    purchases = relationship("Purchase", back_populates="path")
    behavioral_events = relationship("BehavioralEvent", back_populates="path")
    current_recommendations = relationship(
        "CurrentRecommendation", back_populates="path"
    )


class PathCourse(Base):
    """Join table: paths → courses."""

    __tablename__ = "path_courses"

    path_id = Column(String, ForeignKey("paths.id"), primary_key=True)
    course_id = Column(String, ForeignKey("products.id"), primary_key=True)
    sequence_order = Column(Integer, nullable=False)

    # Relationships
    path = relationship("Path", back_populates="courses")
    course = relationship("Product", back_populates="path_courses_as_course")


class Session(Base):
    """User sessions."""

    __tablename__ = "sessions"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    device = Column(String, nullable=True)
    referrer = Column(String, nullable=True)

    # Relationships
    user = relationship("User", back_populates="sessions")
    behavioral_events = relationship("BehavioralEvent", back_populates="session")


class Purchase(Base):
    """Purchase history."""

    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    product_id = Column(String, ForeignKey("products.id"), nullable=True)
    path_id = Column(String, ForeignKey("paths.id"), nullable=True)
    price_paid = Column(Float, nullable=False)
    purchased_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="purchases")
    product = relationship("Product", back_populates="purchases")
    path = relationship("Path", back_populates="purchases")

    __table_args__ = (
        CheckConstraint("NOT (product_id IS NOT NULL AND path_id IS NOT NULL)"),
    )


class BehavioralEvent(Base):
    """User browsing behavior — feeds interest scoring."""

    __tablename__ = "behavioral_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    event_type = Column(String, nullable=False)  # view, dwell, search, click, etc.
    target = Column(String, nullable=True)
    product_id = Column(String, ForeignKey("products.id"), nullable=True)
    path_id = Column(String, ForeignKey("paths.id"), nullable=True)
    query_text = Column(String, nullable=True)
    dwell_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="behavioral_events")
    session = relationship("Session", back_populates="behavioral_events")
    product = relationship("Product", back_populates="behavioral_events")
    path = relationship("Path", back_populates="behavioral_events")

    __table_args__ = (
        CheckConstraint("event_type IN ('view','dwell','search','click','add_to_cart','purchase')"),
        CheckConstraint("NOT (product_id IS NOT NULL AND path_id IS NOT NULL)"),
        Index("idx_events_user_time", "user_id", "created_at"),
        Index("idx_events_user_type_time", "user_id", "event_type", "created_at"),
    )


class RecommendationLog(Base):
    """Audit trail: every recommendation run."""

    __tablename__ = "recommendation_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    trigger_reason = Column(String, nullable=False)  # cold_start, page_change, significant_shift
    # Page context this run was computed for (Section 5.1's context-aware
    # trigger gate) — lets should_rerun() tell "same page, ask again" apart
    # from "different page, needs its own blended recommendation."
    scope = Column(String, nullable=True)  # home, course, path, browse
    context_id = Column(String, nullable=True)  # course_id / path_id / browse topic; null for home
    act_path_candidates = Column(String, nullable=True)  # JSON
    act_course_candidates = Column(String, nullable=True)  # JSON
    validator_status = Column(String, nullable=False)  # pass, retried, failed
    retry_count = Column(Integer, default=0, nullable=False)
    solver_narrative = Column(String, nullable=True)
    # Full SolverOutput (headline/reasoning/narrative/highlights/tiles) as JSON —
    # lets a cache-serve replay the exact last-generated content instead of
    # reconstructing a generic fallback shape from tile references alone.
    solver_output_json = Column(String, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="recommendations_log")
    current_recommendations = relationship(
        "CurrentRecommendation", back_populates="recommendation_log"
    )

    __table_args__ = (
        CheckConstraint("trigger_reason IN ('cold_start','page_change','significant_shift')"),
        CheckConstraint("validator_status IN ('pass','retried','failed')"),
    )


class CurrentRecommendation(Base):
    """Read-shaped: current recommendations for a user (delete-then-insert)."""

    __tablename__ = "current_recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    recommendation_log_id = Column(Integer, ForeignKey("recommendation_log.id"), nullable=False)
    item_type = Column(String, nullable=False)  # path or course
    product_id = Column(String, ForeignKey("products.id"), nullable=True)
    path_id = Column(String, ForeignKey("paths.id"), nullable=True)
    rank = Column(Integer, nullable=False)
    is_hero = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="current_recommendations")
    recommendation_log = relationship("RecommendationLog", back_populates="current_recommendations")
    product = relationship("Product")
    path = relationship("Path", back_populates="current_recommendations")

    __table_args__ = (
        CheckConstraint("item_type IN ('path','course')"),
        CheckConstraint("NOT (product_id IS NOT NULL AND path_id IS NOT NULL)"),
        UniqueConstraint("user_id", "item_type", "rank"),
    )


class VectorSyncLog(Base):
    """Dual-write audit trail."""

    __tablename__ = "vector_sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String, ForeignKey("products.id"), nullable=True)
    path_id = Column(String, ForeignKey("paths.id"), nullable=True)
    operation = Column(String, nullable=False)  # insert, update, delete
    sql_status = Column(String, nullable=False)
    vector_status = Column(String, nullable=False)  # ok, failed
    synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    error_message = Column(String, nullable=True)

    __table_args__ = (
        CheckConstraint("operation IN ('insert','update','delete')"),
        CheckConstraint("NOT (product_id IS NOT NULL AND path_id IS NOT NULL)"),
    )
