CREATE TABLE IF NOT EXISTS submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    llm_score   REAL NOT NULL,
    stylo_score REAL NOT NULL,
    confidence  REAL NOT NULL,
    attribution TEXT NOT NULL CHECK (attribution IN ('likely_ai','likely_human','uncertain')),
    label       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'classified' CHECK (status IN ('classified','under_review')),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appeals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id  INTEGER NOT NULL REFERENCES submissions(id),
    reasoning   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,
    content_id        INTEGER NOT NULL REFERENCES submissions(id),
    creator_id        TEXT NOT NULL,
    attribution       TEXT NOT NULL,
    confidence        REAL NOT NULL,
    llm_score         REAL NOT NULL,
    stylometric_score REAL NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('classified','under_review')),
    appeal_reasoning  TEXT
);
