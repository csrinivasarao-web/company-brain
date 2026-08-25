import streamlit as st

st.set_page_config(page_title="Needletail Company Brain", page_icon="🧠", layout="centered")

st.title("🧠 Needletail Company Brain")
st.caption("Prototype — GTM + Human-in-the-loop Operations pilot")

st.markdown(
    """
This is the live deployment of the Company Brain prototype.
It's being built layer by layer — this page will turn into the
real chat assistant as each piece is wired up.
"""
)

st.divider()
st.subheader("Build status")

layers = [
    ("Repo scaffold & deployment pipeline", True),
    ("Layer 1 — Source (mock data set)", False),
    ("Layer 2 — Structure & store (Vector DB + SQL + metadata)", False),
    ("Layer 3 — Governance (approval, RBAC, freshness, gap detection)", False),
    ("Layer 4 — Query (scoped retrieval)", False),
    ("Layer 5 — Act: Chat assistant", False),
    ("Layer 5 — Act: Build API demo", False),
    ("Layer 5 — Act: Proactive digest", False),
]

for label, done in layers:
    icon = "✅" if done else "⬜"
    st.markdown(f"{icon} {label}")

st.divider()
st.info(
    "If you're seeing this page live at your streamlit.app URL, "
    "the deployment pipeline works end to end — every future update "
    "just needs a push to GitHub.",
    icon="🚀",
)
