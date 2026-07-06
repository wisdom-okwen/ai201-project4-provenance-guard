import sqlite3
from datetime import datetime, timezone

from config import DB_PATH


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        with open("schema.sql") as f:
            conn.executescript(f.read())


def _now():
    return datetime.now(timezone.utc).isoformat()


def insert_submission(creator_id, content, llm_score, stylo_score, confidence, attribution, label):
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO submissions
               (creator_id, content, llm_score, stylo_score, confidence, attribution, label, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'classified', ?)""",
            (creator_id, content, llm_score, stylo_score, confidence, attribution, label, _now()),
        )
        return cur.lastrowid


def get_submission(content_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM submissions WHERE id = ?", (content_id,)).fetchone()
        return dict(row) if row else None


def update_submission_status(content_id, status):
    with _connect() as conn:
        conn.execute("UPDATE submissions SET status = ? WHERE id = ?", (status, content_id))


def insert_appeal(content_id, reasoning):
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO appeals (content_id, reasoning, created_at) VALUES (?, ?, ?)",
            (content_id, reasoning, _now()),
        )
        return cur.lastrowid


def insert_audit_log(content_id, creator_id, attribution, confidence, llm_score, stylo_score, status, appeal_reasoning=None):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO audit_log
               (timestamp, content_id, creator_id, attribution, confidence, llm_score, stylometric_score, status, appeal_reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_now(), content_id, creator_id, attribution, confidence, llm_score, stylo_score, status, appeal_reasoning),
        )


def get_audit_log(creator_id=None, status=None, limit=50, offset=0):
    query = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    if creator_id:
        query += " AND creator_id = ?"
        params.append(creator_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
