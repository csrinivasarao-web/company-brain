"""
Layer 1 processing step for call transcripts. A raw transcript is not stored
in the knowledge base directly (see design doc, Layer 1) - it's summarized
into structured nuggets first: objections raised, open questions/feature
requests, competitors mentioned, and follow-ups owed. This is what actually
gets embedded, not the raw transcript text.
"""
from src.gemini_client import generate_text

SUMMARY_PROMPT = """You are extracting structured knowledge from a sales call transcript for an internal company knowledge base. Read the transcript and produce a concise structured summary with these exact sections:

OBJECTIONS RAISED:
OPEN QUESTIONS / FOLLOW-UPS OWED:
COMPETITORS MENTIONED:
KEY FACTS DISCUSSED:

Only include information actually present in the transcript. If a section has nothing, write "None noted." Do not add commentary or advice - just extract what was said.

TRANSCRIPT:
{transcript}
"""


def summarize_transcript(transcript_text: str) -> str:
    prompt = SUMMARY_PROMPT.format(transcript=transcript_text)
    return generate_text(prompt)
