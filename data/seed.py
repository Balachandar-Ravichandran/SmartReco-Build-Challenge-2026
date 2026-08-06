"""
data/seed.py — Pathwise synthetic dataset generator.

Builds a full synthetic dataset for every table in db/schema.sql (Section 13.2
of PRD_Pathwise_v1.md): 50 courses spread across all 12 TOPIC_VOCABULARY
topics (Section 5.3), ~12 curated Paths stitched from those courses,
10 users (the 5 canonical demo personas from PRD Section 18.2 plus 5 more
for behavioral-data volume), onboarding rows, sessions, behavioral_events,
purchases, and a handful of illustrative recommendation_log /
current_recommendations / vector_sync_log rows.

Deterministic: fixed RNG seed, no wall-clock reads for anything that needs
to reproduce identically across runs (all "created_at" timestamps are
synthesized relative to a fixed EPOCH, not datetime.now()).

Usage:
    python3 seed.py            # writes pathwise.db + CSVs under ./out, ./csv
"""

import json
import random
import sqlite3
import csv
import os
from datetime import datetime, timedelta

random.seed(42)

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "code", "backend", "db", "schema.sql")
DB_PATH = os.path.join(PROJECT_ROOT, "pathwise.db")
CSV_DIR = os.path.join(HERE, "csv")

EPOCH = datetime(2026, 5, 1, 9, 0, 0)  # fixed reference "now" for synthetic created_at values

TOPIC_VOCABULARY = [
    "Agentic AI", "Machine Learning", "Data Engineering", "Generative AI",
    "Cloud & DevOps", "Cybersecurity", "Product & Design", "Business & Finance",
    "Mobile Development", "Career Skills", "MLOps", "Python for AI",
]

LEVELS = ["Beginner", "Intermediate", "Advanced"]

# ---------------------------------------------------------------------------
# 1. Courses (products) — 50 total, distributed across all 12 topics.
#    The first 9 rows are the canonical demo-set courses already named in
#    PRD Section 18.1/18.2 — kept verbatim so the existing synthetic-user
#    scenarios (Sol-viewer, Freed-viewer, Sam-shifter, Sparse-tag user)
#    still resolve exactly as documented.
# ---------------------------------------------------------------------------

CANONICAL_COURSES = [
    # (title, instructor, primary_topic, extra_tags, level, weeks, price, rating, learners)
    ("AI Engineer — by Sol", "Sol Alvarez", "Agentic AI", ["Generative AI", "Python for AI"], "Intermediate", 8, 249.0, 4.9, 1140),
    ("AI Engineer — by Freed", "Freed Osei", "Agentic AI", ["Generative AI"], "Intermediate", 8, 229.0, 4.7, 860),
    ("AI Deployment — by Sam", "Sam Whitfield", "MLOps", ["Cloud & DevOps", "Agentic AI"], "Advanced", 6, 279.0, 4.8, 640),
    ("Agentic Workflows w/ LangGraph", "Priya Nathan", "Agentic AI", ["Python for AI"], "Advanced", 5, 199.0, 4.8, 510),
    ("MLOps for Real Teams", "Sam Whitfield", "MLOps", ["Cloud & DevOps", "Machine Learning"], "Intermediate", 6, 219.0, 4.6, 730),
    ("Building Production RAG Systems", "Elena Kobayashi", "Generative AI", ["Agentic AI", "Python for AI"], "Advanced", 7, 259.0, 4.9, 480),
    ("Machine Learning Foundations", "Marcus Webb", "Machine Learning", ["Python for AI"], "Beginner", 6, 149.0, 4.7, 2100),
    ("Cloud Infra for AI Workloads", "Dana Cho", "Cloud & DevOps", ["MLOps"], "Intermediate", 5, 189.0, 4.5, 590),
    ("Prompt Engineering to Production", "Elena Kobayashi", "Generative AI", ["Agentic AI"], "Beginner", 4, 129.0, 4.8, 1580),
]

