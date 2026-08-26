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


def get_answer(question: str, role: str) -> dict:
    """Returns:
      {
        "answer": str,
        "retrieval": dict,     # the full Layer 4 result, for citation/debug display
        "confidence": float,
        "topic_tag": str,
        "declined": bool,      # True if the LLM was skipped (nothing retrieved)
      }
    """
    retrieval_result = retrieve(question, role)
    confidence = _score_confidence(retrieval_result)
    topic_tag = _tag_topic(question)

    if not retrieval_result["context_text"]:
        answer = (
            "I don't have anything in the knowledge base that covers this. "
            "This looks like a real gap rather than an access issue -- it's "
            "been logged, and three or more questions like this on the same "
            "topic will surface it to that topic's owner."
        )
        declined = True
    else:
        user_prompt = (
            f"Context:\n{retrieval_result['context_text']}\n\n"
            f"Question: {question}"
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
    }
