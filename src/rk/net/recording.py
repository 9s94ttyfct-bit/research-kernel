"""httpx transports that record live responses to fixture files and replay them offline."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx

from rk.canon import canonical_json, sha256_hex
from rk.net.http import SECRET_PARAMS

_TEXT_TYPES = ("json", "xml", "text", "html", "atom")


def request_key(method: str, url: httpx.URL | str) -> tuple[str, dict]:
    u = urlsplit(str(url))
    params = sorted((k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() not in SECRET_PARAMS)
    ident = {"method": method.upper(), "url": f"{u.scheme}://{u.netloc}{u.path}", "params": params}
    return sha256_hex(canonical_json(ident))[:24], ident


class RecordingTransport(httpx.BaseTransport):
    def __init__(self, out_dir: Path, inner: httpx.BaseTransport | None = None):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.inner = inner or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = self.inner.handle_request(request)
        resp.read()
        key, ident = request_key(request.method, request.url)
        ctype = resp.headers.get("content-type", "")
        textual = any(t in ctype for t in _TEXT_TYPES)
        headers = {k.lower(): v for k, v in resp.headers.items()
                   if k.lower() in ("content-type", "retry-after", "x-ratelimit-remaining", "x-ratelimit-reset", "location")}
        record = {
            **ident,
            "status": resp.status_code,
            "headers": headers,
            "body": resp.content.decode("utf-8", "replace") if textual else base64.b64encode(resp.content).decode(),
            "encoding": "text" if textual else "base64",
        }
        (self.out_dir / f"{key}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        clean = [(k, v) for k, v in resp.headers.multi_items() if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(resp.status_code, headers=clean, content=resp.content, request=request)


class UnrecordedRequest(AssertionError):
    pass


class ReplayTransport(httpx.BaseTransport):
    def __init__(self, fixture_dir: Path):
        self.fixture_dir = Path(fixture_dir)
        self.calls: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key, ident = request_key(request.method, request.url)
        p = self.fixture_dir / f"{key}.json"
        if not p.exists():
            raise UnrecordedRequest(f"no fixture for {ident}")
        rec = json.loads(p.read_text(encoding="utf-8"))
        body = rec["body"].encode() if rec["encoding"] == "text" else base64.b64decode(rec["body"])
        self.calls.append(ident["url"])
        return httpx.Response(rec["status"], headers=rec["headers"], content=body, request=request)
