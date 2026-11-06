"""``rk setup``: guided configuration (spec §8.8). Steps: 1 contact email, 2 OpenAlex key, 3 Semantic Scholar key,
4 model providers, 5 library access in the built-in browser, 6 capability list. The Workbench settings page runs the
same checks through ``/ui/api/setup/check/{step}``."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from rk.config import Settings, check_library_proxy, load_settings, save_settings
from rk.llm.drivers import available_all
from rk.net.http import HttpClient

OPENALEX_URL = "https://openalex.org/settings/api"
S2_URL = "https://www.semanticscholar.org/product/api#api-key-form"
SETUP_PROJECT = "setup-check"  # scratch project for the library access test, kept apart from your own libraries

Ask = Callable[[str, str], str]
Say = Callable[[str], None]
Call = Callable[..., dict]  # RkClient.call(name, args, write=...)

PROVIDER_HINTS = {
    "claude_cli": "run `claude` in a terminal and log in",
    "codex_cli": "run `codex login`",
    "ollama": "start Ollama (`ollama serve`); for cloud models run `ollama signin`",
    "anthropic_api": "add an Anthropic API key (Settings or ANTHROPIC_API_KEY)",
    "openai_api": "add an OpenAI API key (Settings or OPENAI_API_KEY)",
    "omp_gateway": "optional: install omp and log in, then set omp_gateway = true",
}


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


def check_providers(settings: Settings, available: Callable = available_all) -> dict[str, dict]:
    """Step 4: every configured driver, the roles that use it, whether it works now and how to fix it."""
    out: dict[str, dict] = {}
    for role, (spec, avail) in available(settings).items():
        name = str(spec)
        entry = out.setdefault(name, {"roles": [], "ok": avail.ok, "reason": None if avail.ok else avail.reason,
                                      "hint": None if avail.ok else PROVIDER_HINTS.get(spec.provider)})
        entry["roles"].append(role)
    for entry in out.values():
        entry["roles"].sort()
    return out


def _http_allowed(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme == "https" or (p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost", "::1"))


def library_settings(s: Settings, *, name: str, portal_url: str, proxy: str | None, discovery: str | None = None,
                     primo_base: str | None = None, primo_vid: str | None = None,
                     summon_base: str | None = None) -> Settings:
    """Step 5 values, validated: https everywhere (plain http only for a proxy on this computer)."""
    if not _http_allowed(portal_url):
        raise ValueError("the library start page must be an https URL")
    if proxy and proxy.startswith("http://") and _http_allowed(proxy.replace("{url}", "").replace("{url_enc}", "")):
        if "{url}" not in proxy and "{url_enc}" not in proxy:
            raise ValueError("the library proxy must contain {url} or {url_enc}")
        checked = proxy.strip()
    else:
        checked = check_library_proxy(proxy)
    discovery = None if discovery in (None, "", "none") else discovery
    if discovery not in (None, "primo", "summon"):
        raise ValueError(f"discovery system must be primo, summon or none, not {discovery!r}")
    if discovery == "primo" and not (primo_base and primo_vid):
        raise ValueError("primo needs its address and view id (vid), e.g. https://uni.primo.exlibrisgroup.com and "
                         "UNI:VIEW")
    if discovery == "summon" and not summon_base:
        raise ValueError("summon needs its address, e.g. https://uni.summon.serialssolutions.com")
    return s.model_copy(update={"library_name": name, "library_portal_url": portal_url, "library_proxy": checked,
                                "library_discovery": discovery, "primo_base": primo_base or s.primo_base,
                                "primo_vid": primo_vid or s.primo_vid, "summon_base": summon_base or s.summon_base})


def library_fetch_test(call: Call, doi: str) -> dict:
    """Fetch one paywalled DOI through the library in the built-in browser, in the scratch project."""
    try:
        if SETUP_PROJECT not in [p["slug"] for p in call("rk_project_list", {})["projects"]]:
            call("rk_project_create", {"slug": SETUP_PROJECT, "title": "Library access check (rk setup)"}, write=True)
        added = call("rk_source_add", {"project": SETUP_PROJECT, "verify": False, "discovered_via": "user",
                                       "item": {"title": f"Library access test ({doi})", "ids": {"doi": doi}}},
                     write=True)
        r = call("rk_browser_fetch_fulltext", {"project": SETUP_PROJECT, "source_id": added["item_id"]}, write=True)
    except Exception as e:  # noqa: BLE001 - RkError from the server or ToolError in-process: reported, not raised
        detail = f"{getattr(e, 'type', type(e).__name__)}: {getattr(e, 'message', e)}"
        return {"status": "failed", "detail": detail, "details": getattr(e, "details", {})}
    return {"status": "ok", "origin": r["origin"], "pages": r["page_count"], "url": r.get("url")}


def library_login_step(client, ask: Ask, say: Say) -> dict:
    """Step 5: library details, sign-in in the browser window (the Kernel never sees the password), then a test."""
    s = load_settings()
    name = ask("Library name (Enter to skip this step)", s.library_name or "").strip()
    if not name:
        return {"status": "skipped"}
    portal = ask("Library start page (https)", s.library_portal_url or "").strip()
    proxy = ask("Proxy link pattern with {url}, e.g. https://login.ezproxy.example.edu/login?url={url} "
                "(Enter if you use the campus network or VPN)", s.library_proxy or "").strip() or None
    discovery = ask("Discovery system: primo, summon or none", s.library_discovery or "none").strip().lower()
    extra: dict = {}
    if discovery == "primo":
        extra = {"primo_base": ask("Primo address", s.primo_base or "").strip(),
                 "primo_vid": ask("Primo view id (vid, in the search URL)", s.primo_vid or "").strip()}
    elif discovery == "summon":
        extra = {"summon_base": ask("Summon address", s.summon_base or "").strip()}
    s = library_settings(s, name=name, portal_url=portal, proxy=proxy, discovery=discovery, **extra)
    save_settings(s)
    client.open_human(portal)
    say(f"Sign in to {name} in the built-in browser window that just opened. Your password stays in the browser; the "
        "Kernel never sees it.")
    ask("Press Enter once you are signed in", "")
    doi = ask("DOI of a paywalled article your library subscribes to, to test access (Enter to skip)", "").strip()
    if not doi:
        return {"status": "saved", "detail": "library settings saved; access not tested"}
    return library_fetch_test(client.call, doi)


def capabilities(s: Settings, *, browser_running: bool, providers: dict[str, dict],
                 checked: dict[str, str] | None = None) -> list[dict]:
    """Step 6: what works now. ``checked`` overrides a status with the result of a live check."""
    caps = [
        ("sources", "Crossref", "available (polite pool)" if s.email else "limited (no contact email)"),
        ("sources", "OpenAlex", "available (own key)" if s.openalex_key else "limited (shared $0.10/day without a key)"),
        ("sources", "Semantic Scholar", "available (1 req/s)" if s.s2_key else "pending (apply for a free key)"),
        *[("sources", n, "available (no key needed)") for n in ("Europe PMC", "PubMed", "arXiv", "bioRxiv")],
        ("full text", "Europe PMC / PMC open access", "available"),
        ("full text", "arXiv HTML", "available"),
        ("full text", "open-access PDF links", "available"),
    ]
    if not browser_running:
        caps.append(("full text", "built-in browser", "not running (start rk serve without --no-browser)"))
    elif s.library_proxy:
        caps.append(("full text", f"built-in browser via {s.library_name or 'the library proxy'}", "available"))
    else:
        caps.append(("full text", "built-in browser (publisher sites)", "available (no library proxy set)"))
    search_status = "available (human pace)" if browser_running else "not running"
    caps += [("browser search", n, search_status) for n in ("Google Scholar", "PubMed", "Europe PMC")]
    if s.library_discovery:
        caps.append(("browser search", f"library catalogue ({s.library_discovery})", search_status))
    for name, p in sorted(providers.items()):
        status = f"available ({', '.join(p['roles'])})" if p["ok"] else \
            f"unavailable: {p['reason']}" + (f" — {p['hint']}" if p.get("hint") else "")
        caps.append(("providers", name, status))
    over = checked or {}
    return [{"area": a, "name": n, "status": over.get(n, st)} for a, n, st in caps]


def run_setup(http: HttpClient, ask: Ask, say: Say, *, client=None,
              providers: dict[str, dict] | None = None) -> tuple[Settings, list[dict]]:
    """Steps 1–6. ``client`` (an RkClient of a running ``rk serve``) enables the library step."""
    s = load_settings()
    checked: dict[str, str] = {}

    say("Step 1/6 - contact email (sent to Crossref/Unpaywall as 'mailto'; never stored in events or exports).")
    email = ask("Email (Enter to skip)", s.email or "").strip()
    if email:
        checked["Crossref"] = "available (polite pool)" if check_crossref(http, email) else "limited (check failed)"
        s.email = s.unpaywall_email = email

    say(f"Step 2/6 - OpenAlex key (free). Create an account and copy your key from {OPENALEX_URL} ; "
        "click the activation e-mail first. Without a key all callers on your IP share $0.10/day.")
    key = ask("OpenAlex key (Enter to skip)", s.openalex_key or "").strip()
    if key:
        info = check_openalex(http, key)
        if info:
            s.openalex_key = key
            checked["OpenAlex"] = f"available (daily budget remaining {info['remaining']})"
        else:
            checked["OpenAlex"] = "missing (key rejected - not saved)"

    say(f"Step 3/6 - Semantic Scholar key (free, by request). Apply at {S2_URL} ; describe the use as "
        "'personal academic research'. Keys arrive by e-mail, so you can come back later with `rk setup`.")
    s2 = ask("Semantic Scholar key (Enter if pending)", s.s2_key or "").strip()
    if s2:
        if check_s2(http, s2):
            s.s2_key = s2
        else:
            checked["Semantic Scholar"] = "missing (key rejected - not saved)"
    save_settings(s)

    say("Step 4/6 - model providers.")
    providers = providers if providers is not None else check_providers(s)
    for name, p in sorted(providers.items()):
        say(f"  {name}: " + ("ready" if p["ok"] else f"not ready ({p['reason']}). To fix: {p['hint'] or 'see rk providers'}"))

    say("Step 5/6 - library access in the built-in browser.")
    browser_running = False
    if client is not None:
        try:
            browser_running = bool(client.call("rk_browser_status", {"project": SETUP_PROJECT}).get("running"))
        except Exception:  # noqa: BLE001 - browser_unavailable: reported below
            browser_running = False
    if browser_running:
        result = library_login_step(client, ask, say)
        say(f"  library access: {result['status']}" + (f" ({result.get('detail')})" if result.get("detail") else ""))
        s = load_settings()
    else:
        say("  skipped: the built-in browser is not running (start `rk serve`, then run `rk setup` again).")

    return s, capabilities(s, browser_running=browser_running, providers=providers, checked=checked)
