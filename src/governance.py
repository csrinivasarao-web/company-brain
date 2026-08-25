"""
Layer 3 - Governance & provisioning. Four things, per the design doc:
  1. RBAC - which access tiers a role can see (the enforcement primitive
     Layer 4 will call on every query, before search runs).
  2. Freshness monitor - flags documents past a staleness threshold.
  3. Gap detection - a rule-based trigger on repeated low-confidence queries.
  4. Approval tiering - classifies how a NEW submission should be handled
     (auto-publish vs needs sign-off). Not wired to a UI yet since there's no
     submission flow until a later task, but built now so Layer 4/5 can call
     it without a redesign later.
"""
from datetime import datetime
import pandas as pd
from src.sql_store import query

# --- 1. RBAC -----------------------------------------------------------

# Every tier a role does NOT hold is invisible to that role, full stop -
# this is checked fresh on every call, never cached per-user, so a saved
# dashboard or an old chat thread re-checks the CURRENT viewer every time.
ROLE_ACCESS = {
    "GTM Rep":         {"public_internal", "team_restricted_gtm"},
    "GTM Leadership":  {"public_internal", "team_restricted_gtm", "leadership_only"},
    "Ops Specialist":  {"public_internal", "team_restricted_ops"},
    "Ops Leadership":  {"public_internal", "team_restricted_ops", "leadership_only"},
}


def get_allowed_tiers(role: str) -> set:
    if role not in ROLE_ACCESS:
        raise ValueError(f"Unknown role: {role}. Must be one of {list(ROLE_ACCESS)}")
    return ROLE_ACCESS[role]


# --- 2. Freshness monitor ------------------------------------------------

STALE_THRESHOLD_DAYS = 90


def get_stale_documents(as_of: str = None) -> pd.DataFrame:
    as_of_ts = pd.Timestamp(as_of) if as_of else pd.Timestamp.now().normalize()
    df = query("SELECT doc_id, title, team, owner, access_tier, last_verified FROM doc_metadata")
    df["last_verified"] = pd.to_datetime(df["last_verified"])
    df["days_since_verified"] = (as_of_ts - df["last_verified"]).dt.days
    return df[df["days_since_verified"] > STALE_THRESHOLD_DAYS].sort_values(
        "days_since_verified", ascending=False
    )


# --- 3. Gap detection -----------------------------------------------------

LOW_CONFIDENCE_THRESHOLD = 0.5
MIN_OCCURRENCES_FOR_GAP = 3


def detect_gaps() -> pd.DataFrame:
    df = query("SELECT * FROM query_log WHERE top_confidence_score < ?".replace("?", str(LOW_CONFIDENCE_THRESHOLD)))
    if df.empty:
        return df

    grouped = (
        df.groupby("topic_tag")
        .agg(occurrences=("query_text", "count"), avg_confidence=("top_confidence_score", "mean"))
        .reset_index()
    )
    return grouped[grouped["occurrences"] >= MIN_OCCURRENCES_FOR_GAP].sort_values(
        "occurrences", ascending=False
    )


# --- 4. Approval tiering (for future submissions) -------------------------

HIGH_STAKES_TIERS = {"leadership_only"}
HIGH_STAKES_KEYWORDS = {"pricing", "compliance", "comp", "delete", "deletion"}


def classify_submission(access_tier: str, topic_tags: str = "") -> str:
    """Returns 'auto_publish' or 'requires_approval'. High-stakes tiers or
    topics always require sign-off; everything else auto-publishes with the
    owner notified after the fact, per the friction/adoption tradeoff in the
    design doc."""
    tags = {t.strip().lower() for t in topic_tags.split(";")} if topic_tags else set()
    if access_tier in HIGH_STAKES_TIERS or tags & HIGH_STAKES_KEYWORDS:
        return "requires_approval"
    return "auto_publish"
