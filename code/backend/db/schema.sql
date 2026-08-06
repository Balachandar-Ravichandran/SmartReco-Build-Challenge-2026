CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('learner','admin')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    digest_enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE user_onboarding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    selected_topics TEXT NOT NULL,          -- JSON array, validated against TOPIC_VOCABULARY
    goal TEXT NOT NULL,
    query_embedding_cache TEXT,             -- JSON array of floats, nullable
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_onboarding_user_latest ON user_onboarding(user_id, created_at DESC);

CREATE TABLE products (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    instructor TEXT NOT NULL,
    description TEXT NOT NULL,
    tags TEXT NOT NULL,                     -- JSON array
    level TEXT NOT NULL,
    duration_weeks INTEGER NOT NULL,
    price REAL NOT NULL,
    rating REAL,
    learners_count INTEGER DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    deleted_at TEXT,
    embedding_synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE paths (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    tags TEXT NOT NULL,
    level_range TEXT NOT NULL,
    duration_months INTEGER NOT NULL,
    price REAL NOT NULL,
    discount_amount REAL NOT NULL DEFAULT 0,
    has_capstone INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    embedding_synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE path_courses (
    path_id TEXT NOT NULL REFERENCES paths(id) ON DELETE CASCADE,
    course_id TEXT NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    sequence_order INTEGER NOT NULL,
    PRIMARY KEY (path_id, course_id)
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    device TEXT,
    referrer TEXT
);

CREATE TABLE purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    product_id TEXT REFERENCES products(id),
    path_id TEXT REFERENCES paths(id),
    price_paid REAL NOT NULL,
    purchased_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((product_id IS NULL) <> (path_id IS NULL))
);

CREATE TABLE behavioral_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    session_id TEXT NOT NULL REFERENCES sessions(id),
    event_type TEXT NOT NULL CHECK (event_type IN ('view','dwell','search','click','add_to_cart','purchase')),
    target TEXT,
    product_id TEXT REFERENCES products(id),
    path_id TEXT REFERENCES paths(id),
    query_text TEXT,
    dwell_seconds INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (NOT (product_id IS NOT NULL AND path_id IS NOT NULL))
);
CREATE INDEX idx_events_user_time ON behavioral_events(user_id, created_at DESC);
CREATE INDEX idx_events_user_type_time ON behavioral_events(user_id, event_type, created_at DESC);

CREATE TABLE recommendation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    trigger_reason TEXT NOT NULL CHECK (trigger_reason IN ('cold_start','page_change','significant_shift')),
    scope TEXT,                             -- home, course, path, browse (page context this run was computed for)
    context_id TEXT,                       -- course_id / path_id / browse topic; null for home
    act_path_candidates TEXT,
    act_course_candidates TEXT,
    validator_status TEXT NOT NULL CHECK (validator_status IN ('pass','retried','failed')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    solver_narrative TEXT,
    solver_output_json TEXT,               -- full SolverOutput (headline/reasoning/narrative/highlights/tiles), for cache-serve replay
    latency_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE current_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    recommendation_log_id INTEGER NOT NULL REFERENCES recommendation_log(id),
    item_type TEXT NOT NULL CHECK (item_type IN ('path','course')),
    product_id TEXT REFERENCES products(id) ON DELETE CASCADE,
    path_id TEXT REFERENCES paths(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    is_hero INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((product_id IS NULL) <> (path_id IS NULL)),
    UNIQUE (user_id, item_type, rank)
);

CREATE TABLE vector_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT REFERENCES products(id),
    path_id TEXT REFERENCES paths(id),
    operation TEXT NOT NULL CHECK (operation IN ('insert','update','delete')),
    sql_status TEXT NOT NULL,
    vector_status TEXT NOT NULL,
    synced_at TEXT NOT NULL DEFAULT (datetime('now')),
    error_message TEXT,
    CHECK ((product_id IS NULL) <> (path_id IS NULL))
);
