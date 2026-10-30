"""Generate frozen synthetic fixtures for real-model Evaluation V2.

Independent from V1 (eval/real_eval/ is never touched). Creates
eval/real_eval_v2/data/ with five scenario folders. Byte-identical on
re-runs; frozen by the SHA-256 manifest. Flags are parsed first so
--help never rewrites fixtures.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "real_eval_v2" / "data"


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
    """Synthetic speech via macOS `say`; False when unavailable (then
    transcription must be reported not measured, never zero)."""
    if not (shutil.which("say") and shutil.which("ffmpeg")):
        return False
    text = ("We propose increasing the Spring Fest budget to twenty eight "
            "thousand rupees. Priya will confirm the final budget.")
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
        description="Generate frozen synthetic fixtures for Evaluation V2."
        ).parse_args()
    for folder in ("lifecycle2", "amounts", "gallery", "conflicts2", "controls"):
        (OUT / folder).mkdir(parents=True, exist_ok=True)

    # Three-step lifecycle: PDF -> poster -> note -> audio proposal -> final.
    make_pdf(OUT / "lifecycle2" / "initial_decision.pdf",
             "Spring Fest 2027 - Initial Decision",
             ["The committee confirmed the Spring Fest venue is Riverside Hall.",
              "The approved budget is 20000 INR for the event.",
              "Registration deadline is March 1, 2027."])
    make_image(OUT / "lifecycle2" / "revision_poster.png",
               "SPRING FEST 2027 - REVISED",
               ["New venue: Grand Arena", "Revised budget: 25000 INR"])
    write("lifecycle2", "update_note.txt",
          "Spring Fest update: the venue Grand Arena is confirmed. "
          "The registration deadline is April 2, 2027.\n")
    audio_ok = make_audio(OUT / "lifecycle2" / "proposal_audio.wav")
    write("lifecycle2", "final_confirmation.txt",
          "Spring Fest final confirmation: the venue is Crystal Pavilion. "
          "The confirmed budget is 28000 INR. "
          "The registration deadline is April 9, 2027.\n")
    write("lifecycle2", "library_grant.txt",
          "The central library grant of 5000 INR covers new books only.\n")

    # Amount-only chain: nothing changes except the figure.
    write("amounts", "quote_a.txt",
          "Venue hire cost for Spring Fest is 12000 INR, quoted by City Halls.\n")
    write("amounts", "quote_b.txt",
          "Venue hire cost for Spring Fest is 15000 INR, quoted by City Halls.\n")
    write("amounts", "quote_c.txt",
          "Venue hire cost for Spring Fest is 18000 INR, confirmed with City Halls.\n")

    # Gallery: one relevant poster plus distractors.
    make_image(OUT / "gallery" / "poster.png", "SPRING FEST 2027",
               ["Venue: Grand Arena", "Budget: 25000 INR"])
    make_image(OUT / "gallery" / "bicycle.png", "A red bicycle leaning on a fence", [])
    make_image(OUT / "gallery" / "stage.png", "Empty stage with blue chairs", [])

    # Genuine conflict + an undecided amount notice (backstop control).
    write("conflicts2", "source_a.txt",
          "Spring Fest catering contract confirmed at 8000 INR with Fresh Foods.\n")
    write("conflicts2", "source_b.txt",
          "Spring Fest catering contract confirmed at 9500 INR with Fresh Foods.\n")
    write("conflicts2", "decision_notice.txt",
          "Council is deciding between Rs 20000 and Rs 25000 for the snack budget.\n")

    # Controls: same venue words, different events; compatible facts.
    write("controls", "spring_fest.txt",
          "Spring Fest venue is Riverside Hall on November 18, 2027.\n")
    write("controls", "autumn_fest.txt",
          "Autumn Fest venue is Riverside Hall on October 5, 2027.\n")

    manifest = {}
    for path in sorted(OUT.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest[str(path.relative_to(OUT))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    manifest["_audio_speech_synthesized"] = audio_ok
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                       encoding="utf-8")
    print(f"V2 fixtures written to {OUT} (audio speech: {audio_ok})")


if __name__ == "__main__":
    main()
