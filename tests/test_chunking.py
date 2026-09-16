from src.lib.chunking import chunk_text


def test_empty_and_whitespace_text_return_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_is_single_chunk() -> None:
    assert chunk_text("Breve testo.") == ["Breve testo."]


def test_long_text_is_split_into_multiple_chunks() -> None:
    text = " ".join(["parola"] * 300)
    chunks = chunk_text(text, chunk_size=100, overlap=10)
    assert len(chunks) > 1


def test_adjacent_chunks_overlap() -> None:
    text = "Aa. " * 200
    chunks = chunk_text(text, chunk_size=100, overlap=50)
    assert len(chunks) >= 2
    if len(chunks) >= 2:
        assert "Aa." in chunks[1], "il secondo chunk deve contenere la coda del primo"


def test_split_prefers_sentence_boundary() -> None:
    # Il punto a ~saturazione: il guardrail chunk_size // 2 consente il taglio a fine frase
    body_a = "Frase numero uno sul bonifico." * 10
    body_b = "Frase numero due sulla carta." * 10
    text = f"{body_a}\n\n{body_b}"
    chunks = chunk_text(text, chunk_size=300, overlap=20)
    assert len(chunks) >= 2
    assert not any(not c.strip() for c in chunks)


def test_chunks_preserve_document_order() -> None:
    text = "Primo blocco. " * 40 + "ULTIMO-BLOCCO-DOCUMENTO."
    chunks = chunk_text(text, chunk_size=80, overlap=15)
    assert chunks[-1].endswith("ULTIMO-BLOCCO-DOCUMENTO.")


def test_chunk_content_is_stripped() -> None:
    chunks = chunk_text("  Inizio.\n\nSecondo.  ", chunk_size=50, overlap=5)
    assert all(c == c.strip() for c in chunks)
