from app.services.chunking_service import chunk_text


def test_single_short_paragraph_produces_one_chunk():
    raw_text = "This is the first sentence. This is the second sentence. This is the third one."

    chunks = chunk_text(raw_text)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_id == 0
    assert chunk.section_heading is None
    assert chunk.text == raw_text
    assert raw_text[chunk.char_start : chunk.char_end] == chunk.text


def test_markdown_headings_are_detected_and_isolate_sections():
    raw_text = (
        "# Introduction\n"
        "\n"
        "This project describes an enterprise RAG pipeline.\n"
        "\n"
        "# Architecture\n"
        "\n"
        "The system is composed of a FastAPI backend and a Next.js frontend.\n"
    )

    chunks = chunk_text(raw_text, target_tokens=5, overlap_ratio=0.15)

    assert len(chunks) >= 2
    assert chunks[0].section_heading == "Introduction"
    assert "FastAPI" not in chunks[0].text

    architecture_chunks = [c for c in chunks if c.section_heading == "Architecture"]
    assert architecture_chunks
    assert "FastAPI" in architecture_chunks[0].text
    # heading lines are metadata, not chunk content
    assert "Architecture" not in architecture_chunks[0].text


def test_long_text_produces_overlapping_chunks():
    raw_text = " ".join(f"Sentence number {i} talks about topic {i}." for i in range(1, 21))

    chunks = chunk_text(raw_text, target_tokens=30, overlap_ratio=0.2)

    assert len(chunks) > 1
    for previous, current in zip(chunks, chunks[1:]):
        # next chunk starts inside the previous chunk's span -> sentences are shared (overlap)
        assert current.char_start < previous.char_end
        # each chunk still moves the window forward
        assert current.char_end > previous.char_end
