import streamlit as st
from src.ingest import run_ingestion
from src.sql_store import query
from src.governance import get_allowed_tiers, get_stale_documents, detect_gaps, ROLE_ACCESS
from src.retrieval import retrieve
from src.chat import get_answer
from src.threads import (
    DEMO_USERS, create_thread, share_thread, get_shared_with,
    get_thread, get_messages, append_message, list_visible_threads,
)

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
            "This is re-checked on every query, thread view, and message — "
            "never cached per document, so access always reflects the current viewer."
        )
else:
    st.info("Governance checks run once ingestion succeeds.")


def _render_sources(retrieval: dict):
    with st.expander("Sources used for this answer"):
        if retrieval.get("vector_hits"):
            st.write("**Knowledge base documents:**")
            for hit in retrieval["vector_hits"]:
                st.markdown(f"- {hit['title']} — owner: {hit['owner']}, last verified: {hit['last_verified']}")
        if retrieval.get("sql_results"):
            st.write("**Structured data:**")
            for t in retrieval["sql_results"]:
                st.markdown(f"- `{t}` table")
        if retrieval.get("blocked_tables"):
            st.write("**Blocked by access tier:**")
            for t in retrieval["blocked_tables"]:
                st.markdown(f"- `{t}` (not visible at this thread's current access level)")
        if not retrieval.get("vector_hits") and not retrieval.get("sql_results"):
            st.write("Nothing retrieved for this question.")


st.divider()
st.subheader("💬 Company Brain — Threads")
st.caption(
    "Persistent, shareable conversations — answers only from the retrieved "
    "knowledge base, cited and freshness-dated. A thread is visible only to "
    "people whose access covers everything ever pulled into it, checked "
    "fresh for whoever is currently viewing — not just whoever created it."
)

if ingestion_ok:
    # Streamlit won't let us reassign a widget's session_state value after
    # that widget has already been drawn in this same run (the bug that
    # just crashed) -- so instead, stash the newly created thread id under
    # a different key, and apply it here, BEFORE the selectbox below is
    # created on the next rerun.
    if "pending_thread_id" in st.session_state:
        st.session_state["selected_thread_id"] = st.session_state.pop("pending_thread_id")

    user_ids = list(DEMO_USERS.keys())
    current_user_id = st.selectbox(
        "You are:",
        user_ids,
        format_func=lambda uid: f"{DEMO_USERS[uid]['name']} ({DEMO_USERS[uid]['role']})",
        key="current_user_id",
    )
    current_role = DEMO_USERS[current_user_id]["role"]

    visible_threads = list_visible_threads(current_user_id)
    thread_options = {t["thread_id"]: t["title"] for t in visible_threads}
    thread_options[None] = "+ New thread"

    selected_thread_id = st.selectbox(
        "Thread:",
        options=list(thread_options.keys()),
        format_func=lambda tid: thread_options[tid],
        key="selected_thread_id",
    )

    if selected_thread_id is None:
        new_title = st.text_input("New thread title:", placeholder="e.g. Meridian renewal questions", key="new_thread_title")
        if st.button("Create thread"):
            if new_title.strip():
                new_id = create_thread(new_title.strip(), current_user_id)
                st.session_state.pending_thread_id = new_id
                st.rerun()
            else:
                st.warning("Give the thread a title first.")
    else:
        thread = get_thread(selected_thread_id)
        shared_with = get_shared_with(selected_thread_id)

        st.markdown(f"**{thread['title']}** — created by {DEMO_USERS[thread['created_by']]['name']}")
        if thread["required_tiers"]:
            st.caption(
                f"This thread has touched: {', '.join(thread['required_tiers'])} — "
                f"only people cleared for ALL of these tiers can open it."
            )

        other_users = [u for u in user_ids if u != current_user_id]
        default_shares = [u for u in shared_with if u in other_users]
        share_targets = st.multiselect(
            "Share this thread with:",
            options=other_users,
            default=default_shares,
            format_func=lambda uid: f"{DEMO_USERS[uid]['name']} ({DEMO_USERS[uid]['role']})",
            key=f"share_{selected_thread_id}",
        )
        for uid in share_targets:
            if uid not in shared_with:
                share_thread(selected_thread_id, uid)

        for msg in get_messages(selected_thread_id):
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                meta = msg.get("meta") or {}
                if msg["role"] == "assistant" and meta:
                    badge = "🔴 declined — no KB coverage" if meta.get("declined") else f"🟢 confidence ~{meta.get('confidence', 0):.2f}"
                    st.caption(f"{badge} · topic tag: `{meta.get('topic_tag')}`")
                    if meta.get("query_rewritten") and meta.get("search_question"):
                        st.caption(f"🔎 Searched as: \"{meta['search_question']}\"")
                    _render_sources(meta.get("retrieval", {}))

        user_question = st.chat_input("Ask something in this thread...", key=f"input_{selected_thread_id}")

        if user_question:
            append_message(selected_thread_id, "user", user_question)
            with st.chat_message("user"):
                st.markdown(user_question)

            history_for_prompt = get_messages(selected_thread_id)[:-1]

            with st.chat_message("assistant"):
                with st.spinner("Retrieving and generating..."):
                    result = get_answer(user_question, current_role, history=history_for_prompt)
                st.markdown(result["answer"])
                badge = "🔴 declined — no KB coverage" if result["declined"] else f"🟢 confidence ~{result['confidence']:.2f}"
                st.caption(f"{badge} · topic tag: `{result['topic_tag']}`")
                if result.get("query_rewritten") and result.get("search_question"):
                    st.caption(f"🔎 Searched as: \"{result['search_question']}\"")
                retrieval_for_display = {
                    "vector_hits": result["retrieval"]["vector_hits"],
                    "sql_results": {k: True for k in result["retrieval"]["sql_results"]},
                    "blocked_tables": result["retrieval"]["blocked_tables"],
                }
                _render_sources(retrieval_for_display)

            append_message(
                selected_thread_id, "assistant", result["answer"],
                meta={
                    "declined": result["declined"],
                    "confidence": result["confidence"],
                    "topic_tag": result["topic_tag"],
                    "retrieval": retrieval_for_display,
                    "search_question": result.get("search_question"),
                    "query_rewritten": result.get("query_rewritten"),
                },
                touched_tiers=result["retrieval"]["tiers_touched"],
            )
            st.rerun()
else:
    st.info("Threads run once ingestion succeeds.")

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
    ("Layer 5 — Act: Threads/projects", ingestion_ok),
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
