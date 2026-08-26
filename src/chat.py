"""
Layer 5 - Act: Chat assistant. The first real caller of Layer 4's retrieve().

Enforces, at the prompt-template level (not left to model discretion):
  - answers only from retrieved context, never the model's own training
    knowledge or the open internet
  - every claim is cited back to a source title + freshness date
  - if two retrieved sources disagree, the conflict is surfaced explicitly
    rather than silently resolved
  - if nothing relevant was retrieved, says so rather than guessing -- and
    if NOTHING at all came back (not even a blocked-access note), the LLM
    is skipped entirely in favor of a deterministic decline, rather than
    trusting it to decline correctly every single time

Also logs every question to query_log (topic tag + an approximate
confidence score) so Layer 3's gap detection has real, live data to work
from -- not just the seeded rows from data/mock/seed/query_log_seed.csv.

Conversation memory: the model is given the last few turns of the CURRENT
tab's session (passed in by app.py from st.session_state.chat_history) so
follow-up questions ("what about their contract terms?") read naturally.
This is deliberately scoped smaller than the "Threads/projects" task on the
roadmap -- there's no new storage here, nothing persists past this browser
tab, and no access-tier question arises, since nothing is shared with
anyone else. Threads/projects is still the right place for persistence
across sessions and people.

Known limitation, worth stating plainly rather than glossing over: this
fix makes the ANSWER context-aware, not the RETRIEVAL. If a follow-up is
elliptical enough that its own wording doesn't retrieve the right chunks on
its own (e.g. "what about their SLA?" with no company name in it), Layer 4
may still come back empty even though a human would know what "their"
refers to from the prior turn. A full fix would rewrite the query using
history before embedding it -- that's a reasonable next increment if this
turns out to matter in practice, but it's a separate, larger change from
what was asked for here, so it's flagged rather than silently bundled in.
"""
import re
from datetime import datetime

from src.retrieval import retrieve
from src.gemini_client import generate_text
from src.sql_store import get_connection

SYSTEM_PROMPT = """You are the Needletail Company Brain, an internal assistant.

Rules, in order of importance:
1. Answer ONLY using the context provided below. Never use your own general
   knowledge, training data, or anything outside this context -- even if
   you happen to know the answer. This is a hard rule, not a preference.
2. Every factual claim must be attributed to a specific source from the
   context, by its title -- e.g. "(Pricing & Packaging FY26, last verified
   2026-08-12)". If the context includes SQL/table data, cite it by table
   name.
3. If two sources in the context disagree with each other, do not silently
   pick one. Say explicitly that they disagree, state both figures or
   claims and their sources, and let the person decide.
4. If the context does not contain enough information to answer the
   question -- including if it only contains a note that relevant data was
   blocked by access control -- say so plainly. Do not guess, estimate, or
   fill the gap with outside knowledge. If something was blocked by access
   control, say that plainly too, without revealing its contents.
5. Be concise. This is an internal tool, not a marketing document.
6. You may be shown a short excerpt of the recent conversation above the
   Context section. Use it ONLY to understand what the current question is
   referring to (e.g. a pronoun, "that account", a follow-up) -- never as a
   source of facts. Every factual claim must still come only from the
   Context section for THIS turn, even if something relevant was stated
   earlier in the conversation.
"""

# --- Query rewriting for retrieval (NOT the same thing as answer memory) ---
# The fix added earlier only gave the ANSWERING step conversation memory --
# retrieval itself still searched on the raw text of a follow-up like
# "Explain more", which carries no topical signal on its own and returns
# whatever happens to be nearby in embedding space. This step resolves a
# follow-up into a standalone question BEFORE it's embedded, so retrieval
# actually searches for what's being asked, not the literal follow-up
# phrasing. One extra small LLM call per turn with history -- skipped
# entirely when there's no history yet, to avoid the cost on a first turn.
REWRITE_SYSTEM_PROMPT = """Rewrite the person's LATEST message into a fully
self-contained question or request, using ONLY the recent conversation to
resolve references (pronouns, "it", "them", "explain more", "what about
that", etc.).

Rules:
- Output ONLY the rewritten question/request. No preamble, no quotes, no
  explanation of what you did.
- If the latest message is already self-contained and doesn't depend on
  prior context, output it unchanged.
- Do not answer the question. Do not add information or specifics that
  aren't implied by the conversation itself.
"""

# --- Confidence scoring --------------------------------------------------
# Chroma's distance metric here is L2 over Gemini's embedding vectors.
# There's no ground-truth "confidence" without a labeled eval set, so this
# is a documented approximation: closer distance -> higher score, on a
# scale set by inspection rather than a calibrated model. It's good enough
# to drive gap detection's *relative* "this topic keeps coming back weak"
# signal -- it is not a claim of statistical precision, and that's worth
# saying out loud rather than presenting a fake-precise number.
DISTANCE_SCALE = 1.5


def _score_confidence(retrieval_result: dict) -> float:
    hits = retrieval_result["vector_hits"]
    sql_hit = bool(retrieval_result["sql_results"])
    if not hits and not sql_hit:
        return 0.0
    if not hits and sql_hit:
        # A keyword-routed SQL match is a deterministic hit, not a fuzzy
        # similarity score, so it doesn't have a "distance" to convert.
        return 0.8
    best_distance = min(h["distance"] for h in hits)
    score = 1 - (best_distance / DISTANCE_SCALE)
    return max(0.0, min(1.0, score))


