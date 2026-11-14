import dataclasses
import time
import types
from urllib.parse import quote

import pytest

from rk.browser import BrowserUnavailable, HumanRequired, SitePaused
from rk.browser.adapters import ADAPTERS
from rk.browser.log import list_browse
from rk.browser.pacing import Pacer
from rk.browser.service import BrowserService
from rk.browser.session import BrowserSession
from rk.checkpoints import answer_checkpoint, get_checkpoint
from rk.config import Settings, save_settings
from rk.fulltext import latest_snapshot
from rk.library.models import Ids, ItemIn
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext, ToolError
from rk.workspace import Kernel

from .browser_sites import FIXTURES, Sites, html, pdf, redirect, tiny_pdf

pytestmark = pytest.mark.browser

FAST = {"default": {"gap": [0, 0], "per_day": None}, "scholar": {"gap": [0, 0], "per_day": 60},
        "pdf_domain": {"gap": [0, 0], "per_day": 40}, "pdf_total": {"gap": [0, 0], "per_day": 150}}
SCOUT = CallContext(role="scout", actor="agent:scout", task_id="tsk_S", idempotency_key="k1")
READER = CallContext(role="reader", actor="agent:reader", task_id="tsk_R", idempotency_key="k2")
HUMAN = CallContext(role="cli", actor="ui:user", idempotency_key="k3")

SSO_PAGE = """<html><head><title>Sign in</title></head><body><form method="post">
<input name="username"><input type="password" name="password"><button>Sign in</button></form></body></html>"""
SCHOLAR_BLOCK = ("<html><head><title>Sorry...</title></head><body><h1>We're sorry...</h1><p>... but your computer or "
                 "network may be sending automated queries.</p></body></html>")


def landing(doi: str, pdf_path: str, title: str = "Fixture article on perovskite stability") -> str:
    return (f'<html><head><title>{title}</title><meta name="citation_title" content="{title}">'
            f'<meta name="citation_author" content="Author, Alice"><meta name="citation_doi" content="{doi}">'
            f'<meta name="citation_publication_date" content="2024/01/05">'
            f'<meta name="citation_pdf_url" content="{pdf_path}"></head><body><h1>{title}</h1>'
            f'<a href="{pdf_path}">Download PDF</a></body></html>')


@pytest.fixture
def sites():
    s = Sites()
    logged_in = lambda r: r.cookies.get("libsess") == "1"
    s.route("/pubmed", lambda r: html((FIXTURES / "pubmed_results.html").read_text()))
    s.route("/pubmed-broken", lambda r: html((FIXTURES / "pubmed_results.html").read_text()
                                             .replace("full-docsum", "redesigned")))
    s.route("/scholar", lambda r: html(SCHOLAR_BLOCK))
    s.route("/proxy", lambda r: redirect(r.query["url"]) if logged_in(r) else redirect("/idp/profile/SAML2/Redirect/SSO"))
    s.route("/idp/profile/SAML2/Redirect/SSO", lambda r: html(SSO_PAGE))
    s.route("/human-login", lambda r: html("<p>signed in</p>", headers={"Set-Cookie": "libsess=1; Path=/; Max-Age=600"}))
    s.route("/doi/10.5555/ok", lambda r: html(landing("10.5555/ok", "/pdf/ok.pdf")))
    s.route("/pdf/ok.pdf", lambda r: pdf(tiny_pdf("Encapsulation slowed degradation of the fixture cells.")) if logged_in(r)
            else redirect("/idp/profile/SAML2/Redirect/SSO"))
    s.route("/doi/10.5555/paywall", lambda r: html(landing("10.5555/paywall", "/pdf/paywall.pdf")))
    s.route("/pdf/paywall.pdf", lambda r: html("<html><body><h1>Purchase this article</h1></body></html>"))
    s.route("/blog", lambda r: html("<html><head><title>Lab notebook: humidity tests</title></head><body>"
                                    "<p>We exposed fixture films to 85% relative humidity for 1000 hours.</p>"
                                    "</body></html>"))
    s.route("/peerj", lambda r: html(landing("10.7717/peerj.1364", "/pdf/none.pdf", title="A page title that differs")))
    yield s
    s.close()


REAL_FORM = """<html><head><title>Form page</title></head><body><form action="/post" method="post">
<input id="q" name="q"><button id="sub">Post</button></form></body></html>"""


