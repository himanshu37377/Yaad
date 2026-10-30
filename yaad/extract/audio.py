import json
import shutil
import subprocess
import tempfile
from pathlib import Path
import wave

def convert_to_wav(path: str) -> tuple[str, float]:
    # WAV is already the API's accepted upload format; avoid an unnecessary
    # ffmpeg round-trip when it is a valid uncompressed PCM recording.
    if Path(path).suffix.lower() == ".wav":
        try:
            with wave.open(path, "rb") as wav:
                if wav.getcomptype() == "NONE":
                    return path, wav.getnframes() / wav.getframerate()
        except (wave.Error, EOFError):
            pass
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to convert this audio format")
    temp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp.close()
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", path, "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", temp.name],
                   check=True, capture_output=True, timeout=180)
    with wave.open(temp.name, "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
    return temp.name, duration

def chunk_wav(path: str, limit_seconds: int = 540, overlap_seconds: int = 2):
    with wave.open(path, "rb") as source:
        rate = source.getframerate()
        frames = source.getnframes()
        stride = max(1, limit_seconds - overlap_seconds) * rate
        for start_frame in range(0, frames, stride):
            source.setpos(start_frame)
            payload = source.readframes(min(limit_seconds * rate, frames - start_frame))
            out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            out.close()
            with wave.open(out.name, "wb") as target:
                target.setparams(source.getparams())
                target.writeframes(payload)
            yield out.name, start_frame / rate
