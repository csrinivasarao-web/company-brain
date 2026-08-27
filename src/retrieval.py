"""
Layer 4 - Query. The single retrieval function every caller (chat assistant,
build API, dashboard builder) uses instead of touching vectorstore.py or
sql_store.py directly.

Access control is resolved FIRST and passed INTO both search paths as a
scope condition -- the vector search's `where` filter and a per-table
allow-list on the SQL side -- rather than fetching everything and filtering
results afterward. See vectorstore.search() for the vector side; the SQL
side is gated per-table below, since these mock tables assign one
access_tier to the whole table (in metadata.csv) rather than per-row.

Returns BOTH a prose-ready context string (for an LLM to answer from) and
the raw hits/rows (for a future caller like the dashboard builder that
needs structured data, not prose) from the same call.

AGGREGATION: when a question needs a sum/average/count over a routed SQL
table, that's computed for real with pandas (src/aggregation.py) and
injected into the context as an explicitly labeled, authoritative block --
the LLM is instructed (see chat.py's system prompt) to copy those numbers
rather than re-derive its own from the raw table.

NL-TO-SQL (dashboards): a second, separate query path in this same file --
still Layer 4, still gated by get_allowed_tiers(), but generating a full
SQL query instead of routing to a fixed table. See nl_to_sql() below.
"""
import re
from src.governance import get_allowed_tiers
from src.gemini_client import embed_texts, generate_text
from src.vectorstore import search as vector_search
from src.sql_store import query, get_readonly_connection
from src.aggregation import detect_aggregation_intent, compute_aggregation, format_for_display

# Simple keyword router for SQL tables. Good enough for a prototype's scope
# (per the design doc: "simple keyword routing is fine for now"). A real
# NL-to-SQL layer would replace this, not the surrounding function shape.
SQL_TABLE_KEYWORDS = {
    "payer_coverage": [
        "payer", "coverage", "delta dental", "cigna", "metlife",
        "guardian", "aetna", "supported", "integration status",
    ],
    "pipeline": [
        "pipeline", "deal", "revenue", "arr", "closed", "prospecting",
        "negotiation", "demo stage", "account", "dso",
    ],
}


def _get_table_access_tiers() -> dict:
    """table_name -> access_tier, read from doc_metadata (loaded from
    metadata.csv, which includes one row per SQL table alongside the
    document rows)."""
    df = query("SELECT topic_tags, access_tier FROM doc_metadata WHERE format = 'sql_table'")
    tiers = {}
    for _, row in df.iterrows():
        table_name = str(row["topic_tags"]).split(";")[0].strip()
        tiers[table_name] = row["access_tier"]
    return tiers


def _route_sql_tables(question: str) -> list:
    q = question.lower()
    matched = []
    for table_name, keywords in SQL_TABLE_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            matched.append(table_name)
    return matched


def _df_to_markdown(df) -> str:
    """Hand-rolled markdown table -- deliberately not pandas' to_markdown(),
    which silently requires the separate `tabulate` package. Avoiding that
    dependency avoids the failure mode entirely rather than just patching
    requirements.txt and hoping nothing else pulls the same trick later."""
    if df.empty:
        return "(no rows)"
    cols = [str(c) for c in df.columns]
    lines = []
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row[c]) for c in cols) + " |")
    return "\n".join(lines)


