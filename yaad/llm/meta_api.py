"""Small shared standard-library transport for Meta Model API JSON and multipart calls."""
import json
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from yaad.llm.meta_config import BASE_URL

# Bounded retries for transient failures only: 429 (rate limited) and
# 5xx are worth another attempt; every other status is permanent and
# raises on the first response. Retry-After wins when the server
# sends it, otherwise exponential backoff 0.5s/1s/2s capped at 8s.
MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 8.0


class MetaAPIError(RuntimeError):
    def __init__(self, status_code: int, body: dict | str, duration_ms: int, headers: dict):
        super().__init__(f"Meta API returned HTTP {status_code}")
        self.status_code = status_code
        self.body = body
        self.duration_ms = duration_ms
        self.headers = headers


def _read_json(response) -> dict:
    raw = response.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Meta API returned invalid JSON") from exc


def _retry_delay(exc: HTTPError | None, attempt: int) -> float:
    """Seconds to wait before attempt+1."""
    from email.utils import parsedate_to_datetime

    headers = getattr(exc, "headers", None) if exc is not None else None
    raw = headers.get("Retry-After") if headers is not None else None
    if raw:
        try:
            return min(max(float(raw), 0.0), _BACKOFF_CAP_S)
        except (TypeError, ValueError):
            pass
        try:  # HTTP-date form, e.g. "Wed, 21 Oct 2015 07:28:00 GMT"
            import datetime

            when = parsedate_to_datetime(str(raw))
            if when is not None:
                delta = (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
                return min(max(delta, 0.0), _BACKOFF_CAP_S)
        except (TypeError, ValueError):
            pass
    return min(_BACKOFF_BASE_S * (2 ** attempt), _BACKOFF_CAP_S)


def _send(request, timeout: float, sleep) -> tuple[dict, dict]:
    """Send once, retrying transient failures up to MAX_ATTEMPTS total
    attempts. The final failure propagates exactly as a single attempt
    would have: MetaAPIError for HTTP responses, the original
    URLError/timeout for transport failures. `sleep` is injectable so
    tests never wait."""
    started = time.perf_counter()
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urlopen(request, timeout=timeout) as response:
                body = _read_json(response)
                return body, {
                    "status_code": response.status,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "headers": dict(response.headers.items()),
                }
        except HTTPError as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            raw = exc.read()
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = raw.decode("utf-8", errors="replace")[:1000]
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt == MAX_ATTEMPTS - 1:
                raise MetaAPIError(exc.code, body, duration_ms,
                                   dict(exc.headers.items())) from None
            sleep(_retry_delay(exc, attempt))
        except (URLError, TimeoutError, OSError):
            if attempt == MAX_ATTEMPTS - 1:
                raise
            sleep(_retry_delay(None, attempt))
    raise AssertionError("retry loop exhausted")  # pragma: no cover


def request_json(api_key: str, endpoint: str, payload: dict, timeout: float = 90,
                 sleep=time.sleep) -> tuple[dict, dict]:
    request = Request(
        BASE_URL + endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    return _send(request, timeout, sleep)


def request_multipart(api_key: str, endpoint: str, fields: dict[str, tuple[str | None, bytes, str]], timeout: float = 180,
                      sleep=time.sleep) -> tuple[dict, dict]:
    boundary = "----YaadMeta" + uuid.uuid4().hex
    chunks = []
    for name, (filename, data, content_type) in fields.items():
        chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"'.encode()))
        if filename is not None:
            chunks[-1] += f'; filename="{filename}"'.encode()
        chunks.extend((b"\r\n", f"Content-Type: {content_type}\r\n\r\n".encode(), data, b"\r\n"))
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    request = Request(
        BASE_URL + endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    return _send(request, timeout, sleep)