@pytest.fixture
def kernel(tmp_home, replay_http, sites):
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com", library_name="Fixture University Library",
                           library_proxy=sites.url("/proxy") + "?url={url}"))
    k = Kernel(tmp_home, http=replay_http)
    k.create_project("demo", "Demo", actor="t")
    yield k
    k.close()


@pytest.fixture
def scholar_site():
    s = Sites()  # its own host, so the service can tell Scholar pages from other sites
    s.route("/scholar", lambda r: html(SCHOLAR_BLOCK))
    s.route("/about", lambda r: html("<html><head><title>About Scholar</title></head><body><p>About</p></body></html>"))
    yield s
    s.close()


@pytest.fixture
def plain():
    s = Sites()  # a site that is not a search site
    s.route("/form", lambda r: html(REAL_FORM))
    yield s
    s.close()


@pytest.fixture
def service(kernel, tmp_path, sites, scholar_site, plain):
    hosts = [x.base.removeprefix("http://") for x in (sites, scholar_site, plain)]
    session = BrowserSession(tmp_path / "profile", headless=True, allow_hosts=hosts)
    try:
        session.start()
    except Exception as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip("Playwright Chromium is not installed")
        raise
    adapters = {**ADAPTERS,
                "pubmed": dataclasses.replace(ADAPTERS["pubmed"], search_url=lambda q, s, p=0: sites.url("/pubmed?term=" + quote(q))),
                "pubmed_broken": dataclasses.replace(ADAPTERS["pubmed"], name="pubmed_broken",
                                                     search_url=lambda q, s, p=0: sites.url("/pubmed-broken")),
                "scholar": dataclasses.replace(ADAPTERS["scholar"],
                                               search_url=lambda q, s, p=0: scholar_site.url("/scholar"))}
    svc = BrowserService(kernel, session, Pacer(tmp_path / "pacer.sqlite", FAST, max_wait_s=5), adapters=adapters,
                         doi_base=sites.url("/doi/"))
    yield svc
    session.close()


def add_item(kernel, **ids) -> str:
    _, lib = kernel.project("demo")
    return lib.add_item(ItemIn(title="Fixture article on perovskite stability", ids=Ids(**ids)), added_by="agent",
                        actor="t").item_id


def human_logs_in(service, sites):
    t = service.session.new_tab(None, sites.url("/human-login"))
    service.session.close_tab(t.id, None)


def test_search_pubmed_fixture_logs_browse(service, kernel):
    out = service.search(SCOUT, "demo", "pubmed", "perovskite stability", max_results=1)
    assert [h["pmid"] for h in out["hits"]] == ["39000001"] and out["tab"].startswith("tab_")
    ps, _ = kernel.project("demo")
    rows = list_browse(ps)
    assert [(r["site"], r["action"], r["query"], r["result_count"], r["task_id"]) for r in rows] == [
        ("pubmed", "search", "perovskite stability", 1, "tsk_S")]


def test_invalid_markup_returns_generic_fallback(service):
    out = service.search(SCOUT, "demo", "pubmed_broken", "x")
    assert out["fallback"] == "generic" and out["hits"] == [] and "ax" in out["snapshot"]


def test_captcha_pauses_site_until_answered(service, kernel, scholar_site, sites):
    reader_tab = service.open(READER, "demo", scholar_site.url("/about"))["tab"]  # a Scholar page, before the CAPTCHA
    with pytest.raises(HumanRequired) as e:
        service.search(SCOUT, "demo", "scholar", "perovskite")
    chk = e.value.details["checkpoint_id"]
    ps, _ = kernel.project("demo")
    assert get_checkpoint(ps, chk)["kind"] == "captcha_wait"
    assert [(r["site"], r["action"]) for r in list_browse(ps)] == [("scholar", "open"), ("scholar", "captcha_wait")]
    hits_before = len(scholar_site.hits)
    with pytest.raises(SitePaused):
        service.search(SCOUT, "demo", "scholar", "perovskite")
    with pytest.raises(SitePaused):  # the pause holds whichever tool reaches Scholar
        service.open(READER, "demo", scholar_site.url("/scholar?q=perovskite"))
    with pytest.raises(SitePaused):
        service.act(READER, "demo", reader_tab, {"kind": "back"})
    assert len(scholar_site.hits) == hits_before  # paused: the site was not contacted again
    assert service.open(READER, "demo", sites.url("/blog"))["title"]  # other sites are not paused
    assert e.value.details["tab"] in {t["tab"] for t in service.tabs(HUMAN)} and not service.tabs(SCOUT)
    answer_checkpoint(ps, chk, {"choice": "done"}, answered_by="human", actor="ui:user")
    scholar_site.route("/scholar", lambda r: html((FIXTURES / "scholar_results.html").read_text()))
    assert len(service.search(SCOUT, "demo", "scholar", "perovskite")["hits"]) == 2