def retrieve(question: str, role: str, k: int = 5) -> dict:
    """
    Returns:
      {
        "context_text": str,       # prose-ready, for the chat assistant
        "vector_hits": [ { text, title, owner, access_tier, last_verified, distance }, ... ],
        "sql_results": { table_name: DataFrame, ... },
        "aggregations": { table_name: { "df": DataFrame, "spec": dict }, ... },
        "blocked_tables": [ table_name, ... ],  # relevant but role lacks access
        "allowed_tiers": [ ... ],
        "tiers_touched": { ... },  # union of tiers actually used in this answer
      }
    """
    allowed_tiers = get_allowed_tiers(role)

    # --- Vector path: access applied inside the Chroma `where` filter ---
    query_embedding = embed_texts([question], task_type="RETRIEVAL_QUERY")[0]
    raw = vector_search(query_embedding, allowed_tiers=allowed_tiers, n_results=k)

    vector_hits = []
    if raw["ids"] and raw["ids"][0]:
        for i in range(len(raw["ids"][0])):
            meta = raw["metadatas"][0][i]
            vector_hits.append({
                "text": raw["documents"][0][i],
                "title": meta.get("title"),
                "owner": meta.get("owner"),
                "access_tier": meta.get("access_tier"),
                "last_verified": meta.get("last_verified"),
                "distance": raw["distances"][0][i],
            })

    # --- SQL path: access applied per-table, before any rows are fetched ---
    table_tiers = _get_table_access_tiers()
    routed_tables = _route_sql_tables(question)

    sql_results = {}
    blocked_tables = []
    for table_name in routed_tables:
        table_tier = table_tiers.get(table_name)
        if table_tier in allowed_tiers:
            sql_results[table_name] = query(f"SELECT * FROM {table_name}")
        else:
            blocked_tables.append(table_name)

    # --- Aggregation: real pandas math, only over tables we were already
    # allowed to fetch -- an access-blocked table is never aggregated,
    # since it was never even queried above. ---
    aggregations = {}
    for table_name, df in sql_results.items():
        spec = detect_aggregation_intent(question, table_name)
        if spec:
            try:
                agg_df = compute_aggregation(df, spec)
                aggregations[table_name] = {"df": agg_df, "spec": spec}
            except Exception:
                pass

    # --- Assemble prose context, citing owner + freshness for everything ---
    context_parts = []
    for hit in vector_hits:
        context_parts.append(
            f"[Source: {hit['title']} \u2014 owner: {hit['owner']}, "
            f"last verified: {hit['last_verified']}]\n{hit['text']}"
        )
    for table_name, df in sql_results.items():
        table_meta_tier = table_tiers.get(table_name)
        context_parts.append(
            f"[Source: {table_name} data \u2014 access tier: {table_meta_tier}]\n"
            f"{_df_to_markdown(df)}"
        )
        if table_name in aggregations:
            agg_df_display = format_for_display(aggregations[table_name]["df"])
            context_parts.append(
                f"[Pre-computed aggregation for {table_name} \u2014 calculated in code "
                f"with pandas, NOT by you. These numbers are already correct. Present "
                f"them as-is; do not recompute, re-derive, or re-sum anything from the "
                f"raw table above.]\n{_df_to_markdown(agg_df_display)}"
            )
    if blocked_tables:
        context_parts.append(
            f"[Note: {', '.join(blocked_tables)} data looked relevant to this "
            f"question but is outside this role's access and was not retrieved.]"
        )

    context_text = "\n\n---\n\n".join(context_parts) if context_parts else ""

    tiers_touched = {hit["access_tier"] for hit in vector_hits if hit.get("access_tier")}
    for table_name in sql_results:
        tiers_touched.add(table_tiers.get(table_name))
    tiers_touched.discard(None)

    return {
        "context_text": context_text,
        "vector_hits": vector_hits,
        "sql_results": sql_results,
        "aggregations": aggregations,
        "blocked_tables": blocked_tables,
        "allowed_tiers": sorted(allowed_tiers),
        "tiers_touched": tiers_touched,
    }


# ===========================================================================
# NL-to-SQL: a second Layer 4 query path, for the dashboard builder.
#
# Trust boundary, stated precisely: the LLM writes a SQL QUERY. It never
# computes an answer itself. The arithmetic is still 100% deterministic
# SQLite/pandas code -- exactly the same trust boundary as the aggregation
# engine above, just extended from "pick from a fixed set of recipes" to
# "generate the query text." This is NOT "trust the LLM's math" creeping
# back in through a side door.
#
# Three independent guardrails, not one:
#   1. Schema visibility -- a role only ever sees schemas for tables its
#      tier already covers. A Rep's prompt never mentions `pipeline`
#      exists, so the model can't reference what it was never shown.
#   2. Static validation -- generated SQL must be a single SELECT, must
#      only reference pre-approved table names, and any write/schema/
#      pragma keyword rejects it outright before it's ever executed.
#   3. A genuinely read-only DB connection -- opened via SQLite's URI
#      mode=ro, so even a validation bug can't result in a write actually
#      succeeding. Belt AND suspenders, not one or the other.
# ===========================================================================

TABLE_SCHEMAS = {
    "pipeline": {
        "description": "Sales pipeline. One row per deal.",
        "columns": {
            "deal_id": "text, unique deal id",
            "account_name": "text, customer/prospect name",
            "segment": "text: enterprise, mid_market, or smb",
            "stage": "text: prospecting, demo, negotiation, closed_won, or closed_lost",
            "amount_usd": "integer, deal value in USD",
            "close_date": "text date, YYYY-MM-DD",
            "owner": "text, sales rep name",
        },
    },
    "payer_coverage": {
        "description": "Insurance payer integration coverage. One row per payer.",
        "columns": {
            "payer_id": "text",
            "payer_name": "text",
            "region": "text: National or Regional",
            "supported": "boolean",
            "coverage_pct": "integer, 0-100",
            "integration_status": "text: stable, unstable, voice_fallback_pending, or not_supported",
            "last_verified": "text date, YYYY-MM-DD",
        },
    },
}

FORBIDDEN_SQL_KEYWORDS = [
    "insert", "update", "delete", "drop", "alter", "attach", "detach",
    "pragma", "create", "replace", "transaction", "vacuum", "reindex",
    "trigger", "grant", "revoke",
]

NL_TO_SQL_SYSTEM_PROMPT = """You write a single, read-only SQLite SELECT query to answer a business question, using ONLY the tables and columns listed below.

Rules:
- Output ONLY the raw SQL query. No markdown code fences, no explanation, no semicolon at the end.
- Must be exactly one SELECT statement. Never write INSERT, UPDATE, DELETE, DROP, ALTER, ATTACH, PRAGMA, CREATE, or multiple statements separated by semicolons.
- Only reference tables and columns explicitly listed below. Never invent a column or table name, and never reference a table not listed here even if you know it might exist.
- If the question cannot be answered using only these tables, output exactly: NONE

Available tables:
{schema_text}
"""


