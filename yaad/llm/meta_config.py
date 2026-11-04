"""Shared, Standard-mode Meta Model API configuration and cost accounting."""
import os
from pathlib import Path


BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
STANDARD_MODELS = frozenset({"muse-spark-1.3", "muse-spark-1.2", "muse-spark-1.1", "muse-wonder-1.0"})
MODEL_PRICES_PER_MILLION = {
    "muse-spark-1.3": {"input": 1.25, "cached_input": 0.15, "output": 4.25},
    "muse-spark-1.2": {"input": 1.25, "cached_input": 0.15, "output": 4.25},
    "muse-spark-1.1": {"input": 1.25, "cached_input": 0.15, "output": 4.25},
    "muse-wonder-1.0": {"input": 4.00, "cached_input": 0.20, "output": 20.00},
}
VOICE_PRICE_PER_HOUR = 0.18
# Voice transcription uses its own model family; it still goes through
# an explicit allowlist so an unlisted/retired voice model can never be
# sent audio silently.
VOICE_MODELS = frozenset({"muse-voice-transcribe-1.0"})


def load_project_env(env_file: Path | None = None) -> None:
    """Load simple KEY=value entries without replacing explicitly set variables."""
    path = env_file or Path.cwd() / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(name, value)


def _token_count(value: object) -> int:
    # Providers occasionally report null token fields; a null must price
    # as zero, never crash an otherwise successful call.
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def estimate_text_cost_usd(model: str, usage: dict) -> float:
    prices = MODEL_PRICES_PER_MILLION[model]
    input_tokens = _token_count((usage or {}).get("input_tokens", 0))
    cached = min(input_tokens, _token_count(((usage or {}).get("input_tokens_details") or {}).get("cached_tokens", 0)))
    output_tokens = _token_count((usage or {}).get("output_tokens", 0))
    regular = input_tokens - cached
    return (regular * prices["input"] + cached * prices["cached_input"] + output_tokens * prices["output"]) / 1_000_000


def estimate_voice_cost_usd(duration_seconds: float) -> float:
    return max(0.0, duration_seconds) / 3600 * VOICE_PRICE_PER_HOUR


def recorded_cloud_spend_usd(con) -> float:
    """Total estimated cloud spend across every egress receipt in
    this database -- the global cap's ledger."""
    row = con.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd),0) AS s FROM egress_log").fetchone()
    return float((row["s"] if row else 0) or 0)


def spend_refusal(db, settings, estimate_usd: float = 0.0) -> str | None:
    """Why a cloud call must not start, or None when it may.

    Enforces the configured global cap (Settings.cloud_spend_cap_usd;
    zero disables it) against all recorded egress plus the caller's
    estimate for the call ahead. Checked BEFORE any request is made,
    so a refusal itself spends nothing and falls back to no provider."""
    cap = float(getattr(settings, "cloud_spend_cap_usd", 0.0) or 0.0)
    if cap <= 0:
        return None
    with db.connect() as con:
        spent = recorded_cloud_spend_usd(con)
    if spent + max(0.0, estimate_usd) > cap:
        return (f"global cloud spend cap ${cap:.2f} reached "
                f"(${spent:.4f} recorded in egress receipts)")
    return None
