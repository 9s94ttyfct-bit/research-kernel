"""Local fixture websites for browser tests: no network, synthetic pages that mirror the structure of the real sites.

``Sites`` runs a ``ThreadingHTTPServer`` on 127.0.0.1. Routes are plain functions ``(handler) -> (status, headers,
body)``; tests add or replace routes per case (``sites.route("/path", fn)``)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

Response = tuple[int, dict[str, str], bytes]
Route = Callable[["Request"], Response]

FIXTURES = Path(__file__).parent / "fixtures" / "browser"


class Request:
    def __init__(self, h: BaseHTTPRequestHandler):
        parts = urlsplit(h.path)
        self.path, self.query = parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.headers = h.headers
        c = SimpleCookie(h.headers.get("Cookie") or "")
        self.cookies = {k: m.value for k, m in c.items()}


def html(body: str, status: int = 200, headers: dict | None = None) -> Response:
    return status, {"Content-Type": "text/html; charset=utf-8", **(headers or {})}, body.encode()


def redirect(location: str, headers: dict | None = None) -> Response:
    return 302, {"Location": location, **(headers or {})}, b""


def pdf(raw: bytes) -> Response:
    return 200, {"Content-Type": "application/pdf"}, raw


def tiny_pdf(text: str = "Fixture PDF text about perovskite stability.") -> bytes:
    from fpdf import FPDF

    doc = FPDF(unit="pt", format="A4")  # as tests/fixtures/docs/make_pdfs.py: glyphs inside the page box
    doc.add_page()
    doc.add_font("U", fname="/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    doc.set_font("U", size=12)
    doc.text(72, 100, text)
    return bytes(doc.output())


class Sites:
    def __init__(self):
        self.routes: dict[str, Route] = {}
        self.hits: list[str] = []
        self.posts: list[str] = []  # paths of POST requests (form submissions)
        sites = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                req = Request(self)
                sites.hits.append(self.path)
                fn = sites.routes.get(req.path)
                status, headers, body = fn(req) if fn else html("<h1>not found</h1>", 404)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                sites.posts.append(Request(self).path)
                body = b"<title>submitted</title>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return self.base + path

    def route(self, path: str, fn: Route) -> None:
        self.routes[path] = fn

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
