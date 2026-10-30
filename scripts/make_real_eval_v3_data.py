"""Generate frozen synthetic fixtures for real-model Evaluation V3.

Independent from V1/V2 (their directories are never touched). Creates
eval/real_eval_v3/data/ with six scenario folders targeting the five
post-V2 fixes. Byte-identical on re-runs; frozen by SHA-256 manifest.
Flags are parsed first so --help never rewrites fixtures.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "real_eval_v3" / "data"


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


def make_audio(path: Path) -> bool:
    if not (shutil.which("say") and shutil.which("ffmpeg")):
        return False
    text = ("We propose raising the Spring Fest hall budget to thirty "
            "thousand rupees. Arjun will confirm the final figure.")
    with tempfile.NamedTemporaryFile(suffix=".aiff") as speech:
        subprocess.run(["say", "-o", speech.name, text], check=True,
                       capture_output=True, timeout=60)
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", speech.name,
                        "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
                        str(path)], check=True, capture_output=True)
    return True


def write(folder: str, name: str, text: str) -> None:
    (OUT / folder / name).write_text(text, encoding="utf-8")


def main() -> None:
    argparse.ArgumentParser(
        description="Generate frozen synthetic fixtures for Evaluation V3."
        ).parse_args()
    for folder in ("slotgrid", "chains", "amounts3", "undecided3", "gallery3", "controls3"):
        (OUT / folder).mkdir(parents=True, exist_ok=True)

    # Slot grid: one event, three attributes in separate sources.
    write("slotgrid", "venue.txt",
          "Harbor Fest venue is Marina Hall, deposit Rs 5000.\n")
    write("slotgrid", "budget.txt",
          "Harbor Fest budget is Rs 40000, approved by the council.\n")
    write("slotgrid", "deadline.txt",
          "Harbor Fest registration deadline is May 3, 2027.\n")

    # Three-step chains across modalities.
    make_pdf(OUT / "chains" / "step1.pdf", "Spring Fest 2027 - Initial Decision",
             ["The committee confirmed the Spring Fest venue is Riverside Hall.",
              "The approved budget is 20000 INR for the event.",
              "Registration deadline is March 1, 2027."])
    make_image(OUT / "chains" / "step2_poster.png", "SPRING FEST 2027 - REVISED",
               ["New venue: Grand Arena", "Revised budget: 25000 INR"])
    write("chains", "step3_note.txt",
          "Spring Fest update: the venue Grand Arena is confirmed. "
          "The registration deadline is April 2, 2027.\n")
    audio_ok = make_audio(OUT / "chains" / "step4_proposal.wav")
    write("chains", "step5_final.txt",
          "Spring Fest final confirmation: the venue is Crystal Pavilion. "
          "The confirmed budget is 30000 INR. "
          "The registration deadline is April 9, 2027.\n")

    # Quoted -> quoted -> confirmed amount revisions.
    write("amounts3", "quote_a.txt",
          "Hall hire cost for Spring Fest is 12000 INR, quoted by City Halls.\n")
    write("amounts3", "quote_b.txt",
          "Hall hire cost for Spring Fest is 15000 INR, quoted by City Halls.\n")
    write("amounts3", "quote_c.txt",
          "Hall hire cost for Spring Fest is 18000 INR, confirmed with City Halls.\n")

    # Undecided + genuine conflict + proposal control.
    write("undecided3", "snack_notice.txt",
          "Council is deciding between Rs 20000 and Rs 25000 for the snack budget.\n")
    write("undecided3", "catering_a.txt",
          "Spring Fest catering contract confirmed at 8000 INR with Fresh Foods.\n")
    write("undecided3", "catering_b.txt",
          "Spring Fest catering contract confirmed at 9500 INR with Fresh Foods.\n")

    # Photo ranking: filename-only and caption-only matches.
    make_image(OUT / "gallery3" / "poster.png", "SPRING FEST 2027",
               ["Venue: Grand Arena", "Budget: 25000 INR"])
    make_image(OUT / "gallery3" / "bicycle.png", "A red bicycle leaning on a fence", [])
    make_image(OUT / "gallery3" / "covered_wagon.png", "A covered wagon in a field", [])

    # Controls: different events, incidental amounts, abstention traps.
    write("controls3", "spring_fest.txt",
          "Spring Fest venue is Riverside Hall on November 18, 2027.\n")
    write("controls3", "autumn_fest.txt",
          "Autumn Fest venue is Riverside Hall, deposit Rs 7000, on October 5, 2027.\n")

    manifest = {}
    for path in sorted(OUT.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest[str(path.relative_to(OUT))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    manifest["_audio_speech_synthesized"] = audio_ok
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                       encoding="utf-8")
    print(f"V3 fixtures written to {OUT} (audio speech: {audio_ok})")


if __name__ == "__main__":
    main()
