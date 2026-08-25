"""
Layer 1 -> Layer 2 pipeline. Run once per app startup (see app.py, wrapped in
st.cache_resource). Each data type is processed according to the rule set in
the design doc:
  - docs & wikis: chunked into passages
  - call transcripts: summarized into structured nuggets first, THEN chunked
  - structured CSVs: loaded directly into the SQL store
  - the metadata manifest: loaded into its own SQL table for Layer 3 to read
"""
import os
import pandas as pd

from src.chunking import chunk_text
from src.summarize_transcript import summarize_transcript
from src.gemini_client import embed_texts
from src.vectorstore import add_chunks, count as vector_count
from src.sql_store import load_csv_as_table, load_metadata_table, query

DATA_DIR = "data/mock"
QUERY_LOG_PATH = os.path.join(DATA_DIR, "seed", "query_log_seed.csv")


def run_ingestion() -> dict:
    metadata_path = os.path.join(DATA_DIR, "metadata.csv")
    meta_df = pd.read_csv(metadata_path)
    load_metadata_table(metadata_path)

    report = {"documents": [], "sql_tables": {}, "errors": []}

    all_ids, all_embeddings, all_documents, all_metadatas = [], [], [], []

    for _, row in meta_df.iterrows():
        if row["format"] == "sql_table":
            continue  # handled separately below

        file_path = os.path.join(DATA_DIR, row["filename"])
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except FileNotFoundError:
            report["errors"].append(f"Missing file: {file_path}")
            continue

        if row["format"] == "transcript":
            # Source-layer processing step for calls: summarize before storing.
            processed_text = summarize_transcript(raw_text)
        else:
            processed_text = raw_text

        chunks = chunk_text(processed_text)
        if not chunks:
            continue

        embeddings = embed_texts(chunks, task_type="RETRIEVAL_DOCUMENT")

        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            all_ids.append(f"{row['doc_id']}-chunk-{i}")
            all_embeddings.append(emb)
            all_documents.append(chunk)
            all_metadatas.append({
                "doc_id": row["doc_id"],
                "title": row["title"],
                "team": row["team"],
                "owner": row["owner"],
                "access_tier": row["access_tier"],
                "last_verified": row["last_verified"],
                "topic_tags": row["topic_tags"],
            })

        report["documents"].append({
            "doc_id": row["doc_id"],
            "title": row["title"],
            "chunks": len(chunks),
            "access_tier": row["access_tier"],
        })

    if all_ids:
        add_chunks(all_ids, all_embeddings, all_documents, all_metadatas)

    # Structured tables
    sql_rows = meta_df[meta_df["format"] == "sql_table"]
    for _, row in sql_rows.iterrows():
        file_path = os.path.join(DATA_DIR, row["filename"])
        table_name = row["topic_tags"].split(";")[0]
        n_rows = load_csv_as_table(file_path, table_name)
        report["sql_tables"][table_name] = {
            "rows": n_rows,
            "access_tier": row["access_tier"],
        }

    # Seeded low-confidence query log, for gap detection (Layer 3) to read.
    if os.path.exists(QUERY_LOG_PATH):
        n_log_rows = load_csv_as_table(QUERY_LOG_PATH, "query_log")
        report["sql_tables"]["query_log"] = {"rows": n_log_rows, "access_tier": "internal_system"}

    report["total_chunks_embedded"] = vector_count()
    report["total_documents"] = len(report["documents"])
    return report
