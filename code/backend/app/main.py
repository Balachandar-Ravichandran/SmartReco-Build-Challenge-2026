"""FastAPI app factory (Section 4.1, 4.4) — mounts all routers, runs startup sequence."""
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.core.config import get_settings
from backend.db.session import init_db, get_db
from backend.tools import vector_tool
from backend.api import auth, onboarding, catalog, events as events_api, recommendations, admin_console
from backend.scheduler import reconciliation, proactive_refresh, daily_digest

# NOTE: every route below uses `backend.db.session.get_db` (a real
# @contextmanager — `with get_db() as db: ...` commits on normal exit) rather
# than `backend.api.deps.get_db` (a plain FastAPI-dependency generator meant
# to be driven by FastAPI's own `Depends()` machinery). Manually driving that
# generator with `next()` + `.close()` raises GeneratorExit at the `yield`
# point instead of resuming past it — `db.commit()` is never reached, so
# writes silently vanish. This bit real user signups: onboarding rows were
# inserted, flushed (visible within that one request), then rolled back on
# close, so the very next request's read found nothing.

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = REPO_ROOT / "code" / "frontend" / "templates"
STATIC_DIR = REPO_ROOT / "code" / "frontend" / "static"

COOKIE_NAME = "pathwise_user_id"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days — convenience for a hackathon demo,
# not a security-hardened session (Section 15 defers real auth/session validation).

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_scheduler = AsyncIOScheduler()


