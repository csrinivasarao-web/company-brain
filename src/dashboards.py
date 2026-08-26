"""
Layer 5 - Act: No-code dashboard builder. Saves a FROZEN SQL QUERY, never
a stored result -- opening a dashboard re-runs that exact query live,
through the same read-only connection every time. "Frozen" means the
query text itself, not the natural-language question: re-asking the LLM
the same question later could theoretically generate a slightly different
query, so freezing at save time is what makes "same recipe every time"
actually true, not just "same intent, maybe different SQL."

Access model: a dashboard's required tiers are the union of every table
its frozen SQL references (see retrieval.py's tables_used). Checked fresh
on every open against the CURRENT viewer's role -- same pattern as
threads.py, same reasoning: a viewer's own clearance decides what they
see, never whoever created it.

Deletion is restricted to the creator -- a shared viewer can use a
dashboard, not delete it out from under whoever built it.
"""
import json
from datetime import datetime

from src.sql_store import get_connection
from src.governance import get_allowed_tiers
from src.retrieval import run_readonly_query, _get_table_access_tiers
from src.threads import DEMO_USERS


def _ensure_tables():
    conn = get_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS dashboards (
        dashboard_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        created_by TEXT,
        created_at TEXT,
        question TEXT,
        sql_query TEXT,
        chart_type TEXT,
        tables_used TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dashboard_shares (
        dashboard_id INTEGER,
        user_id TEXT
    )""")
    conn.commit()


def create_dashboard(title: str, created_by: str, question: str, sql_query: str, chart_type: str, tables_used: set) -> int:
    _ensure_tables()
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO dashboards (title, created_by, created_at, question, sql_query, chart_type, tables_used)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title, created_by, datetime.utcnow().isoformat(), question, sql_query, chart_type,
         json.dumps(sorted(tables_used))),
    )
    conn.commit()
    dashboard_id = cur.lastrowid
    share_dashboard(dashboard_id, created_by)
    return dashboard_id


def delete_dashboard(dashboard_id: int, requesting_user_id: str) -> bool:
    """Only the creator can delete. Returns False (no-op) rather than
    raising if someone else tries -- the caller decides how to surface
    that rather than this function throwing."""
    d = _fetch_dashboard_row(dashboard_id)
    if not d or d["created_by"] != requesting_user_id:
        return False
    conn = get_connection()
    conn.execute("DELETE FROM dashboards WHERE dashboard_id=?", (dashboard_id,))
    conn.execute("DELETE FROM dashboard_shares WHERE dashboard_id=?", (dashboard_id,))
    conn.commit()
    return True


def share_dashboard(dashboard_id: int, user_id: str):
    _ensure_tables()
    conn = get_connection()
    existing = conn.execute(
        "SELECT 1 FROM dashboard_shares WHERE dashboard_id=? AND user_id=?", (dashboard_id, user_id)
    ).fetchone()
    if not existing:
        conn.execute("INSERT INTO dashboard_shares (dashboard_id, user_id) VALUES (?, ?)", (dashboard_id, user_id))
        conn.commit()


def revoke_dashboard_share(dashboard_id: int, user_id: str):
    _ensure_tables()
    conn = get_connection()
    conn.execute("DELETE FROM dashboard_shares WHERE dashboard_id=? AND user_id=?", (dashboard_id, user_id))
    conn.commit()


def get_shared_with(dashboard_id: int) -> list:
    conn = get_connection()
    rows = conn.execute("SELECT user_id FROM dashboard_shares WHERE dashboard_id=?", (dashboard_id,)).fetchall()
    return [r[0] for r in rows]


def _is_shared_with(dashboard_id: int, user_id: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM dashboard_shares WHERE dashboard_id=? AND user_id=?", (dashboard_id, user_id)
    ).fetchone()
    return row is not None


def _fetch_dashboard_row(dashboard_id: int) -> dict:
    conn = get_connection()
    row = conn.execute(
        """SELECT dashboard_id, title, created_by, created_at, question, sql_query, chart_type, tables_used
           FROM dashboards WHERE dashboard_id=?""",
        (dashboard_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "dashboard_id": row[0], "title": row[1], "created_by": row[2], "created_at": row[3],
        "question": row[4], "sql_query": row[5], "chart_type": row[6],
        "tables_used": set(json.loads(row[7])) if row[7] else set(),
    }


def list_visible_dashboards(user_id: str) -> list:
    _ensure_tables()
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT d.dashboard_id, d.title, d.created_by, d.tables_used
        FROM dashboards d
        JOIN dashboard_shares s ON d.dashboard_id = s.dashboard_id
        WHERE s.user_id = ?
        ORDER BY d.created_at DESC
    """, (user_id,)).fetchall()
    return [
        {"dashboard_id": r[0], "title": r[1], "created_by": r[2],
         "tables_used": set(json.loads(r[3])) if r[3] else set()}
        for r in rows
    ]


def get_dashboard_result(dashboard_id: int, requesting_user_id: str) -> dict:
    """Re-runs the FROZEN sql_query live, every call -- no cached result.
    Returns:
      { "ok": True, "dashboard": {...}, "df": DataFrame }
      or
      { "ok": False, "reason": "not_shared"|"access_blocked"|"not_found", "dashboard": {...}|None, ... }
    """
    dashboard = _fetch_dashboard_row(dashboard_id)
    if not dashboard:
        return {"ok": False, "reason": "not_found", "dashboard": None}

    if not _is_shared_with(dashboard_id, requesting_user_id):
        return {"ok": False, "reason": "not_shared", "dashboard": dashboard}

    role = DEMO_USERS[requesting_user_id]["role"]
    allowed_tiers = get_allowed_tiers(role)
    table_tiers = _get_table_access_tiers()
    required_tiers = {table_tiers.get(t) for t in dashboard["tables_used"]}
    required_tiers.discard(None)

    if not required_tiers.issubset(allowed_tiers):
        return {
            "ok": False, "reason": "access_blocked", "dashboard": dashboard,
            "required_tiers": sorted(required_tiers),
        }

    try:
        df = run_readonly_query(dashboard["sql_query"])
    except Exception as e:
        return {"ok": False, "reason": "execution_failed", "dashboard": dashboard, "detail": str(e)}

    return {"ok": True, "dashboard": dashboard, "df": df}