def test_sso_redirect_opens_login_wait(service, kernel):
    iid = add_item(kernel, doi="10.5555/ok")
    with pytest.raises(HumanRequired) as e:
        service.fetch_fulltext(READER, "demo", iid)
    ps, _ = kernel.project("demo")
    assert e.value.details["kind"] == "login_wait"
    assert get_checkpoint(ps, e.value.details["checkpoint_id"])["kind"] == "login_wait"
    assert ps.db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
    assert latest_snapshot(ps, iid) is None


def test_fetch_via_proxy_attaches_library_browser_pdf(service, kernel, sites):
    human_logs_in(service, sites)
    iid = add_item(kernel, doi="10.5555/ok")
    r = service.fetch_fulltext(READER, "demo", iid)
    ps, _ = kernel.project("demo")
    att = dict(ps.db.execute("SELECT * FROM attachments").fetchone())
    assert (r.kind, r.origin, att["origin"]) == ("pdf", "library_browser", "library_browser")
    assert "Fixture University Library" in att["access_note"] and "personal research use" in att["access_note"]
    assert "Encapsulation slowed degradation" in latest_snapshot(ps, iid)[1].pages[0].text
    assert [x["action"] for x in list_browse(ps)] == ["open", "download"]


def test_pdf_link_returning_html_not_attached(service, kernel, sites):
    human_logs_in(service, sites)
    iid = add_item(kernel, doi="10.5555/paywall")
    with pytest.raises(ToolError) as e:
        service.fetch_fulltext(READER, "demo", iid)
    assert e.value.type == "pdf_not_obtained" and e.value.details["final_url"].endswith("/pdf/paywall.pdf")
    ps, _ = kernel.project("demo")
    assert ps.db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0


def test_save_page_page_metadata_only(service, kernel, sites):
    service.session.new_tab(None, sites.url("/blog"))
    out = service.save_page(HUMAN, "demo")
    _, lib = kernel.project("demo")
    item = lib.get(out["item_id"])
    assert (item.title, item.ref_status, item.added_by, item.discovered_via) == (
        "Lab notebook: humidity tests", "page_metadata_only", "human", "browser")
    assert out["attachment"]["kind"] == "html_snapshot"
    ps, _ = kernel.project("demo")
    assert "85% relative humidity" in latest_snapshot(ps, item.id)[1].pages[0].text
    assert lib.collection_by_name("Search hits") in item.collection_ids


def test_save_page_doi_api_wins(service, kernel, sites):
    service.session.new_tab(None, sites.url("/peerj"))
    out = service.save_page(HUMAN, "demo")
    _, lib = kernel.project("demo")
    item = lib.get(out["item_id"])
    assert item.title != "A page title that differs" and item.ids.doi == "10.7717/peerj.1364"
    assert out["page_metadata_diff"]["title"]["page"] == "A page title that differs"
    ps, _ = kernel.project("demo")
    ev = ps.db.execute("SELECT payload FROM events WHERE type='item.added'").fetchone()["payload"]
    assert "A page title that differs" in ev


def test_step_limit(service, kernel, sites):
    save_settings(kernel.settings().model_copy(update={"browser_max_steps": 2}))
    tab = service.open(SCOUT, "demo", sites.url("/blog"))["tab"]
    for _ in range(2):
        service.act(SCOUT, "demo", tab, {"kind": "scroll"})
    with pytest.raises(ToolError) as e:
        service.act(SCOUT, "demo", tab, {"kind": "scroll"})
    assert e.value.type == "step_limit"


def test_source_fetch_fulltext_falls_back_to_browser(service, kernel, sites):
    human_logs_in(service, sites)
    iid = add_item(kernel, url=sites.url("/doi/10.5555/ok"))  # no DOI/PMCID/arXiv/OA link: the API channels fail
    reg = build_registry(kernel, browser=lambda: service)
    out = reg.call("rk_source_fetch_fulltext", {"project": "demo", "source_id": iid}, READER)
    assert out["origin"] == "library_browser"