# --- Topic tagging, for gap detection ------------------------------------
# Known mappings first, so tags stay consistent with the seeded gap example
# (curve_dental_integration). Falls back to a generic slug of the question
# so genuinely new topics still accumulate a consistent tag across repeated
# questions about the same thing, instead of a fresh random tag every time.
KNOWN_TOPIC_KEYWORDS = {
    "curve_dental_integration": ["curve dental"],
    "payer_coverage": ["payer", "coverage", "delta dental", "cigna", "metlife", "aetna", "guardian"],
    "pipeline_pricing": ["pipeline", "deal", "arr", "revenue", "pricing", "packaging"],
    "onboarding": ["onboard", "kickoff", "carestack", "denticon"],
    "claims_roadmap": ["claims", "payment posting"],
}

_STOPWORDS = {"the", "a", "an", "is", "are", "do", "we", "our", "of", "for",
              "to", "how", "many", "what", "on", "in", "does"}


def _tag_topic(question: str) -> str:
    q = question.lower()
    for tag, keywords in KNOWN_TOPIC_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return tag
    words = re.findall(r"[a-z0-9]+", q)
    slug_words = [w for w in words if w not in _STOPWORDS][:4]
    return "_".join(slug_words) if slug_words else "general"


def _ensure_log_columns(conn):
    """The seeded query_log table (from query_log_seed.csv) is only
    guaranteed to have query_text, topic_tag, top_confidence_score --
    that's the contract governance.py's detect_gaps() relies on. role and
    logged_at are enrichments this layer adds; add them if missing rather
    than assuming they're already there."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(query_log)").fetchall()]
    if "role" not in cols:
        conn.execute("ALTER TABLE query_log ADD COLUMN role TEXT")
    if "logged_at" not in cols:
        conn.execute("ALTER TABLE query_log ADD COLUMN logged_at TEXT")
    conn.commit()


def _log_query(question: str, topic_tag: str, confidence: float, role: str):
    conn = get_connection()
    _ensure_log_columns(conn)
    conn.execute(
        "INSERT INTO query_log (query_text, topic_tag, top_confidence_score, role, logged_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (question, topic_tag, confidence, role, datetime.utcnow().isoformat()),
    )
    conn.commit()


def _format_history(history: list, max_turns: int = 3) -> str:
    """history is the app's st.session_state.chat_history, EXCLUDING the
    current question (app.py passes history[:-1]). Keeps only the last
    max_turns user+assistant pairs so the prompt doesn't grow unbounded as
    a conversation gets long."""
    if not history:
        return ""
    recent = history[-(max_turns * 2):]
    lines = []
    for msg in recent:
        speaker = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"{speaker}: {msg['content']}")
    return "\n".join(lines)


def _rewrite_query_with_history(question: str, history_text: str) -> str:
    """Resolves a follow-up like "Explain more" into a standalone question
    BEFORE retrieval runs. Falls back to the raw question on any failure --
    a broken rewrite should degrade to the old (imperfect) behavior, never
    crash the whole turn."""
    if not history_text:
        return question
    prompt = f"Recent conversation:\n{history_text}\n\nLatest message: {question}\n\nRewritten standalone version:"
    try:
        rewritten = generate_text(prompt, system_instruction=REWRITE_SYSTEM_PROMPT).strip()
        return rewritten if rewritten else question
    except Exception:
        return question


def get_answer(question: str, role: str, history: list = None) -> dict:
    """Returns:
      {
        "answer": str,
        "retrieval": dict,     # the full Layer 4 result, for citation/debug display
        "confidence": float,
        "topic_tag": str,
        "declined": bool,       # True if the LLM was skipped (nothing retrieved)
        "search_question": str, # what was actually searched for (may differ from `question`)
        "query_rewritten": bool,
      }

    `history` should be the CURRENT tab's session_state.chat_history with
    the current question already removed (app.py passes history[:-1]).
    Lives only in this browser tab's memory -- see module docstring for why
    this is intentionally smaller than Threads/projects.
    """
    history_text = _format_history(history)
    search_question = _rewrite_query_with_history(question, history_text)
    query_rewritten = search_question != question

    retrieval_result = retrieve(search_question, role)
    confidence = _score_confidence(retrieval_result)
    topic_tag = _tag_topic(search_question)

    if not retrieval_result["context_text"]:
        answer = (
            "I don't have anything in the knowledge base that covers this. "
            "This looks like a real gap rather than an access issue -- it's "
            "been logged, and three or more questions like this on the same "
            "topic will surface it to that topic's owner."
        )
        declined = True
    else:
        history_block = (
            f"Recent conversation so far (for understanding what this question "
            f"refers to only -- NOT a source of facts):\n{history_text}\n\n"
            if history_text else ""
        )
        user_prompt = (
            f"{history_block}"
            f"Context:\n{retrieval_result['context_text']}\n\n"
            f"Question: {search_question}"
        )
        answer = generate_text(user_prompt, system_instruction=SYSTEM_PROMPT)
        declined = False

    try:
        _log_query(question, topic_tag, confidence, role)
    except Exception:
        # Logging failure should never break the chat response itself.
        pass

    return {
        "answer": answer,
        "retrieval": retrieval_result,
        "confidence": confidence,
        "topic_tag": topic_tag,
        "declined": declined,
        "search_question": search_question,
        "query_rewritten": query_rewritten,
    }
