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

Access rule: a thread's required-tier set is the UNION of the access_tier
of every source ever actually retrieved inside it - not a single "highest
tier", since our tiers aren't a strict ladder (team_restricted_gtm and
team_restricted_ops are siblings, not one above the other; leadership_only
sits above both). A person can open a thread only if their role's allowed
tiers are a SUPERSET of that thread's required tiers - checked fresh every
time, against the CURRENT viewer, never the thread's creator. Being
invited to a thread is necessary but never sufficient: an invite can never
override this check.

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
    conn.execute("""CREATE TABLE IF NOT EXISTS threads (
        thread_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        created_by TEXT,
        created_at TEXT,
        required_tiers TEXT
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
        created_at TEXT
    )""")
    conn.commit()


def create_thread(title: str, created_by: str) -> int:
    _ensure_tables()
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO threads (title, created_by, created_at, required_tiers) VALUES (?, ?, ?, ?)",
        (title, created_by, datetime.utcnow().isoformat(), json.dumps([])),
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


def get_shared_with(thread_id: int) -> list:
    conn = get_connection()
    rows = conn.execute("SELECT user_id FROM thread_shares WHERE thread_id=?", (thread_id,)).fetchall()
    return [r[0] for r in rows]


def append_message(thread_id: int, role: str, content: str, meta: dict = None, touched_tiers: set = None):
    _ensure_tables()
    conn = get_connection()
    conn.execute(
        "INSERT INTO thread_messages (thread_id, role, content, meta, created_at) VALUES (?, ?, ?, ?, ?)",
        (thread_id, role, content, json.dumps(meta or {}), datetime.utcnow().isoformat()),
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


def get_messages(thread_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT role, content, meta, created_at FROM thread_messages WHERE thread_id=? ORDER BY message_id",
        (thread_id,),
    ).fetchall()
    return [
        {"role": r[0], "content": r[1], "meta": json.loads(r[2]) if r[2] else {}, "created_at": r[3]}
        for r in rows
    ]


def get_thread(thread_id: int) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT thread_id, title, created_by, created_at, required_tiers FROM threads WHERE thread_id=?",
        (thread_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "thread_id": row[0], "title": row[1], "created_by": row[2],
        "created_at": row[3], "required_tiers": json.loads(row[4]) if row[4] else [],
    }


def can_view(thread_id: int, user_id: str) -> bool:
    thread = get_thread(thread_id)
    if not thread:
        return False
    required = set(thread["required_tiers"])
    role = DEMO_USERS[user_id]["role"]
    allowed = get_allowed_tiers(role)
    return required.issubset(allowed)


def list_visible_threads(user_id: str) -> list:
    """Threads the user was invited to AND currently clears the access bar
    for - both checked every call, never cached, so a newly-touched tier
    (someone else asks a leadership-tier question in a shared thread) is
    reflected immediately, even mid-conversation."""
    _ensure_tables()
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT t.thread_id, t.title, t.created_by, t.created_at, t.required_tiers
        FROM threads t
        JOIN thread_shares s ON t.thread_id = s.thread_id
        WHERE s.user_id = ?
        ORDER BY t.created_at DESC
    """, (user_id,)).fetchall()

    visible = []
    for thread_id, title, created_by, created_at, required_tiers in rows:
        if can_view(thread_id, user_id):
            visible.append({
                "thread_id": thread_id, "title": title, "created_by": created_by,
                "created_at": created_at,
                "required_tiers": json.loads(required_tiers) if required_tiers else [],
            })
    return visible
