"""Ollama-compatible local model adapter. Same ModelClient shape as MetaClient.

Only ever talks to the configured local endpoint (loopback by default); it
cannot construct cloud requests. Unverified against real weights: covered by
fake-server contract tests until run against Ollama + Muse Glimmer.
"""
import base64
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


JSON_INSTRUCTION = "Return ONLY a JSON object matching the requested schema. No prose, no fences."

LOCAL_TIMEOUT = 300.0


class LocalModelError(RuntimeError):
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


class LocalUnsupportedError(LocalModelError):
    def __init__(self, message: str):
        super().__init__(message, "local_unsupported")


class LocalClient:
    """Minimal Ollama /api/chat adapter; never used for cloud calls."""

    def __init__(self, settings):
        self.settings = settings
        self.endpoint = str(settings.local_endpoint or "http://localhost:11434").rstrip("/")
        self.model = settings.local_model
        self.last_call = {}

    def _record_call(self, status_code=None, success=None, duration_ms=None,
                     input_tokens=None, output_tokens=None, error_code=None):
        # Token counts are runtime-reported stats, not billed usage; local
        # inference has no API cost, so the estimate is always exactly 0.
        self.last_call = {
            "provider": "local",
            "model": self.model,
            "endpoint": "/api/chat",
            "status_code": status_code,
            "success": success,
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": None,
            "estimated_cost_usd": 0.0,
            "error_code": error_code,
        }

    def _chat(self, messages: list[dict], schema: dict, timeout: float = LOCAL_TIMEOUT) -> dict:
        started = time.perf_counter()
        payload = {"model": self.model,
                   "messages": [{"role": "system", "content": JSON_INSTRUCTION}] + messages,
                   "format": schema, "stream": False, "options": {"temperature": 0}}
        request = Request(
            self.endpoint + "/api/chat",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
                status = response.status
        except HTTPError as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 404 and "not found" in detail.lower():
                self._record_call(exc.code, False, duration_ms, error_code="local_model_missing")
                raise LocalModelError(
                    f"Local model '{self.model}' is not installed. Run: ollama pull {self.model}",
                    "local_model_missing") from None
            self._record_call(exc.code, False, duration_ms, error_code=f"http_{exc.code}")
            raise LocalModelError(
                f"Local model request failed (HTTP {exc.code}). Is the runtime up at {self.endpoint}?",
                f"http_{exc.code}") from None
        except (OSError, URLError, TimeoutError) as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            self._record_call(None, False, duration_ms, error_code="local_unreachable")
            raise LocalModelError(
                f"Local model did not respond at {self.endpoint}. Start Ollama and retry.",
                "local_unreachable") from exc
        duration_ms = round((time.perf_counter() - started) * 1000)
        try:
            body = json.loads(raw.decode("utf-8"))
            content = body["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            self._record_call(status, False, duration_ms, error_code="invalid_json")
            raise LocalModelError("Local model returned an unreadable response.",
                                  "invalid_json") from None
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            self._record_call(status, False, duration_ms, error_code="malformed_structured_response")
            raise LocalModelError("Local model returned output outside the requested schema.",
                                  "malformed_structured_response") from None
        self._record_call(status, True, duration_ms, body.get("prompt_eval_count"),
                          body.get("eval_count"))
        return parsed

    def reason_json(self, messages: list[dict], schema: dict, prompt_id: str) -> dict:
        return self._chat(messages, schema)

    def vision_extract(self, image_bytes: bytes, schema: dict, prompt_id: str) -> dict:
        return self._chat([{"role": "user",
                            "content": f"Analyze the attached image and return ONLY JSON matching the requested schema. Task: {prompt_id}.",
                            "images": [base64.b64encode(image_bytes).decode()]}], schema)

    def transcribe(self, audio_path: str) -> list[dict]:
        self._record_call(None, False, 0, error_code="local_unsupported")
        raise LocalUnsupportedError(
            "Audio transcription is not supported by the local model "
            "(Muse Glimmer has no audio input). Index the file with cloud "
            "processing and consent, or keep it untranscribed.")

    def understand_pdf(self, pdf_bytes: bytes, filename: str, page_texts) -> dict:
        self._record_call(None, False, 0, error_code="local_unsupported")
        raise LocalUnsupportedError(
            "PDF document understanding is not available in local mode. "
            "Local PDF text is still indexed and searchable.")

    def describe_photo(self, image_bytes: bytes) -> dict:
        self._record_call(None, False, 0, error_code="local_unsupported")
        raise LocalUnsupportedError("Photo description is not available in local mode.")
