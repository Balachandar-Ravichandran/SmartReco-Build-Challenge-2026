"""All Pydantic models — inter-agent contracts via AgentCertificate pattern.

Every arrow in Sections 4.2–4.3 of the PRD is a typed handoff wrapped in AgentCertificate.
No raw data crosses an agent boundary — only facts, summary, and decision signals.
"""
from typing import Literal, Any
from pydantic import BaseModel, Field


# ==================== UNIVERSAL ENVELOPE ====================
class Fact(BaseModel):
    """Atomic, distilled fact — never raw data."""

    key: str
    value: str | float | int | bool


class AgentCertificate(BaseModel):
    """Universal inter-agent communication envelope (Section 12.0).

    Every stage (Planner, Act, Validator, Solver, etc.) communicates through this.
    - fact_block: atomic, distilled facts only (no raw data)
    - summary: 1–3 sentence plain-language recap
    - reason: populated ONLY when success=False
    - retry: explicit signal for graph conditional routing
    """

    stage: Literal[
        "planner",
        "embed",
        "act_path",
        "act_course",
        "validate_1",
        "validate_2",
        "solver",
        "fallback",
        "write",
    ]
    success: bool
    fact_block: list[Fact]
    summary: str
    reason: str | None = None
    retry: bool = False
    retry_count: int = 0


# ==================== BEHAVIORAL EVENT ====================
class BehavioralEventInput(BaseModel):
    """Client→API input for behavioral event (Section 12.4)."""

    event_type: Literal[
        "view", "dwell", "search", "click", "add_to_cart", "purchase"
    ]
    product_id: str | None = None
    path_id: str | None = None
    query_text: str | None = None
    dwell_seconds: int | None = None
    target: str | None = None  # for click events
    client_ts: str  # ISO8601

    def validate_exclusive(self):
        """Ensure product_id and path_id are mutually exclusive."""
        if self.product_id and self.path_id:
            raise ValueError("product_id and path_id are mutually exclusive")
        return self


# ==================== PLANNER OUTPUT ====================
class PlannerOutput(BaseModel):
    """Planner node output (Section 12.1)."""

    scope: Literal["home", "course", "path", "browse"]
    tags: list[str]  # from TOPIC_VOCABULARY
    query_text: str  # human-readable query for embedding
    course_context_id: str | None = None  # set when scope="course"
    # Kept distinct (not just folded into `tags`) so the Solver can explicitly
    # name BOTH signals in its "why this recommendation" reasoning — a page-
    # context blend (e.g. historical Agentic AI interest + currently browsing
    # Cybersecurity) is only tellable apart from a single-signal home
    # recommendation if these two stay separate all the way to the LLM.
    historical_top_tag: str = ""  # "" if the user has no scoreable history yet
    current_context_tag: str = ""  # "" for scope="home" — purely signal-driven, no page context
    # Top-5 ranked historical tags (not just the #1) — used to BOOST retrieval
    # ranking toward items that also match the user's history, without being
    # required to. See tools/db_tool.py::tag_overlap_query's primary/boost
    # split: current_context_tag is the near-required match, this list is the
    # bonus, so an item doesn't need to share the single #1 tag exactly to
    # benefit from the user's broader interest profile.
    historical_tags: list[str] = Field(default_factory=list)


# ==================== SEARCH CANDIDATE ====================
class SearchCandidate(BaseModel):
    """Intermediate: used only inside Act workers, never crosses boundary."""

    item_type: Literal["path", "course"]
    item_id: str
    title: str
    keyword_overlap_ratio: float
    semantic_similarity: float
    combined_score: float
    is_match: bool
    discount_amount: float | None = None  # path only
    has_capstone: bool | None = None  # path only
    rating: float | None = None  # course only — re-rank popularity signal
    learners_count: int | None = None  # course only — re-rank popularity signal
    rerank_score: float = 0.0  # combined_score blended with popularity; final sort key


# ==================== RECOMMENDATION STATE ====================
class RecommendationState(BaseModel):
    """LangGraph state — holds certificates, never raw objects (Section 12.2)."""

    user_id: str
    scope: Literal["home", "course"]
    course_id: str | None = None
    trigger_reason: Literal["page_change", "significant_shift"]
    # cold_start never enters this graph
    planner_cert: AgentCertificate | None = None
    query_vector: list[float] | None = None  # the ONE exception (computed artifact)
    act_path_cert: AgentCertificate | None = None
    act_course_cert: AgentCertificate | None = None
    validate_1_path_cert: AgentCertificate | None = None
    validate_1_course_cert: AgentCertificate | None = None
    validate_2_path_cert: AgentCertificate | None = None
    validate_2_course_cert: AgentCertificate | None = None
    solver_cert: AgentCertificate | None = None
    retry_count: int = 0
    status: Literal[
        "planning",
        "embedding",
        "acting",
        "validating",
        "solving",
        "writing",
        "done",
        "escalated_fallback",
        "failed",
    ] = "planning"


