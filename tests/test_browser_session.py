import pytest

from rk.browser.session import BrowserSession
from rk.tools.registry import ToolError

from .browser_sites import Sites, html, pdf, redirect, tiny_pdf

pytestmark = pytest.mark.browser

FORM = """<html><head><title>Form</title></head><body>
<input id="q" name="q"><input id="pw" type="password">
<button id="go" onclick="document.title = 'clicked:' + document.getElementById('q').value">Go</button>
</body></html>"""


@pytest.fixture
def sites():
    s = Sites()
    s.route("/page", lambda r: html("<html><head><title>Page</title></head><body><p>Hello</p></body></html>"))
    s.route("/form", lambda r: html(FORM))
    s.route("/set-cookie",
            lambda r: html("<title>cookie</title>", headers={"Set-Cookie": "sess=ok; Path=/; Max-Age=3600"}))
    s.route("/needs-cookie.pdf",
            lambda r: pdf(tiny_pdf()) if r.cookies.get("sess") == "ok" else html("<p>forbidden</p>", 403))
    s.route("/needs-hop-cookie",
            lambda r: html("ok") if r.cookies.get("hop") == "1" else html("<p>no cookie</p>", 403))
    yield s
    s.close()


@pytest.fixture
def workbench():
    w = Sites()
    w.route("/ui/api/secret", lambda r: html("SECRET"))
    yield w
    w.close()


def launch(profile, workbench_origin=None, allow=()) -> BrowserSession:
    s = BrowserSession(profile, headless=True, workbench_origin=workbench_origin, allow_hosts=allow)
    try:
        s.start()
    except Exception as e:  # Chromium not installed for this Playwright version
        if "Executable doesn't exist" in str(e):
            pytest.skip("Playwright Chromium is not installed")
        raise
    return s


def host_of(site: Sites) -> str:
    return site.base.removeprefix("http://")


@pytest.fixture
def session(tmp_path, workbench, sites):
    # fixture sites live on 127.0.0.1, which agent tabs may not reach unless the host is explicitly allowed
    s = launch(tmp_path / "profile", workbench.base,
               allow=[host_of(sites), host_of(sites).replace("127.0.0.1", "localhost"), host_of(workbench)])
    s.open_workbench(workbench.url("/ui/api/secret"))
    yield s
    s.close()


@pytest.fixture
def intranet():
    i = Sites()
    i.route("/admin", lambda r: html("<title>router admin</title>INTRANET"))
    yield i
    i.close()


def test_agent_tab_refuses_cross_site_iframes(session, sites, intranet):
    # localhost and 127.0.0.1 are different sites: with site isolation the iframe runs in its own renderer process,
    # out of reach of the tab's request interceptor, so agent tabs refuse cross-site frames at every hop.
    other = sites.base.replace("127.0.0.1", "localhost")
    sites.route("/frame-redir", lambda r: redirect(intranet.url("/admin?frame")))
    sites.route("/frame-inner", lambda r: html("<title>inner</title><img src='/img-redir'>"))
    sites.route("/img-redir", lambda r: redirect(intranet.url("/pixel")))
    sites.route("/hop-to-other", lambda r: redirect(other + "/frame-inner"))
    sites.route("/frame-page", lambda r: html(
        f"<title>framed</title><iframe src='{other}/frame-redir'></iframe><iframe src='{other}/frame-inner'></iframe>"
        f"<iframe src='/hop-to-other'></iframe><iframe src='/form'></iframe>"))
    t = session.new_tab("tsk_A", sites.url("/frame-page"))
    session.call(session._page(t.id).wait_for_timeout(1000), 30)
    frames = [f.url for f in session._page(t.id).frames]
    assert intranet.hits == []
    assert not any(u.startswith(other) for u in frames)  # no cross-site frame was committed
    assert any(u.endswith("/form") for u in frames)  # same-site frames still load


def test_agent_tab_blocks_private_destinations(session, sites, intranet):
    t = session.new_tab("tsk_A", sites.url("/page"))
    for url in [intranet.url("/admin"), intranet.url("/admin").replace("127.0.0.1", "localhost"), "http://10.1.2.3/",
                "http://169.254.169.254/latest/meta-data/", "http://[::1]:9/", "file:///etc/hosts",
                "chrome://settings"]:
        with pytest.raises(ToolError) as e:
            session.goto(t.id, "tsk_A", url)
        assert e.value.type == "forbidden_url", url
    with pytest.raises(ToolError):
        session.new_tab("tsk_A", intranet.url("/admin"))
    page = session._page(t.id)
    session.call(page.evaluate("u => fetch(u, {mode: 'no-cors'}).catch(e => 0)", intranet.url("/admin")), 30)
    session.call(page.wait_for_timeout(300), 30)
    assert intranet.hits == []  # subresource requests from agent pages are aborted
    sites.route("/redir", lambda r: redirect(sites.url("/redir2")))  # public -> public -> private
    sites.route("/redir2", lambda r: redirect(intranet.url("/admin")))
    with pytest.raises(ToolError) as e:
        session.goto(t.id, "tsk_A", sites.url("/redir"))
    assert e.value.type == "forbidden_url" and session.get_tab(t.id, "tsk_A").url == "about:blank"
    with pytest.raises(ToolError) as e:
        session.fetch_bytes(t.id, "tsk_A", sites.url("/redir"))
    assert e.value.type == "forbidden_url"
    sites.route("/img-page", lambda r: html("<title>img</title><img src='/redir'><iframe src='/redir'></iframe>"))
    session.goto(t.id, "tsk_A", sites.url("/img-page"))
    session.call(page.evaluate("u => { window.open(u); }", intranet.url("/admin?popup")), 30)
    session.call(page.wait_for_timeout(800), 30)
    assert intranet.hits == []  # no hop of any redirect chain, subresource or popup reached the private server
    human = session.new_tab(None, intranet.url("/admin"))  # your own browsing is not restricted
    assert session.content(human.id, None).title == "router admin"


