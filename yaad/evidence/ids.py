import hashlib
import json

def evidence_id(file_sha256: str, locator: dict) -> str:
    canonical = json.dumps(locator, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "ev_" + hashlib.sha1((file_sha256 + canonical).encode()).hexdigest()[:12]
