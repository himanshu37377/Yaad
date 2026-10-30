"""Generate the cross-file retrieval test corpus (proposal PDF, poster PNG, notes TXT).

Complements an existing real audio recording of a college event planning
discussion; that audio is neither read nor modified here.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import registerFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parents[1] / "yaad_cross_file_test"

# Georgia is used for the PDF because it is the available TTF whose rupee glyph
# reportlab embeds with a correct ToUnicode mapping (Arial/Tahoma/Verdana/Times
# all extract "\u20b9" as \x00 through Yaad's PyMuPDF path).
GEORGIA = "/System/Library/Fonts/Supplemental/Georgia.ttf"
GEORGIA_BOLD = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"


def pdf_fonts() -> tuple[str, str]:
    registerFont(TTFont("Corpus", GEORGIA))
    registerFont(TTFont("Corpus-Bold", GEORGIA_BOLD))
    return "Corpus", "Corpus-Bold"


def make_proposal(path: Path) -> None:
    regular, bold = pdf_fonts()
    doc = SimpleDocTemplate(str(path), pagesize=letter,
                            leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title="Autumn Fest 2026 - Event Proposal")
    title = ParagraphStyle("title", fontName=bold, fontSize=22, leading=26, spaceAfter=4)
    sub = ParagraphStyle("sub", fontName=regular, fontSize=11, leading=15, spaceAfter=14)
    head = ParagraphStyle("head", fontName=bold, fontSize=13, leading=16, spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("body", fontName=regular, fontSize=11, leading=16, spaceAfter=6)
    cell = ParagraphStyle("cell", fontName=regular, fontSize=10.5, leading=14)
    cell_bold = ParagraphStyle("cell-bold", fontName=bold, fontSize=10.5, leading=14)
    story = [
        Paragraph("Autumn Fest 2026 &mdash; Event Proposal", title),
        Paragraph("Student organizing committee &middot; Inter-college tech and cultural fest", sub),
        Paragraph("Purpose", head),
        Paragraph("Autumn Fest is the college's annual one-day fest combining technical workshops, "
                  "a project showcase, and cultural performances. The 2026 edition aims to give "
                  "first- and second-year students a low-cost way to present their work and meet "
                  "peers from neighbouring colleges.", body),
        Paragraph("Event details", head),
        Paragraph("The event will be held on <b>November 18, 2026</b>. The initial budget approved "
                  "by the committee is <b>\u20b925,000</b>. Entry is open to all college students at "
                  "a registration fee of <b>\u20b9150 per person</b>, and registration closes on "
                  "<b>November 15, 2026</b>.", body),
        Paragraph("Budget outline", head),
        Table(
            [[Paragraph("<b>Item</b>", cell_bold), Paragraph("<b>Amount (INR)</b>", cell_bold)],
             [Paragraph("Venue setup and seating", cell), Paragraph("8,000", cell)],
             [Paragraph("Sound and lighting", cell), Paragraph("6,000", cell)],
             [Paragraph("Printing and publicity", cell), Paragraph("3,500", cell)],
             [Paragraph("Prizes", cell), Paragraph("5,000", cell)],
             [Paragraph("Contingency", cell), Paragraph("2,500", cell)],
             [Paragraph("<b>Total</b>", cell_bold), Paragraph("<b>25,000</b>", cell_bold)]],
            colWidths=[4.2 * inch, 2.0 * inch],
            style=TableStyle([("GRID", (0, 0), (-1, -1), 0.6, "#8a8f98"),
                              ("BACKGROUND", (0, 0), (-1, 0), "#e4e8ec"),
                              ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("TOPPADDING", (0, 0), (-1, -1), 5),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                              ("LEFTPADDING", (0, 0), (-1, -1), 8)]),
        ),
        Paragraph("Key dates", head),
        Paragraph("Venue confirmation is due by <b>November 10, 2026</b>, and the event poster must "
                  "be ready by <b>November 12, 2026</b> so that publicity can begin before "
                  "registrations close.", body),
        Paragraph("Workshops", head),
        Paragraph("The fest will include student-led workshops. Three workshop topics still need to "
                  "be decided; the committee will collect suggestions and finalize them at the next "
                  "planning meeting.", body),
    ]
    doc.build(story)


def pil_font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(GEORGIA_BOLD if bold else GEORGIA, size)


def centered(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str, font, fill: str) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (box[2] - box[0]) / 2, y), text, font=font, fill=fill)
    return y + (box[3] - box[1])


def make_poster(path: Path) -> None:
    width, height = 1000, 1300
    image = Image.new("RGB", (width, height), "#f5f1e6")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 300), fill="#1f3a5f")
    draw.rectangle((0, height - 190, width, height), fill="#1f3a5f")
    draw.rectangle((36, 336, width - 36, 940), outline="#1f3a5f", width=4)
    cx = width / 2
    y = centered(draw, cx, 60, "AUTUMN FEST 2026", pil_font(True, 84), "#f5c542")
    centered(draw, cx, y + 16, "Tech  \u00b7  Culture  \u00b7  Workshops", pil_font(False, 40), "#ffffff")
    y = centered(draw, cx, 420, "November 18, 2026", pil_font(True, 72), "#1f3a5f")
    y = centered(draw, cx, y + 40, "One day. Three stages.", pil_font(False, 40), "#4a5568")
    y = centered(draw, cx, y + 60, "Registration: \u20b9150 per person", pil_font(True, 52), "#1f3a5f")
    y = centered(draw, cx, y + 36, "Register by November 15, 2026", pil_font(True, 46), "#8a2be2")
    centered(draw, cx, y + 40, "Volunteers welcome \u2014 join the team", pil_font(False, 38), "#4a5568")
    centered(draw, cx, height - 150, "Organized by the student committee", pil_font(False, 34), "#ffffff")
    image.save(path, format="PNG", optimize=True)


NOTES = """\
Organizer notes - Autumn Fest planning (Nov 3, 2026)

- Next planning meeting: November 6, 2026, seminar hall.
- Need at least 6 volunteers for stage, registration, and crowd duty.
- Volunteer list due: November 8, 2026.
- Venue confirmation deadline: November 10, 2026.
- Poster must be ready by November 12, 2026.
- Three workshop topics still need to be decided; collect suggestions before the meeting.
- Riya to confirm the sound-system quote; Arjun to book the projector.
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    targets = [OUT / "event_proposal.pdf", OUT / "event_poster.png", OUT / "organizer_notes.txt"]
    existing = [path.name for path in targets if path.exists()]
    if existing:
        raise SystemExit(f"Refusing to overwrite existing corpus files: {', '.join(existing)}")
    make_proposal(targets[0])
    make_poster(targets[1])
    targets[2].write_text(NOTES, encoding="utf-8")
    print(f"Cross-file test corpus written to {OUT}")


if __name__ == "__main__":
    main()