def test_status_shows_only_the_callers_tabs(service, kernel, sites):
    other = CallContext(role="scout", actor="agent:scout", task_id="tsk_OTHER", idempotency_key="k9")
    mine = service.open(SCOUT, "demo", sites.url("/blog"))["tab"]
    theirs = service.open(other, "demo", sites.url("/blog"))["tab"]
    human = service.session.new_tab(None, sites.url("/blog")).id
    assert [t["tab"] for t in service.status(SCOUT)["tabs"]] == [mine]
    assert {t["tab"] for t in service.status(HUMAN)["tabs"]} >= {mine, theirs, human}


def test_browser_tools_through_registry(service, kernel, sites):
    reg = build_registry(kernel, browser=lambda: service)
    out = reg.call("rk_browser_open", {"project": "demo", "url": sites.url("/blog")}, SCOUT)
    assert out["title"] == "Lab notebook: humidity tests"
    status = reg.call("rk_browser_status", {"project": "demo"}, SCOUT)
    assert status["running"] is True and [t["tab"] for t in status["tabs"]] == [out["tab"]]


def test_browser_tools_unavailable_without_browser(kernel):
    reg = build_registry(kernel)
    with pytest.raises(BrowserUnavailable):
        reg.call("rk_browser_status", {"project": "demo"}, SCOUT)


def test_request_human_url_is_checked_like_an_agent_tab(service, kernel, sites):
    intranet = Sites()  # not an allowed host: stands in for a private address
    try:
        sites.route("/to-intranet", lambda r: redirect(intranet.url("/")))
        for call in (lambda: service.request_human(SCOUT, "demo", "please look", sites.url("/to-intranet")),
                     lambda: service.open(SCOUT, "demo", sites.url("/to-intranet"))):
            with pytest.raises(ToolError) as e:
                call()
            assert e.value.type == "forbidden_url"
        assert intranet.hits == [] and service.tabs(SCOUT) == []  # a refused first page leaves no tab
        ps, _ = kernel.project("demo")
        assert ps.db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
    finally:
        intranet.close()
    sites.route("/form", lambda r: html(REAL_FORM))
    out = service.request_human(SCOUT, "demo", "please sign in", sites.url("/form"))
    assert service.tabs(SCOUT) == []
    assert next(t for t in service.tabs(HUMAN) if t["tab"] == out["tab"])["owner"] == "human"
    service.act(HUMAN, "demo", out["tab"], {"kind": "click", "selector": "#sub"})  # now yours: you can sign in
    assert sites.posts == ["/post"]


def test_agent_keeps_at_most_five_tabs(service, sites):
    opened = [service.open(SCOUT, "demo", sites.url("/blog"))["tab"] for _ in range(7)]
    assert [t["tab"] for t in service.tabs(SCOUT)] == opened[-5:]


def test_blocked_page_becomes_your_tab(service, kernel, sites):
    iid = add_item(kernel, doi="10.5555/ok")
    with pytest.raises(HumanRequired) as e:
        service.fetch_fulltext(READER, "demo", iid)
    tab = e.value.details["tab"]
    assert service.tabs(READER) == []  # the agent no longer holds it
    assert next(t for t in service.tabs(HUMAN) if t["tab"] == tab)["owner"] == "human"
    service.act(HUMAN, "demo", tab, {"kind": "fill", "selector": "input[name=username]", "text": "me"})
    service.act(HUMAN, "demo", tab, {"kind": "click", "selector": "button"})  # your sign-in form posts
    assert any(p.startswith("/idp/") for p in sites.posts)


def test_step_limit_without_task_is_per_tab(service, kernel, sites):
    save_settings(kernel.settings().model_copy(update={"browser_max_steps": 1}))
    lead = CallContext(role="lead", actor="agent:lead", idempotency_key="k7")
    a, b = (service.open(lead, "demo", sites.url("/blog"))["tab"] for _ in range(2))
    service.act(lead, "demo", a, {"kind": "scroll"})
    service.act(lead, "demo", b, {"kind": "scroll"})
    with pytest.raises(ToolError) as e:
        service.act(lead, "demo", a, {"kind": "scroll"})
    assert e.value.type == "step_limit"


