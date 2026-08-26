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
"""
from src.governance import get_allowed_tiers
from src.gemini_client import embed_texts
from src.vectorstore import search as vector_search
from src.sql_store import query

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
        "blocked_tables": blocked_tables,
        "allowed_tiers": sorted(allowed_tiers),
        "tiers_touched": tiers_touched,
    }
