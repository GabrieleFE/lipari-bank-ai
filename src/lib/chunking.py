"""Chunking recursive fixed-size con overlap e taglio a fine frase.

Regole del "guardrail": un chunk non viene tagliato prima di meta'
della lunghezza prevista (chunk_size // 2), altrimenti si produce
un frammento minuscolo che mutila il contesto.
"""

from __future__ import annotations


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into chunks of ~chunk_size chars with overlap.

    Cerca di rompere su un separatore di frase (. ? ! \\n\\n) vicino a fine
    chunk; se il piu' vicino sta prima di chunk_size // 2, taglia fisso.
    """
    if not text.strip():
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            for sep in [". ", ".\n", "? ", "! ", "\n\n"]:
                idx = text.rfind(sep, start, end)
                if idx > start + chunk_size // 2:  # almeno mezzo chunk pieno
                    end = idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks
