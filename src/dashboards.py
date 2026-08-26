"""
Layer 5 - Act: No-code dashboard builder. A saved AGGREGATION RECIPE, never
a stored answer -- opening a dashboard re-runs the real pandas aggregation
(src/aggregation.py) live, through the same access-scoped retrieve() path
everything else uses, every single time. This is the same "always fresh,
always re-checked for the current viewer" guarantee Threads already
proved for prose conversations, applied here to structured data instead.

What's saved: {table, group_by: [...], agg_function, agg_column, filter}.
What's NEVER saved: a result. A dashboard has no cached numbers sitting in
the database waiting to go stale -- every open is a fresh compute_aggregation()
call against whatever the SQL store currently holds.

Access model: identical in spirit to threads.py, deliberately not
reinvented. A dashboard belongs to one SQL table, and that table has one
access_tier (from doc_metadata). Opening a dashboard requires (a) being
shared on it, same share/revoke mechanics as a thread, AND (b) the
CURRENT viewer's role covering that table's access_tier -- checked fresh
on every open, never cached, never grandfathered from whoever created it.
Unlike threads (where access is checked per-message, since a thread can
touch many tiers over time), a dashboard is pinned to exactly one table
for its whole life, so a single tier check at open time is the correct
granularity here -- not a simplification, just the right level of
complexity for what a dashboard actually is.

There's no real login system in this prototype -- "people" are the same
four named demo users from threads.py, reused here rather than duplicated.
"""
import json
from datetime import datetime

from src.sql_store import get_connection, query
from src.governance import get_allowed_tiers
from src.aggregation import compute_aggregation, format_for_display
from src.threads import DEMO_USERS  # same simulated people, one source of truth


def _ensure_tables():
    conn = get_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS dashboards (
        dashboard_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        created_by TEXT,
        created_at TEXT,
        table_name TEXT,
        group_by TEXT,
        agg_func TEXT,
        agg_column TEXT,
        filter_column TEXT,
        filter_value TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dashboard_shares (
        dashboard_id INTEGER,
        user_id TEXT
    )""")
    conn.commit()


def _table_access_tier(table_name: str) -> str:
    """table_name -> its access_tier, read from doc_metadata. Same
    convention as retrieval.py's _get_table_access_tiers: topic_tags
    stores the table name as the first ;-separated token."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT access_tier, topic_tags FROM doc_metadata WHERE format = 'sql_table'"
    ).fetchall()
    for access_tier, topic_tags in rows:
        if str(topic_tags).split(";")[0].strip() == table_name:
            return access_tier
    return None


def create_dashboard(
    title: str, created_by: str, table_name: str, group_by: list,
    agg_func: str, agg_column: str = None, filter_column: str = None, filter_value: str = None,
) -> int:
    _ensure_tables()
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO dashboards
           (title, created_by, created_at, table_name, group_by, agg_func, agg_column, filter_column, filter_value)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, created_by, datetime.utcnow().isoformat(), table_name,
         json.dumps(group_by), agg_func, agg_column, filter_column, filter_value),
    )
    conn.commit()
    dashboard_id = cur.lastrowid
    share_dashboard(dashboard_id, created_by)
    return dashboard_id


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
    """Same self-revocation hazard as threads.py's revoke_share -- callers
    must never pass the CURRENT viewer's own user_id. See app.py's sync
    logic, which excludes it explicitly."""
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
        """SELECT dashboard_id, title, created_by, created_at, table_name,
                  group_by, agg_func, agg_column, filter_column, filter_value
           FROM dashboards WHERE dashboard_id=?""",
        (dashboard_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "dashboard_id": row[0], "title": row[1], "created_by": row[2], "created_at": row[3],
        "table_name": row[4], "group_by": json.loads(row[5]) if row[5] else [],
        "agg_func": row[6], "agg_column": row[7],
        "filter_column": row[8], "filter_value": row[9],
    }


def list_visible_dashboards(user_id: str) -> list:
    """Shared-on only, same as threads.list_visible_threads -- the tier
    check happens at OPEN time (get_dashboard_result), not here, so a
    dashboard can still show up in the list with a locked indicator rather
    than disappearing outright. Being able to SEE that a dashboard exists
    (its title) is treated the same as a thread's aggregate required_tiers
    being visible to anyone shared on it -- reveals existence, not
    content."""
    _ensure_tables()
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT d.dashboard_id, d.title, d.created_by, d.table_name
        FROM dashboards d
        JOIN dashboard_shares s ON d.dashboard_id = s.dashboard_id
        WHERE s.user_id = ?
        ORDER BY d.created_at DESC
    """, (user_id,)).fetchall()
    out = []
    for dashboard_id, title, created_by, table_name in rows:
        out.append({
            "dashboard_id": dashboard_id, "title": title, "created_by": created_by,
            "table_name": table_name, "required_tier": _table_access_tier(table_name),
        })
    return out


def get_dashboard_result(dashboard_id: int, requesting_user_id: str) -> dict:
    """The one function that matters. Returns:
      { "ok": True, "dashboard": {...}, "result_df": DataFrame }
      or
      { "ok": False, "reason": "not_shared" | "access_blocked", "dashboard": {...} }

    Re-runs compute_aggregation() fresh against whatever the SQL store
    currently holds -- there is no stored result to go stale. The tier
    check happens here, on every call, against the CURRENT viewer's role,
    never the dashboard's creator."""
    dashboard = _fetch_dashboard_row(dashboard_id)
    if not dashboard:
        return {"ok": False, "reason": "not_found", "dashboard": None}

    if not _is_shared_with(dashboard_id, requesting_user_id):
        return {"ok": False, "reason": "not_shared", "dashboard": dashboard}

    role = DEMO_USERS[requesting_user_id]["role"]
    allowed_tiers = get_allowed_tiers(role)
    required_tier = _table_access_tier(dashboard["table_name"])

    if required_tier not in allowed_tiers:
        return {"ok": False, "reason": "access_blocked", "dashboard": dashboard, "required_tier": required_tier}

    df = query(f"SELECT * FROM {dashboard['table_name']}")
    if dashboard["filter_column"] and dashboard["filter_value"]:
        df = df[df[dashboard["filter_column"]] == dashboard["filter_value"]]

    spec = {
        "group_by": dashboard["group_by"],
        "agg_func": dashboard["agg_func"],
        "numeric_col": dashboard["agg_column"],
    }
    result_df = compute_aggregation(df, spec)

    return {"ok": True, "dashboard": dashboard, "result_df": result_df, "result_display": format_for_display(result_df)}
