import pytest

from rk.browser.session import BrowserSession
from rk.tools.registry import ToolError

from .browser_sites import Sites, html, pdf, tiny_pdf

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
    yield s
    s.close()


@pytest.fixture
def workbench():
    w = Sites()
    w.route("/ui/api/secret", lambda r: html("SECRET"))
    yield w
    w.close()


def launch(profile, workbench_origin=None) -> BrowserSession:
    s = BrowserSession(profile, headless=True, workbench_origin=workbench_origin)
    try:
        s.start()
    except Exception as e:  # Chromium not installed for this Playwright version
        if "Executable doesn't exist" in str(e):
            pytest.skip("Playwright Chromium is not installed")
        raise
    return s


@pytest.fixture
def session(tmp_path, workbench):
    s = launch(tmp_path / "profile", workbench.base)
    s.open_workbench(workbench.url("/ui/api/secret"))
    yield s
    s.close()


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
    s = launch(tmp_path / "p2")
    t = s.new_tab("tsk_A", sites.url("/set-cookie"))
    s.close()
    s2 = launch(tmp_path / "p2")
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
