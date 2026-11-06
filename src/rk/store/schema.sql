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

-- Operational table (not a projection): tool-call idempotency results.
CREATE TABLE IF NOT EXISTS idempotency (
    tool   TEXT NOT NULL,
    key    TEXT NOT NULL,
    result TEXT NOT NULL,
    PRIMARY KEY (tool, key)
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

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    item_id UNINDEXED, title, abstract, creators, notes, fulltext
);
