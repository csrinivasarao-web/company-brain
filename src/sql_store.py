"""
Layer 2 - the structured (SQL) side, plus the document metadata table that
Layer 3 (governance) reads from. Uses an in-memory SQLite database for the
same reason the vector store is in-memory: the container is ephemeral, so we
rebuild both from committed source files on every startup rather than
depending on a file surviving between deploys.
"""
import sqlite3
import pandas as pd

_connection = None


def get_connection():
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(":memory:", check_same_thread=False)
    return _connection


def load_csv_as_table(csv_path: str, table_name: str):
    conn = get_connection()
    df = pd.read_csv(csv_path)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    return len(df)


def load_metadata_table(csv_path: str):
    return load_csv_as_table(csv_path, "doc_metadata")


def query(sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, get_connection())
