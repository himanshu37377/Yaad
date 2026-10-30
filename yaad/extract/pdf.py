from pathlib import Path
import fitz

def extract_pdf(path: Path) -> tuple[list[tuple[str, dict]], list[int]]:
    doc = fitz.open(path)
    units, scanned = [], []
    for page_index, page in enumerate(doc):
        text = page.get_text("text").strip()
        page_no = page_index + 1
        if text:
            units.append((text, {"type": "pdf_page", "page": page_no, "quote": "", "bbox": None}))
        elif page.get_images(full=True):
            scanned.append(page_index)
    return units, scanned

def render_page(path: str, page_number: int, scale: float = 2.0) -> bytes:
    doc = fitz.open(path)
    page = doc.load_page(page_number - 1)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return pix.tobytes("png")
