"""``rk setup``: guided configuration of contact email and free API keys (spec §8.8, CLI version)."""

from __future__ import annotations

from collections.abc import Callable

from rk.config import Settings, load_settings, save_settings
from rk.net.http import HttpClient

OPENALEX_URL = "https://openalex.org/settings/api"
S2_URL = "https://www.semanticscholar.org/product/api#api-key-form"

Ask = Callable[[str, str], str]
Say = Callable[[str], None]


def check_crossref(http: HttpClient, email: str) -> bool:
    res = http.get("https://api.crossref.org/works/10.7717/peerj.1364", {"mailto": email}, ttl_s=0)
    return res.status == 200


def check_openalex(http: HttpClient, key: str) -> dict | None:
    res = http.get("https://api.openalex.org/works/W2741809807", {"api_key": key, "select": "id"}, ttl_s=0)
    if res.status != 200:
        return None
    return {"remaining": res.headers.get("x-ratelimit-remaining"), "limit": res.headers.get("x-ratelimit-limit")}


def check_s2(http: HttpClient, key: str) -> bool:
    res = http.get("https://api.semanticscholar.org/graph/v1/paper/search", {"query": "test", "limit": 1},
                   headers={"x-api-key": key}, ttl_s=0)
    return res.status == 200


def run_setup(http: HttpClient, ask: Ask, say: Say) -> tuple[Settings, dict[str, str]]:
    s = load_settings()
    caps: dict[str, str] = {}

    say("Step 1/3 - contact email (sent to Crossref/Unpaywall as 'mailto'; never stored in events or exports).")
    email = ask("Email (Enter to skip)", s.email or "").strip()
    if email:
        caps["crossref"] = "available (polite pool)" if check_crossref(http, email) else "limited (check failed)"
        s.email = s.unpaywall_email = email
    else:
        caps["crossref"] = "limited (public pool, no mailto)"

    say(f"Step 2/3 - OpenAlex key (free). Create an account and copy your key from {OPENALEX_URL} ; "
        "click the activation e-mail first. Without a key all callers on your IP share $0.10/day.")
    key = ask("OpenAlex key (Enter to skip)", s.openalex_key or "").strip()
    if key:
        info = check_openalex(http, key)
        if info:
            s.openalex_key = key
            caps["openalex"] = f"available (daily budget remaining {info['remaining']})"
        else:
            caps["openalex"] = "missing (key rejected - not saved)"
    else:
        caps["openalex"] = "limited (no key: shared $0.10/day)"

    say(f"Step 3/3 - Semantic Scholar key (free, by request). Apply at {S2_URL} ; describe the use as "
        "'personal academic research'. Keys arrive by e-mail, so you can come back later with `rk setup`.")
    s2 = ask("Semantic Scholar key (Enter if pending)", s.s2_key or "").strip()
    if s2:
        if check_s2(http, s2):
            s.s2_key = s2
            caps["semantic_scholar"] = "available (1 req/s)"
        else:
            caps["semantic_scholar"] = "missing (key rejected - not saved)"
    else:
        caps["semantic_scholar"] = "pending (apply via the form, then rerun rk setup)"

    for src in ("europepmc", "pubmed", "arxiv", "biorxiv"):
        caps[src] = "available (no key needed)"
    save_settings(s)
    return s, caps
