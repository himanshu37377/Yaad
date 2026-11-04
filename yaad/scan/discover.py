import heapq
from pathlib import Path

SUPPORTED = {".pdf", ".txt", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".wav", ".m4a", ".mp3", ".opus", ".ogg"}
KIND = {".pdf": "pdf", ".txt": "text", ".docx": "docx", ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
        ".wav": "audio", ".m4a": "audio", ".mp3": "audio", ".opus": "audio", ".ogg": "audio"}

def discover(folder: Path, limit: int) -> tuple[list[tuple[Path, str]], int]:
    """First `limit` indexable paths in sorted order, plus overflow count.

    Bounded memory (no full listing materialized): only the smallest `limit`
    paths are retained while the rest are counted. Under the limit this is
    exactly the old sorted order.
    """
    seen = 0

    root = folder.resolve()

    def candidates():
        nonlocal seen
        for path in folder.rglob("*"):
            # Hidden entries *inside* the folder are skipped; a hidden
            # component in the folder's own path must not empty the index.
            # Only supported kinds consume the budget: unsupported files
            # must not starve indexable ones beyond the cap. Symlinks
            # resolving outside the consented folder are never followed:
            # consent covers the folder, not wherever a link points.
            relative = path.relative_to(folder)
            if (path.is_file() and path.suffix.lower() in SUPPORTED
                    and not any(part.startswith(".") for part in relative.parts)
                    and path.resolve().is_relative_to(root)):
                seen += 1
                yield path

    smallest = heapq.nsmallest(limit, candidates())
    return ([(path, KIND.get(path.suffix.lower(), "other")) for path in smallest],
            max(0, seen - limit))
