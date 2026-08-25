"""
Layer 2 - the structured (SQL) side, plus the document metadata table that
Layer 3 (governance) reads from. Uses a file-based SQLite database in the
container's temp directory rather than ":memory:". The container is still
ephemeral -- we rebuild from committed source on every startup -- but a
file path guarantees every connection in the process sees the same data,
where ":memory:" only guarantees that for the single connection object
that created it. That distinction matters more in Streamlit's rerun/caching
model than in a plain script, which is what caused the "no such table"
error: the write and the read ended up on two different in-memory databases.
"""
import os
import sqlite3
import tempfile
import pandas as pd

DB_PATH = os.path.join(tempfile.gettempdir(), "needletail_structured.db")

_connection = None


def get_connection():
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    return _connection


def load_csv_as_table(csv_path: str, table_name: str):
    conn = get_connection()
    df = pd.read_csv(csv_path)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    conn.commit()
    return len(df)


def load_metadata_table(csv_path: str):
    return load_csv_as_table(csv_path, "doc_metadata")


def query(sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, get_connection())