# Extra course pools per topic — enough to reach 50 total (9 canonical + 41 more).
EXTRA_COURSE_POOL = {
    "Agentic AI": [
        ("Multi-Agent Systems Design", "Priya Nathan", ["Python for AI"], "Advanced", 6, 219.0, 4.7, 410),
        ("Agent Evaluation & Observability", "Dana Cho", ["MLOps"], "Advanced", 4, 169.0, 4.5, 300),
    ],
    "Machine Learning": [
        ("Supervised Learning in Practice", "Marcus Webb", ["Python for AI"], "Beginner", 6, 139.0, 4.6, 1900),
        ("Deep Learning Foundations", "Elena Kobayashi", ["Python for AI"], "Intermediate", 8, 199.0, 4.7, 1450),
        ("Feature Engineering at Scale", "Dana Cho", ["Data Engineering"], "Intermediate", 5, 169.0, 4.5, 720),
        ("Time Series Forecasting", "Marcus Webb", ["Business & Finance"], "Intermediate", 5, 159.0, 4.4, 540),
    ],
    "Data Engineering": [
        ("Data Pipelines with Python", "Dana Cho", ["Python for AI"], "Beginner", 6, 149.0, 4.6, 980),
        ("Streaming Data Systems", "Sam Whitfield", ["Cloud & DevOps"], "Advanced", 6, 219.0, 4.5, 340),
        ("Data Warehousing Fundamentals", "Marcus Webb", ["Business & Finance"], "Beginner", 5, 129.0, 4.4, 760),
        ("Building Reliable ETL", "Dana Cho", ["Cloud & DevOps"], "Intermediate", 5, 169.0, 4.6, 500),
    ],
    "Generative AI": [
        ("Diffusion Models Explained", "Elena Kobayashi", ["Machine Learning"], "Advanced", 5, 199.0, 4.7, 460),
        ("Fine-Tuning LLMs", "Priya Nathan", ["Python for AI"], "Advanced", 6, 229.0, 4.8, 690),
        ("Retrieval-Augmented Generation Basics", "Elena Kobayashi", ["Agentic AI"], "Beginner", 4, 129.0, 4.6, 1010),
    ],
    "Cloud & DevOps": [
        ("Containers & Kubernetes for AI", "Sam Whitfield", ["MLOps"], "Intermediate", 6, 189.0, 4.5, 610),
        ("CI/CD for ML Pipelines", "Dana Cho", ["MLOps"], "Intermediate", 5, 179.0, 4.4, 420),
        ("Infrastructure as Code Essentials", "Dana Cho", ["Cybersecurity"], "Intermediate", 5, 159.0, 4.5, 450),
    ],
    "Cybersecurity": [
        ("Securing AI Applications", "Nadia Farouk", ["Agentic AI"], "Advanced", 5, 209.0, 4.6, 290),
        ("Cloud Security Fundamentals", "Nadia Farouk", ["Cloud & DevOps"], "Beginner", 5, 139.0, 4.5, 640),
        ("Threat Modeling in Practice", "Nadia Farouk", ["Career Skills"], "Intermediate", 4, 149.0, 4.4, 310),
        ("Applied Cryptography Basics", "Nadia Farouk", ["Python for AI"], "Intermediate", 5, 159.0, 4.3, 260),
    ],
    "Product & Design": [
        ("Product Sense for AI Features", "Freed Osei", ["Generative AI"], "Beginner", 4, 119.0, 4.6, 830),
        ("UX Research Fundamentals", "Lena Marsh", ["Career Skills"], "Beginner", 4, 109.0, 4.5, 700),
        ("Design Systems at Scale", "Lena Marsh", ["Product & Design"], "Intermediate", 5, 149.0, 4.4, 380),
        ("Prototyping with AI Tools", "Freed Osei", ["Generative AI"], "Beginner", 3, 99.0, 4.5, 920),
    ],
    "Business & Finance": [
        ("AI ROI for Business Leaders", "Owen Castillo", ["Career Skills", "Generative AI"], "Beginner", 3, 99.0, 4.4, 640),
        ("Financial Modeling with Python", "Owen Castillo", ["Python for AI"], "Intermediate", 6, 179.0, 4.5, 470),
        ("Data-Driven Decision Making", "Owen Castillo", ["Data Engineering"], "Intermediate", 5, 149.0, 4.5, 520),
    ],
    "Mobile Development": [
        ("iOS Development with Swift", "Ravi Menon", ["Product & Design"], "Beginner", 7, 179.0, 4.6, 1230),
        ("Android Development with Kotlin", "Ravi Menon", ["Product & Design"], "Beginner", 7, 179.0, 4.5, 1080),
        ("Cross-Platform Apps with React Native", "Ravi Menon", ["Career Skills", "Generative AI"], "Intermediate", 6, 169.0, 4.4, 690),
        ("Mobile App Performance Tuning", "Ravi Menon", ["Cloud & DevOps"], "Advanced", 4, 159.0, 4.3, 210),
    ],
    "Career Skills": [
        ("Technical Interview Prep", "Lena Marsh", ["Career Skills"], "Beginner", 3, 89.0, 4.6, 1740),
        ("Communicating Technical Work", "Lena Marsh", ["Product & Design"], "Beginner", 3, 79.0, 4.5, 980),
        ("Negotiation for Engineers", "Owen Castillo", ["Business & Finance"], "Beginner", 2, 69.0, 4.4, 560),
    ],
    "MLOps": [
        ("Model Monitoring in Production", "Sam Whitfield", ["Cloud & DevOps"], "Advanced", 5, 199.0, 4.6, 350),
        ("Experiment Tracking & Reproducibility", "Dana Cho", ["Machine Learning"], "Intermediate", 4, 149.0, 4.5, 400),
        ("Scaling Inference Systems", "Dana Cho", ["Cloud & DevOps"], "Advanced", 5, 209.0, 4.5, 300),
    ],
    "Python for AI": [
        ("Python Fundamentals for AI", "Marcus Webb", ["Machine Learning"], "Beginner", 5, 99.0, 4.7, 2600),
        ("NumPy & Pandas in Depth", "Marcus Webb", ["Data Engineering"], "Beginner", 4, 109.0, 4.6, 1900),
        ("Async Python for AI Services", "Priya Nathan", ["Cloud & DevOps"], "Intermediate", 4, 139.0, 4.5, 640),
        ("Testing AI Pipelines in Python", "Priya Nathan", ["MLOps"], "Intermediate", 4, 139.0, 4.4, 380),
    ],
}

