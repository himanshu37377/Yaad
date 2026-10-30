"""Small shared standard-library transport for Meta Model API JSON and multipart calls."""
import json
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from yaad.llm.meta_config import BASE_URL


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


def request_json(api_key: str, endpoint: str, payload: dict, timeout: float = 90) -> tuple[dict, dict]:
    started = time.perf_counter()
    request = Request(
        BASE_URL + endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
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
        raise MetaAPIError(exc.code, body, duration_ms, dict(exc.headers.items())) from None


def request_multipart(api_key: str, endpoint: str, fields: dict[str, tuple[str | None, bytes, str]], timeout: float = 180) -> tuple[dict, dict]:
    boundary = "----YaadMeta" + uuid.uuid4().hex
    chunks = []
    for name, (filename, data, content_type) in fields.items():
        chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"'.encode()))
        if filename is not None:
            chunks[-1] += f'; filename="{filename}"'.encode()
        chunks.extend((b"\r\n", f"Content-Type: {content_type}\r\n\r\n".encode(), data, b"\r\n"))
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    started = time.perf_counter()
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
    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = _read_json(response)
            return response_body, {
                "status_code": response.status,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "headers": dict(response.headers.items()),
            }
    except HTTPError as exc:
        duration_ms = round((time.perf_counter() - started) * 1000)
        raw = exc.read()
        try:
            response_body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            response_body = raw.decode("utf-8", errors="replace")[:1000]
        raise MetaAPIError(exc.code, response_body, duration_ms, dict(exc.headers.items())) from None
