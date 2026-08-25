"""
Chunking for Layer 1 -> Layer 2. Deliberately simple: our mock documents are
short (SOPs, playbooks, notes), so paragraph-aware chunking up to a max size
is enough to get sensible retrieval units without pulling in a heavier
chunking library for a prototype this size.
"""


def chunk_text(text: str, max_chars: int = 700) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks = []
    current = ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current.strip())
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para

    if current.strip():
        chunks.append(current.strip())

    # Guard against a single oversized paragraph (e.g. a dense table row)
    final = []
    for c in chunks:
        if len(c) <= max_chars * 1.5:
            final.append(c)
        else:
            for i in range(0, len(c), max_chars):
                final.append(c[i:i + max_chars])
    return final
