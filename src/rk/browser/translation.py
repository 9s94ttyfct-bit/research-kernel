"""Optional Zotero translation-server (spec §8.5, §13 item 4): better metadata for pages without good meta tags.

It runs only if you start it (``npm start`` in a checkout, port 1969) and set ``translation_server_url``. ``/web``
takes a URL and returns Zotero items; ``/export?format=csljson`` turns them into CSL-JSON. Any failure returns ``None``
and the caller falls back to the page's own metadata."""

from __future__ import annotations

import httpx


class TranslationServer:
    def __init__(self, base_url: str, *, client: httpx.Client | None = None, timeout_s: float = 30.0):
        self.base = base_url.rstrip("/")
        self._http = client or httpx.Client(timeout=timeout_s)

    def web(self, url: str) -> list[dict] | None:
        try:
            r = self._http.post(f"{self.base}/web", content=url, headers={"Content-Type": "text/plain"})
        except httpx.HTTPError:
            return None
        if r.status_code != 200:  # 300 = several items to choose from, 501 = no translator
            return None
        data = r.json()
        return data if isinstance(data, list) and data else None

    def to_csl(self, items: list[dict]) -> list[dict] | None:
        try:
            r = self._http.post(f"{self.base}/export", params={"format": "csljson"}, json=items)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        data = r.json()
        return data if isinstance(data, list) and data else None

    def csl_for(self, url: str) -> dict | None:
        items = self.web(url)
        csl = self.to_csl(items) if items else None
        return csl[0] if csl else None