def get_visible_table_schemas(role: str) -> dict:
    """Only tables this role's tier already covers -- the LLM is never
    even shown the existence of a table it can't access, which is a
    stronger guarantee than trusting it to decline to use one it CAN see."""
    allowed_tiers = get_allowed_tiers(role)
    table_tiers = _get_table_access_tiers()
    return {
        t: schema for t, schema in TABLE_SCHEMAS.items()
        if table_tiers.get(t) in allowed_tiers
    }


def _format_schema_text(schemas: dict) -> str:
    parts = []
    for table_name, schema in schemas.items():
        cols = "\n".join(f"  - {col}: {desc}" for col, desc in schema["columns"].items())
        parts.append(f"TABLE {table_name} -- {schema['description']}\n{cols}")
    return "\n\n".join(parts)


def _strip_code_fences(sql: str) -> str:
    s = sql.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip().rstrip(";").strip()


def validate_sql(sql: str, visible_tables: set) -> tuple:
    """Returns (is_valid, reason). `visible_tables` is the set this
    specific role is allowed to see -- checked again here independently
    of get_visible_table_schemas(), so validation doesn't silently trust
    that the prompt-construction step got it right."""
    if not sql or sql.strip().upper() == "NONE":
        return False, "no_answerable_query"

    lowered = sql.lower().strip()
    if not lowered.startswith("select"):
        return False, "not_a_select_statement"
    if ";" in sql:
        return False, "multiple_statements_not_allowed"

    for kw in FORBIDDEN_SQL_KEYWORDS:
        if re.search(rf"\b{kw}\b", lowered):
            return False, f"forbidden_keyword: {kw}"

    disallowed_tables = set(TABLE_SCHEMAS.keys()) - visible_tables
    for t in disallowed_tables:
        if re.search(rf"\b{re.escape(t)}\b", lowered):
            return False, f"references_disallowed_table: {t}"

    referenced = {t for t in TABLE_SCHEMAS if re.search(rf"\b{re.escape(t)}\b", lowered)}
    if not referenced:
        return False, "references_no_known_table"

    return True, "ok"


def run_readonly_query(sql: str):
    """Executes against a connection opened in SQLite's URI read-only
    mode -- the third guardrail. Even if validate_sql() had a bug, the
    database file itself refuses to be written to over this connection."""
    import pandas as pd
    conn = get_readonly_connection()
    try:
        return pd.read_sql_query(sql, conn)
    finally:
        conn.close()


def pick_chart_type(df) -> str:
    """Deliberately simple, deterministic chart selection -- not asked of
    the LLM, for the same reason arithmetic isn't: consistent, predictable
    behavior over guessing.

    FIXED: previously only recognized exactly 2 columns (one category, one
    number), so any two-dimension group-by -- e.g. "amount by segment AND
    stage", which naturally produces 3 columns -- fell through to a plain
    table with no chart and no explanation. Now recognizes 1 OR 2
    non-numeric (category) columns alongside 1+ numeric columns as
    chartable; app.py's rendering pivots the two-category case so both
    dimensions actually show up in the chart, rather than one of them
    silently getting dropped or mis-typed as a value."""
    if df.empty:
        return "table"
    if df.shape == (1, 1):
        return "metric"
    numeric_cols = df.select_dtypes(include="number").columns
    non_numeric_cols = [c for c in df.columns if c not in numeric_cols]
    if df.shape[0] > 1 and len(numeric_cols) >= 1 and len(non_numeric_cols) in (1, 2):
        return "bar"
    return "table"


def nl_to_sql(question: str, role: str) -> dict:
    """The dashboard builder's entry point. Returns:
      { "ok": True, "sql": str, "df": DataFrame, "chart_type": str, "tables_used": set }
      or
      { "ok": False, "error": str, "detail": str, "sql": str|None }
    """
    schemas = get_visible_table_schemas(role)
    if not schemas:
        return {"ok": False, "error": "no_visible_tables", "detail": None, "sql": None}

    schema_text = _format_schema_text(schemas)
    system_instruction = NL_TO_SQL_SYSTEM_PROMPT.format(schema_text=schema_text)
    raw = generate_text(f"Question: {question}", system_instruction=system_instruction)
    sql = _strip_code_fences(raw)

    visible_tables = set(schemas.keys())
    valid, reason = validate_sql(sql, visible_tables)
    if not valid:
        return {"ok": False, "error": "invalid_query", "detail": reason, "sql": sql}

    try:
        df = run_readonly_query(sql)
    except Exception as e:
        return {"ok": False, "error": "execution_failed", "detail": str(e), "sql": sql}

    tables_used = {t for t in TABLE_SCHEMAS if re.search(rf"\b{re.escape(t)}\b", sql.lower())}
    return {"ok": True, "sql": sql, "df": df, "chart_type": pick_chart_type(df), "tables_used": tables_used}
