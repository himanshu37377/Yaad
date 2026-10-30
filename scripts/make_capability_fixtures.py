"""Create small, synthetic, non-personal image and speech fixtures for API probes."""
from pathlib import Path
import argparse
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont


OUT = Path(__file__).resolve().parents[1] / "eval" / "generated"


def font(size: int):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for name in candidates:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def draw_still_life(path: Path, mug_color: str, book_color: str) -> None:
    image = Image.new("RGB", (640, 440), "#e9eef2")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 310, 640, 440), fill="#b98e68")
    draw.line((0, 310, 640, 310), fill="#99714e", width=4)
    # Potted plant
    draw.rectangle((493, 223, 572, 307), fill="#bd7058")
    draw.ellipse((493, 211, 572, 236), fill="#d28a6e")
    draw.line((533, 226, 532, 142), fill="#55704e", width=8)
    for box in [(495, 150, 535, 185), (529, 125, 572, 163), (502, 180, 544, 211), (538, 164, 579, 198)]:
        draw.ellipse(box, fill="#78916a")
    # Closed book
    draw.rounded_rectangle((140, 254, 372, 300), radius=5, fill=book_color, outline="#304152", width=3)
    draw.line((154, 263, 154, 291), fill="#e9e1ce", width=3)
    # Mug and handle
    draw.ellipse((393, 236, 489, 276), fill="#f2f0e9", outline="#4a555e", width=3)
    draw.rounded_rectangle((399, 248, 482, 307), radius=9, fill=mug_color, outline="#4a555e", width=3)
    draw.arc((462, 253, 510, 299), start=270, end=90, fill="#4a555e", width=8)
    draw.line((411, 266, 470, 266), fill="#ffffff", width=3)
    image.save(path, format="PNG", optimize=True)


def draw_screenshot(path: Path) -> None:
    image = Image.new("RGB", (900, 560), "#f3f5f8")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 900, 78), fill="#26384d")
    draw.text((44, 25), "ALPINE TRAVEL  /  BOOKING DETAILS", font=font(27), fill="white")
    draw.text((54, 125), "TRIP SUMMARY", font=font(20), fill="#49627c")
    draw.text((54, 185), "Booking ID: TR-1842", font=font(29), fill="#172435")
    draw.text((54, 245), "Travel date: 2026-08-17", font=font(29), fill="#172435")
    draw.text((54, 305), "Destination: Bergen", font=font(29), fill="#172435")
    draw.line((54, 369, 842, 369), fill="#cbd3dd", width=2)
    draw.text((54, 405), "TOTAL", font=font(19), fill="#53677d")
    draw.text((54, 444), "$286.50", font=font(36), fill="#1d573c")
    image.save(path, format="PNG", optimize=True)


def draw_table(path: Path) -> None:
    image = Image.new("RGB", (900, 560), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((42, 30), "WAREHOUSE STOCK — SAMPLE REPORT", font=font(28), fill="#243449")
    x0, y0, widths, row_h = 42, 100, [360, 210, 230], 74
    headers = ["Item", "Quantity", "Value"]
    rows = [["USB cables", "18", "$45"], ["Adapters", "12", "$72"], ["Laptop cases", "9", "$135"]]
    for r, cells in enumerate([headers] + rows):
        y = y0 + r * row_h
        bg = "#dbe5ef" if r == 0 else ("#f7f9fb" if r % 2 else "#ffffff")
        draw.rectangle((x0, y, x0 + sum(widths), y + row_h), fill=bg, outline="#aeb9c5", width=2)
        x = x0
        for c, (cell, width) in enumerate(zip(cells, widths)):
            draw.text((x + 16, y + 23), cell, font=font(24 if r == 0 else 23), fill="#263746")
            x += width
            if c < len(widths) - 1:
                draw.line((x, y, x, y + row_h), fill="#aeb9c5", width=2)
    image.save(path, format="PNG", optimize=True)


def draw_chart(path: Path) -> None:
    image = Image.new("RGB", (900, 560), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((42, 30), "QUARTERLY SIGNUPS (SAMPLE)", font=font(28), fill="#243449")
    left, top, bottom = 120, 115, 460
    draw.line((left, top, left, bottom), fill="#516273", width=3)
    draw.line((left, bottom, 790, bottom), fill="#516273", width=3)
    for value in (0, 20, 40, 60, 80):
        y = bottom - value * 4
        draw.line((left, y, 790, y), fill="#e3e8ed", width=1)
        draw.text((65, y - 11), str(value), font=font(17), fill="#53677d")
    values = [("Q1", 42), ("Q2", 68), ("Q3", 55)]
    for i, (label, value) in enumerate(values):
        x = 220 + i * 190
        y = bottom - value * 4
        draw.rectangle((x, y, x + 95, bottom), fill=["#748da6", "#54795f", "#b18a5b"][i])
        draw.text((x + 26, y - 35), str(value), font=font(21), fill="#263746")
        draw.text((x + 27, bottom + 13), label, font=font(21), fill="#263746")
    image.save(path, format="PNG", optimize=True)


def make_speech(path: Path) -> bool:
    if not shutil.which("say") or not shutil.which("afconvert"):
        return False
    with tempfile.TemporaryDirectory(prefix="yaad-synthetic-speech-") as temp:
        aiff = Path(temp) / "speech.aiff"
        subprocess.run(
            ["say", "-o", str(aiff), "The synthetic project deadline is November twelve, twenty twenty six."],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["afconvert", str(aiff), str(path), "-f", "WAVE", "-d", "LEI16@24000", "-c", "1"],
            check=True, capture_output=True, timeout=30,
        )
    return True


def main() -> None:
    # Parse flags first: --help (and bad flags) must never rewrite fixtures.
    argparse.ArgumentParser(
        description="Create small, synthetic, non-personal image and speech "
                    "fixtures for API probes.").parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    draw_still_life(OUT / "synthetic_still_life.png", "#6688a5", "#b05c50")
    draw_still_life(OUT / "synthetic_still_life_variant.png", "#d2aa54", "#4c6e8f")
    draw_screenshot(OUT / "synthetic_screenshot.png")
    draw_table(OUT / "synthetic_table.png")
    draw_chart(OUT / "synthetic_chart.png")
    if make_speech(OUT / "synthetic_voice.wav"):
        print("Created a short synthetic WAV at 24 kHz, mono, 16-bit PCM.")
    else:
        print("Speech fixture skipped: macOS say and afconvert are unavailable.")
    print(f"Synthetic capability fixtures written to {OUT}")


if __name__ == "__main__":
    main()
