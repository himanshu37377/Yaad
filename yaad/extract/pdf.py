from pathlib import Path
import fitz

MAX_RENDER_PIXELS = 40_000_000


def extract_pdf(path: Path) -> tuple[list[tuple[str, dict]], list[int]]:
    units, scanned = [], []
    with fitz.open(path) as doc:
        if doc.needs_pass or doc.is_encrypted:
            raise ValueError("PDF is encrypted or password-protected")
        for page_index, page in enumerate(doc):
            text = page.get_text("text").strip()
            page_no = page_index + 1
            if text:
                units.append((text, {"type": "pdf_page", "page": page_no, "quote": "", "bbox": None}))
            elif page.get_images(full=True):
                scanned.append(page_index)
            else:
                # Blank/vector-only page: no text, no raster to OCR.
                # Surface it as a scanned candidate is wrong; record an
                # empty unit so the file is honestly partial upstream.
                scanned.append(page_index)
    return units, scanned


def render_page(path: str, page_number: int, scale: float = 2.0) -> bytes:
    with fitz.open(path) as doc:
        if page_number < 1 or page_number > doc.page_count:
            raise ValueError(f"page {page_number} out of range 1..{doc.page_count}")
        page = doc.load_page(page_number - 1)
        # Cap the raster size: a huge page at scale 2 would otherwise
        # allocate an unbounded pixmap.
        rect = page.rect
        pixels = max(1.0, rect.width * scale) * max(1.0, rect.height * scale)
        if pixels > MAX_RENDER_PIXELS:
            scale = max(0.1, scale * (MAX_RENDER_PIXELS / pixels) ** 0.5)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pix.tobytes("png")
