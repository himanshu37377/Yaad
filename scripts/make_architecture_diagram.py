"""Render Yaad's system-architecture diagram for the pitch deck (slide 6).

Vector-precise PIL rendering (no AI image synthesis) so every label is
technically accurate. Output: docs/architecture.png at 3840x2160 (16:9).

Layout: 1920x1080 design grid scaled x2.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "architecture.png"

W, H, S = 1920, 1080, 2  # design grid + render scale

# Palette
BG = (248, 248, 253)
INK = (30, 27, 46)
PURPLE = (42, 27, 78)
BLUE = (37, 99, 235)
BLUE_FILL = (242, 246, 255)
BLUE_DARK = (30, 64, 175)
CLOUD = (124, 58, 237)
CLOUD_FILL = (244, 239, 254)
GREEN = (21, 128, 61)
GREEN_FILL = (231, 246, 236)
ORANGE = (194, 65, 12)
ORANGE_FILL = (255, 244, 232)
GRAY = (100, 100, 115)
GRAY_FILL = (241, 241, 244)
WHITE = (255, 255, 255)

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
ARIAL_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(ARIAL_B if bold else ARIAL, size * S)


F_TITLE = font(True, 34)
F_PRINCIPLE = font(True, 21)
F_CONT = font(True, 20)
F_BOX = font(True, 19)
F_ITEM = font(False, 15)
F_TINY = font(False, 13)
F_MOTE = font(True, 23)
F_LEG = font(False, 14)


def box(d: ImageDraw.ImageDraw, r, fill, outline, width=2, radius=10):
    d.rounded_rectangle([r[0] * S, r[1] * S, r[2] * S, r[3] * S],
                        radius=int(radius * S), fill=fill, outline=outline, width=int(width * S))


def text(d, xy, s, f, fill=INK, anchor="la", halo=None):
    kw = {"font": f, "fill": fill, "anchor": anchor}
    if halo is not None:
        kw["stroke_width"] = 3 * S // 2
        kw["stroke_fill"] = halo
    d.text((xy[0] * S, xy[1] * S), s, **kw)


def center(d, cx, y, s, f, fill=INK):
    text(d, (cx, y), s, f, fill, anchor="ma")


def arrow(d, x1, y1, x2, y2, fill, width=2.5, dash=None):
    x1, y1, x2, y2 = x1 * S, y1 * S, x2 * S, y2 * S
    if dash:
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5
        ux, uy = dx / length, dy / length
        pos, on = 0.0, True
        while pos < length - 14 * S:
            nxt = min(pos + dash * S, length - 14 * S)
            if on:
                d.line([(x1 + ux * pos, y1 + uy * pos), (x1 + ux * nxt, y1 + uy * nxt)],
                       fill=fill, width=int(width * S))
            pos, on = nxt, not on
        bx, by = x2 - ux * 14 * S, y2 - uy * 14 * S
    else:
        d.line([(x1, y1), (x2, y2)], fill=fill, width=int(width * S))
        import math
        ang = math.atan2(y2 - y1, x2 - x1)
        bx, by = x2, y2
        ux, uy = math.cos(ang), math.sin(ang)
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    L, Wd = 11 * S, 7 * S
    tip = (x2, y2)
    base = (x2 - L * math.cos(ang), y2 - L * math.sin(ang))
    px, py = -math.sin(ang), math.cos(ang)
    d.polygon([tip, (base[0] + px * Wd, base[1] + py * Wd),
               (base[0] - px * Wd, base[1] - py * Wd)], fill=fill)


def _dashline(d: ImageDraw.ImageDraw, x1, y1, x2, y2, fill, width=2.5, dash=10):
    """Dashed connector with no arrowhead (bus segments)."""
    x1, y1, x2, y2 = x1 * S, y1 * S, x2 * S, y2 * S
    import math
    length = math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    pos, on = 0.0, True
    while pos < length:
        nxt = min(pos + dash * S, length)
        if on:
            d.line([(x1 + ux * pos, y1 + uy * pos), (x1 + ux * nxt, y1 + uy * nxt)],
                   fill=fill, width=int(width * S))
        pos, on = nxt, not on


def main() -> None:
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)

    # Title + principle
    text(d, (24, 12), "YAAD  —  Local-First, Evidence-Validated Personal Memory", F_TITLE, PURPLE)
    d.rounded_rectangle([15 * S, 58 * S, 1235 * S, 100 * S], radius=10 * S,
                        fill=(237, 233, 250), outline=None)
    center(d, 625, 66, "Meta models understand.  Yaad validates, remembers and retrieves.",
           F_PRINCIPLE, PURPLE)
    # Legend (top right)
    lx = 1290
    text(d, (lx, 14), "Legend:", F_LEG, PURPLE)
    d.line([(lx * S, 40 * S), ((lx + 60) * S, 40 * S)], fill=BLUE, width=3 * S)
    text(d, (lx + 66, 28), "deterministic local flow", F_LEG)
    x = lx + 260
    for i, xx in enumerate(range(0, 60, 15)):
        if i % 2 == 0:
            d.line([((x + xx) * S, 40 * S), ((x + xx + 11) * S, 40 * S)], fill=CLOUD, width=3 * S)
    text(d, (x + 66, 28), "model-assisted call", F_LEG)
    text(d, (lx, 52), "Green = verified   •   Red/orange = rejected / conflict / security boundary", F_LEG, GRAY)

    # ============ LOCAL container ============
    box(d, (15, 112, 1245, 985), BLUE_FILL, BLUE, width=3, radius=14)
    text(d, (32, 122), "LOCAL EXECUTION ENVIRONMENT", F_CONT, BLUE_DARK)

    # Security strip (cross-cutting)
    box(d, (30, 158, 206, 950), ORANGE_FILL, ORANGE, width=2.5, radius=10)
    center(d, 118, 168, "SECURITY", font(True, 15), ORANGE)
    center(d, 118, 188, "& PRIVACY", font(True, 15), ORANGE)
    center(d, 118, 210, "cross-cutting", F_TINY, GRAY)
    sec_items = [("Folder consent", "per folder"), ("Cloud switch", "kill-switch"),
                 ("PII redaction", "pre-egress"), ("Injection quarantine", "tainted units"),
                 ("Path containment", "symlink-safe"), ("API token gate", "remote binds"),
                 ("Spend + egress log", "receipts")]
    y = 244
    for item, sub in sec_items:
        center(d, 118, y, item, F_ITEM, INK)
        center(d, 118, y + 24, sub, F_TINY, GRAY)
        y += 98

    # Pipeline boxes
    boxes = [
        ("A  CLIENT & API", ["CLI  •  Web UI", "FastAPI endpoints", "Folders  •  Questions",
                             "Memory  •  Privacy controls"], 175, 2),
        ("B  MULTIMODAL INGESTION",
         ["PDF", "DOCX + tables", "TXT", "Images", "Audio", "Discovery",
          "Fingerprint", "Path checks", "Index units"], 301, 3),
        ("C  STORAGE  —  SQLite + FTS5",
         ["File metadata", "Evidence units", "Memory records", "Provenance",
          "Relationships", "History ledger", "Egress receipts"], 427, 4),
        ("D  MEMORY ENGINE",
         ["Extract candidates", "Validate vs source", "Verify relations", "Detect conflicts",
          "Supersession", "Lifecycle", "Learn from chat", "Reject unsupported"], 553, 4),
        ("E  RETRIEVAL & REASONING",
         ["Understand query", "FTS5 keywords", "Memory retrieval", "Hybrid RRF rank",
          "Semantic (opt-in, off)", "Relevance checks", "Verify claims",
          "Recheck citations", "Abstain if weak"], 679, 3),
    ]
    tops = [175, 301, 427, 553, 679]
    for (title, items, top, cols) in boxes:
        box(d, (226, top, 1100, top + 108), WHITE, (150, 170, 220), width=2, radius=8)
        text(d, (242, top + 8), title, F_BOX, BLUE_DARK)
        rows = (len(items) + cols - 1) // cols
        for i, item in enumerate(items):
            c, r = i % cols, i // cols
            x = 242 + c * (840 // cols)
            yy = top + 36 + r * 22
            fill = (ORANGE if item == "Reject unsupported" else
                    (GRAY if "opt-in" in item else INK))
            text(d, (x, yy), "•  " + item, F_ITEM, fill)

    # Vertical arrows between pipeline boxes
    for top in [283, 409, 535, 661, 787]:
        arrow(d, 663, top + 2, 663, top + 16, BLUE, width=3)

    # F Answer outcomes
    box(d, (226, 805, 1100, 950), WHITE, (150, 170, 220), width=2, radius=8)
    text(d, (242, 813), "F  FINAL ANSWER", F_BOX, BLUE_DARK)
    pills = [("ANSWER", "cited evidence", GREEN, GREEN_FILL, 250),
             ("CONFLICT", "both sides cited", ORANGE, ORANGE_FILL, 541),
             ("ABSTAIN", "says so honestly", BLUE_DARK, BLUE_FILL, 832)]
    for label, sub, fg, fill, x in pills:
        box(d, (x, 845, x + 234, 932), fill, fg, width=2.5, radius=10)
        center(d, x + 117, 856, label, font(True, 19), fg)
        center(d, x + 117, 884, sub, F_TINY, INK)

    # ============ Verification gates ============
    for gy, t1, t2 in [(585, "VALIDATE", "vs source"), (700, "GROUND", "citations")]:
        box(d, (1112, gy, 1233, gy + 78), GREEN_FILL, GREEN, width=3, radius=12)
        center(d, 1172, gy + 8, t1, font(True, 17), GREEN)
        center(d, 1172, gy + 34, t2, F_TINY, GREEN)
        box(d, (1112, gy + 86, 1233, gy + 114), (253, 238, 228), (220, 38, 38), width=2, radius=8)
        center(d, 1172, gy + 91, "REJECT", font(True, 14), (220, 38, 38))

    # ============ META cloud container ============
    box(d, (1265, 112, 1905, 700), CLOUD_FILL, CLOUD, width=3, radius=14)
    text(d, (1292, 122), "META AI — CONSENT-GATED CLOUD SERVICES", F_CONT, (109, 40, 217))
    cloud_boxes = [
        ("Muse Spark", ["PDF + image understanding", "Visual extraction", "Answer review"],
         168, 300),
        ("Muse Spark 1.3", ["Memory extraction", "JSON-schema outputs", "Candidate review"],
         314, 446),
        ("Meta Voice Transcribe 1.0", ["Audio transcription", "Timestamped transcripts"],
         460, 576),
    ]
    for title, items, top, bottom in cloud_boxes:
        box(d, (1285, top, 1885, bottom), WHITE, CLOUD, width=2, radius=8)
        text(d, (1301, top + 8), title, font(True, 18), (109, 40, 217))
        for i, item in enumerate(items):
            text(d, (1301, top + 36 + i * 22), "•  " + item, F_ITEM)
    center(d, 1585, 596, "Every call: consent + spend checks", font(True, 15), ORANGE)
    center(d, 1585, 620, "Redacted egress copies only — local originals stay authoritative", F_TINY, INK)
    center(d, 1585, 644, "for grounding and citation validation", F_TINY, INK)

    # Cross-boundary arrows (dashed) in separated lanes; halo labels
    # stay legible where the transcribe/extraction lanes cross once.
    PUR = (109, 40, 217)
    # Horizontal purple send lanes from each local stage into an
    # in-cloud bus that fans out to the model boxes; green returns from
    # the cloud edge back to the gates and storage. Nothing crosses.
    for y, label, above in [(336, "media understanding", True),
                            (398, "transcribe", False),
                            (600, "extraction", True),
                            (716, "answer review", False)]:
        arrow(d, 1100, y, 1273 if y != 398 else 1281, y, CLOUD, width=2.5, dash=10)
        text(d, (1108, y - 22 if above else y + 6), label, F_TINY, PUR, halo=WHITE)
    # In-cloud bus + fan-out stubs into the model boxes
    _dashline(d, 1273, 250, 1273, 716, CLOUD, width=2.5, dash=10)
    _dashline(d, 1281, 398, 1281, 515, CLOUD, width=2, dash=8)
    arrow(d, 1273, 250, 1285, 250, CLOUD, width=2)
    arrow(d, 1273, 285, 1285, 285, CLOUD, width=2)
    arrow(d, 1273, 390, 1285, 390, CLOUD, width=2)
    arrow(d, 1281, 515, 1285, 515, CLOUD, width=2)
    # Returns: proposals -> VALIDATE gate, reviewed claims -> GROUND gate,
    # checked media + transcripts -> storage.
    arrow(d, 1420, 700, 1165, 665, GREEN, width=2.5, dash=10)
    text(d, (1300, 689), "proposals", F_TINY, GREEN, halo=WHITE)
    arrow(d, 1280, 700, 1180, 780, GREEN, width=2.5, dash=10)
    center(d, 1176, 820, "reviewed claims", F_TINY, GREEN)
    arrow(d, 1550, 700, 1100, 481, GREEN, width=2.5, dash=10)
    text(d, (1196, 534), "checked media + transcripts", F_TINY, GREEN, halo=WHITE)
    arrow(d, 1112, 624, 1100, 624, GREEN, width=3)
    arrow(d, 1112, 739, 1100, 739, GREEN, width=3)

    # ============ Ollama (outside cloud) ============
    d.rounded_rectangle([1265 * S, 720 * S, 1905 * S, 985 * S], radius=14 * S,
                        fill=GRAY_FILL, outline=GRAY, width=3 * S)
    text(d, (1292, 736), "Ollama — optional local inference", font(True, 19), INK)
    center(d, 1585, 780, "capability-dependent", font(True, 17), ORANGE)
    for i, line in enumerate(["text + vision review  •  extraction",
                              "no audio transcription",
                              "no PDF understanding"]):
        center(d, 1585, 818 + i * 30, line, F_ITEM, INK)
    center(d, 1585, 918, "never falls back to cloud", F_TINY, GRAY)
    arrow(d, 1265, 884, 1120, 884, GRAY, width=2.5, dash=10)
    text(d, (1116, 844), "serves review +", F_TINY, GRAY, halo=WHITE)
    text(d, (1116, 862), "extraction locally", F_TINY, GRAY, halo=WHITE)

    # ============ Footer motto band ============
    box(d, (15, 1000, 1905, 1062), GREEN_FILL, GREEN, width=3, radius=12)
    center(d, 960, 1016, "MODELS PROPOSE  →  YAAD VERIFIES  →  VERIFIED EVIDENCE BECOMES MEMORY",
           F_MOTE, (20, 100, 50))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(f"wrote {OUT} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
