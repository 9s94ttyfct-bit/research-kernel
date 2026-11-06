"""Crossref REST adapter (works by DOI, CSL transform, update/retraction notices)."""

from __future__ import annotations

import re
from urllib.parse import quote

from pydantic import BaseModel

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import HttpClient

BASE = "https://api.crossref.org"

_TYPE = {
    "journal-article": "journalArticle", "posted-content": "preprint", "proceedings-article": "conferencePaper",
    "book": "book", "monograph": "book", "edited-book": "book", "book-chapter": "bookSection",
    "dataset": "dataset", "dissertation": "thesis", "report": "report",
}


class Update(BaseModel):
    type: str
    source: str
    doi: str | None = None
    date: str | None = None


class CrossrefWork(BaseModel):
    item: ItemIn
    updates: list[Update]
    raw: dict


def _year(m: dict) -> int | None:
    for k in ("issued", "published", "published-print", "published-online", "created"):
        parts = (m.get(k) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def _strip_jats(s: str | None) -> str | None:
    if not s:
        return None
    t = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", t).strip() or None


def _date(d: dict | None) -> str | None:
    parts = (d or {}).get("date-parts") or []
    if parts and parts[0]:
        return "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts[0]))
    return None


def message_to_item(m: dict) -> ItemIn:
    return ItemIn(
        item_type=_TYPE.get(m.get("type", ""), "report"),
        title=(m.get("title") or ["(untitled)"])[0],
        creators=[
            Creator(family=a.get("family") or a.get("name") or "", given=a.get("given"))
            for a in m.get("author", [])
            if a.get("family") or a.get("name")
        ],
        year=_year(m),
        venue=(m.get("container-title") or [None])[0],
        abstract=_strip_jats(m.get("abstract")),
        ids=Ids(doi=m.get("DOI"), isbn=(m.get("ISBN") or [None])[0], url=m.get("URL")).normalized(),
        discovered_via="search",
    )


def get_work(http: HttpClient, doi: str, mailto: str | None) -> CrossrefWork | None:
    data, res = http.get_json(f"{BASE}/works/{quote(doi, safe='/()')}", {"mailto": mailto} if mailto else None)
    if res.status == 404 or not data:
        return None
    m = data["message"]
    updates = [
        Update(
            type=u.get("type", "unknown"),
            source=u.get("source", "publisher"),
            doi=(u.get("DOI") or "").lower() or None,
            date=_date(u.get("updated")),
        )
        for u in m.get("updated-by", [])
    ]
    return CrossrefWork(item=message_to_item(m), updates=updates, raw=m)


def csl(http: HttpClient, doi: str, mailto: str | None) -> dict | None:
    data, res = http.get_json(
        f"{BASE}/works/{quote(doi, safe='/()')}/transform/application/vnd.citationstyles.csl+json",
        {"mailto": mailto} if mailto else None,
    )
    return data if res.status == 200 else None
