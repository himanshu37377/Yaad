"""Generate synthetic admit-card/deadline PDFs and a question set."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT = Path("eval/generated")

def make_pdf(path, title, pairs):
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 18); c.drawString(72, 730, title)
    c.setFont("Helvetica", 12)
    y = 680
    for key, value in pairs:
        c.drawString(72, y, f"{key}: {value}")
        y -= 30
    c.save()

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    docs = [
        ("synthetic_admit_card.pdf", "Synthetic Admit Card", [("Candidate", "Example Person"), ("Roll number", "YAAD-482731"), ("Exam date", "2026-11-02")]),
        ("deadline_notice_a.pdf", "Synthetic Application Notice", [("Application deadline", "2026-11-10"), ("Fee", "1250 INR")]),
        ("deadline_notice_b.pdf", "Synthetic Revised Notice", [("Application deadline", "2026-11-12"), ("Fee", "1250 INR")]),
    ]
    for name, title, rows in docs:
        make_pdf(OUT / name, title, rows)
    questions = [
        {"question":"What is the roll number on the admit card?","file":"synthetic_admit_card.pdf","value":"YAAD-482731","page":1},
        {"question":"What is the exam date?","file":"synthetic_admit_card.pdf","value":"2026-11-02","page":1},
        {"question":"What is the application deadline in the first notice?","file":"deadline_notice_a.pdf","value":"2026-11-10","page":1},
        {"question":"What is the applicant's blood group?","file":None,"value":None,"page":None},
    ]
    (OUT / "questions.jsonl").write_text("".join(json.dumps(q)+"\n" for q in questions), encoding="utf-8")
    if shutil.which("say") and shutil.which("ffmpeg"):
        with tempfile.NamedTemporaryFile(suffix=".aiff") as speech:
            subprocess.run(["say", "-o", speech.name, "The deposit is fifty dollars, due on November fifth."], check=True)
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", speech.name, "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(OUT / "synthetic_deposit_note.wav")],
                           check=True, capture_output=True)
    else:
        print("Skipped synthetic speech: macOS say and ffmpeg are needed.")
    print(f"Synthetic data written to {OUT}")

if __name__ == "__main__":
    main()