def _resolve_user_id(request: Request, user_id: str | None) -> str | None:
    """Query param wins (keeps ?user_id=... demo/testing links working),
    falling back to the login-issued cookie for a real signed-in session."""
    return user_id or request.cookies.get(COOKIE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Step 1: apply schema if tables don't exist
    init_db()

    # Step 2: seed loader is a separate manual step (data/seed.py) — not run
    # automatically here, since it makes real network calls for embeddings.

    # Step 3: warm Chroma collections
    try:
        vector_tool.warm_collections()
    except Exception as e:
        print(f"Warning: Chroma warm-up failed (will retry on first real query): {e}")

    # Step 4: tracing initializes lazily via core.tracing.get_tracer_config()

    # Step 5: start APScheduler jobs
    _scheduler.add_job(
        reconciliation.run,
        "interval",
        minutes=settings.RECONCILIATION_INTERVAL_MINUTES,
        id="reconciliation",
    )
    if settings.ENABLE_PROACTIVE_REFRESH:
        _scheduler.add_job(
            proactive_refresh.run,
            "interval",
            minutes=settings.PROACTIVE_REFRESH_INTERVAL_MINUTES,
            id="proactive_refresh",
        )
    if settings.ENABLE_DAILY_DIGEST:
        _scheduler.add_job(
            daily_digest.run,
            CronTrigger(hour=settings.DIGEST_HOUR, minute=settings.DIGEST_MINUTE),
            id="daily_digest",
        )
    _scheduler.start()

    yield

    _scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="Pathwise", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(auth.router)
    app.include_router(onboarding.router)
    app.include_router(catalog.router)
    app.include_router(events_api.router)
    app.include_router(recommendations.router)
    app.include_router(admin_console.router)

    # ==================== AUTH PAGES ====================
    @app.get("/login")
    async def login_page(request: Request, error: str | None = None):
        return templates.TemplateResponse(request, "login.html", {"error": error})

    @app.post("/web/login")
    async def web_login(email: str = Form(...), password: str = Form(...)):
        from backend.api.auth import authenticate
        from backend.tools import db_tool

        with get_db() as db:
            user = authenticate(db, email, password)
            if not user:
                return RedirectResponse("/login?error=Invalid+credentials", status_code=303)

            if user.role == "admin":
                dest = "/admin"
            else:
                onboarding_row = db_tool.get_latest_onboarding(db, user.id)
                dest = "/" if onboarding_row else "/onboarding"
            user_id = user.id  # read before the `with` block closes the session

        response = RedirectResponse(dest, status_code=303)
        response.set_cookie(COOKIE_NAME, user_id, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
        return response

    @app.get("/signup")
    async def signup_page(request: Request, error: str | None = None):
        return templates.TemplateResponse(request, "signup.html", {"error": error})

    @app.post("/web/signup")
    async def web_signup(
        email: str = Form(...), password: str = Form(...), role: str = Form("learner")
    ):
        from backend.api.auth import create_user_row

        with get_db() as db:
            try:
                user = create_user_row(db, email, password, role)
            except ValueError as e:
                from urllib.parse import quote
                return RedirectResponse(f"/signup?error={quote(str(e))}", status_code=303)
            user_id, role = user.id, user.role

        dest = "/admin" if role == "admin" else "/onboarding"
        response = RedirectResponse(dest, status_code=303)
        response.set_cookie(COOKIE_NAME, user_id, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
        return response

    @app.get("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME)
        return response

    @app.get("/cart")
    async def cart_page(request: Request, user_id: str | None = None):
        resolved_user_id = _resolve_user_id(request, user_id)
        if not resolved_user_id:
            return RedirectResponse("/login", status_code=303)

        # Cart contents live only in the browser's localStorage (Section 2:
        # checkout/payment out of scope, so there's no server-side cart to
        # read) — this route just needs enough context to render the shared
        # nav and the Signal sidebar; app.js does the actual rendering of
        # #cart-items on load.
        with get_db() as db:
            ctx = _build_signal_context(db, resolved_user_id, "page_change")

        return templates.TemplateResponse(
            request, "cart.html",
            {"user_id": resolved_user_id, **ctx},
        )

    # ==================== ONBOARDING PAGE ====================
    GOAL_CARDS = [
        {"value": "Get hired / switch careers", "icon": "&#127919;",
         "desc": "Job-ready outcomes, path-first recommendations."},
        {"value": "Upskill in my current role", "icon": "&#128200;",
         "desc": "Targeted, single-course picks that fill gaps fast."},
        {"value": "Build a project", "icon": "&#128296;",
         "desc": "Hands-on, applied courses over theory."},
        {"value": "Get certified", "icon": "&#127891;",
         "desc": "Structured, certificate-bearing paths."},
    ]

    @app.get("/onboarding")
    async def onboarding_page(request: Request, error: str | None = None, step: str | None = None):
        settings = get_settings()
        user_id = request.cookies.get(COOKIE_NAME)
        if not user_id:
            return RedirectResponse("/login", status_code=303)

        return templates.TemplateResponse(
            request,
            "onboarding.html",
            {"topics": settings.TOPIC_VOCABULARY, "goals": GOAL_CARDS, "error": error, "start_step": step},
        )

    @app.post("/web/onboarding")
    async def web_onboarding(request: Request, selected_topics: list[str] = Form([]), goal: str = Form(...)):
        from urllib.parse import quote
        from backend.core.schemas import OnboardingRequest
        from backend.api.onboarding import create_onboarding
        from fastapi import HTTPException

        user_id = request.cookies.get(COOKIE_NAME)
        if not user_id:
            return RedirectResponse("/login", status_code=303)

        with get_db() as db:
            try:
                create_onboarding(
                    OnboardingRequest(user_id=user_id, selected_topics=selected_topics, goal=goal), db
                )
            except HTTPException as e:
                return RedirectResponse(
                    f"/onboarding?step=goal&error={quote(str(e.detail))}", status_code=303
                )

        return RedirectResponse("/", status_code=303)

    # ==================== RECOMMENDATION PAGES ====================
    def _build_signal_context(db, user_id: str, trigger_reason: str) -> dict:
        """Shared assembly for home.html + course.html's sidebar/greeting data."""
        from backend.agent import signal_panel
        from backend.tools import db_tool
        from backend.db.models import User

        settings = get_settings()
        user = db.get(User, user_id)
        display_name = (
            user.email.split("@")[0].replace(".", " ").replace("_", " ").title()
            if user else "there"
        )

        top_tags = _top_tags_for(db, user_id)
        top_topic = top_tags[0] if top_tags else None

        has_events = db_tool.has_any_events(db, user_id)
        ticker = signal_panel.build_ticker(db, user_id)
        scores = signal_panel.build_scores(db, user_id)
        top_score_tag = scores[0]["label"] if scores else None
        top_score_value = scores[0]["v"] if scores else None
        reason = signal_panel.build_reason(trigger_reason, has_events, top_score_tag, top_score_value)

        return {
            "display_name": display_name,
            "user_initial": display_name[0].upper() if display_name else "?",
            "avatar_menu_id": "avatarmenu",
            "top_topic": top_topic,
            "all_topics": settings.TOPIC_VOCABULARY,
            "ticker": ticker,
            "scores": scores,
            "reason": reason,
        }

    def _top_tags_for(db, user_id: str, n: int = 1) -> list[str]:
        from backend.agent import scoring
        from backend.tools import db_tool

        if db_tool.has_any_events(db, user_id):
            return scoring.top_interest_tags(db, user_id, n=n)
        onboarding = db_tool.get_latest_onboarding(db, user_id)
        return json.loads(onboarding.selected_topics)[:n] if onboarding else []

    @app.get("/")
    async def home(request: Request, user_id: str | None = None):
        from backend.api.recommendations import get_recommendations
        from backend.tools import db_tool
        from backend.agent import tiles

        resolved_user_id = _resolve_user_id(request, user_id)
        if not resolved_user_id:
            return RedirectResponse("/login", status_code=303)

        with get_db() as db:
            result = await get_recommendations(
                user_id=resolved_user_id, scope="home", course_id=None, db=db
            )
            ctx = _build_signal_context(db, resolved_user_id, result.trigger_reason)

            popular_rows = db_tool.get_popular_courses(db, limit=6, topic=ctx["top_topic"])
            popular = [
                {"id": c.id, "kind": "course", "title": c.title, "description": c.description,
                 "price": c.price, "rating": c.rating, "learners_count": c.learners_count,
                 "tags": json.loads(c.tags)}
                for c in popular_rows
            ]
            recently_viewed = db_tool.get_recently_viewed(db, resolved_user_id, limit=6)

            # Display-only padding: the warm Solver deliberately resolves to ONE
            # decided shape (curated path OR 2-course combo OR single course —
            # PRD Section 7.4), and that's what's persisted/logged/graded. Below
            # we only widen what the home page *renders*, appending extra real
            # catalog items after the Solver's own pick so "Agent Recommended"
            # doesn't read as near-empty — never touches result/the DB write.
            rec = result.model_dump()
            existing_path_ids = {t["id"] for t in rec.get("pathTiles") or [] if t.get("id")}
            existing_course_ids = {t["id"] for t in rec.get("courseTiles") or [] if t.get("id")}

            path_gap = 2 - len(rec.get("pathTiles") or [])
            if path_gap > 0:
                for p in db_tool.get_extra_paths(db, limit=path_gap, topic=ctx["top_topic"], exclude_ids=existing_path_ids):
                    course_rows = [pc.course for pc in p.courses]
                    rec.setdefault("pathTiles", []).append(
                        tiles.build_path_tile(p, course_rows, "More for you").model_dump()
                    )

            course_gap = 3 - len(rec.get("courseTiles") or [])
            if course_gap > 0:
                extra_courses = [
                    c for c in db_tool.get_popular_courses(db, limit=course_gap + len(existing_course_ids), topic=ctx["top_topic"])
                    if c.id not in existing_course_ids
                ][:course_gap]
                for c in extra_courses:
                    rec.setdefault("courseTiles", []).append(
                        tiles.build_course_tile(c, "More for you").model_dump()
                    )

        return templates.TemplateResponse(
            request,
            "home.html",
            {"user_id": resolved_user_id, "rec": rec, "popular": popular,
             "recently_viewed": recently_viewed, **ctx},
        )

    @app.get("/course/{course_id}")
    async def course_page(request: Request, course_id: str, user_id: str | None = None):
        from backend.api.recommendations import get_recommendations
        from backend.db.models import Product
        from backend.tools import db_tool

        resolved_user_id = _resolve_user_id(request, user_id)
        if not resolved_user_id:
            return RedirectResponse("/login", status_code=303)

        with get_db() as db:
            course = db.get(Product, course_id)
            result = await get_recommendations(
                user_id=resolved_user_id, scope="course", course_id=course_id, db=db
            )
            ctx = _build_signal_context(db, resolved_user_id, result.trigger_reason)

            course_dict = None
            similar = []
            if course:
                course_dict = {
                    "id": course.id, "title": course.title, "instructor": course.instructor,
                    "description": course.description, "duration_weeks": course.duration_weeks,
                    "level": course.level, "price": course.price, "rating": course.rating,
                    "learners_count": course.learners_count, "tags": json.loads(course.tags),
                }
                similar_rows = db_tool.get_similar_courses(db, course_id, limit=3)
                similar = [
                    {"id": c.id, "title": c.title, "description": c.description,
                     "price": c.price, "rating": c.rating, "learners_count": c.learners_count,
                     "tags": json.loads(c.tags)}
                    for c in similar_rows
                ]

        return templates.TemplateResponse(
            request,
            "course.html",
            {"user_id": resolved_user_id, "course": course_dict, "rec": result.model_dump(),
             "similar": similar, **ctx},
        )

    @app.get("/path/{path_id}")
    async def path_page(request: Request, path_id: str, user_id: str | None = None):
        from backend.api.recommendations import get_recommendations
        from backend.db.models import Path as PathModel
        from backend.tools import db_tool

        resolved_user_id = _resolve_user_id(request, user_id)
        if not resolved_user_id:
            return RedirectResponse("/login", status_code=303)

        with get_db() as db:
            path_row = db.get(PathModel, path_id)
            result = await get_recommendations(
                user_id=resolved_user_id, scope="path", course_id=path_id, db=db
            )
            ctx = _build_signal_context(db, resolved_user_id, result.trigger_reason)

            path_dict = None
            similar = []
            if path_row:
                course_rows = sorted(path_row.courses, key=lambda pc: pc.sequence_order)
                path_dict = {
                    "id": path_row.id, "title": path_row.title,
                    "description": path_row.description, "tags": json.loads(path_row.tags),
                    "level_range": path_row.level_range, "duration_months": path_row.duration_months,
                    "price": path_row.price, "discount_amount": path_row.discount_amount,
                    "has_capstone": bool(path_row.has_capstone),
                    "courses": [
                        {"id": pc.course.id, "title": pc.course.title, "level": pc.course.level,
                         "duration_weeks": pc.course.duration_weeks}
                        for pc in course_rows
                    ],
                }
                path_tags = set(path_dict["tags"])
                for p in db_tool.get_all_paths(db):
                    if p.id == path_id:
                        continue
                    if path_tags & set(json.loads(p.tags)):
                        similar.append({
                            "id": p.id, "title": p.title, "description": p.description,
                            "price": p.price, "duration_months": p.duration_months,
                            "tags": json.loads(p.tags),
                        })
                    if len(similar) == 3:
                        break

        return templates.TemplateResponse(
            request,
            "path.html",
            {"user_id": resolved_user_id, "path": path_dict, "similar": similar,
             "rec": result.model_dump(), **ctx},
        )

    @app.get("/paths")
    async def paths_page(request: Request, user_id: str | None = None):
        from backend.tools import db_tool

        resolved_user_id = _resolve_user_id(request, user_id)
        if not resolved_user_id:
            return RedirectResponse("/login", status_code=303)

        with get_db() as db:
            ctx = _build_signal_context(db, resolved_user_id, "page_change")
            rows = db_tool.get_all_paths(db)
            paths = [
                {"id": p.id, "title": p.title, "description": p.description,
                 "price": p.price, "discount_amount": p.discount_amount,
                 "duration_months": p.duration_months, "has_capstone": bool(p.has_capstone),
                 "tags": json.loads(p.tags), "course_count": len(p.courses)}
                for p in rows
            ]

        return templates.TemplateResponse(
            request, "paths.html", {"user_id": resolved_user_id, "paths": paths, **ctx},
        )

    @app.get("/browse")
    async def browse_page(
        request: Request, topic: str | None = None, q: str | None = None, user_id: str | None = None
    ):
        from backend.api.recommendations import get_recommendations
        from backend.tools import db_tool

        resolved_user_id = _resolve_user_id(request, user_id)
        if not resolved_user_id:
            return RedirectResponse("/login", status_code=303)

        with get_db() as db:
            result = await get_recommendations(
                user_id=resolved_user_id, scope="browse", course_id=topic, db=db
            )
            ctx = _build_signal_context(db, resolved_user_id, result.trigger_reason)
            course_rows = db_tool.get_all_products(db)
            path_rows = db_tool.get_all_paths(db)
            if q:
                needle = q.strip().lower()

                def _text_match(title, description, tags):
                    if needle in title.lower() or needle in description.lower():
                        return True
                    return any(needle in t.lower() for t in tags)

                course_rows = [c for c in course_rows if _text_match(c.title, c.description, json.loads(c.tags))]
                path_rows = [p for p in path_rows if _text_match(p.title, p.description, json.loads(p.tags))]
            elif topic:
                course_rows = [c for c in course_rows if topic in json.loads(c.tags)]
                path_rows = [p for p in path_rows if topic in json.loads(p.tags)]
            courses = [
                {"id": c.id, "title": c.title, "description": c.description,
                 "price": c.price, "rating": c.rating, "learners_count": c.learners_count,
                 "tags": json.loads(c.tags)}
                for c in course_rows
            ]
            paths = [
                {"id": p.id, "title": p.title, "description": p.description,
                 "price": p.price, "discount_amount": p.discount_amount,
                 "duration_months": p.duration_months, "has_capstone": bool(p.has_capstone),
                 "tags": json.loads(p.tags), "course_count": len(p.courses)}
                for p in path_rows
            ]

        return templates.TemplateResponse(
            request, "browse.html",
            {"user_id": resolved_user_id, "topic": topic, "q": q, "courses": courses, "paths": paths,
             "rec": result.model_dump(), **ctx},
        )

    @app.get("/profile")
    async def profile_page(request: Request, user_id: str | None = None):
        from backend.db.models import User, Purchase, Product, Path as PathModel
        from backend.tools import db_tool

        resolved_user_id = _resolve_user_id(request, user_id)
        if not resolved_user_id:
            return RedirectResponse("/login", status_code=303)

        goal_icon_by_value = {g["value"]: g["icon"] for g in GOAL_CARDS}

        with get_db() as db:
            user = db.get(User, resolved_user_id)
            if not user:
                return RedirectResponse("/login", status_code=303)

            onboarding_row = db_tool.get_latest_onboarding(db, resolved_user_id)
            onboarding_ctx = None
            if onboarding_row:
                onboarding_ctx = {
                    "selected_topics": json.loads(onboarding_row.selected_topics),
                    "goal": onboarding_row.goal,
                    "goal_icon": goal_icon_by_value.get(onboarding_row.goal, "&#127919;"),
                }

            purchase_rows = (
                db.query(Purchase)
                .filter(Purchase.user_id == resolved_user_id)
                .order_by(Purchase.purchased_at.desc())
                .all()
            )
            purchases = []
            for p in purchase_rows:
                if p.product_id:
                    row = db.get(Product, p.product_id)
                    title, item_type = (row.title if row else p.product_id), "Course"
                else:
                    row = db.get(PathModel, p.path_id)
                    title, item_type = (row.title if row else p.path_id), "Path (bundle)"
                purchases.append({
                    "title": title, "item_type": item_type,
                    "purchased_at": p.purchased_at.strftime("%b %d, %Y"),
                    "price_paid": p.price_paid,
                })

            email, role, digest_enabled = user.email, user.role, bool(user.digest_enabled)
            display_name = email.split("@")[0].replace(".", " ").replace("_", " ").title()

        return templates.TemplateResponse(
            request,
            "profile.html",
            {"user_id": resolved_user_id, "onboarding": onboarding_ctx,
             "purchases": purchases, "email": email, "role": role,
             "digest_enabled": digest_enabled,
             "user_initial": display_name[0].upper() if display_name else "?",
             "avatar_menu_id": "avatarmenu"},
        )

    @app.post("/web/profile/digest")
    async def web_toggle_digest(request: Request, digest_enabled: str | None = Form(None)):
        from backend.db.models import User

        resolved_user_id = _resolve_user_id(request, None)
        if not resolved_user_id:
            return RedirectResponse("/login", status_code=303)

        with get_db() as db:
            user = db.get(User, resolved_user_id)
            if user:
                user.digest_enabled = digest_enabled is not None

        return RedirectResponse("/profile", status_code=303)

    @app.get("/admin")
    async def admin_page(request: Request, tab: str | None = None):
        from backend.tools import db_tool

        settings = get_settings()
        with get_db() as db:
            all_courses = [{"id": c.id, "title": c.title} for c in db_tool.get_all_products(db)]

        return templates.TemplateResponse(
            request,
            "admin.html",
            {"active_tab": tab, "all_topics": settings.TOPIC_VOCABULARY, "all_courses": all_courses},
        )

    return app


app = create_app()
