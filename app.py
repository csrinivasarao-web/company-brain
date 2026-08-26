import streamlit as st
from src.ingest import run_ingestion
from src.sql_store import query
from src.governance import get_allowed_tiers, get_stale_documents, detect_gaps, ROLE_ACCESS
from src.retrieval import retrieve
from src.chat import get_answer
from src.threads import (
    DEMO_USERS, create_thread, share_thread, revoke_share, get_shared_with,
    get_thread, get_messages, append_message, list_visible_threads,
    create_project, list_projects_for_user,
)

st.set_page_config(page_title="Needletail Company Brain", page_icon="🧠", layout="wide")


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
                st.markdown(f"- `{t}` (not visible at this role's access level)")
        if not retrieval.get("vector_hits") and not retrieval.get("sql_results"):
            st.write("Nothing retrieved for this question.")


@st.cache_resource(show_spinner="Running ingestion — chunking docs, summarizing the call transcript, embedding, loading SQL tables...")
def get_ingestion_report():
    return run_ingestion()


try:
    report = get_ingestion_report()
    ingestion_ok = True
except Exception as e:
    report = None
    ingestion_error = e
    ingestion_ok = False

with st.expander("⚙️ System status — ingestion, governance, retrieval diagnostics", expanded=False):
    st.subheader("Layer 1 + 2 — Source & Structure/Store")
    if ingestion_ok:
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
    else:
        st.error(f"Ingestion failed: {ingestion_error}")

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
            st.caption("Re-checked on every query and every message read — never cached per document.")
    else:
        st.info("Governance checks run once ingestion succeeds.")

    st.divider()
    st.subheader("Layer 4 raw test harness (debug retrieval directly, without the chat layer)")
    if ingestion_ok:
        test_role = st.selectbox("Test as role:", list(ROLE_ACCESS.keys()), key="l4_role")
        test_question = st.text_input("Test question:", "What's our pipeline for enterprise DSOs?", key="l4_question")
        if st.button("Run retrieval", key="l4_run"):
            try:
                result = retrieve(test_question, test_role)
                st.write(f"Allowed tiers: {result['allowed_tiers']}")
                if result["blocked_tables"]:
                    st.warning(f"Blocked by access tier: {result['blocked_tables']}")
                st.text_area("Assembled context (what the LLM would see):", result["context_text"], height=300)
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
        ("Layer 5 — Act: Threads/projects (message-level access)", ingestion_ok),
        ("Layer 5 — Act: Build API demo", False),
        ("Layer 5 — Act: No-code dashboard builder", False),
        ("Layer 5 — Act: Proactive digest", False),
    ]
    for label, done in layers:
        icon = "✅" if done else "⬜"
        st.markdown(f"{icon} {label}")

if not ingestion_ok:
    st.stop()

if "selected_thread_id" not in st.session_state:
    st.session_state.selected_thread_id = None

with st.sidebar:
    st.markdown("### 🧠 Company Brain")

    user_ids = list(DEMO_USERS.keys())
    current_user_id = st.selectbox(
        "You are:",
        user_ids,
        format_func=lambda uid: f"{DEMO_USERS[uid]['name']} ({DEMO_USERS[uid]['role']})",
        key="current_user_id",
    )
    current_role = DEMO_USERS[current_user_id]["role"]

    st.divider()

    projects = list_projects_for_user(current_user_id)
    visible_threads = list_visible_threads(current_user_id)

    threads_by_project = {}
    ungrouped_threads = []
    for t in visible_threads:
        pid = t.get("project_id")
        if pid:
            threads_by_project.setdefault(pid, []).append(t)
        else:
            ungrouped_threads.append(t)

    def _thread_button(t, key_prefix):
        active = st.session_state.selected_thread_id == t["thread_id"]
        label = ("● " if active else "") + t["title"]
        if st.button(label, key=f"{key_prefix}_{t['thread_id']}", use_container_width=True):
            st.session_state.selected_thread_id = t["thread_id"]
            st.rerun()

    if projects:
        st.markdown("**Projects**")
        for proj in projects:
            proj_threads = threads_by_project.get(proj["project_id"], [])
            with st.expander(f"📁 {proj['title']}", expanded=True):
                if not proj_threads:
                    st.caption("No chats yet.")
                for t in proj_threads:
                    _thread_button(t, "pthread")
                new_title = st.text_input(
                    "New chat in this project", key=f"newtitle_{proj['project_id']}",
                    placeholder="Chat title...", label_visibility="collapsed",
                )
                if st.button("+ New chat", key=f"newbtn_{proj['project_id']}", use_container_width=True):
                    if new_title.strip():
                        new_id = create_thread(new_title.strip(), current_user_id, project_id=proj["project_id"])
                        st.session_state.selected_thread_id = new_id
                        st.rerun()
                    else:
                        st.warning("Give the chat a title first.")

    st.markdown("**Chats**")
    if not ungrouped_threads:
        st.caption("No chats outside a project yet.")
    for t in ungrouped_threads:
        _thread_button(t, "uthread")

    new_chat_title = st.text_input(
        "New chat title", key="new_chat_title", placeholder="e.g. Payer coverage questions",
        label_visibility="collapsed",
    )
    if st.button("+ New chat", key="new_chat_btn", use_container_width=True):
        if new_chat_title.strip():
            new_id = create_thread(new_chat_title.strip(), current_user_id, project_id=None)
            st.session_state.selected_thread_id = new_id
            st.rerun()
        else:
            st.warning("Give the chat a title first.")

    st.divider()
    new_proj_title = st.text_input(
        "New project name", key="new_proj_title", placeholder="e.g. Q4 Renewals",
        label_visibility="collapsed",
    )
    if st.button("+ New project", key="new_proj_btn", use_container_width=True):
        if new_proj_title.strip():
            create_project(new_proj_title.strip(), current_user_id)
            st.rerun()
        else:
            st.warning("Give the project a name first.")

