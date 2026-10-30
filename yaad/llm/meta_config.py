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


def estimate_text_cost_usd(model: str, usage: dict) -> float:
    prices = MODEL_PRICES_PER_MILLION[model]
    input_tokens = max(0, int(usage.get("input_tokens", 0)))
    cached = min(input_tokens, max(0, int((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))))
    output_tokens = max(0, int(usage.get("output_tokens", 0)))
    regular = input_tokens - cached
    return (regular * prices["input"] + cached * prices["cached_input"] + output_tokens * prices["output"]) / 1_000_000


def estimate_voice_cost_usd(duration_seconds: float) -> float:
    return max(0.0, duration_seconds) / 3600 * VOICE_PRICE_PER_HOUR
