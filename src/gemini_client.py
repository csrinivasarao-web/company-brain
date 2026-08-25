"""
Thin wrapper around the Gemini API. Centralizing this here means every other
module (ingestion, chat assistant, digest) calls the model the same way, with
the same model names, in one place to update if Google changes them again.
"""
import streamlit as st
from google import genai
from google.genai import types

# Model choices, as of building this (Aug 2026):
# - gemini-3.5-flash-lite: GA, cheap, good enough for summarization and
#   grounded chat answers. Deliberately NOT gemini-2.5-flash, which Google
#   has scheduled for shutdown in October 2026.
# - gemini-embedding-001: GA, stable embedding model.
GENERATION_MODEL = "gemini-3.5-flash-lite"
EMBEDDING_MODEL = "gemini-embedding-001"


@st.cache_resource
def get_client():
    api_key = st.secrets["GEMINI_API_KEY"]
    return genai.Client(api_key=api_key)


def generate_text(prompt: str, system_instruction: str | None = None) -> str:
    client = get_client()
    config = types.GenerateContentConfig(system_instruction=system_instruction) if system_instruction else None
    response = client.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt,
        config=config,
    )
    return response.text


def embed_texts(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Embed a batch of texts. task_type should be RETRIEVAL_DOCUMENT when
    embedding knowledge-base content, and RETRIEVAL_QUERY when embedding an
    incoming question — Gemini's embedding model treats these differently."""
    client = get_client()
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return [e.values for e in response.embeddings]
