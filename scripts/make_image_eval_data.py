"""Generate frozen synthetic image-evaluation fixtures.

Ten messy-real-world style images with ground-truth fields/text.
Byte-identical manifest; flags parsed first so --help writes nothing.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "image_eval" / "data"

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
ARIAL_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
HAND = "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"
DEV = "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc"


def fonts():
    from PIL import ImageFont
    try:
        return (ImageFont.truetype(ARIAL_B, 34), ImageFont.truetype(ARIAL, 26),
                ImageFont.truetype(ARIAL, 15), ImageFont.truetype(HAND, 30),
                ImageFont.truetype(DEV, 26))
    except OSError:
        from PIL import ImageFont as F
        d = F.load_default()
        return d, d, d, d, d


def main() -> None:
    argparse.ArgumentParser(description="Generate image-evaluation fixtures.").parse_args()
    from PIL import Image, ImageDraw, ImageFilter

    OUT.mkdir(parents=True, exist_ok=True)
    bold, body, small, hand, dev = fonts()
    truth = {}

    # 1 poster (clean)
    img = Image.new("RGB", (900, 600), "#f3f5f8"); d = ImageDraw.Draw(img)
    d.text((50, 50), "CITY MARATHON 2027", font=bold, fill="#172435")
    d.text((50, 140), "Venue: Lakeview Stadium", font=body, fill="#172435")
    d.text((50, 200), "Entry fee: 750 INR", font=body, fill="#172435")
    d.text((50, 260), "Date: November 14, 2027", font=body, fill="#172435")
    img.save(OUT / "poster.png")
    truth["poster.png"] = {"venue": "Lakeview Stadium", "fee": "750 INR",
                           "date": "November 14, 2027", "text": ["CITY MARATHON 2027"]}

    # 2 screenshot-style settings window
    img = Image.new("RGB", (1000, 650), "#20242c"); d = ImageDraw.Draw(img)
    d.rectangle((60, 60, 940, 590), fill="#2b303c")
    d.text((90, 90), "Account Settings", font=bold, fill="#ffffff")
    d.text((90, 170), "Plan: Premium Annual", font=body, fill="#dfe5ee")
    d.text((90, 230), "Renewal date: January 5, 2028", font=body, fill="#dfe5ee")
    d.text((90, 290), "Storage used: 82 GB of 100 GB", font=body, fill="#dfe5ee")
    img.save(OUT / "screenshot.png")
    truth["screenshot.png"] = {"plan": "Premium Annual", "renewal": "January 5, 2028",
                               "storage": "82 GB", "text": ["Account Settings"]}

    # 3 photographed document (skew + perspective feel via rotation)
    img = Image.new("RGB", (1000, 700), "#e8e2d4"); d = ImageDraw.Draw(img)
    d.text((60, 60), "RENT RECEIPT", font=bold, fill="#222222")
    d.text((60, 140), "Tenant: Meera Shah", font=body, fill="#222222")
    d.text((60, 200), "Amount: 18500 INR for March 2027", font=body, fill="#222222")
    d.text((60, 260), "Landlord: K. Verma", font=body, fill="#222222")
    img = img.rotate(4, expand=True, fillcolor="#cfc8b8")
    img.save(OUT / "doc_photo.png")
    truth["doc_photo.png"] = {"tenant": "Meera Shah", "amount": "18500 INR",
                              "landlord": "K. Verma", "text": ["RENT RECEIPT"]}

    # 4 blurry version of a notice
    img = Image.new("RGB", (900, 550), "#ffffff"); d = ImageDraw.Draw(img)
    d.text((50, 60), "OFFICE NOTICE", font=bold, fill="#111111")
    d.text((50, 140), "Office closed on December 25, 2027", font=body, fill="#111111")
    d.text((50, 200), "Contact: Ravi Menon", font=body, fill="#111111")
    img = img.filter(ImageFilter.GaussianBlur(2.2))
    img.save(OUT / "blurry.png")
    truth["blurry.png"] = {"closed": "December 25, 2027", "contact": "Ravi Menon",
                           "text": ["OFFICE NOTICE"]}

    # 5 table / structured layout
    img = Image.new("RGB", (1000, 620), "#ffffff"); d = ImageDraw.Draw(img)
    d.text((50, 40), "Exam Timetable", font=bold, fill="#111111")
    rows = [("Subject", "Date", "Room"), ("Mathematics", "June 2, 2027", "Room 12"),
            ("Physics", "June 4, 2027", "Room 9"), ("Chemistry", "June 6, 2027", "Room 12")]
    y = 120
    for a, b, c in rows:
        d.text((50, y), a, font=body, fill="#111111")
        d.text((350, y), b, font=body, fill="#111111")
        d.text((700, y), c, font=body, fill="#111111")
        y += 70
    img.save(OUT / "table.png")
    truth["table.png"] = {"physics_date": "June 4, 2027", "chemistry_room": "Room 12",
                          "text": ["Exam Timetable", "Mathematics"]}

    # 6 small text dense page
    img = Image.new("RGB", (1100, 750), "#ffffff"); d = ImageDraw.Draw(img)
    d.text((40, 30), "Terms and Conditions", font=body, fill="#111111")
    y = 90
    for line in ["Refund window is 14 days from purchase.",
                 "Support email: help@northwind.example",
                 "Helpline: 1800-419-0000",
                 "Warranty period is 24 months."]:
        d.text((40, y), line, font=small, fill="#333333"); y += 34
    img.save(OUT / "small_text.png")
    truth["small_text.png"] = {"refund": "14 days", "email": "help@northwind.example",
                               "warranty": "24 months", "text": ["Terms and Conditions"]}

    # 7 handwritten-style note
    img = Image.new("RGB", (900, 550), "#fdf6d8"); d = ImageDraw.Draw(img)
    d.text((50, 60), "Team lunch on Friday", font=hand, fill="#27355e")
    d.text((50, 130), "Budget 2400 rupees - ask Sana", font=hand, fill="#27355e")
    img.save(OUT / "handwritten.png")
    truth["handwritten.png"] = {"event": "Team lunch on Friday", "budget": "2400",
                                "person": "Sana", "text": ["Team lunch"]}

    # 8 Hindi + English bilingual sign
    img = Image.new("RGB", (950, 550), "#eef4ea"); d = ImageDraw.Draw(img)
    d.text((50, 60), "पुस्तकालय समय", font=dev, fill="#17351f")
    d.text((50, 130), "Library hours: 9 AM to 8 PM", font=body, fill="#17351f")
    d.text((50, 200), "Sunday closed / रविवार बंद", font=body, fill="#17351f")
    img.save(OUT / "bilingual.png")
    truth["bilingual.png"] = {"hours": "9 AM to 8 PM", "text": ["Library hours"]}

    # 9 visual interpretation: count + chart-ish bars
    img = Image.new("RGB", (900, 600), "#ffffff"); d = ImageDraw.Draw(img)
    d.text((50, 40), "Books on the shelf", font=bold, fill="#111111")
    for i, h in enumerate((120, 200, 160)):
        d.rectangle((120 + i * 220, 450 - h, 260 + i * 220, 450), fill="#7aa2d6")
    d.text((50, 500), "Three blue bars are shown.", font=body, fill="#111111")
    img.save(OUT / "visual.png")
    truth["visual.png"] = {"bars": "3", "text": ["Books on the shelf"]}

    # 10 multi-region: two notices in one frame
    img = Image.new("RGB", (1050, 620), "#f7f7f7"); d = ImageDraw.Draw(img)
    d.rectangle((40, 40, 500, 560), outline="#888888", width=3)
    d.rectangle((550, 40, 1010, 560), outline="#888888", width=3)
    d.text((60, 70), "GYM NOTICE", font=bold, fill="#111111")
    d.text((60, 140), "Gym fee: 1200 INR monthly", font=body, fill="#111111")
    d.text((570, 70), "POOL NOTICE", font=bold, fill="#111111")
    d.text((570, 140), "Pool closed May 1, 2027", font=body, fill="#111111")
    img.save(OUT / "multi_region.png")
    truth["multi_region.png"] = {"gym_fee": "1200 INR", "pool_closed": "May 1, 2027",
                                 "text": ["GYM NOTICE", "POOL NOTICE"]}

    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(OUT.iterdir()) if p.name != "manifest.json"}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"image fixtures: {len(truth)} images in {OUT}")


if __name__ == "__main__":
    main()
