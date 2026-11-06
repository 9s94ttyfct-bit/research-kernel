-- Append-only event log (source of truth).
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY,
    evt_id      TEXT NOT NULL UNIQUE,
    ts          TEXT NOT NULL,
    project_id  TEXT NOT NULL,
    actor_kind  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    type        TEXT NOT NULL,
    entity_id   TEXT,
    payload     TEXT NOT NULL,
    prev_hash   TEXT,
    hash        TEXT NOT NULL
);

-- ---- Projections (rebuildable from events) ----
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    title_norm  TEXT NOT NULL,
    year        INTEGER
);
CREATE INDEX IF NOT EXISTS items_title_year ON items(title_norm, year);

CREATE TABLE IF NOT EXISTS item_ids (
    kind    TEXT NOT NULL,
    value   TEXT NOT NULL,
    item_id TEXT NOT NULL,
    PRIMARY KEY (kind, value)
);

CREATE TABLE IF NOT EXISTS tags (
    item_id TEXT NOT NULL,
    tag     TEXT NOT NULL,
    PRIMARY KEY (item_id, tag)
);

CREATE TABLE IF NOT EXISTS collections (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    parent_id TEXT,
    query     TEXT
);

CREATE TABLE IF NOT EXISTS collection_items (
    collection_id TEXT NOT NULL,
    item_id       TEXT NOT NULL,
    PRIMARY KEY (collection_id, item_id)
);

CREATE TABLE IF NOT EXISTS attachments (
    id          TEXT PRIMARY KEY,
    item_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    origin      TEXT NOT NULL,
    access_note TEXT,
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    sha256     TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    url        TEXT,
    fetched_at TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    seq        INTEGER NOT NULL,
    PRIMARY KEY (sha256, item_id)
);

CREATE TABLE IF NOT EXISTS notes (
    id      TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    data    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verifications (
    id          TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    verifier    TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    details     TEXT NOT NULL,
    ts          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS injection_flags (
    id      TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    snippet TEXT NOT NULL,
    reason  TEXT NOT NULL,
    actor   TEXT NOT NULL,
    ts      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS browse_log (
    id              TEXT PRIMARY KEY,
    task_id         TEXT,
    site            TEXT NOT NULL,
    action          TEXT NOT NULL,
    url             TEXT NOT NULL,
    query           TEXT,
    result_count    INTEGER,
    screenshot_path TEXT,
    ts              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    stage       TEXT,
    prompt      TEXT NOT NULL,
    options     TEXT,
    task_id     TEXT,
    status      TEXT NOT NULL,
    answer      TEXT,
    answered_by TEXT,
    created     TEXT NOT NULL,
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id       TEXT PRIMARY KEY,
    role     TEXT NOT NULL,
    driver   TEXT NOT NULL,
    vendor   TEXT,
    status   TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    output   TEXT,
    error    TEXT,
    created  TEXT NOT NULL,
    finished TEXT
);

CREATE TABLE IF NOT EXISTS claims (
    id     TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    data   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id       TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    data     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_claim ON evidence(claim_id);

CREATE TABLE IF NOT EXISTS edges (
    id      TEXT PRIMARY KEY,
    from_id TEXT NOT NULL,
    to_id   TEXT NOT NULL,
    data    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS objections (
    id        TEXT PRIMARY KEY,
    target_id TEXT NOT NULL,
    data      TEXT NOT NULL
);

-- Discovery (Plan 4): searches, landscape clusters, gaps, topics, research questions, exploration rounds.
CREATE TABLE IF NOT EXISTS search_log (
    id       TEXT PRIMARY KEY,
    stage    TEXT NOT NULL,
    attempt  INTEGER NOT NULL,
    purpose  TEXT NOT NULL,
    source   TEXT NOT NULL,
    query    TEXT NOT NULL,
    limit_n  INTEGER NOT NULL,
    item_ids TEXT NOT NULL,
    ts       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS search_log_stage ON search_log(stage, attempt);

CREATE TABLE IF NOT EXISTS clusters (
    id                 TEXT PRIMARY KEY,
    attempt            INTEGER NOT NULL,
    label              TEXT NOT NULL,
    description        TEXT NOT NULL,
    query              TEXT NOT NULL,
    item_ids           TEXT NOT NULL,
    representative_ids TEXT NOT NULL,
    year_counts        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gaps (
    id         TEXT PRIMARY KEY,
    attempt    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    statement  TEXT NOT NULL,
    cluster_id TEXT,
    evidence   TEXT NOT NULL,
    details    TEXT NOT NULL,
    verified   INTEGER NOT NULL,
    checks     TEXT NOT NULL,
    created    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id          TEXT PRIMARY KEY,
    attempt     INTEGER NOT NULL,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    gap_ids     TEXT NOT NULL,
    closest     TEXT NOT NULL,
    feasibility TEXT NOT NULL,
    data_needed TEXT NOT NULL,
    paper_type  TEXT NOT NULL,
    risks       TEXT NOT NULL,
    status      TEXT NOT NULL,
    created     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rqs (
    id           TEXT PRIMARY KEY,
    attempt      INTEGER NOT NULL,
    topic_id     TEXT NOT NULL,
    body         TEXT NOT NULL,
    already_done TEXT,
    status       TEXT NOT NULL,
    text_hash    TEXT,
    adopted_by   TEXT,
    created      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id            TEXT PRIMARY KEY,
    rq_id         TEXT NOT NULL,
    statement     TEXT NOT NULL,
    falsification TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS hypotheses_rq ON hypotheses(rq_id);

CREATE TABLE IF NOT EXISTS exploration_rounds (
    id       TEXT PRIMARY KEY,
    n        INTEGER NOT NULL,
    stage    TEXT NOT NULL,
    feedback TEXT NOT NULL,
    created  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_tasks (
    task_id TEXT PRIMARY KEY,
    run_id  TEXT,
    session INTEGER,
    stage   TEXT NOT NULL,
    attempt INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS run_tasks_stage ON run_tasks(run_id, session, stage);

-- Orchestrator (spec §7): one row per discovery stage, back-edge and run. `guidance` is the guidance requested for
-- the current attempt (kept to rerun it when interrupted) or, once reopened, for the next one; `waiting_checkpoint`
-- the checkpoint the current attempt waits on (checkpoints have no attempt).
CREATE TABLE IF NOT EXISTS stages (
    stage              TEXT PRIMARY KEY,
    status             TEXT NOT NULL,          -- running | waiting | reopened | passed
    attempt            INTEGER NOT NULL,
    passed_attempt     INTEGER,
    last_gate          TEXT,                   -- JSON {attempt, ok, failures}
    waiting_checkpoint TEXT,
    guidance           TEXT,
    updated            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS back_edges (
    edge    TEXT PRIMARY KEY,                  -- "from->to"
    count   INTEGER NOT NULL DEFAULT 0,
    allowed INTEGER NOT NULL DEFAULT 0         -- extra uses granted past the limit
);

CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    until           TEXT NOT NULL,
    session         INTEGER NOT NULL,
    session_started REAL NOT NULL,             -- epoch seconds
    started         REAL NOT NULL,
    ended           REAL,
    reason          TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    item_id UNINDEXED, title, abstract, creators, notes, fulltext
);
