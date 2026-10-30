from pathlib import Path

# Largest text payload indexed from one file (~280 units). Larger files are
# truncated loudly (partial + error), never read unbounded into memory.
MAX_TEXT_BYTES = 1_000_000


def _chunk(text: str, section: str) -> list[tuple[str, dict]]:
    return [(text[i:i+3500], {"type": "text_span", "section": section, "start": i, "end": min(i+3500, len(text))}) for i in range(0, len(text), 3000)]


def extract_text(path: Path) -> tuple[list[tuple[str, dict]], bool]:
    """Extract text units plus a truncated flag (never silent truncation)."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        raw = path.read_bytes()
        truncated = len(raw) > MAX_TEXT_BYTES
        text = raw[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
        return _chunk(text, path.stem) or [("", {"type": "text_span", "section": path.stem, "start": 0, "end": 0})], truncated
    if suffix == ".docx":
        from docx import Document
        doc = Document(path)
        result = []
        offset = 0
        total = 0
        truncated = False
        for para in doc.paragraphs:
            value = para.text.strip()
            if not value:
                continue
            total += len(value.encode("utf-8")) + 1
            if total > MAX_TEXT_BYTES:
                truncated = True
                break
            result.append((value, {"type": "text_span", "section": path.stem, "start": offset, "end": offset + len(value)}))
            offset += len(value) + 1
        return result, truncated
    return [], False
