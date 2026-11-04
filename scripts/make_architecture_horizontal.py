"""Render Yaad's horizontal system-architecture diagram (same layout as the
deck graphic): local pipeline strip on top, validation gate in the middle,
Meta AI cloud + optional local model at the bottom.

Vector-precise PIL rendering (no AI image synthesis) so every label is
technically accurate and every connector is aligned. Output:
docs/yaad_architecture_horizontal.png at 3840x2160 (16:9).

Layout: 1920x1080 design grid scaled x2.

Content verified against the codebase:
- SHA-256 fingerprinting: yaad/scan/hashing.py, worker.py
- Storage tables: yaad/db.py (files, units, units_fts), yaad/memory/
  (memory_records, memory_record_evidence, memory_transitions), egress_log
- Retrieval: yaad/query/hybrid.py (Tier A + RRF), pipeline stages
  (query understanding first), yaad/query/verify.py (citation checks)
- Gate formula: yaad/scan/worker.py
  model_allowed = local_mode or (consent and cloud_enabled)
- Models: yaad/llm/meta_config.py (muse-spark-1.3 default,
  muse-voice-transcribe-1.0), yaad/config.py (muse-glimmer:30b local)
- Local limits: yaad/llm/local_client.py (no audio transcription, no PDF
  understanding, no photo description; text + vision extraction/review OK)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "yaad_architecture_horizontal.png"

W, H, S = 1920, 1080, 2  # design grid + render scale

# Palette (matches the deck graphic)
BG = (255, 255, 255)
INK = (30, 30, 40)
NAVY = (30, 58, 138)
NAVY_OUTLINE = (36, 64, 150)
LOCAL_FILL = (214, 232, 253)
BOX_FILL = (235, 244, 255)
BLUE = (37, 80, 200)
CLOUD = (109, 40, 217)
CLOUD_FILL = (240, 231, 252)
CLOUD_BOX = (247, 243, 255)
GATE_FILL = (211, 191, 247)
GREEN = (22, 130, 60)
GREEN_FILL = (208, 242, 220)
GREEN_PILL = (196, 235, 208)
RED = (231, 53, 36)
RED_FILL = (253, 232, 228)
GRAY = (110, 110, 125)
WHITE = (255, 255, 255)
CITATION_PILL = (200, 226, 250)

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
ARIAL_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(ARIAL_B if bold else ARIAL, size * S)


F_STRIP = font(True, 30)
F_BOX = font(True, 19)
F_ITEM = font(False, 15)
F_TINY = font(False, 14)
F_PILL = font(True, 21)
F_REJ = font(True, 18)
F_CLOUD = font(True, 24)
F_GATE = font(True, 19)
F_GATE2 = font(False, 16)
F_LEG = font(False, 14)
F_LEG_B = font(True, 15)


def box(d, r, fill, outline, width=2, radius=10):
    d.rounded_rectangle([r[0] * S, r[1] * S, r[2] * S, r[3] * S],
                        radius=int(radius * S), fill=fill, outline=outline,
                        width=int(width * S))


def text(d, xy, s, f, fill=INK, anchor="la", halo=None):
    kw = {"font": f, "fill": fill, "anchor": anchor}
    if halo is not None:
        kw["stroke_width"] = 2 * S // 2 + 1
        kw["stroke_fill"] = halo
    d.text((xy[0] * S, xy[1] * S), s, **kw)


def center(d, cx, y, s, f, fill=INK, halo=None):
    text(d, (cx, y), s, f, fill, anchor="ma", halo=halo)


def rtext(d, x, y, s, f, fill=INK, halo=None):
    text(d, (x, y), s, f, fill, anchor="ra", halo=halo)


def arrow(d, x1, y1, x2, y2, fill, width=2.5, dash=None):
    import math
    x1, y1, x2, y2 = x1 * S, y1 * S, x2 * S, y2 * S
    if dash:
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5
        ux, uy = dx / length, dy / length
        pos, on = 0.0, True
        while pos < length - 14 * S:
            nxt = min(pos + dash * S, length - 14 * S)
            if on:
                d.line([(x1 + ux * pos, y1 + uy * pos),
                        (x1 + ux * nxt, y1 + uy * nxt)],
                       fill=fill, width=int(width * S))
            pos, on = nxt, not on
    else:
        d.line([(x1, y1), (x2, y2)], fill=fill, width=int(width * S))
    ang = math.atan2(y2 - y1, x2 - x1)
    L, Wd = 11 * S, 7 * S
    base = (x2 - L * math.cos(ang), y2 - L * math.sin(ang))
    px, py = -math.sin(ang), math.cos(ang)
    d.polygon([(x2, y2), (base[0] + px * Wd, base[1] + py * Wd),
               (base[0] - px * Wd, base[1] - py * Wd)], fill=fill)


def _dash_seg(d, x1, y1, x2, y2, fill, width=2.5, dash=10):
    import math
    x1, y1, x2, y2 = x1 * S, y1 * S, x2 * S, y2 * S
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 1:
        return
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    pos, on = 0.0, True
    while pos < length:
        nxt = min(pos + dash * S, length)
        if on:
            d.line([(x1 + ux * pos, y1 + uy * pos),
                    (x1 + ux * nxt, y1 + uy * nxt)],
                   fill=fill, width=int(width * S))
        pos, on = nxt, not on


def ortho(d, pts, fill, width=2.5, dash=10):
    """Orthogonal dashed connector; arrowhead on the final segment."""
    for (x1, y1), (x2, y2) in zip(pts[:-2], pts[1:-1]):
        _dash_seg(d, x1, y1, x2, y2, fill, width, dash)
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    import math
    x1s, y1s, x2s, y2s = x1 * S, y1 * S, x2 * S, y2 * S
    dx, dy = x2s - x1s, y2s - y1s
    length = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / length, dy / length
    pos, on = 0.0, True
    while pos < length - 14 * S:
        nxt = min(pos + dash * S, length - 14 * S)
        if on:
            d.line([(x1s + ux * pos, y1s + uy * pos),
                    (x1s + ux * nxt, y1s + uy * nxt)],
                   fill=fill, width=int(width * S))
        pos, on = nxt, not on
    ang = math.atan2(dy, dx)
    L, Wd = 11 * S, 7 * S
    base = (x2s - L * math.cos(ang), y2s - L * math.sin(ang))
    px, py = -math.sin(ang), math.cos(ang)
    d.polygon([(x2s, y2s), (base[0] + px * Wd, base[1] + py * Wd),
               (base[0] - px * Wd, base[1] - py * Wd)], fill=fill)


def bullets(d, x, y, items, step=31, f=F_ITEM, fill=INK):
    """Bulleted list; '\\n' inside an item makes a hanging continuation."""
    for item in items:
        lines = item.split("\n")
        text(d, (x, y), "\u2022  " + lines[0], f, fill)
        for extra in lines[1:]:
            y += 24
            text(d, (x + 22, y), extra, f, fill)
        y += step
    return y


BOX_TOP, BOX_BOT = 76, 344
BW = 295
BOX_X = [35 + i * (BW + 16) for i in range(6)]  # 35..1885


def pipeline_box(d, i, title, items):
    x0 = BOX_X[i]
    box(d, (x0, BOX_TOP, x0 + BW, BOX_BOT), BOX_FILL, NAVY_OUTLINE,
        width=2, radius=14)
    text(d, (x0 + 16, BOX_TOP + 10), title, F_BOX, NAVY)
    bullets(d, x0 + 16, BOX_TOP + 52, items)


def main() -> None:
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)

    # ============ LOCAL strip ============
    box(d, (15, 12, 1905, 372), LOCAL_FILL, LOCAL_FILL, width=1,
        radius=22)
    text(d, (40, 22), "LOCAL \u2014 Always On-Device", F_STRIP, NAVY)

    pipeline_box(d, 0, "CLIENTS",
                 ["CLI", "Web UI (SPA)", "FastAPI endpoints"])
    pipeline_box(d, 1, "FILE INGESTION",
                 ["discovery", "SHA-256 fingerprinting",
                  "multimodal parsing", "evidence units"])
    pipeline_box(d, 2, "SQLITE + FTS5",
                 ["files", "evidence units", "search index",
                  "structured memory", "transition ledger", "egress log"])
    pipeline_box(d, 3, "YAAD MEMORY ENGINE",
                 ["evidence validation", "relationship verification",
                  "conflict detection", "supersession",
                  "lifecycle management"])
    # Retrieval box: bullets + citation pill pinned at the bottom.
    x4 = BOX_X[4]
    box(d, (x4, BOX_TOP, x4 + BW, BOX_BOT), BOX_FILL, NAVY_OUTLINE,
        width=2, radius=14)
    text(d, (x4 + 16, BOX_TOP + 10), "RETRIEVAL & ANSWER", F_BOX, NAVY)
    bullets(d, x4 + 16, BOX_TOP + 52,
            ["hybrid search\n(FTS5 + Tier A memory, RRF)",
             "query understanding", "relevance / slot checks",
             "answer review when needed"], step=30)
    box(d, (x4 + 24, BOX_BOT - 44, x4 + BW - 24, BOX_BOT - 12),
        CITATION_PILL, CITATION_PILL, width=1, radius=14)
    center(d, x4 + BW / 2, BOX_BOT - 38, "citation re-validation",
           F_TINY, NAVY)
    # Final response box: outcome pill + citation note.
    x5 = BOX_X[5]
    box(d, (x5, BOX_TOP, x5 + BW, BOX_BOT), BOX_FILL, NAVY_OUTLINE,
        width=2, radius=14)
    text(d, (x5 + 16, BOX_TOP + 10), "FINAL", F_BOX, NAVY)
    text(d, (x5 + 16, BOX_TOP + 36), "RESPONSE", F_BOX, NAVY)
    box(d, (x5 + 40, BOX_TOP + 96, x5 + BW - 40, BOX_TOP + 210),
        GREEN_PILL, GREEN_PILL, width=1, radius=12)
    for j, line in enumerate(["Answer /", "Conflict /", "Abstain"]):
        center(d, x5 + BW / 2, BOX_TOP + 108 + j * 32, line,
               font(True, 20), INK)
    center(d, x5 + BW / 2, BOX_TOP + 226, "with source citations",
           F_ITEM, INK)

    # Blue flow arrows between the six boxes.
    for i in range(5):
        arrow(d, BOX_X[i] + BW + 2, 210, BOX_X[i + 1] - 6, 210,
              BLUE, width=3)

    # ============ Validation gate (middle band) ============
    box(d, (860, 462, 1250, 578), RED, RED, width=1, radius=58)
    for j, line in enumerate(["VALIDATE AGAINST", "SOURCE EVIDENCE",
                              "BEFORE STORAGE"]):
        center(d, 1055, 478 + j * 32, line, F_PILL, WHITE)
    box(d, (1280, 492, 1490, 548), RED_FILL, RED, width=2.5,
        radius=14)
    center(d, 1385, 506, "\u00d7  REJECTED", F_REJ, RED)
    arrow(d, 1252, 520, 1278, 520, RED, width=3)
    center(d, 1355, 452, "Invalid", F_TINY, RED)
    center(d, 1355, 470, "candidates", F_TINY, RED)

    # Memory engine <-> gate: candidates down, validated memories up.
    arrow(d, 1090, 376, 1090, 458, BLUE, width=3)
    text(d, (1102, 404), "candidates", F_TINY, NAVY)
    arrow(d, 1020, 458, 1020, 376, BLUE, width=3)
    rtext(d, 1008, 398, "validated", F_TINY, NAVY)
    rtext(d, 1008, 418, "memories", F_TINY, NAVY)
    # Model proposals arrive from the cloud for validation.
    arrow(d, 1160, 646, 1160, 582, CLOUD, width=2.5, dash=10)
    text(d, (1172, 596), "model candidates", F_TINY, CLOUD)

    # ============ Left lanes: ingestion -> local / cloud ============
    # Green: vision extraction runs on the local model.
    _dash_seg(d, 470, 348, 470, 618, GREEN, width=2.5, dash=10)
    rtext(d, 458, 478, "vision", F_TINY, GREEN)
    rtext(d, 458, 498, "extraction", F_TINY, GREEN)
    # Purple: PDF / audio processing needs the cloud.
    ortho(d, [(560, 348), (560, 590), (666, 590)], CLOUD, width=2.5)
    text(d, (572, 542), "PDF / audio", F_TINY, CLOUD)
    text(d, (572, 562), "processing", F_TINY, CLOUD)
    # Explicit on-demand memory extraction: storage -> cloud.
    arrow(d, 800, 348, 800, 646, CLOUD, width=2.5, dash=10)
    rtext(d, 788, 470, "explicit memory", F_TINY, CLOUD)
    rtext(d, 788, 490, "extraction", F_TINY, CLOUD)
    rtext(d, 788, 510, "(on demand)", F_TINY, CLOUD)

    # ============ Right lanes: answer review ============
    # Purple down: review request to the cloud.
    arrow(d, 1505, 348, 1505, 646, CLOUD, width=2.5, dash=10)
    rtext(d, 1493, 406, "review", F_TINY, CLOUD)
    rtext(d, 1493, 426, "when needed", F_TINY, CLOUD)
    # Purple up: review result back to retrieval.
    arrow(d, 1532, 646, 1532, 376, CLOUD, width=2.5, dash=10)
    text(d, (1572, 406), "review", F_TINY, CLOUD)
    text(d, (1572, 426), "result", F_TINY, CLOUD)
    # Green bus: local review path above the cloud box, joining the
    # vision lane and dropping into the local model box. (Crosses two
    # purple lanes at right angles; no same-colour crossings.)
    _dash_seg(d, 1560, 348, 1560, 618, GREEN, width=2.5, dash=10)
    _dash_seg(d, 327, 618, 1560, 618, GREEN, width=2.5, dash=10)
    arrow(d, 327, 616, 327, 646, GREEN, width=2.5, dash=10)
    center(d, 943, 588, "local review + extraction (no cloud)",
           F_TINY, GREEN, halo=WHITE)

    # ============ META AI cloud container ============
    box(d, (670, 650, 1905, 1062), CLOUD_FILL, CLOUD, width=2.5,
        radius=20)
    text(d, (700, 662), "META AI \u2014 CLOUD (Consent-Gated)",
         F_CLOUD, CLOUD)
    cloud_boxes = [
        (695, 1085, "MUSE SPARK",
         ["PDF and image\nunderstanding", "OCR / visual extraction",
          "semantic answer review"]),
        (1100, 1490, "MUSE SPARK 1.3",
         ["structured memory\ncandidate extraction\n(JSON)"]),
        (1505, 1880, "META VOICE\nTRANSCRIBE 1.0",
         ["timestamped audio\ntranscription"]),
    ]
    for x0, x1, title, items in cloud_boxes:
        box(d, (x0, 712, x1, 902), CLOUD_BOX, CLOUD, width=2,
            radius=12)
        for j, line in enumerate(title.split("\n")):
            text(d, (x0 + 18, 724 + j * 28), line, font(True, 20),
                 CLOUD)
        top = 724 + len(title.split("\n")) * 28 + 6
        bullets(d, x0 + 18, top, items, step=32)
    # Consent gate strip.
    box(d, (695, 918, 1880, 1042), GATE_FILL, GATE_FILL, width=1,
        radius=14)
    center(d, 1287, 944,
           "Cloud calls require BOTH folder consent AND global cloud switch",
           F_GATE, (58, 28, 113))
    center(d, 1287, 982,
           "model_allowed = local_mode OR (folder_consent AND cloud_enabled)",
           F_GATE2, (58, 28, 113))

    # ============ Optional local model ============
    box(d, (15, 650, 640, 878), GREEN_FILL, GREEN_FILL, width=1,
        radius=20)
    text(d, (40, 666), "OPTIONAL LOCAL MODEL \u2014", font(True, 20),
         INK)
    text(d, (40, 694), "Glimmer via Ollama", font(True, 20), INK)
    box(d, (40, 734, 615, 772), GREEN_PILL, GREEN_PILL, width=1,
        radius=10)
    center(d, 327, 742, "local inference \u00b7 no cloud API egress",
           font(True, 15), INK)
    center(d, 327, 792, "text + vision extraction and review",
           F_TINY, INK)
    center(d, 327, 814, "no audio \u00b7 no PDF understanding \u00b7",
           F_TINY, INK)
    center(d, 327, 836, "no photo description", F_TINY, INK)

    # ============ Legend ============
    box(d, (15, 893, 640, 1062), WHITE, GRAY, width=2, radius=14)
    text(d, (32, 903), "Legend", F_LEG_B, INK)
    rows = [(BLUE, None, "Local data flow (on-device)"),
            (CLOUD, 10, "Model call (Meta AI cloud, consent-gated)"),
            (GREEN, 10, "Local inference path (Glimmer, no cloud)"),
            (RED, None, "Invalid / rejected")]
    y = 933
    for colour, dash, label in rows:
        if dash:
            _dash_seg(d, 32, y + 8, 92, y + 8, colour, width=3,
                      dash=dash)
            d.polygon([(92 * S, (y + 8) * S), (80 * S, (y + 2) * S),
                       (80 * S, (y + 14) * S)], fill=colour)
        else:
            arrow(d, 32, y + 8, 92, y + 8, colour, width=3)
        text(d, (104, y - 2), label, F_LEG, INK)
        y += 34
    text(d, (330, 1033), "\u221e  =  Meta AI model", F_LEG, CLOUD)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    print(f"wrote {OUT} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()

