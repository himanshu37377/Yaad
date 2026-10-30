import re

IDENTITY_TERMS = ("roll", "admit", "passport", "account", "identity", "aadhaar", "ssn", "tax", "financial")

def is_sensitive(text: str, filename: str = "") -> bool:
    low = (filename + " " + text).lower()
    return any(term in low for term in IDENTITY_TERMS) or bool(re.search(r"\b\d{7,}\b", text))

def mask_value(value: str) -> str:
    if len(value) < 5:
        return "••••"
    return "••••" + value[-4:]