def slugify(title: str) -> str:
    return "crs_" + "".join(c.lower() if c.isalnum() else "-" for c in title).strip("-").replace("--", "-")[:40]

def build_courses():
    courses = []
    seen_ids = set()

    def add(title, instructor, primary_topic, extra_tags, level, weeks, price, rating, learners):
        cid = slugify(title)
        base = cid
        n = 2
        while cid in seen_ids:
            cid = f"{base}-{n}"
            n += 1
        seen_ids.add(cid)
        tags = [primary_topic] + [t for t in extra_tags if t != primary_topic]
        days_ago = random.randint(30, 540)
        created = EPOCH - timedelta(days=days_ago)
        courses.append({
            "id": cid,
            "title": title,
            "instructor": instructor,
            "description": f"{title} — a {level.lower()}-level, {weeks}-week course covering {', '.join(tags)}.",
            "tags": json.dumps(tags),
            "level": level,
            "duration_weeks": weeks,
            "price": price,
            "rating": rating,
            "learners_count": learners,
            "is_active": 1,
            "deleted_at": None,
            "embedding_synced_at": (created + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": created.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": created.strftime("%Y-%m-%d %H:%M:%S"),
        })
        return cid

    canonical_ids = {}
    for title, instructor, topic, extra, level, weeks, price, rating, learners in CANONICAL_COURSES:
        cid = add(title, instructor, topic, extra, level, weeks, price, rating, learners)
        canonical_ids[title] = cid

    for topic, pool in EXTRA_COURSE_POOL.items():
        for title, instructor, extra, level, weeks, price, rating, learners in pool:
            add(title, instructor, topic, extra, level, weeks, price, rating, learners)

    assert len(courses) == 50, f"expected 50 courses, got {len(courses)}"
    return courses, canonical_ids


# ---------------------------------------------------------------------------
# 2. Paths — canonical 3 (verbatim from PRD Section 18.1) + 9 more, stitched
#    from the 50-course catalog. ~half get has_capstone=1 / a real discount,
#    the rest are deliberately "no real discount" so resolve_recommendation_
#    shape()'s ad-hoc-combo branch (Section 7.4) has real negative examples
#    to exercise, not just curated-Path positives.
# ---------------------------------------------------------------------------

def build_paths(courses_by_title):
    def pid_of(title):
        return "path_" + "".join(c.lower() if c.isalnum() else "-" for c in title).strip("-")[:30]

    specs = [
        # (title, description, level_range, course_titles, months, discount, has_capstone)
        ("Production AI Engineer Path",
         "From agentic fundamentals to deploying production AI systems.",
         "Intermediate-Advanced",
         ["AI Engineer — by Sol", "AI Deployment — by Sam"], 4, 60.0, 1),
        ("Full-Stack GenAI Path",
         "Retrieval-augmented generation through prompt-engineering-to-production.",
         "Intermediate-Advanced",
         ["Building Production RAG Systems", "Prompt Engineering to Production"], 3, 45.0, 1),
        ("ML → MLOps Path",
         "Machine learning foundations through operating models in production.",
         "Beginner-Intermediate",
         ["Machine Learning Foundations", "MLOps for Real Teams"], 4, 40.0, 0),
        ("Agentic Systems Specialist Path",
         "Deep, hands-on path for building and evaluating multi-agent systems.",
         "Advanced",
         ["Agentic Workflows w/ LangGraph", "Multi-Agent Systems Design", "Agent Evaluation & Observability"], 5, 70.0, 1),
        ("AI Infrastructure Path",
         "Cloud, containers, and CI/CD for teams running AI workloads.",
         "Intermediate-Advanced",
         ["Cloud Infra for AI Workloads", "Containers & Kubernetes for AI", "CI/CD for ML Pipelines"], 4, 55.0, 0),
        ("Applied Deep Learning Path",
         "From ML foundations to fine-tuning and diffusion models.",
         "Intermediate-Advanced",
         ["Deep Learning Foundations", "Fine-Tuning LLMs", "Diffusion Models Explained"], 5, 65.0, 1),
        ("Data Foundations for AI Path",
         "Data engineering fundamentals that feed every downstream ML system.",
         "Beginner-Intermediate",
         ["Data Pipelines with Python", "NumPy & Pandas in Depth", "Feature Engineering at Scale"], 4, 35.0, 0),
        ("AI Security Path",
         "Securing AI applications and the cloud infrastructure they run on.",
         "Intermediate-Advanced",
         ["Cloud Security Fundamentals", "Securing AI Applications", "Threat Modeling in Practice"], 4, 50.0, 1),
        ("AI Product Builder Path",
         "Product sense, prototyping, and UX research for AI-powered features.",
         "Beginner-Intermediate",
         ["Product Sense for AI Features", "Prototyping with AI Tools", "UX Research Fundamentals"], 3, 30.0, 0),
        ("Mobile AI Developer Path",
         "Native and cross-platform mobile development for AI-powered apps.",
         "Beginner-Intermediate",
         ["iOS Development with Swift", "Cross-Platform Apps with React Native"], 4, 0.0, 0),
        ("MLOps Reliability Path",
         "Monitoring, experiment tracking, and inference scaling for production ML.",
         "Advanced",
         ["Model Monitoring in Production", "Experiment Tracking & Reproducibility", "Scaling Inference Systems"], 4, 45.0, 1),
        ("AI for Business Leaders Path",
         "ROI framing and data-driven decision-making for non-technical leaders.",
         "Beginner",
         ["AI ROI for Business Leaders", "Data-Driven Decision Making"], 2, 0.0, 0),
    ]

    paths = []
    path_courses = []
    for title, desc, level_range, course_titles, months, discount, has_capstone in specs:
        pid = pid_of(title)
        course_ids = [courses_by_title[t] for t in course_titles]
        tags = sorted({tag for cid in course_ids for tag in json.loads(next(c["tags"] for c in ALL_COURSES if c["id"] == cid))})
        sum_price = sum(next(c["price"] for c in ALL_COURSES if c["id"] == cid) for cid in course_ids)
        price = round(sum_price - discount, 2)
        days_ago = random.randint(30, 480)
        created = EPOCH - timedelta(days=days_ago)
        paths.append({
            "id": pid, "title": title, "description": desc, "tags": json.dumps(tags),
            "level_range": level_range, "duration_months": months, "price": price,
            "discount_amount": discount, "has_capstone": has_capstone, "is_active": 1,
            "embedding_synced_at": (created + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": created.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": created.strftime("%Y-%m-%d %H:%M:%S"),
        })
        for i, cid in enumerate(course_ids):
            path_courses.append({"path_id": pid, "course_id": cid, "sequence_order": i + 1})
    return paths, path_courses


# ---------------------------------------------------------------------------
# 3. Users — the 5 canonical demo personas from PRD Section 18.2, plus 5
#    more (2 admin/instructor-facing, 3 additional learners) purely to give
#    sessions/behavioral_events/purchases realistic volume.
# ---------------------------------------------------------------------------

def build_users():
    users = [
        {"id": "usr_bala_cold", "email": "bala.cold@example.com", "role": "learner"},
        {"id": "usr_sol_viewer", "email": "sol.viewer@example.com", "role": "learner"},
        {"id": "usr_freed_viewer", "email": "freed.viewer@example.com", "role": "learner"},
        {"id": "usr_sam_shifter", "email": "sam.shifter@example.com", "role": "learner"},
        {"id": "usr_sparse_tag", "email": "sparse.tag@example.com", "role": "learner"},
        {"id": "usr_nadia_admin", "email": "nadia.admin@example.com", "role": "admin"},
        {"id": "usr_priya_learner", "email": "priya.learner@example.com", "role": "learner"},
        {"id": "usr_owen_learner", "email": "owen.learner@example.com", "role": "learner"},
        {"id": "usr_lena_learner", "email": "lena.learner@example.com", "role": "learner"},
        {"id": "usr_ravi_learner", "email": "ravi.learner@example.com", "role": "learner"},
    ]
    for i, u in enumerate(users):
        created = EPOCH - timedelta(days=60 - i * 3)
        u["password_hash"] = "bcrypt$demo$not-a-real-hash"
        u["created_at"] = created.strftime("%Y-%m-%d %H:%M:%S")
    return users


ONBOARDING = {
    "usr_bala_cold":     (["Agentic AI", "Machine Learning"], "Get hired as an AI engineer"),
    "usr_sol_viewer":    (["Agentic AI", "Machine Learning"], "Get hired as an AI engineer"),
    "usr_freed_viewer":  (["Agentic AI", "Machine Learning"], "Get hired as an AI engineer"),
    "usr_sam_shifter":   (["Agentic AI", "Machine Learning"], "Get hired as an AI engineer"),
    "usr_sparse_tag":    (["Mobile Development"], "Build a project"),
    "usr_priya_learner": (["Generative AI", "Python for AI"], "Ship a side project"),
    "usr_owen_learner":  (["Business & Finance", "Career Skills"], "Move into a leadership role"),
    "usr_lena_learner":  (["Product & Design", "Career Skills"], "Switch careers into tech"),
    "usr_ravi_learner":  (["Mobile Development", "Product & Design"], "Launch an app"),
}

def build_onboarding(users):
    rows = []
    oid = 1
    for u in users:
        if u["id"] not in ONBOARDING:
            continue
        topics, goal = ONBOARDING[u["id"]]
        created = datetime.strptime(u["created_at"], "%Y-%m-%d %H:%M:%S") + timedelta(minutes=10)
        rows.append({
            "id": oid, "user_id": u["id"], "selected_topics": json.dumps(topics),
            "goal": goal, "query_embedding_cache": None,
            "created_at": created.strftime("%Y-%m-%d %H:%M:%S"),
        })
        oid += 1
    return rows


# ---------------------------------------------------------------------------
# 4. Sessions + behavioral_events — reproduces PRD Section 18.2's 5 named
#    scenarios exactly, then adds lighter-weight sessions for the 4 extra
#    learners so the catalog isn't the only thing with real volume.
# ---------------------------------------------------------------------------

def build_sessions_and_events(courses_by_title, canonical_ids):
    sessions = []
    events = []
    sid_n = 1
    eid_n = 1

    def new_session(user_id, started_offset_min, device="web"):
        nonlocal sid_n
        sid = f"sess_{sid_n:03d}"
        sid_n += 1
        started = EPOCH - timedelta(minutes=started_offset_min)
        sessions.append({
            "id": sid, "user_id": user_id,
            "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": (started + timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S"),
            "device": device, "referrer": "direct",
        })
        return sid, started

    def add_event(user_id, session_id, ts, event_type, target=None, product_id=None,
                  path_id=None, query_text=None, dwell_seconds=None):
        nonlocal eid_n
        events.append({
            "id": eid_n, "user_id": user_id, "session_id": session_id, "event_type": event_type,
            "target": target, "product_id": product_id, "path_id": path_id,
            "query_text": query_text, "dwell_seconds": dwell_seconds,
            "created_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
        })
        eid_n += 1

    # usr_bala_cold: onboarding only, zero behavioral events (cold start).
    new_session("usr_bala_cold", 180)

    # usr_sol_viewer: 1 view + 1 dwell(45s) on "AI Engineer — by Sol".
    sid, t0 = new_session("usr_sol_viewer", 150)
    sol_id = canonical_ids["AI Engineer — by Sol"]
    add_event("usr_sol_viewer", sid, t0 + timedelta(minutes=1), "view", product_id=sol_id)
    add_event("usr_sol_viewer", sid, t0 + timedelta(minutes=2), "dwell", product_id=sol_id, dwell_seconds=45)

    # usr_freed_viewer: same as Sol-viewer, plus a view on "AI Engineer — by Freed"
    # (same category as prior top tag -> should_rerun() stays False).
    sid, t0 = new_session("usr_freed_viewer", 140)
    freed_id = canonical_ids["AI Engineer — by Freed"]
    sol_id2 = canonical_ids["AI Engineer — by Sol"]
    add_event("usr_freed_viewer", sid, t0 + timedelta(minutes=1), "view", product_id=sol_id2)
    add_event("usr_freed_viewer", sid, t0 + timedelta(minutes=2), "dwell", product_id=sol_id2, dwell_seconds=45)
    add_event("usr_freed_viewer", sid, t0 + timedelta(minutes=6), "view", product_id=freed_id)

    # usr_sam_shifter: adds a view on "AI Deployment — by Sam" + a search for
    # "deploy ai agents production" -> significance shift True (new category, MLOps).
    sid, t0 = new_session("usr_sam_shifter", 130)
    sol_id3 = canonical_ids["AI Engineer — by Sol"]
    sam_id = canonical_ids["AI Deployment — by Sam"]
    add_event("usr_sam_shifter", sid, t0 + timedelta(minutes=1), "view", product_id=sol_id3)
    add_event("usr_sam_shifter", sid, t0 + timedelta(minutes=2), "dwell", product_id=sol_id3, dwell_seconds=45)
    add_event("usr_sam_shifter", sid, t0 + timedelta(minutes=8), "view", product_id=sam_id)
    add_event("usr_sam_shifter", sid, t0 + timedelta(minutes=9), "search",
              query_text="deploy ai agents production")

    # usr_sparse_tag: 1 view on a Mobile Development course with almost no tag
    # overlap to the catalog's dominant Agentic AI cluster.
    sid, t0 = new_session("usr_sparse_tag", 120)
    mobile_course_id = courses_by_title["iOS Development with Swift"]
    add_event("usr_sparse_tag", sid, t0 + timedelta(minutes=1), "view", product_id=mobile_course_id)

    # Extra learners — lighter, varied sessions across other topics, purely for volume.
    extra_activity = {
        "usr_priya_learner": ["Fine-Tuning LLMs", "Building Production RAG Systems", "Retrieval-Augmented Generation Basics"],
        "usr_owen_learner": ["AI ROI for Business Leaders", "Financial Modeling with Python"],
        "usr_lena_learner": ["UX Research Fundamentals", "Technical Interview Prep", "Communicating Technical Work"],
        "usr_ravi_learner": ["iOS Development with Swift", "Android Development with Kotlin", "Cross-Platform Apps with React Native"],
    }
    offset = 100
    for user_id, titles in extra_activity.items():
        sid, t0 = new_session(user_id, offset)
        offset -= 15
        for i, title in enumerate(titles):
            cid = courses_by_title[title]
            add_event(user_id, sid, t0 + timedelta(minutes=1 + i * 3), "view", product_id=cid)
            if i == 0:
                add_event(user_id, sid, t0 + timedelta(minutes=2 + i * 3), "dwell",
                          product_id=cid, dwell_seconds=random.randint(20, 60))

    return sessions, events


# ---------------------------------------------------------------------------
# 5. Purchases — a handful of realistic completed purchases.
# ---------------------------------------------------------------------------

def build_purchases(courses_by_title, paths_by_title):
    rows = []
    specs = [
        ("usr_priya_learner", "course", "Retrieval-Augmented Generation Basics", 129.0, 40),
        ("usr_owen_learner", "course", "AI ROI for Business Leaders", 99.0, 35),
        ("usr_lena_learner", "path", "AI Product Builder Path", None, 30),
        ("usr_ravi_learner", "path", "Mobile AI Developer Path", None, 20),
    ]
    for i, (user_id, kind, title, override_price, days_ago) in enumerate(specs, start=1):
        purchased_at = EPOCH - timedelta(days=days_ago)
        if kind == "course":
            cid = courses_by_title[title]
            price = override_price if override_price is not None else next(
                c["price"] for c in ALL_COURSES if c["id"] == cid)
            rows.append({"id": i, "user_id": user_id, "product_id": cid, "path_id": None,
                         "price_paid": price, "purchased_at": purchased_at.strftime("%Y-%m-%d %H:%M:%S")})
        else:
            pid = paths_by_title[title]
            price = next(p["price"] for p in ALL_PATHS if p["id"] == pid)
            rows.append({"id": i, "user_id": user_id, "product_id": None, "path_id": pid,
                         "price_paid": price, "purchased_at": purchased_at.strftime("%Y-%m-%d %H:%M:%S")})
    return rows


# ---------------------------------------------------------------------------
# 6. A few illustrative recommendation_log / current_recommendations /
#    vector_sync_log rows — normally runtime-produced, seeded here only so
#    the admin console / observability screens have something to render on
#    first boot with an empty history.
# ---------------------------------------------------------------------------

def build_recommendation_rows(canonical_ids, paths_by_title):
    log_rows = [{
        "id": 1, "user_id": "usr_sam_shifter", "trigger_reason": "significant_shift",
        "act_path_candidates": json.dumps([{"top_item_id": paths_by_title["Production AI Engineer Path"], "top_combined_score": 0.91}]),
        "act_course_candidates": json.dumps([{"top_item_id": canonical_ids["AI Deployment — by Sam"], "top_combined_score": 0.88}]),
        "validator_status": "pass", "retry_count": 0,
        "solver_narrative": "Since you've been exploring agent deployment, the Production AI Engineer Path picks up right where AI Deployment left off.",
        "latency_ms": 2380,
        "created_at": (EPOCH - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
    }]
    current_rec_rows = [
        {"id": 1, "user_id": "usr_sam_shifter", "recommendation_log_id": 1, "item_type": "path",
         "product_id": None, "path_id": paths_by_title["Production AI Engineer Path"],
         "rank": 1, "is_hero": 1, "updated_at": (EPOCH - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")},
        {"id": 2, "user_id": "usr_sam_shifter", "recommendation_log_id": 1, "item_type": "course",
         "product_id": canonical_ids["AI Deployment — by Sam"], "path_id": None,
         "rank": 1, "is_hero": 0, "updated_at": (EPOCH - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")},
    ]
    vector_sync_rows = [
        {"id": 1, "product_id": canonical_ids["AI Engineer — by Sol"], "path_id": None,
         "operation": "insert", "sql_status": "ok", "vector_status": "ok",
         "synced_at": (EPOCH - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S"), "error_message": None},
        {"id": 2, "product_id": None, "path_id": paths_by_title["Production AI Engineer Path"],
         "operation": "insert", "sql_status": "ok", "vector_status": "ok",
         "synced_at": (EPOCH - timedelta(days=195)).strftime("%Y-%m-%d %H:%M:%S"), "error_message": None},
    ]
    return log_rows, current_rec_rows, vector_sync_rows


# ---------------------------------------------------------------------------
# Assembly + write-out
# ---------------------------------------------------------------------------

ALL_COURSES = []
ALL_PATHS = []

def main():
    global ALL_COURSES, ALL_PATHS

    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM products")
            if cur.fetchone()[0] > 0:
                print(f"Idempotent skip: {DB_PATH} already has products. Delete it to reseed.")
                return
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet — fall through and (re)build
        finally:
            conn.close()

    ALL_COURSES, canonical_ids = build_courses()
    courses_by_title = {c["title"]: c["id"] for c in ALL_COURSES}
    courses_by_title.update(canonical_ids)

    ALL_PATHS, path_courses = build_paths(courses_by_title)
    paths_by_title = {p["title"]: p["id"] for p in ALL_PATHS}

    users = build_users()
    onboarding = build_onboarding(users)
    sessions, events = build_sessions_and_events(courses_by_title, canonical_ids)
    purchases = build_purchases(courses_by_title, paths_by_title)
    rec_log, current_rec, vector_sync = build_recommendation_rows(canonical_ids, paths_by_title)

    tables = {
        "users": users,
        "user_onboarding": onboarding,
        "products": ALL_COURSES,
        "paths": ALL_PATHS,
        "path_courses": path_courses,
        "sessions": sessions,
        "purchases": purchases,
        "behavioral_events": events,
        "recommendation_log": rec_log,
        "current_recommendations": current_rec,
        "vector_sync_log": vector_sync,
    }

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(CSV_DIR, exist_ok=True)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(open(SCHEMA_PATH).read())

    for table_name, rows in tables.items():
        if not rows:
            continue
        cols = list(rows[0].keys())
        placeholders = ",".join("?" for _ in cols)
        conn.executemany(
            f"INSERT INTO {table_name} ({','.join(cols)}) VALUES ({placeholders})",
            [tuple(r[c] for c in cols) for r in rows],
        )
    conn.commit()

    # --- sanity checks: referential integrity + CHECK-constraint spot checks ---
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_key_check")
    fk_violations = cur.fetchall()
    assert not fk_violations, f"FK violations: {fk_violations}"

    cur.execute("SELECT COUNT(*) FROM products")
    assert cur.fetchone()[0] == 50, "expected exactly 50 products"

    cur.execute("SELECT COUNT(*) FROM paths")
    n_paths = cur.fetchone()[0]

    cur.execute("SELECT path_id, COUNT(*) FROM path_courses GROUP BY path_id HAVING COUNT(*) < 2")
    thin_paths = cur.fetchall()
    assert not thin_paths, f"paths with fewer than 2 courses: {thin_paths}"

    cur.execute("""
        SELECT DISTINCT t.value FROM products, json_each(products.tags) t
        WHERE t.value NOT IN ({})
    """.format(",".join("?" for _ in TOPIC_VOCABULARY)), TOPIC_VOCABULARY)
    bad_tags = cur.fetchall()
    assert not bad_tags, f"product tags outside TOPIC_VOCABULARY: {bad_tags}"

    print(f"OK: {len(ALL_COURSES)} courses, {n_paths} paths, {len(users)} users, "
          f"{len(events)} behavioral_events, {len(purchases)} purchases. No FK violations, "
          f"no path with <2 courses, no tag outside TOPIC_VOCABULARY.")

    # --- CSV export, one file per table ---
    for table_name, rows in tables.items():
        if not rows:
            continue
        with open(os.path.join(CSV_DIR, f"{table_name}.csv"), "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    conn.close()
    print(f"SQLite DB written to {DB_PATH}")
    print(f"CSVs written to {CSV_DIR}/")


if __name__ == "__main__":
    main()
