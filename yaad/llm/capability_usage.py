"""Append-only local metadata log for capability tests; imposes no call or spend limits."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile


class CapabilityUsageLog:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.getenv("CAPABILITY_USAGE_PATH", "docs/capability_usage.json"))
        self.enabled = True
        try:
            self.data = self._read()
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            self.data = {"scope": "Yaad capability-suite API calls only; other team usage is not included", "calls": []}
            self.enabled = False

    def _read(self) -> dict:
        if not self.path.exists():
            return {"scope": "Yaad capability-suite API calls only; other team usage is not included", "calls": []}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value.get("calls"), list):
            raise ValueError("calls must be a list")
        return value

    def record(self, call: dict) -> None:
        if not self.enabled:
            print(f"Usage log unavailable; continuing without log write: {self.path}", file=sys.stderr)
            return
        entry = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), **call}
        self.data["calls"].append(entry)
        temp_name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix="capability-usage-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.data, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
            os.replace(temp_name, self.path)
        except OSError:
            self.enabled = False
            self.data["calls"].pop()
            print(f"Usage log write failed; continuing capability tests: {self.path}", file=sys.stderr)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
