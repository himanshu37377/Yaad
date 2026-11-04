"""Generate frozen synthetic fixtures for the hostel-logistics eval pack.

Independent domain from V3 (its directory is never touched). Creates
eval/real_eval_hostel/data/ with four scenario folders. Byte-identical
on re-runs given identical library versions; frozen by SHA-256
manifest. Flags are parsed first so --help never rewrites fixtures.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "real_eval_hostel" / "data"


def make_pdf(path: Path, title: str, lines: list[str]) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 730, title)
    c.setFont("Helvetica", 12)
    y = 690
    for line in lines:
        c.drawString(72, y, line)
        y -= 28
    c.save()


def make_image(path: Path, heading: str, lines: list[str]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 560), "#f3f5f8")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
        small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
        small = font
    draw.text((50, 60), heading, font=font, fill="#172435")
    y = 150
    for line in lines:
        draw.text((50, y), line, font=small, fill="#172435")
        y += 60
    image.save(path, format="PNG", optimize=True)


def write(folder: str, name: str, text: str) -> None:
    (OUT / folder).mkdir(parents=True, exist_ok=True)
    (OUT / folder / name).write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate hostel eval fixtures.")
    parser.parse_args()
    write("fees", "fee_notice.txt",
          "Hostel fee notice.\n\nThe hostel fee for Spring term is Rs. 42,000.\n"
          "Pay by January 15, 2027 at the fee counter.\n\n\u2014 Warden Meera Rao\n")
    make_pdf(OUT / "fees" / "fee_revision.pdf", "REVISED FEE NOTICE",
             ["The hostel fee is now Rs. 45,000.",
              "Revised due date: January 20, 2027.",
              "All residents must clear dues by the revised date."])
    write("fees", "mess_advance.txt",
          "Mess advance of Rs. 8,000 is payable along with the hostel fee.\n")
    write("fees", "warden_memo.txt",
          "Warden Meera Rao: the fee counter stays open January 2 to January 20, 2027.\n")
    write("mess", "mess_timing_a.txt",
          "Mess notice: breakfast is served from 7:30 to 9:00.\n")
    write("mess", "mess_timing_b.txt",
          "Mess circular: breakfast is served from 8:00 to 9:30.\n")
    write("mess", "mess_menu.txt",
          "Monday dinner: dal, rice and roti.\nTuesday dinner: sambar, rice and poriyal.\n")
    write("rooms", "allotment.txt",
          "Room 214 is allotted to Aarav Sharma for Spring term.\n")
    make_image(OUT / "rooms" / "hostel_fest_poster.png", "HOSTEL FEST",
               ["March 5", "Main Ground", "All residents welcome"])
    write("complaints", "lift_request.txt",
          "Residents request lift repair for Block B.\n")
    write("complaints", "laundry_vendor.txt",
          "Laundry vendor selection is still pending; quotes are under review.\n")
    manifest = {}
    for path in sorted(OUT.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest[str(path.relative_to(OUT))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                       encoding="utf-8")
    print(f"wrote {len(manifest)} hostel fixtures to {OUT}")


if __name__ == "__main__":
    main()
