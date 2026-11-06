"""bioRxiv / medRxiv details adapter."""

from __future__ import annotations

from rk.library.models import Creator, Ids, ItemIn
from rk.net.http import HttpClient

BASE = "https://api.biorxiv.org/details"


def _creators(s: str) -> list[Creator]:
    out = []
    for a in (s or "").split(";"):
        fam, _, giv = a.strip().partition(",")
        if fam.strip():
            out.append(Creator(family=fam.strip(), given=giv.strip() or None))
    return out


def details(http: HttpClient, doi: str) -> ItemIn | None:
    for server in ("biorxiv", "medrxiv"):
        data, res = http.get_json(f"{BASE}/{server}/{doi}")
        coll = (data or {}).get("collection") or []
        if res.status == 200 and coll:
            c = coll[-1]
            return ItemIn(
                item_type="preprint",
                title=c.get("title") or "(untitled)",
                creators=_creators(c.get("authors", "")),
                year=int(c["date"][:4]) if c.get("date", "")[:4].isdigit() else None,
                venue=server,
                abstract=c.get("abstract"),
                ids=Ids(doi=c.get("doi")).normalized(),
                oa_urls=[f"https://www.{server}.org/content/{c.get('doi')}v{c.get('version', 1)}.full.pdf"],
                discovered_via="search",
            )
    return None
