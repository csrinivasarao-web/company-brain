"""
Layer 5 - Act: Call Insights tool. A third independent Layer-4-family tool
(same spirit as Pipeline Lookup): built separately from the chat assistant,
reusing existing primitives (governance, sql_store, gemini_client) rather
than duplicating access logic.

Trust boundary: the LLM EXTRACTS facts stated in the transcript into a
fixed schema -- it does not judge, score, or infer trend direction. Counts
(open blocker count, stakeholder count) are computed in Python from the
extracted lists, same "LLM produces facts, code does arithmetic" boundary
as aggregation.py. Trend/"is this improving" judgment is deliberately left
to the human reading the table side-by-side across calls, rather than
having the LLM assert a trend -- asserting improvement from four short
calls is exactly the kind of confident-sounding but ungrounded claim the
whole system is built to avoid.

Access control: transcripts are documents like any other -- gated by the
same access_tier check as everything else, read from doc_metadata. A role
without team_restricted_gtm never sees that a Bluegrass transcript exists,
same principle as NL-to-SQL never showing a schema the role can't access.
"""
import os
import re
import json

from src.governance import get_allowed_tiers
from src.gemini_client import generate_text
from src.sql_store import query

DATA_DIR = "data/mock"

EXTRACTION_SYSTEM_PROMPT = """Extract ONLY facts explicitly stated in this sales call transcript into the JSON schema below. Do not infer, guess, or add anything not directly stated. If a field isn't addressed in the call, use an empty list [] or the string "Not mentioned".

Output ONLY valid JSON, no markdown fences, no explanation. Schema:
{
  "deal_stage": "string - the deal's stage as of this call (e.g. discovery, demo, negotiation, late-stage/approval pending)",
  "buying_commitment": "string - any explicit statement of intent to proceed, quoted or closely paraphrased, or 'Not mentioned'",
  "open_blockers": ["list of strings - unresolved items blocking the deal"],
  "stakeholders": ["list of strings - people/roles explicitly mentioned as involved in the decision"],
  "pain_quantified": "string - any specific number/metric describing the customer's pain (hours, dollars, volume), or 'Not mentioned'",
  "objections_raised": ["list of strings - concerns or objections raised in THIS call"],
  "objections_resolved": ["list of strings - objections that were addressed/resolved in THIS call"],
  "customer_commitments": ["list of strings - things the CUSTOMER agreed to do"],
  "rep_commitments": ["list of strings - things the REP agreed to do"],
  "next_step": "string - the agreed next step, or 'Not mentioned'"
}
"""

_extraction_cache = {}


def _strip_json_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()


def get_visible_transcripts(role: str) -> list:
    """Transcripts this role's tier covers, with account name parsed from
    the title convention "<Call type> Call - <Account name>". Ordered by
    last_verified (call date) so across-call views come back chronological
    for free, without a separate sort step downstream."""
    allowed_tiers = get_allowed_tiers(role)
    df = query("SELECT doc_id, filename, title, access_tier, last_verified FROM doc_metadata WHERE format = 'transcript'")
    df = df[df["access_tier"].isin(allowed_tiers)].sort_values("last_verified")

    out = []
    for _, row in df.iterrows():
        account = row["title"].split(" - ", 1)[-1].strip() if " - " in row["title"] else "Unknown"
        out.append({
            "doc_id": row["doc_id"], "filename": row["filename"], "title": row["title"],
            "account": account, "last_verified": row["last_verified"],
        })
    return out


def _read_transcript(filename: str) -> str:
    with open(os.path.join(DATA_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()


def extract_call_metrics(doc_id: str, filename: str) -> dict:
    """Cached per doc_id for the life of the process -- a transcript's
    content doesn't change, so re-extracting on every rerun would just be
    repeated LLM cost for an identical answer. Returns:
      { "ok": True, **schema_fields }  or  { "ok": False, "error": str, "raw": str }
    """
    if doc_id in _extraction_cache:
        return _extraction_cache[doc_id]

    transcript_text = _read_transcript(filename)
    raw = generate_text(transcript_text, system_instruction=EXTRACTION_SYSTEM_PROMPT)
    cleaned = _strip_json_fences(raw)

    try:
        data = json.loads(cleaned)
        data["ok"] = True
        result = data
    except json.JSONDecodeError:
        result = {"ok": False, "error": "Could not parse extraction as JSON", "raw": raw}

    _extraction_cache[doc_id] = result
    return result


METRIC_LABELS = [
    ("deal_stage", "Deal stage"),
    ("buying_commitment", "Explicit buying commitment"),
    ("open_blockers", "Open blockers"),
    ("stakeholders", "Stakeholders mentioned"),
    ("pain_quantified", "New pain quantified"),
    ("objections_raised", "Objections raised"),
    ("objections_resolved", "Objections resolved"),
    ("customer_commitments", "Customer commitments"),
    ("rep_commitments", "Rep commitments"),
    ("next_step", "Next step"),
]

CROSS_CALL_METRIC_LABELS = [
    ("deal_stage", "Stage progression"),
    ("open_blocker_count", "Open blocker count"),
    ("objections_raised", "Objection recurrence (raised)"),
    ("stakeholder_count", "Stakeholder coverage (count)"),
    ("customer_commitments", "Customer commitments"),
    ("rep_commitments", "Rep commitments"),
    ("pain_quantified", "Pain quantified"),
    ("buying_commitment", "Explicit commitment language"),
]


def format_value(v) -> str:
    if isinstance(v, list):
        return "; ".join(v) if v else "None"
    return v or "Not mentioned"


def build_cross_call_table(extractions: list) -> dict:
    """extractions: list of {title, last_verified, **schema_fields}, already
    in chronological order. Returns {metric_label: [value_per_call, ...]} --
    plain tabulation, no trend judgment computed here; that's left to
    whoever reads the table (see module docstring)."""
    table = {}
    for key, label in CROSS_CALL_METRIC_LABELS:
        row = []
        for e in extractions:
            if key == "open_blocker_count":
                row.append(str(len(e.get("open_blockers", []))))
            elif key == "stakeholder_count":
                row.append(str(len(e.get("stakeholders", []))))
            else:
                row.append(format_value(e.get(key)))
        table[label] = row
    return table