def test_agents_submit_forms_only_on_search_sites(service, plain):
    plain.route("/jsform", lambda r: html(REAL_FORM.replace('<button id="sub">', '<button id="sub" type="button" '
                                                            'onclick="document.forms[0].submit()">')))
    tab = service.open(SCOUT, "demo", plain.url("/form"))["tab"]
    for action in ({"kind": "click", "selector": "#sub"}, {"kind": "press", "selector": "#q", "key": "Enter"}):
        with pytest.raises(ToolError) as e:
            service.act(SCOUT, "demo", tab, action)
        assert e.value.type == "forbidden_action"
    tab = service.open(SCOUT, "demo", plain.url("/jsform"))["tab"]  # a script submits it: refused at the request
    with pytest.raises(ToolError):
        service.act(SCOUT, "demo", tab, {"kind": "click", "selector": "#sub"})
    plain.route("/xhr", lambda r: html("<html><head><title>ready</title></head><body><button id='b' onclick=\""
                                       "fetch('/post', {method: 'POST'}).then(() => document.title = 'sent', "
                                       "() => document.title = 'refused')\">Like</button></body></html>"))
    tab = service.open(SCOUT, "demo", plain.url("/xhr"))["tab"]  # a button whose script posts
    service.act(SCOUT, "demo", tab, {"kind": "click", "selector": "#b"})
    for _ in range(50):
        if (title := service.session.content(tab, SCOUT.task_id).title) != "ready":
            break
        time.sleep(0.1)
    assert title == "refused" and plain.posts == []


def test_save_page_again_keeps_reference_check_and_text_layer(service, kernel, sites):
    service.session.new_tab(None, sites.url("/blog"))
    first = service.save_page(HUMAN, "demo")
    ps, lib = kernel.project("demo")
    lib.update(first["item_id"], ref_status="pass", actor="t", actor_kind="human")
    sha = latest_snapshot(ps, first["item_id"])[0]
    col = lib.create_collection("Humidity", actor="t")
    again = service.save_page(HUMAN, "demo", collection=col)
    assert (again["item_id"], again["created"], again["ref_status"]) == (first["item_id"], False, "pass")
    assert again["attachment"]["snapshot_sha"] is None and latest_snapshot(ps, first["item_id"])[0] == sha
    assert col in lib.get(first["item_id"]).collection_ids


@pytest.mark.parametrize("proxy", ["https://libproxy.example.edu/login?url={url}",
                                   "https://login.libproxy.example.edu/login?url={url}"])
def test_an_ezproxy_that_proxies_by_host_name_is_the_library(tmp_home, replay_http, proxy):
    """Calibration (a university EZproxy, 2026-11-14): an EZproxy that proxies by host name serves the article from
    www-nature-com.libproxy.<university>, whether its start URL is on libproxy.<university> or EZproxy's
    login.libproxy.<university>; that page is the library's (its origin, its pause and pace), and only a host under the
    proxy's domain is."""
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com", library_proxy=proxy))
    k = Kernel(tmp_home, http=replay_http)
    try:
        svc = BrowserService(k, types.SimpleNamespace(submit_hosts=set()), pacer=None)
        for url, library in (("https://www-nature-com.libproxy.example.edu/articles/x", True),
                             (proxy.replace("{url}", "https://doi.org/10.1/x"), True),
                             ("https://www.nature.com/articles/x", False),
                             ("https://notlibproxy.example.edu/x", False)):
            assert svc._via_library(url) is library, url
            assert (svc._site_of(url) == "library") is library, url
    finally:
        k.close()


def test_a_proxy_on_this_computer_is_told_apart_by_its_port(tmp_home, replay_http):
    """A library proxy on 127.0.0.1 is the library only on its own port: another site on the same host is not."""
    save_settings(Settings(email="rk-fixtures@users.noreply.github.com",
                           library_proxy="http://127.0.0.1:8001/proxy?url={url}"))
    k = Kernel(tmp_home, http=replay_http)
    try:
        svc = BrowserService(k, types.SimpleNamespace(submit_hosts=set()), pacer=None)
        assert svc._via_library("http://127.0.0.1:8001/proxy?url=x") is True
        assert svc._via_library("http://127.0.0.1:8002/scholar") is False
    finally:
        k.close()
