"""
Layer 2 - the structured (SQL) side, plus the document metadata table that
Layer 3 (governance) reads from. Uses a file-based SQLite database (not
:memory:) so every part of the app - ingestion, the SQL peek, governance
queries - reliably sees the same data regardless of which thread Streamlit
runs a given rerun on. The file lives outside git (.gitignore'd) and gets
rebuilt fresh each time ingestion runs.
"""
import os
import sqlite3
import pandas as pd

DB_PATH = "data/db/company_brain.sqlite3"


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def get_readonly_connection():
    """Opened via SQLite's URI read-only mode -- a genuine OS-level
    guarantee, not just an application-level promise. Used by NL-to-SQL
    (retrieval.py) as a third line of defense: even a bug in query
    validation can't result in a write actually succeeding over this
    connection."""
    uri = f"file:{DB_PATH}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def load_csv_as_table(csv_path: str, table_name: str):
    conn = get_connection()
    df = pd.read_csv(csv_path)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    return len(df)


def load_metadata_table(csv_path: str):
    return load_csv_as_table(csv_path, "doc_metadata")


def query(sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, get_connection())
