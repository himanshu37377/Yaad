"""Generate the realistic-condition image evaluation corpus (V2 set).

Keeps the clean synthetic benchmark (eval/image_eval/) untouched.
These are simulated realistic conditions built with PIL transforms
(perspective, shadow gradients, blur, uneven lighting, JPEG artifacts)
plus drawn natural-scene/handwriting/chart content. Provenance:
fully synthetic simulations — NOT natural photographs. Ground truth
is defined here, before any model run.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "image_eval_v2" / "data"

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
ARIAL_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
HAND = "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"


def main() -> None:
    argparse.ArgumentParser(description="Generate realistic image corpus.").parse_args()
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    OUT.mkdir(parents=True, exist_ok=True)
    try:
        bold = ImageFont.truetype(ARIAL_B, 34)
        body = ImageFont.truetype(ARIAL, 26)
        small = ImageFont.truetype(ARIAL, 18)
        hand = ImageFont.truetype(HAND, 32)
    except OSError:
        bold = body = small = hand = ImageFont.load_default()
    truth = {}

    # Natural-scene style: colored objects + a sign with text.
    img = Image.new("RGB", (1000, 650), "#9db8d2")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 430, 1000, 650), fill="#7a9e6e")
    d.rectangle((120, 180, 420, 430), fill="#c9d4de")  # building
    d.rectangle((150, 90, 390, 170), fill="#20405e")   # sign board
    d.text((165, 105), "CITY LIBRARY", font=body, fill="#ffffff")
    d.text((165, 140), "Est. 1962", font=small, fill="#dfe9f2")
    for x in (560, 660, 760):
        d.ellipse((x, 330, x + 70, 400), fill="#b03a3a")  # red objects (bikes stylized)
    img.save(OUT / "natural_scene.png")
    truth["natural_scene.png"] = {"category": "natural_scene",
        "questions": [{"q": "What does the sign say?", "expect": ["CITY LIBRARY"]},
                      {"q": "How many red objects are near the building?", "expect": ["3"]}]}

    # Document photo: perspective + shadow + uneven light + blur + JPEG.
    img = Image.new("RGB", (1100, 760), "#e9e2cf")
    d = ImageDraw.Draw(img)
    d.text((60, 50), "CLINIC BILL", font=bold, fill="#1c1c1c")
    d.text((60, 130), "Patient: Arjun Mehta", font=body, fill="#1c1c1c")
    d.text((60, 190), "Total due: 3250 INR", font=body, fill="#1c1c1c")
    d.text((60, 250), "Date: August 19, 2027", font=body, fill="#1c1c1c")
    img = img.rotate(-5, expand=True, fillcolor="#b8b0a0", resample=Image.BICUBIC)
    # Uneven lighting: darken a soft diagonal band (shadow falloff).
    overlay = Image.new("RGB", img.size, "#8a8474")
    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    md.polygon([(700, 0), (img.width, 0), (img.width, img.height), (950, img.height)], fill=90)
    mask = mask.filter(ImageFilter.GaussianBlur(80))
    img = Image.composite(overlay, img, mask)
    img = img.filter(ImageFilter.GaussianBlur(1.1))
    img.save(OUT / "doc_photo_real.png", quality=52)
    truth["doc_photo_real.png"] = {"category": "document_photo",
        "questions": [{"q": "Who is the patient?", "expect": ["Arjun Mehta"]},
                      {"q": "What is the total due?", "expect": ["3250 INR"]}]}

    # Handwriting: legible + degraded legibility.
    img = Image.new("RGB", (950, 560), "#fdf6d8")
    d = ImageDraw.Draw(img)
    d.text((50, 60), "Buy tickets for Saturday", font=hand, fill="#27355e")
    d.text((50, 140), "Call Dr Rao at 5 pm", font=hand, fill="#27355e")
    img.save(OUT / "hand_legible.png")
    truth["hand_legible.png"] = {"category": "handwriting",
        "questions": [{"q": "What should be bought?", "expect": ["tickets"]}]}
    img2 = img.filter(ImageFilter.GaussianBlur(2.6))
    img2.save(OUT / "hand_degraded.png", quality=45)
    truth["hand_degraded.png"] = {"category": "handwriting",
        "questions": [{"q": "What should be bought?", "expect": ["tickets"]}]}

    # Chart requiring interpretation (values + comparison).
    img = Image.new("RGB", (1000, 640), "#ffffff")
    d = ImageDraw.Draw(img)
    d.text((50, 30), "Monthly signups", font=bold, fill="#111111")
    for i, (label, h) in enumerate([("Jan", 120), ("Feb", 210), ("Mar", 160)]):
        x = 140 + i * 250
        d.rectangle((x, 480 - h, x + 130, 480), fill="#4a7dbb")
        d.text((x + 20, 495), label, font=body, fill="#111111")
        d.text((x + 35, 450 - h), str(h // 10), font=body, fill="#111111")
    img.save(OUT / "chart.png")
    truth["chart.png"] = {"category": "chart",
        "questions": [{"q": "Which month had the most signups?", "expect": ["Feb"]},
                      {"q": "Were March signups higher than January?", "expect": ["yes", "higher"]}]}

    # Dense table photographed (small text + rotation + JPEG).
    img = Image.new("RGB", (1150, 720), "#ffffff")
    d = ImageDraw.Draw(img)
    d.text((40, 30), "Train Schedule", font=body, fill="#111111")
    rows = [("Train", "Depart", "Platform"), ("Express 101", "06:30", "2"),
            ("Local 204", "07:15", "5"), ("Express 309", "08:05", "2")]
    y = 100
    for a, b, c in rows:
        d.text((40, y), a, font=small, fill="#222222")
        d.text((420, y), b, font=small, fill="#222222")
        d.text((760, y), c, font=small, fill="#222222")
        y += 52
    img = img.rotate(-3, expand=True, fillcolor="#d8d8d8")
    img.save(OUT / "table_photo.jpg", quality=50)
    truth["table_photo.jpg"] = {"category": "table_photo",
        "questions": [{"q": "When does Local 204 depart?", "expect": ["07:15"]},
                      {"q": "Which platform does Express 309 use?", "expect": ["2"]}]}

    # Multi-region with distractors.
    img = Image.new("RGB", (1050, 620), "#f4f1e8")
    d = ImageDraw.Draw(img)
    d.text((50, 50), "SALE 50% OFF SHOES", font=bold, fill="#a33")
    d.text((50, 140), "Bakery opens 7 AM", font=body, fill="#222222")
    d.text((50, 210), "Pharmacy: 080-555-0142", font=body, fill="#222222")
    d.text((50, 280), "Parking full after 6 PM", font=body, fill="#222222")
    img.save(OUT / "multi_distractor.png")
    truth["multi_distractor.png"] = {"category": "multi_region",
        "questions": [{"q": "What is the pharmacy phone number?", "expect": ["080-555-0142"]}]}

    # Illegible: expected honest handling (abstain/unreadable).
    img = Image.new("RGB", (900, 500), "#ffffff")
    d = ImageDraw.Draw(img)
    d.text((50, 80), "Secret code: LOTUS-9917", font=body, fill="#111111")
    img = img.filter(ImageFilter.GaussianBlur(9))
    img.save(OUT / "illegible.jpg", quality=40)
    truth["illegible.jpg"] = {"category": "illegible",
        "questions": [{"q": "What is the secret code?", "expect_status": ["not_found", "unverified", "answered"],
                       "must_not_claim": ["LOTUS-9917"]}]}

    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(OUT.iterdir()) if p.name != "manifest.json"}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"realistic corpus: {len(truth)} images in {OUT}")


if __name__ == "__main__":
    main()
