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
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        doc = Document(path)
        result = []
        offset = 0
        total = 0
        truncated = False

        def _add(value: str) -> bool:
            nonlocal offset, total, truncated
            total += len(value.encode("utf-8")) + 1
            if total > MAX_TEXT_BYTES:
                truncated = True
                return False
            result.append((value, {"type": "text_span", "section": path.stem, "start": offset, "end": offset + len(value)}))
            offset += len(value) + 1
            return True

        # Walk body children in document order so table cell text (fees,
        # deadlines, rosters) is indexed alongside paragraphs instead of
        # being silently dropped.
        for child in doc.element.body.iterchildren():
            if child.tag.endswith("}p"):
                value = Paragraph(child, doc).text.strip()
                if value and not _add(value):
                    break
            elif child.tag.endswith("}tbl"):
                table = Table(child, doc)
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    value = " | ".join(cell for cell in cells if cell)
                    if value and not _add(value):
                        break
                if truncated:
                    break
        return result, truncated
    return [], False