# ==================== SOLVER OUTPUT ====================
class IncludesRow(BaseModel):
    """Course/module included in a path."""

    name: str
    level: str
    dur: str


class OptionTile(BaseModel):
    """UI tile for a recommendation option."""

    kicker: str  # e.g. "Path 1 · Top match"
    title: str
    rating: str | None = None  # e.g. "4.9 · 1.1k learners"
    timeline: str
    level: str
    youget: str
    price: float
    strike: float | None = None  # pre-discount price
    cta: str  # e.g. "Start the path"
    note: str | None = None
    savingsNote: str | None = None
    includes: list[IncludesRow] = Field(default_factory=list)
    # Catalog id + kind for linking the tile to its product page. Always set
    # server-side after construction (tiles.py, solver.py) from the real
    # catalog row — never trust an LLM-emitted value for this.
    id: str | None = None
    kind: Literal["path", "course"] | None = None


class Badge(BaseModel):
    """Was `badge: dict` per the PRD's literal Section 12.6 typing — a bare
    dict compiles to an unconstrained `{"type": "object"}` JSON Schema, so a
    model asked for structured output correctly (if unhelpfully) satisfies it
    with `{}`. A proper nested model gives the schema real required keys."""

    text: str
    cls: Literal["cached", "fresh"]


class SolverOutput(BaseModel):
    """Solver node structured output — the FE contract (Section 12.6)."""

    badge: Badge
    headline: str
    # Short, explicit "why this recommendation" statement — names the actual
    # signal(s) behind the pick (historical interest, and the current page's
    # category when there is one). Distinct from `narrative`: this is the
    # transparent, factual reasoning; narrative is the persuasive story built
    # on top of it. Every producer (solver.py, coldstart.py, tiles.py) sets
    # this explicitly — the "" default only guards against a producer
    # forgetting to, not a valid empty state to design around.
    reasoning: str = ""
    narrative: str
    # Short, scannable pointers (3-5, each a standalone fact/benefit) — the
    # persuasive substance now lives HERE, not in `narrative`. `narrative`
    # became a single dense paragraph in practice, which read poorly in the
    # panel; splitting it into pointers keeps the sell but makes it skimmable.
    highlights: list[str] = Field(default_factory=list)
    pathTiles: list[OptionTile] = Field(default_factory=list)
    courseTiles: list[OptionTile] = Field(default_factory=list)


# ==================== WRITE ROW ====================
class RecommendationRow(BaseModel):
    """Final write to current_recommendations table (Section 12.7)."""

    user_id: str
    recommendation_log_id: int
    item_type: Literal["path", "course"]
    product_id: str | None = None
    path_id: str | None = None
    rank: int
    is_hero: bool


# ==================== ADMIN REQUESTS ====================
class CreateCourseRequest(BaseModel):
    """Admin: create a course."""

    id: str
    title: str
    instructor: str
    description: str
    tags: list[str]  # validated against TOPIC_VOCABULARY
    level: str
    duration_weeks: int
    price: float
    rating: float | None = None


class CreatePathRequest(BaseModel):
    """Admin: create a learning path."""

    id: str
    title: str
    description: str
    tags: list[str]  # validated against TOPIC_VOCABULARY
    level_range: str
    duration_months: int
    price: float
    discount_amount: float = 0
    has_capstone: bool = False
    course_ids: list[str] = Field(default_factory=list)


# ==================== API RESPONSES ====================
class RecommendationResponse(BaseModel):
    """GET /api/v1/recommendations response."""

    trigger_reason: str
    badge: dict
    headline: str
    reasoning: str
    narrative: str
    highlights: list[str] = Field(default_factory=list)
    pathTiles: list[OptionTile]
    courseTiles: list[OptionTile]


class OnboardingRequest(BaseModel):
    """POST /api/v1/onboarding request."""

    user_id: str
    selected_topics: list[str]
    goal: str


class OnboardingResponse(BaseModel):
    """POST /api/v1/onboarding response."""

    onboarding_id: int
