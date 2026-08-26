"""
Real aggregation, computed in code -- not asked of an LLM as prose.

Why this file exists: retrieve() used to hand an LLM the full raw SQL
table as a markdown blob and let it eyeball sums in text. That's the wrong
tool for arithmetic -- it happens to work at small row counts and silently
degrades as tables grow, with no error message when it's wrong. Real
aggregation belongs in code (pandas groupby/agg), always deterministic,
regardless of row count.

This lives in its own module, separate from retrieval.py, specifically so
Task 9 (the no-code dashboard builder) can import and reuse the exact same
compute_aggregation() function a saved dashboard calls on every open --
one aggregation engine, two callers (chat's auto-detection here, and a
dashboard's explicit saved recipe later), never two implementations that
can drift out of sync with each other.

Scope, stated plainly: detect_aggregation_intent() is simple keyword
matching against each table's known columns, not a real NL-to-SQL layer --
consistent with the same "good enough for a prototype" scope already
called out in retrieval.py's SQL_TABLE_KEYWORDS. It only ever recognizes
real column names/synonyms for the specific table already routed by
retrieval.py; it does not guess at columns that don't exist.
"""
import re
import pandas as pd

TABLE_AGG_CONFIG = {
    "pipeline": {
        "numeric_column": "amount_usd",
        "groupable_synonyms": {
            "segment": "segment", "segments": "segment",
            "stage": "stage", "stages": "stage",
            "owner": "owner", "rep": "owner", "sales rep": "owner",
            "account name": "account_name", "account": "account_name", "customer": "account_name",
        },
    },
    "payer_coverage": {
        "numeric_column": "coverage_pct",
        "groupable_synonyms": {
            "region": "region",
            "integration status": "integration_status", "status": "integration_status",
            "supported": "supported", "support status": "supported",
            "payer name": "payer_name", "payer": "payer_name",
        },
    },
}

AGG_FUNC_KEYWORDS = {
    "sum": ["total", "sum", "aggregate", "add up", "combined"],
    "mean": ["average", "avg", "mean"],
    "count": ["how many", "count", "number of"],
}


def _detect_agg_func(q: str):
    for func, keywords in AGG_FUNC_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return func
    return None


def _detect_group_by(q: str, groupable_synonyms: dict) -> list:
    """Word-boundary matching, NOT plain substring containment -- a naive
    `"payer" in question` matches inside "payers", which was causing "how
    many payers by integration status" to spuriously group by individual
    payer name on top of the actually-requested integration_status
    grouping, since "payers" contains "payer" as a substring. \\b anchors
    this to whole words only."""
    found = []
    for synonym in sorted(groupable_synonyms, key=len, reverse=True):
        col = groupable_synonyms[synonym]
        pattern = r"\b" + re.escape(synonym) + r"\b"
        match = re.search(pattern, q)
        if match and col not in [c for _, c in found]:
            found.append((match.start(), col))
    found.sort(key=lambda x: x[0])
    return [col for _, col in found][:2]


def detect_aggregation_intent(question: str, table_name: str):
    config = TABLE_AGG_CONFIG.get(table_name)
    if not config:
        return None
    q = question.lower()
    agg_func = _detect_agg_func(q)
    group_by = _detect_group_by(q, config["groupable_synonyms"])
    if not agg_func and not group_by:
        return None
    if not agg_func:
        agg_func = "sum"
    numeric_col = None if agg_func == "count" else config["numeric_column"]
    return {"agg_func": agg_func, "group_by": group_by, "numeric_col": numeric_col, "table": table_name}


def compute_aggregation(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    group_by = spec["group_by"]
    agg_func = spec["agg_func"]
    numeric_col = spec["numeric_col"]

    if agg_func == "count":
        if group_by:
            return df.groupby(group_by).size().reset_index(name="count")
        return pd.DataFrame({"count": [len(df)]})

    if group_by:
        return df.groupby(group_by)[numeric_col].agg(agg_func).reset_index()
    return pd.DataFrame({numeric_col: [df[numeric_col].agg(agg_func)]})


def format_for_display(df: pd.DataFrame) -> pd.DataFrame:
    formatted = df.copy()
    for col in formatted.select_dtypes(include="number").columns:
        formatted[col] = formatted[col].apply(
            lambda v: f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
        )
    return formatted
