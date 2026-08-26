import streamlit as st
from src.ingest import run_ingestion
from src.sql_store import query
from src.governance import get_allowed_tiers, get_stale_documents, detect_gaps, ROLE_ACCESS
from src.retrieval import retrieve
from src.chat import get_answer

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
st.subheader("Layer 3 — Governance & provisioning")

if ingestion_ok:
    gov_col1, gov_col2 = st.columns(2)

    with gov_col1:
        st.markdown("**Freshness monitor** — docs past 90 days unverified")
        stale = get_stale_documents()
        if stale.empty:
            st.write("None currently.")
        else:
            for _, row in stale.iterrows():
                st.markdown(f"⚠️ **{row['title']}** — {row['days_since_verified']} days old")

    with gov_col2:
        st.markdown("**Gap detection** — repeated low-confidence topics")
        gaps = detect_gaps()
        if gaps.empty:
            st.write("None currently.")
        else:
            for _, row in gaps.iterrows():
                st.markdown(
                    f"🔍 **{row['topic_tag']}** — {row['occurrences']} low-confidence "
                    f"queries, avg confidence {row['avg_confidence']:.2f}"
                )

    with st.expander("RBAC — what each role can see"):
        for role, tiers in ROLE_ACCESS.items():
            st.write(f"**{role}**: {', '.join(sorted(tiers))}")
        st.caption(
            "This is re-checked on every query, dashboard view, and thread open — "
            "never cached per document, so access always reflects the current viewer."
        )
else:
    st.info("Governance checks run once ingestion succeeds.")

st.divider()
st.subheader("💬 Chat with the Company Brain")
st.caption(
    "Answers only from the retrieved knowledge base — cited, freshness-dated, "
    "and honest when it doesn't know something. Every question here also feeds "
    "gap detection above."
)

if ingestion_ok:
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    chat_role = st.selectbox("Chatting as:", list(ROLE_ACCESS.keys()), key="chat_role")

    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("meta"):
                meta = msg["meta"]
                badge = "🔴 declined — no KB coverage" if meta["declined"] else f"🟢 confidence ~{meta['confidence']:.2f}"
                st.caption(f"{badge} · topic tag: `{meta['topic_tag']}` · asked as: {meta['role']}")
                with st.expander("Sources used for this answer"):
                    if meta["retrieval"]["vector_hits"]:
                        st.write("**Knowledge base documents:**")
                        for hit in meta["retrieval"]["vector_hits"]:
                            st.markdown(f"- {hit['title']} — owner: {hit['owner']}, last verified: {hit['last_verified']}")
                    if meta["retrieval"]["sql_results"]:
                        st.write("**Structured data:**")
                        for t in meta["retrieval"]["sql_results"]:
                            st.markdown(f"- `{t}` table")
                    if meta["retrieval"]["blocked_tables"]:
                        st.write("**Blocked by access tier:**")
                        for t in meta["retrieval"]["blocked_tables"]:
                            st.markdown(f"- `{t}` (not visible to {meta['role']})")
                    if not meta["retrieval"]["vector_hits"] and not meta["retrieval"]["sql_results"]:
                        st.write("Nothing retrieved for this question.")

    user_question = st.chat_input("Ask the Company Brain something...")

    if user_question:
        st.session_state.chat_history.append({"role": "user", "content": user_question})
        with st.chat_message("user"):
            st.markdown(user_question)

        with st.chat_message("assistant"):
            with st.spinner("Retrieving and generating..."):
                result = get_answer(user_question, chat_role, history=st.session_state.chat_history[:-1])
            st.markdown(result["answer"])
            badge = "🔴 declined — no KB coverage" if result["declined"] else f"🟢 confidence ~{result['confidence']:.2f}"
            st.caption(f"{badge} · topic tag: `{result['topic_tag']}` · asked as: {chat_role}")
            with st.expander("Sources used for this answer"):
                if result["retrieval"]["vector_hits"]:
                    st.write("**Knowledge base documents:**")
                    for hit in result["retrieval"]["vector_hits"]:
                        st.markdown(f"- {hit['title']} — owner: {hit['owner']}, last verified: {hit['last_verified']}")
                if result["retrieval"]["sql_results"]:
                    st.write("**Structured data:**")
                    for t in result["retrieval"]["sql_results"]:
                        st.markdown(f"- `{t}` table")
                if result["retrieval"]["blocked_tables"]:
                    st.write("**Blocked by access tier:**")
                    for t in result["retrieval"]["blocked_tables"]:
                        st.markdown(f"- `{t}` (not visible to {chat_role})")
                if not result["retrieval"]["vector_hits"] and not result["retrieval"]["sql_results"]:
                    st.write("Nothing retrieved for this question.")

        st.session_state.chat_history.append({
            "role": "assistant",
            "content": result["answer"],
            "meta": {
                "declined": result["declined"],
                "confidence": result["confidence"],
                "topic_tag": result["topic_tag"],
                "retrieval": result["retrieval"],
                "role": chat_role,
            },
        })

    if st.session_state.chat_history:
        if st.button("Clear chat"):
            st.session_state.chat_history = []
            st.rerun()
else:
    st.info("Chat runs once ingestion succeeds.")

st.divider()
with st.expander("Layer 4 raw test harness (for debugging retrieval directly, without the chat layer)"):
    if ingestion_ok:
        test_role = st.selectbox("Test as role:", list(ROLE_ACCESS.keys()), key="l4_role")
        test_question = st.text_input("Test question:", "What's our pipeline for enterprise DSOs?", key="l4_question")

        if st.button("Run retrieval", key="l4_run"):
            try:
                result = retrieve(test_question, test_role)
                st.write(f"Allowed tiers: {result['allowed_tiers']}")
                if result["blocked_tables"]:
                    st.warning(f"Blocked by access tier: {result['blocked_tables']}")
                st.text_area(
                    "Assembled context (what the LLM would see):",
                    result["context_text"],
                    height=300,
                )
                with st.expander("Raw vector hits"):
                    st.json(result["vector_hits"])
                with st.expander("Raw SQL results"):
                    for t, df in result["sql_results"].items():
                        st.write(t)
                        st.dataframe(df)
            except Exception as e:
                st.error(f"Retrieval failed: {e}")
    else:
        st.info("Query layer test runs once ingestion succeeds.")

st.divider()
st.subheader("Build status")

layers = [
    ("Repo scaffold & deployment pipeline", True),
    ("Layer 1 — Source (mock data set)", True),
    ("Layer 2 — Structure & store (Vector DB + SQL + metadata)", ingestion_ok),
    ("Layer 3 — Governance (approval, RBAC, freshness, gap detection)", ingestion_ok),
    ("Layer 4 — Query (scoped retrieval)", ingestion_ok),
    ("Layer 5 — Act: Chat assistant", ingestion_ok),
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
