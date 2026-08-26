"""
Layer 5 - Act: Threads/projects. The persistent, shareable version of chat
history (as opposed to the single-tab session memory added earlier).

Persistence model, stated plainly: threads live in the same file-based
SQLite database as everything else (data/db/company_brain.sqlite3), which
persists for as long as this deployment's container stays running - shared
automatically across every visitor to the live URL, since Streamlit
Community Cloud runs one process per app, not one per visitor. It does NOT
survive a redeploy or a cold restart after the app sleeps from inactivity.
Durability across restarts would need a real external database (e.g.
hosted Postgres) - a reasonable future upgrade if this ever needs to hold
real production history, not a gap in the current design's logic.

ACCESS MODEL:
Opening a thread requires only that you were shared on it -- being invited
IS sufficient to open it. Access control happens PER MESSAGE instead: each
message stores which access tiers it actually touched (touched_tiers).
When a thread's messages are read, each message is checked individually
against the CURRENT viewer's allowed tiers -- a message they're not
cleared for is replaced with a visible redaction notice, not silently
dropped and not blocking the rest of the thread. This is what lets a
thread's creator keep seeing their own conversation even after a
higher-clearance teammate adds a leadership-tier answer to it: they see
everything they're entitled to, plus a visible marker wherever something
else was withheld.

Projects are a lightweight grouping on top of threads: a thread optionally
belongs to one project. A project is visible to whoever created it, or
whoever has access to at least one thread inside it.

There's no real login system in this prototype, so "people" are simulated
via a small set of named demo users, each tied to one of the four RBAC
roles - this is what makes "share this thread with Priya specifically"
meaningfully different from "share with any Ops Specialist."
"""
import json
from datetime import datetime

from src.sql_store import get_connection
from src.governance import get_allowed_tiers

DEMO_USERS = {
    "jamie": {"name": "Jamie", "role": "GTM Rep"},
    "alex": {"name": "Alex", "role": "GTM Leadership"},
    "priya": {"name": "Priya", "role": "Ops Specialist"},
    "sam": {"name": "Sam", "role": "Ops Leadership"},
}


def _ensure_tables():
    conn = get_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS projects (
        project_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        created_by TEXT,
        created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS threads (
        thread_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        created_by TEXT,
        created_at TEXT,
        required_tiers TEXT,
        project_id INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS thread_shares (
        thread_id INTEGER,
        user_id TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS thread_messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id INTEGER,
        role TEXT,
        content TEXT,
        meta TEXT,
        created_at TEXT,
        touched_tiers TEXT
    )""")
    conn.commit()

    thread_cols = [r[1] for r in conn.execute("PRAGMA table_info(threads)").fetchall()]
    if "project_id" not in thread_cols:
        conn.execute("ALTER TABLE threads ADD COLUMN project_id INTEGER")
    msg_cols = [r[1] for r in conn.execute("PRAGMA table_info(thread_messages)").fetchall()]
    if "touched_tiers" not in msg_cols:
        conn.execute("ALTER TABLE thread_messages ADD COLUMN touched_tiers TEXT")
    conn.commit()


# --- Projects -------------------------------------------------------------

def create_project(title: str, created_by: str) -> int:
    _ensure_tables()
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO projects (title, created_by, created_at) VALUES (?, ?, ?)",
        (title, created_by, datetime.utcnow().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def list_projects_for_user(user_id: str) -> list:
    _ensure_tables()
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT p.project_id, p.title, p.created_by, p.created_at
        FROM projects p
        LEFT JOIN threads t ON t.project_id = p.project_id
        LEFT JOIN thread_shares s ON s.thread_id = t.thread_id
        WHERE p.created_by = ? OR s.user_id = ?
        ORDER BY p.created_at DESC
    """, (user_id, user_id)).fetchall()
    return [
        {"project_id": r[0], "title": r[1], "created_by": r[2], "created_at": r[3]}
        for r in rows
    ]


# --- Threads ----------------------------------------------------------

def create_thread(title: str, created_by: str, project_id: int = None) -> int:
    _ensure_tables()
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO threads (title, created_by, created_at, required_tiers, project_id) VALUES (?, ?, ?, ?, ?)",
        (title, created_by, datetime.utcnow().isoformat(), json.dumps([]), project_id),
    )
    conn.commit()
    thread_id = cur.lastrowid
    share_thread(thread_id, created_by)
    return thread_id


def share_thread(thread_id: int, user_id: str):
    _ensure_tables()
    conn = get_connection()
    existing = conn.execute(
        "SELECT 1 FROM thread_shares WHERE thread_id=? AND user_id=?", (thread_id, user_id)
    ).fetchone()
    if not existing:
        conn.execute("INSERT INTO thread_shares (thread_id, user_id) VALUES (?, ?)", (thread_id, user_id))
        conn.commit()


