"""Inspect generated PDF requests and responses directly in the terminal."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yaad.llm.meta_api import MetaAPIError, request_json
from yaad.llm.meta_config import DEFAULT_MODEL, MODEL_PRICES_PER_MILLION, STANDARD_MODELS, estimate_text_cost_usd, load_project_env
from yaad.llm.capability_usage import CapabilityUsageLog

FIXTURES = ROOT / "eval" / "generated"
PROMPT = "Read this synthetic test document. Return its document text and key-value fields as concise JSON. Do not infer information that is not present."


def extract_text(raw: bytes) -> str | None:
    try:
        import pymupdf as pdf_library
    except ImportError:
        try:
            import fitz as pdf_library
        except ImportError:
            return None
    pdf = pdf_library.open(stream=raw, filetype="pdf")
    try:
        return "\n".join(page.get_text().strip() for page in pdf)
    finally:
        pdf.close()


def visible_text(response: dict) -> str:
    if response.get("output_text"):
        return response["output_text"]
    return "\n".join(
        block.get("text", "")
        for item in response.get("output", [])
        for block in item.get("content", [])
        if block.get("type") in ("output_text", "text")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Send synthetic PDF fixtures to Meta")
    parser.add_argument("--model", choices=sorted(STANDARD_MODELS), default=DEFAULT_MODEL)
    parser.add_argument("--effort", choices=("minimal", "low", "medium", "high", "xhigh", "max"), default="low")
    args = parser.parse_args()
    load_project_env(ROOT / ".env")
    print("Meta Model API PDF capability check")
    print(f"model={args.model} | endpoint=POST https://api.meta.ai/v1/responses | effort={args.effort}")
    print(f"Standard mode prices: ${MODEL_PRICES_PER_MILLION[args.model]['input']}/1M input, ${MODEL_PRICES_PER_MILLION[args.model]['output']}/1M output")
    print("API key present:", bool(os.getenv("MODEL_API_KEY")), "(value hidden)")
    print("Video is out of scope.")

    if not args.run:
        print("No API calls made. Add --run to send the synthetic PDFs.")
        return
    key = os.getenv("MODEL_API_KEY")
    if not key:
        raise SystemExit("MODEL_API_KEY missing; no API calls made.")
    files = sorted(FIXTURES.glob("*.pdf"))
    if not files:
        raise SystemExit("No generated PDFs found. Run scripts/make_synthetic_data.py first.")
    usage_log = CapabilityUsageLog(ROOT / "docs" / "capability_usage.json")

    jobs = []
    for path in files:
        raw = path.read_bytes()
        if len(raw) > 50_000_000:
            raise SystemExit(f"{path.name} exceeds Meta's 50 MB inline-file limit; no generation requests sent.")
        payload = {
            "model": args.model,
            "reasoning": {"effort": args.effort, "summary": "auto"},
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": PROMPT},
                {"type": "input_file", "filename": path.name,
                 "file_data": "data:application/pdf;base64," + base64.b64encode(raw).decode("ascii")},
            ]}],
        }
        jobs.append((path, raw, payload))

    for path, raw, payload in jobs:
        extracted = extract_text(raw)
        print("\n" + "=" * 76)
        print(f"FILE SENT: {path.name} | {len(raw)} PDF bytes")
        print("LOCAL TEXT FOR COMPARISON:")
        print(extracted if extracted is not None else "<PyMuPDF is not installed; the PDF bytes below are still sent>")
        if extracted == "":
            print("<no embedded text; this fixture may be scanned>")
        displayed = json.loads(json.dumps(payload))
        displayed["input"][0]["content"][1]["file_data"] = f"<base64 PDF bytes omitted: {len(raw)} bytes>"
        print("\nREQUEST BODY (file bytes abbreviated):")
        print(json.dumps(displayed, indent=2, ensure_ascii=False))
        started = time.perf_counter()
        try:
            response, info = request_json(key, "/responses", payload, timeout=90)
            usage = response.get("usage") or {}
            actual_cost = estimate_text_cost_usd(args.model, usage)
            output = visible_text(response)
            print(f"\nHTTP STATUS: {info['status_code']} | LATENCY: {info['duration_ms']} ms")
            print("RESPONSE ID:", response.get("id", "<not returned>"))
            print("VISIBLE MODEL OUTPUT:\n" + (output or "<empty output>"))
            for item in response.get("output", []):
                if item.get("type") == "reasoning":
                    for summary in item.get("summary", []):
                        if summary.get("text"):
                            print("REASONING SUMMARY:\n" + summary["text"])
            print("TOKEN USAGE:", json.dumps(usage, ensure_ascii=False))
            print(f"ESTIMATED COST: ${actual_cost:.6f}")
            entry = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "model": args.model, "endpoint": "/responses",
                     "status_code": info["status_code"], "duration_ms": info["duration_ms"], "input_tokens": usage.get("input_tokens"),
                     "output_tokens": usage.get("output_tokens"), "cached_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens"),
                     "cost_usd": actual_cost, "fixture": path.name}
            usage_log.record(entry)
        except MetaAPIError as exc:
            print(f"API ERROR: HTTP {exc.status_code}; no automatic retry.")
            usage_log.record({"timestamp_utc": datetime.now(timezone.utc).isoformat(), "model": args.model, "endpoint": "/responses",
                           "status_code": exc.status_code, "duration_ms": exc.duration_ms, "cost_usd": None,
                           "fixture": path.name})
        except Exception as exc:
            print(f"REQUEST ERROR: {type(exc).__name__}; no automatic retry.")
            usage_log.record({"timestamp_utc": datetime.now(timezone.utc).isoformat(), "model": args.model, "endpoint": "/responses",
                           "status_code": None, "duration_ms": round((time.perf_counter() - started) * 1000),
                           "cost_usd": None, "fixture": path.name})
    print(f"\nCapability usage log: {usage_log.path}")


if __name__ == "__main__":
    main()