selected_thread_id = st.session_state.selected_thread_id

if selected_thread_id is None:
    st.title("🧠 Needletail Company Brain")
    st.caption("Prototype — GTM + Human-in-the-loop Operations pilot")
    st.markdown(
        "Answers only from the retrieved knowledge base — cited, "
        "freshness-dated, and honest when it doesn't know something.\n\n"
        "Pick a chat from the sidebar, or start a new one to begin."
    )
else:
    thread = get_thread(selected_thread_id, current_user_id)
    if thread is None:
        st.warning("This chat isn't accessible to you. Pick another from the sidebar.")
        st.session_state.selected_thread_id = None
        st.stop()

    shared_with = get_shared_with(selected_thread_id)

    st.subheader(thread["title"])
    st.caption(f"Created by {DEMO_USERS[thread['created_by']]['name']}")
    if thread["required_tiers"]:
        st.caption(
            f"This chat has touched: {', '.join(thread['required_tiers'])} — "
            f"you'll see a 🔒 marker on any response you're not currently cleared to view."
        )

    other_users = [u for u in list(DEMO_USERS.keys()) if u != current_user_id]
    default_shares = [u for u in shared_with if u in other_users]
    # Keyed by (thread, viewer) -- NOT just the thread. Switching "You are:"
    # stays in the same browser session, so a key scoped to the thread
    # alone would let one viewer's stored selection get silently reused
    # (and reset, since the options list excludes whoever's currently
    # viewing) by the next simulated person, misreading their filtered
    # leftover state as an intentional revoke. This is what caused Jamie
    # to get revoked simply from Priya opening the share box.
    share_targets = st.multiselect(
        "Share this chat with:",
        options=other_users,
        default=default_shares,
        format_func=lambda uid: f"{DEMO_USERS[uid]['name']} ({DEMO_USERS[uid]['role']})",
        key=f"share_{selected_thread_id}_{current_user_id}",
    )

    # Sync BOTH directions against the widget's current value: add anyone
    # newly checked, revoke anyone newly unchecked. `current_user_id` is
    # never in `other_users`, so it can never appear in either diff below --
    # this is what stops the revoke branch from deleting the current
    # viewer's own access on every rerun (see revoke_share's docstring).
    target_set = set(share_targets)
    shared_set = set(shared_with)
    for uid in target_set - shared_set:
        share_thread(selected_thread_id, uid)
    for uid in shared_set - target_set:
        if uid != current_user_id:
            revoke_share(selected_thread_id, uid)

    for msg in get_messages(selected_thread_id, current_user_id):
        with st.chat_message(msg["role"]):
            meta = msg.get("meta") or {}
            if meta.get("redacted"):
                st.info(msg["content"], icon="🔒")
            else:
                st.markdown(msg["content"])
                if msg["role"] == "assistant" and meta:
                    badge = "🔴 declined — no KB coverage" if meta.get("declined") else f"🟢 confidence ~{meta.get('confidence', 0):.2f}"
                    st.caption(f"{badge} · topic tag: `{meta.get('topic_tag')}`")
                    if meta.get("query_rewritten") and meta.get("search_question"):
                        st.caption(f"🔎 Searched as: \"{meta['search_question']}\"")
                    _render_sources(meta.get("retrieval", {}))

    user_question = st.chat_input("Ask something...", key=f"input_{selected_thread_id}")

    if user_question:
        append_message(selected_thread_id, "user", user_question)
        with st.chat_message("user"):
            st.markdown(user_question)

        history_for_prompt = get_messages(selected_thread_id, current_user_id)[:-1]

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
