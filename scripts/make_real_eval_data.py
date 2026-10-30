"""Generate the frozen synthetic fixtures for the real-model evaluation.

Creates eval/real_eval/data/ with two folders:
  lifecycle/  PDF decision -> revision image -> proposal audio -> final text
  conflict/   two confirmed incompatible values, no supersession link

Everything is synthetic. Running this script is explicit generation; flags
are parsed first so --help never rewrites fixtures. Re-running overwrites
with byte-identical content (fixtures are frozen by manifest hash).
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "real_eval" / "data"


def make_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 730, "Spring Fest 2027 - Initial Decision")
    c.setFont("Helvetica", 12)
    lines = [
        "The committee confirmed the Spring Fest venue is Riverside Hall.",
        "The approved budget is 20000 INR for the event.",
        "Registration deadline is March 1, 2027.",
    ]
    y = 690
    for line in lines:
        c.drawString(72, y, line)
        y -= 28
    c.save()


def make_image(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 560), "#f3f5f8")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
        small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
        small = font
    draw.text((50, 60), "SPRING FEST 2027 - REVISED", font=font, fill="#172435")
    draw.text((50, 150), "New venue: Grand Arena", font=small, fill="#172435")
    draw.text((50, 210), "Revised budget: 25000 INR", font=small, fill="#172435")
    image.save(path, format="PNG", optimize=True)


def make_audio(path: Path) -> bool:
    """Synthetic speech via macOS `say`; False when unavailable (the WAV
    is then a placeholder and transcription scores must be reported as
    not measured, never as zero)."""
    if not (shutil.which("say") and shutil.which("ffmpeg")):
        return False
    text = ("We propose increasing the Spring Fest budget to twenty five "
            "thousand rupees. John will confirm the final budget.")
    with tempfile.NamedTemporaryFile(suffix=".aiff") as speech:
        subprocess.run(["say", "-o", speech.name, text], check=True,
                       capture_output=True, timeout=60)
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", speech.name,
                        "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
                        str(path)], check=True, capture_output=True)
    return True


def main() -> None:
    argparse.ArgumentParser(
        description="Generate frozen synthetic fixtures for the real-model "
                    "evaluation.").parse_args()
    lifecycle = OUT / "lifecycle"
    conflict = OUT / "conflict"
    lifecycle.mkdir(parents=True, exist_ok=True)
    conflict.mkdir(parents=True, exist_ok=True)
    make_pdf(lifecycle / "initial_decision.pdf")
    make_image(lifecycle / "revision_poster.png")
    audio_ok = make_audio(lifecycle / "proposal_audio.wav")
    (lifecycle / "final_confirmation.txt").write_text(
        "Spring Fest final confirmation: the venue is Grand Arena. "
        "The confirmed budget is 25000 INR. "
        "The registration deadline is April 2, 2027.\n", encoding="utf-8")
    (lifecycle / "library_grant.txt").write_text(
        "The central library grant of 5000 INR covers new books only.\n",
        encoding="utf-8")
    (conflict / "source_a.txt").write_text(
        "Spring Fest catering contract confirmed at 8000 INR with Fresh Foods.\n",
        encoding="utf-8")
    (conflict / "source_b.txt").write_text(
        "Spring Fest catering contract confirmed at 9500 INR with Fresh Foods.\n",
        encoding="utf-8")
    manifest = {}
    for path in sorted(OUT.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest[str(path.relative_to(OUT))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    manifest["_audio_speech_synthesized"] = audio_ok
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                       encoding="utf-8")
    print(f"Real-eval fixtures written to {OUT} (audio speech: {audio_ok})")


if __name__ == "__main__":
    main()
