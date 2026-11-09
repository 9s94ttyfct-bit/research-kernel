"""BibTeX, RIS and CSL-JSON import/export for the project library."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence

import bibtexparser
import bibtexparser.middlewares as bm
import rispy
from bibtexparser.model import Entry, Field

from rk.library.models import Creator, Ids, Item, ItemIn
from rk.sources.identifiers import normalize_arxiv

_CSL_TYPE = {
    "journalArticle": "article-journal", "preprint": "article", "book": "book", "bookSection": "chapter",
    "conferencePaper": "paper-conference", "thesis": "thesis", "report": "report", "dataset": "dataset",
    "webpage": "webpage",
}
_CSL_TYPE_REV = {v: k for k, v in _CSL_TYPE.items()} | {"article-magazine": "journalArticle", "article-newspaper": "journalArticle"}

_BIB_TYPE = {
    "journalArticle": "article", "preprint": "misc", "book": "book", "bookSection": "incollection",
    "conferencePaper": "inproceedings", "thesis": "phdthesis", "report": "techreport", "dataset": "misc",
    "webpage": "misc",
}
_BIB_TYPE_REV = {
    "article": "journalArticle", "book": "book", "incollection": "bookSection", "inbook": "bookSection",
    "inproceedings": "conferencePaper", "conference": "conferencePaper", "phdthesis": "thesis",
    "mastersthesis": "thesis", "techreport": "report", "misc": "webpage", "online": "webpage",
    "dataset": "dataset", "unpublished": "preprint",
}
_BIB_VENUE = {"journalArticle": "journal", "bookSection": "booktitle", "conferencePaper": "booktitle", "book": "publisher"}

_RIS_TYPE = {
    "journalArticle": "JOUR", "preprint": "UNPB", "book": "BOOK", "bookSection": "CHAP", "conferencePaper": "CPAPER",
    "thesis": "THES", "report": "RPRT", "dataset": "DATA", "webpage": "ELEC",
}
_RIS_TYPE_REV = {v: k for k, v in _RIS_TYPE.items()} | {"CONF": "conferencePaper", "GEN": "webpage", "EJOUR": "journalArticle"}

_STOP = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "with"}


def _ascii(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _note_ids(note: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (note or "").splitlines():
        m = re.match(r"\s*(arXiv|PMID|PMCID)\s*:\s*(\S+)", line, re.IGNORECASE)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


# ---------------- CSL-JSON ----------------
def to_csl(item: Item | ItemIn) -> dict:
    d: dict = {"type": _CSL_TYPE[item.item_type], "title": item.title}
    if isinstance(item, Item):
        d["id"] = item.id
    if item.creators:
        d["author"] = [{"family": c.family, **({"given": c.given} if c.given else {})} for c in item.creators]
    if item.year is not None:
        d["issued"] = {"date-parts": [[item.year]]}
    if item.venue:
        d["container-title"] = item.venue
    if item.abstract:
        d["abstract"] = item.abstract
    ids = item.ids
    for k, csl_k in (("doi", "DOI"), ("pmid", "PMID"), ("pmcid", "PMCID"), ("isbn", "ISBN"), ("url", "URL")):
        if getattr(ids, k):
            d[csl_k] = getattr(ids, k)
    if ids.arxiv:
        d["note"] = f"arXiv: {ids.arxiv}"
    return d


def from_csl(d: dict) -> ItemIn:
    year = None
    parts = (d.get("issued") or {}).get("date-parts") or []
    if parts and parts[0] and parts[0][0]:
        year = int(parts[0][0])
    note = _note_ids(d.get("note"))
    return ItemIn(
        item_type=_CSL_TYPE_REV.get(d.get("type", ""), "journalArticle"),
        title=d.get("title") or "(untitled)",
        creators=[Creator(family=a.get("family") or a.get("literal") or "", given=a.get("given")) for a in d.get("author", [])],
        year=year,
        venue=d.get("container-title"),
        abstract=d.get("abstract"),
        ids=Ids(doi=d.get("DOI"), pmid=d.get("PMID") or note.get("pmid"), pmcid=d.get("PMCID") or note.get("pmcid"),
                arxiv=note.get("arxiv"), isbn=d.get("ISBN"), url=d.get("URL")).normalized(),
        discovered_via="import",
        csl=d,
    )


def export_csl_json(items: Sequence[Item | ItemIn]) -> str:
    return json.dumps([to_csl(i) for i in items], ensure_ascii=False, indent=2)


def import_csl_json(text: str) -> list[ItemIn]:
    data = json.loads(text)
    return [from_csl(d) for d in (data if isinstance(data, list) else [data])]


# ---------------- BibTeX ----------------
def _cite_key(item: Item | ItemIn, used: set[str]) -> str:
    fam = re.sub(r"[^a-z]", "", _ascii(item.creators[0].family).lower()) if item.creators else "anon"
    words = [w for w in re.findall(r"[a-z0-9]+", _ascii(item.title).lower()) if w not in _STOP]
    base = f"{fam or 'anon'}{item.year or 'nd'}{words[0] if words else ''}"
    key, n = base, 0
    while key in used:
        key = base + chr(ord("a") + n)
        n += 1
    used.add(key)
    return key


def export_bibtex(items: Sequence[Item | ItemIn], *, keys: Sequence[str] | None = None) -> str:
    """BibTeX of ``items``. ``keys``: each item's cite key, in order (the export passes the item ids, so ``refs.bib``
    matches the citations of ``paper.md``); by default author, year and the first title word."""
    if keys is not None and len(keys) != len(items):
        raise ValueError("one key per item")
    lib = bibtexparser.Library()
    used: set[str] = set()
    for n, it in enumerate(items):
        fields: list[Field] = [Field("title", it.title)]
        if it.creators:
            fields.append(Field("author", " and ".join(f"{c.family}, {c.given}" if c.given else c.family for c in it.creators)))
        if it.year is not None:
            fields.append(Field("year", str(it.year)))
        if it.venue:
            fields.append(Field(_BIB_VENUE.get(it.item_type, "howpublished"), it.venue))
        ids = it.ids
        for k in ("doi", "pmid", "pmcid", "isbn", "url"):
            if getattr(ids, k):
                fields.append(Field(k, getattr(ids, k)))
        if ids.arxiv:
            fields += [Field("eprint", ids.arxiv), Field("archiveprefix", "arXiv")]
        if it.abstract:
            fields.append(Field("abstract", it.abstract))
        entry_type = _BIB_TYPE[it.item_type]
        if it.item_type == "dataset":
            fields.append(Field("type", "dataset"))
        lib.add(Entry(entry_type, keys[n] if keys is not None else _cite_key(it, used), fields))
    return bibtexparser.write_string(lib)


def import_bibtex(text: str) -> list[ItemIn]:
    lib = bibtexparser.parse_string(
        text, append_middleware=[bm.LatexDecodingMiddleware(), bm.SeparateCoAuthors(), bm.SplitNameParts()]
    )
    out: list[ItemIn] = []
    for e in lib.entries:
        f = {k.lower(): v.value for k, v in e.fields_dict.items()}
        etype = e.entry_type.lower()
        arxiv = None
        if f.get("eprint") and (str(f.get("archiveprefix", "")).lower() == "arxiv" or normalize_arxiv(f["eprint"])):
            arxiv = normalize_arxiv(f["eprint"])
        item_type = _BIB_TYPE_REV.get(etype, "journalArticle")
        if etype == "misc" and arxiv:
            item_type = "preprint"
        if etype == "misc" and str(f.get("type", "")).lower() == "dataset":
            item_type = "dataset"
        creators = []
        for np in f.get("author", []) or []:
            if isinstance(np, str):
                creators.append(Creator(family=np))
            else:
                creators.append(Creator(family=" ".join(np.von + np.last), given=" ".join(np.first) or None))
        year = int(m.group()) if (m := re.search(r"\d{4}", str(f.get("year", "")))) else None
        venue = f.get("journal") or f.get("journaltitle") or f.get("booktitle") or f.get("publisher") or f.get("howpublished")
        out.append(ItemIn(
            item_type=item_type,
            title=re.sub(r"[{}]", "", str(f.get("title", "(untitled)"))),
            creators=creators,
            year=year,
            venue=re.sub(r"[{}]", "", venue) if isinstance(venue, str) else None,
            abstract=f.get("abstract"),
            ids=Ids(doi=f.get("doi"), pmid=f.get("pmid"), pmcid=f.get("pmcid"), arxiv=arxiv, isbn=f.get("isbn"),
                    url=f.get("url")).normalized(),
            discovered_via="import",
        ))
    return out


# ---------------- RIS ----------------
def export_ris(items: Sequence[Item | ItemIn]) -> str:
    entries = []
    for it in items:
        e: dict = {"type_of_reference": _RIS_TYPE[it.item_type], "title": it.title}
        if it.creators:
            e["authors"] = [f"{c.family}, {c.given}" if c.given else c.family for c in it.creators]
        if it.year is not None:
            e["year"] = str(it.year)
        if it.venue:
            e["secondary_title"] = it.venue
        if it.abstract:
            e["abstract"] = it.abstract
        ids = it.ids
        if ids.doi:
            e["doi"] = ids.doi
        if ids.url:
            e["urls"] = [ids.url]
        if ids.isbn:
            e["issn"] = ids.isbn
        notes = [f"{lab}: {v}" for lab, v in (("PMID", ids.pmid), ("PMCID", ids.pmcid), ("arXiv", ids.arxiv)) if v]
        if notes:
            e["notes"] = notes
        entries.append(e)
    return rispy.dumps(entries)


def import_ris(text: str) -> list[ItemIn]:
    out: list[ItemIn] = []
    for e in rispy.loads(text):
        creators = []
        for a in e.get("authors", []) + e.get("first_authors", []):
            fam, _, giv = a.partition(",")
            creators.append(Creator(family=fam.strip(), given=giv.strip() or None))
        note = _note_ids("\n".join(e.get("notes", [])))
        year = int(m.group()) if (m := re.search(r"\d{4}", str(e.get("year") or e.get("publication_year") or ""))) else None
        out.append(ItemIn(
            item_type=_RIS_TYPE_REV.get(e.get("type_of_reference", ""), "journalArticle"),
            title=e.get("title") or e.get("primary_title") or "(untitled)",
            creators=creators,
            year=year,
            venue=e.get("secondary_title") or e.get("journal_name"),
            abstract=e.get("abstract"),
            ids=Ids(doi=e.get("doi"), pmid=note.get("pmid"), pmcid=note.get("pmcid"), arxiv=note.get("arxiv"),
                    isbn=e.get("issn"), url=(e.get("urls") or [None])[0]).normalized(),
            discovered_via="import",
        ))
    return out


FORMATS = {
    "bibtex": (export_bibtex, import_bibtex),
    "ris": (export_ris, import_ris),
    "csl": (export_csl_json, import_csl_json),
}