def test_agent_redirect_chain_to_public_page_works(session, sites):
    sites.route("/hop", lambda r: redirect(sites.url("/hop2"), headers={"Set-Cookie": "hop=1; Path=/"}))
    sites.route("/hop2", lambda r: redirect(sites.url("/form")))
    t = session.new_tab("tsk_A", sites.url("/hop"))
    state = session.content(t.id, "tsk_A")
    assert (state.url, state.title) == (sites.url("/form"), "Form")
    assert session.goto(t.id, "tsk_A", sites.url("/hop")).title == "Form"
    status, *_ = session.fetch_bytes(t.id, "tsk_A", sites.url("/needs-hop-cookie"))
    assert status == 200  # the cookie set on a redirect hop is kept


def test_tabs_are_owned(session, sites):
    a = session.new_tab("tsk_A", sites.url("/page"))
    b = session.new_tab("tsk_B", sites.url("/page"))
    assert [t.id for t in session.tabs("tsk_A")] == [a.id]
    assert {t.id for t in session.tabs(None)} == {a.id, b.id}  # the human sees every agent tab, not the Workbench
    with pytest.raises(ToolError) as e:
        session.get_tab(b.id, "tsk_A")
    assert e.value.type == "tab_not_found"
    assert session.get_tab(b.id, None).id == b.id


def test_agent_tab_cannot_reach_workbench(session, sites, workbench):
    t = session.new_tab("tsk_A", sites.url("/page"))
    with pytest.raises(ToolError) as e:
        session.goto(t.id, "tsk_A", workbench.url("/ui/api/secret"))
    assert e.value.type == "forbidden_url"
    before = list(workbench.hits)  # the Workbench tab's own load
    page = session._page(t.id)
    session.call(page.evaluate("u => fetch(u, {mode: 'no-cors'}).catch(e => 'blocked')",
                               workbench.url("/ui/api/secret")), 30)
    session.call(page.evaluate("u => { const i = new Image(); i.src = u; }", workbench.url("/ui/api/secret?img")), 30)
    session.call(page.wait_for_timeout(500), 30)
    localhost = workbench.url("/ui/api/secret").replace("127.0.0.1", "localhost")
    with pytest.raises(ToolError):
        session.goto(t.id, "tsk_A", localhost)
    assert workbench.hits == before  # no request from the agent tab reached the Workbench server


def test_snapshot_has_aria_and_screenshot(session, sites, tmp_path):
    t = session.new_tab("tsk_A", sites.url("/form"))
    snap = session.snapshot(t.id, "tsk_A", screenshot_to=tmp_path / "s.png")
    assert snap["title"] == "Form" and "button" in snap["ax"] and "Go" in snap["ax"]
    assert (tmp_path / "s.png").read_bytes()[:4] == b"\x89PNG"


def test_act_click_and_fill(session, sites):
    t = session.new_tab("tsk_A", sites.url("/form"))
    session.act(t.id, "tsk_A", {"kind": "fill", "selector": "#q", "text": "perovskite"})
    state = session.act(t.id, "tsk_A", {"kind": "click", "selector": "#go"})
    assert state.title == "clicked:perovskite"


def test_password_fill_forbidden(session, sites):
    t = session.new_tab("tsk_A", sites.url("/form"))
    with pytest.raises(ToolError) as e:
        session.act(t.id, "tsk_A", {"kind": "fill", "selector": "#pw", "text": "x"})
    assert e.value.type == "forbidden_action"


def test_fetch_bytes_shares_cookies(session, sites):
    t = session.new_tab("tsk_A", sites.url("/page"))
    status, _, _, _ = session.fetch_bytes(t.id, "tsk_A", sites.url("/needs-cookie.pdf"), referer=sites.url("/page"))
    assert status == 403
    session.goto(t.id, "tsk_A", sites.url("/set-cookie"))
    status, ctype, body, _final = session.fetch_bytes(t.id, "tsk_A", sites.url("/needs-cookie.pdf"),
                                                     referer=sites.url("/set-cookie"))
    assert (status, ctype, body[:5]) == (200, "application/pdf", b"%PDF-")


def test_profile_persists_cookie(tmp_path, sites):
    s = launch(tmp_path / "p2", allow=[host_of(sites)])
    t = s.new_tab("tsk_A", sites.url("/set-cookie"))
    s.close()
    s2 = launch(tmp_path / "p2", allow=[host_of(sites)])
    try:
        t = s2.new_tab("tsk_A", sites.url("/page"))
        status, *_ = s2.fetch_bytes(t.id, "tsk_A", sites.url("/needs-cookie.pdf"), referer=sites.url("/page"))
        assert status == 200
    finally:
        s2.close()


def test_human_tab_is_tracked(session, sites):
    agent = session.new_tab("tsk_A", sites.url("/page"))
    human = session.new_tab(None, sites.url("/form"))  # a tab the user opened (or rk setup opened for the user)
    assert session.active_human_tab().id == human.id
    assert agent.id != human.id and session.get_tab(human.id, None).url.endswith("/form")