def revoke_share(thread_id: int, user_id: str):
    """Removes a person's access to a thread. Callers must never pass the
    CURRENT viewer's own user_id here -- see app.py's sync logic, which
    explicitly excludes it from the revocation diff. Without that guard,
    every person always appears in their own thread's share list (that's
    WHY they can see it), so a naive diff would revoke the viewer's own
    access on every single rerun."""
    _ensure_tables()
    conn = get_connection()
    conn.execute("DELETE FROM thread_shares WHERE thread_id=? AND user_id=?", (thread_id, user_id))
    conn.commit()


def get_shared_with(thread_id: int) -> list:
    conn = get_connection()
    rows = conn.execute("SELECT user_id FROM thread_shares WHERE thread_id=?", (thread_id,)).fetchall()
    return [r[0] for r in rows]


def _is_shared_with(thread_id: int, user_id: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM thread_shares WHERE thread_id=? AND user_id=?", (thread_id, user_id)
    ).fetchone()
    return row is not None


def can_view(thread_id: int, user_id: str) -> bool:
    return _is_shared_with(thread_id, user_id)


def append_message(thread_id: int, role: str, content: str, meta: dict = None, touched_tiers: set = None):
    _ensure_tables()
    conn = get_connection()
    conn.execute(
        "INSERT INTO thread_messages (thread_id, role, content, meta, created_at, touched_tiers) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (thread_id, role, content, json.dumps(meta or {}), datetime.utcnow().isoformat(),
         json.dumps(sorted({t for t in touched_tiers if t})) if touched_tiers else json.dumps([])),
    )
    if touched_tiers:
        row = conn.execute("SELECT required_tiers FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
        current = set(json.loads(row[0])) if row and row[0] else set()
        updated = current | {t for t in touched_tiers if t}
        conn.execute(
            "UPDATE threads SET required_tiers=? WHERE thread_id=?",
            (json.dumps(sorted(updated)), thread_id),
        )
    conn.commit()


def _fetch_thread_row(thread_id: int) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT thread_id, title, created_by, created_at, required_tiers, project_id FROM threads WHERE thread_id=?",
        (thread_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "thread_id": row[0], "title": row[1], "created_by": row[2],
        "created_at": row[3], "required_tiers": json.loads(row[4]) if row[4] else [],
        "project_id": row[5],
    }


def get_thread(thread_id: int, requesting_user_id: str) -> dict:
    thread = _fetch_thread_row(thread_id)
    if not thread:
        return None
    if not can_view(thread_id, requesting_user_id):
        return None
    return thread


def _redact_if_needed(row, viewer_allowed_tiers: set) -> dict:
    role, content, meta_json, created_at, touched_tiers_json = row
    touched = set(json.loads(touched_tiers_json)) if touched_tiers_json else set()
    if touched and not touched.issubset(viewer_allowed_tiers):
        missing = sorted(touched - viewer_allowed_tiers)
        return {
            "role": role,
            "content": f"\U0001F512 This response used {', '.join(missing)} content you're not currently cleared to view.",
            "meta": {"redacted": True, "required_tiers": sorted(touched)},
            "created_at": created_at,
        }
    return {
        "role": role,
        "content": content,
        "meta": json.loads(meta_json) if meta_json else {},
        "created_at": created_at,
    }


def get_messages(thread_id: int, requesting_user_id: str) -> list:
    if not can_view(thread_id, requesting_user_id):
        return []
    role = DEMO_USERS[requesting_user_id]["role"]
    viewer_allowed = get_allowed_tiers(role)
    conn = get_connection()
    rows = conn.execute(
        "SELECT role, content, meta, created_at, touched_tiers FROM thread_messages "
        "WHERE thread_id=? ORDER BY message_id",
        (thread_id,),
    ).fetchall()
    return [_redact_if_needed(r, viewer_allowed) for r in rows]


def list_visible_threads(user_id: str) -> list:
    _ensure_tables()
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT t.thread_id, t.title, t.created_by, t.created_at, t.required_tiers, t.project_id
        FROM threads t
        JOIN thread_shares s ON t.thread_id = s.thread_id
        WHERE s.user_id = ?
        ORDER BY t.created_at DESC
    """, (user_id,)).fetchall()
    return [
        {
            "thread_id": r[0], "title": r[1], "created_by": r[2], "created_at": r[3],
            "required_tiers": json.loads(r[4]) if r[4] else [], "project_id": r[5],
        }
        for r in rows
    ]
