"""Text chunking for RAG ingestion.

Pure functions only — no FastAPI/pydantic/AWS dependencies — so this module
can be unit-tested in isolation and reused by any ingestion pipeline.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import spacy
from docx import Document
from spacy.language import Language

__all__ = ["Chunk", "chunk_text", "extract_text_from_file"]

DEFAULT_TARGET_TOKENS = 400
DEFAULT_OVERLAP_RATIO = 0.15

_HEADING_MAX_WORDS = 10
_HEADING_MAX_CHARS = 80
_HEADING_TERMINAL_PUNCTUATION = (".", "!", "?", ",", ";", ":")


@dataclass
class Chunk:
    chunk_id: int
    text: str
    char_start: int
    char_end: int
    section_heading: Optional[str]


@dataclass
class _SentenceSpan:
    text: str
    char_start: int
    char_end: int
    section_heading: Optional[str]
    token_count: int


@lru_cache(maxsize=1)
def _get_nlp() -> Language:
    nlp = spacy.blank("en")
    nlp.add_pipe("sentencizer")
    return nlp


def _is_heading_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return True
    if stripped.endswith(_HEADING_TERMINAL_PUNCTUATION):
        return False

    words = stripped.split()
    if not words or len(words) > _HEADING_MAX_WORDS or len(stripped) > _HEADING_MAX_CHARS:
        return False

    if stripped.isupper():
        return True

    capitalized = sum(1 for word in words if word[:1].isupper())
    return capitalized / len(words) >= 0.7


def _clean_heading_text(line: str) -> str:
    return line.strip().lstrip("#").strip()


def _iter_lines_with_offsets(text: str) -> list[tuple[int, int, str]]:
    """(abs_start, abs_end, content) for each line, content excludes the line terminator."""
    lines = []
    cursor = 0
    for raw_line in text.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        lines.append((cursor, cursor + len(content), content))
        cursor += len(raw_line)
    return lines


def _extract_sentences(raw_text: str) -> list[_SentenceSpan]:
    """Walk raw_text line by line, splitting into spaCy sentences.

    Heading lines are detected per-line (not just at paragraph starts) so a
    heading immediately followed by body text, with no blank line between
    them, is still recognized — real extracted documents aren't always
    cleanly spaced.
    """
    nlp = _get_nlp()
    sentences: list[_SentenceSpan] = []
    current_heading: Optional[str] = None
    run_start: Optional[int] = None
    run_end = 0

    def flush_run() -> None:
        nonlocal run_start
        if run_start is None:
            return
        body_text = raw_text[run_start:run_end]
        if body_text.strip():
            doc = nlp(body_text)
            for sent in doc.sents:
                leading_ws = len(sent.text) - len(sent.text.lstrip())
                trailing_ws = len(sent.text) - len(sent.text.rstrip())
                text = sent.text.strip()
                if not text:
                    continue
                sentences.append(
                    _SentenceSpan(
                        text=text,
                        char_start=run_start + sent.start_char + leading_ws,
                        char_end=run_start + sent.end_char - trailing_ws,
                        section_heading=current_heading,
                        token_count=len(sent),
                    )
                )
        run_start = None

    for start, end, content in _iter_lines_with_offsets(raw_text):
        if not content.strip():
            flush_run()
        elif _is_heading_line(content):
            flush_run()
            current_heading = _clean_heading_text(content)
        else:
            if run_start is None:
                run_start = start
            run_end = end

    flush_run()
    return sentences


def _group_sentences_into_chunks(
    sentences: list[_SentenceSpan],
    target_tokens: int,
    overlap_ratio: float,
) -> list[Chunk]:
    if not sentences:
        return []

    min_chunk_tokens = max(1, int(target_tokens * 0.5))
    chunks: list[Chunk] = []
    n = len(sentences)
    i = 0
    chunk_id = 0

    while i < n:
        start_index = i
        current: list[_SentenceSpan] = []
        token_count = 0

        while i < n:
            sent = sentences[i]
            starts_new_section = current and sent.section_heading != current[-1].section_heading
            if starts_new_section and token_count >= min_chunk_tokens:
                break
            if current and token_count + sent.token_count > target_tokens:
                break
            current.append(sent)
            token_count += sent.token_count
            i += 1

        if not current:
            # A single sentence alone exceeds target_tokens — take it rather than loop forever.
            current = [sentences[i]]
            token_count = sentences[i].token_count
            i += 1

        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                text=" ".join(s.text for s in current),
                char_start=current[0].char_start,
                char_end=current[-1].char_end,
                section_heading=current[0].section_heading,
            )
        )
        chunk_id += 1

        if i >= n:
            break

        overlap_target = round(token_count * overlap_ratio)
        overlap_tokens = 0
        step_back = 0
        for sent in reversed(current):
            if overlap_tokens >= overlap_target:
                break
            overlap_tokens += sent.token_count
            step_back += 1
        i = max(start_index + 1, i - step_back)

    return chunks


def chunk_text(
    raw_text: str,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> list[Chunk]:
    """Split raw extracted text into overlapping, section-aware chunks.

    Sentence boundaries come from spaCy's rule-based sentencizer; `token_count`
    per sentence is spaCy's token count, used as a lightweight proxy for an
    LLM tokenizer (good enough for chunk sizing, not exact).
    """
    sentences = _extract_sentences(raw_text)
    return _group_sentences_into_chunks(sentences, target_tokens, overlap_ratio)


def _extract_pdf_text(file_path: str | Path) -> str:
    lines: list[str] = []
    doc = fitz.open(file_path)
    try:
        for page in doc:
            page_dict = page.get_text("dict")
            sizes = [
                span["size"]
                for block in page_dict.get("blocks", [])
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ]
            if not sizes:
                continue
            body_size = statistics.median(sizes)

            for block in page_dict.get("blocks", []):
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue
                    line_text = "".join(span["text"] for span in spans).strip()
                    if not line_text:
                        continue
                    max_size = max(span["size"] for span in spans)
                    is_bold = any(span.get("flags", 0) & 2**4 for span in spans)
                    if max_size >= body_size * 1.15 or is_bold:
                        lines.append(f"# {line_text}")
                        lines.append("")
                    else:
                        lines.append(line_text)
            lines.append("")
    finally:
        doc.close()
    return "\n".join(lines)


def _extract_docx_text(file_path: str | Path) -> str:
    document = Document(file_path)
    lines: list[str] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            lines.append("")
            continue

        style_name = para.style.name if para.style else ""
        is_heading = style_name and (style_name.lower().startswith("heading") or style_name.lower() == "title")
        if is_heading:
            level_match = re.search(r"(\d+)", style_name)
            level = int(level_match.group(1)) if level_match else 1
            prefix = "#" * max(1, min(level, 6))
            lines.append(f"{prefix} {text}")
            lines.append("")
        else:
            lines.append(text)
    return "\n".join(lines)


def extract_text_from_file(file_path: str | Path, file_type: str) -> str:
    """Extract raw text from a PDF or DOCX file, marking detected headings with '# '."""
    normalized_type = file_type.strip().lower().lstrip(".")
    if normalized_type == "pdf":
        return _extract_pdf_text(file_path)
    if normalized_type == "docx":
        return _extract_docx_text(file_path)
    raise ValueError(f"Unsupported file_type '{file_type}'. Expected 'pdf' or 'docx'.")
