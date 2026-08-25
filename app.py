import streamlit as st
from src.ingest import run_ingestion
from src.sql_store import query

st.set_page_config(page_title="Needletail Company Brain", page_icon="🧠", layout="centered")

st.title("🧠 Needletail Company Brain")
st.caption("Prototype — GTM + Human-in-the-loop Operations pilot")

st.markdown(
    """
This is the live deployment of the Company Brain prototype.
It's being built layer by layer — this page proves each layer actually
works, not just that the page loads.
"""
)


@st.cache_resource(show_spinner="Running ingestion — chunking docs, summarizing the call transcript, embedding, loading SQL tables...")
def get_ingestion_report():
    return run_ingestion()


st.divider()
st.subheader("Layer 1 + 2 — Source & Structure/Store")

try:
    report = get_ingestion_report()

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Documents ingested", report["total_documents"])
        st.metric("Chunks embedded (Vector DB)", report["total_chunks_embedded"])
    with col2:
        for table_name, info in report["sql_tables"].items():
            st.metric(f"{table_name} rows (SQL)", info["rows"])

    if report["errors"]:
        st.warning("Some files had issues: " + "; ".join(report["errors"]))

    with st.expander("See what was ingested, per document"):
        for doc in report["documents"]:
            st.write(f"**{doc['title']}** — {doc['chunks']} chunk(s) — access tier: `{doc['access_tier']}`")

    with st.expander("Peek at the SQL store"):
        for table_name in report["sql_tables"]:
            st.write(f"`{table_name}` (first 5 rows):")
            st.dataframe(query(f"SELECT * FROM {table_name} LIMIT 5"))

    ingestion_ok = True
except Exception as e:
    st.error(f"Ingestion failed: {e}")
    ingestion_ok = False

st.divider()
st.subheader("Build status")

layers = [
    ("Repo scaffold & deployment pipeline", True),
    ("Layer 1 — Source (mock data set)", True),
    ("Layer 2 — Structure & store (Vector DB + SQL + metadata)", ingestion_ok),
    ("Layer 3 — Governance (approval, RBAC, freshness, gap detection)", False),
    ("Layer 4 — Query (scoped retrieval)", False),
    ("Layer 5 — Act: Chat assistant", False),
    ("Layer 5 — Act: Threads/projects", False),
    ("Layer 5 — Act: Build API demo", False),
    ("Layer 5 — Act: No-code dashboard builder", False),
    ("Layer 5 — Act: Proactive digest", False),
]

for label, done in layers:
    icon = "✅" if done else "⬜"
    st.markdown(f"{icon} {label}")

st.divider()
st.info(
    "If you're seeing real numbers above (not zeros or an error), the "
    "ingestion pipeline actually ran against your Gemini API key and built "
    "both stores from the mock data — this isn't a mockup.",
    icon="🚀",
)
