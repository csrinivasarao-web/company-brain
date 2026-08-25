"""
Layer 2 - the vector store side. Uses an in-memory Chroma client rather than
a persistent one on disk: Streamlit Community Cloud containers are ephemeral
anyway, so we rebuild the store from the committed mock data on every app
startup (see ingest.py) instead of relying on a file surviving between
deploys. Embeddings are computed by us via Gemini (see gemini_client.py) and
passed in directly, so Chroma's own default embedding model is never used.
"""
import chromadb

_COLLECTION_NAME = "company_brain"


def get_collection():
    client = chromadb.Client()
    try:
        return client.get_collection(_COLLECTION_NAME)
    except Exception:
        return client.create_collection(_COLLECTION_NAME)


def add_chunks(ids, embeddings, documents, metadatas):
    collection = get_collection()
    collection.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)


def search(query_embedding, allowed_tiers, n_results=5):
    """Access control happens HERE, inside the query itself, via Chroma's
    `where` filter -- not by fetching everything and dropping results
    afterward. A chunk outside `allowed_tiers` is never returned, full stop.
    """
    collection = get_collection()
    return collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where={"access_tier": {"$in": list(allowed_tiers)}},
    )


def count() -> int:
    return get_collection().count()
