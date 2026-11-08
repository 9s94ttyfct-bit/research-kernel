# ruff: noqa: F811 - fixtures imported from test_browser_service are re-bound as test parameters
import uuid

import pytest

from rk.cli.setup import (
    capabilities,
    check_providers,
    library_login_step,
    library_settings,
)
from rk.config import Settings, load_settings, save_settings
from rk.llm.types import Availability
from rk.tools.builtin import build_registry
from rk.tools.registry import CallContext

from .test_browser_service import kernel, plain, scholar_site, service, sites  # noqa: F401 - fixtures


def test_providers_step_hints():
    def fake_available(settings):
        from rk.llm.drivers import DriverSpec

        return {"lead": (DriverSpec.parse("claude_cli"), Availability(ok=False, reason="not logged in")),
                "skeptic": (DriverSpec.parse("codex_cli"), Availability(ok=True)),
                "judge": (DriverSpec.parse("codex_cli"), Availability(ok=True))}

    out = check_providers(Settings(), available=fake_available)
    assert out["claude_cli"] == {"roles": ["lead"], "ok": False, "reason": "not logged in",
                                 "hint": "run `claude` in a terminal and log in"}
    assert out["codex_cli"]["ok"] is True and out["codex_cli"]["roles"] == ["judge", "skeptic"]


def test_library_step_validates_proxy_template():
    s = library_settings(Settings(), name="Uni Library", portal_url="https://library.example.edu",
                         proxy="https://login.ezproxy.example.edu/login?url={url}", discovery="primo",
                         primo_base="https://uni.primo.example", primo_vid="UNI:VIEW")
    assert (s.library_proxy, s.library_discovery, s.primo_vid) == (
        "https://login.ezproxy.example.edu/login?url={url}", "primo", "UNI:VIEW")
    with pytest.raises(ValueError, match="https"):
        library_settings(Settings(), name="x", portal_url="https://l.example", proxy="http://proxy.example/?url={url}")
    with pytest.raises(ValueError, match="url"):
        library_settings(Settings(), name="x", portal_url="https://l.example", proxy="https://proxy.example/login")
    with pytest.raises(ValueError, match="primo"):
        library_settings(Settings(), name="x", portal_url="https://l.example", proxy=None, discovery="primo")
    local = library_settings(Settings(), name="x", portal_url="http://127.0.0.1:8080/", proxy="http://127.0.0.1:8080/p?url={url}")
    assert local.library_proxy.startswith("http://127.0.0.1")  # a proxy on this computer may use plain http


def test_library_step_rejects_unknown_discovery_system():
    with pytest.raises(ValueError, match="primo, summon or none"):
        library_settings(Settings(), name="x", portal_url="https://l.example", proxy=None, discovery="primoo")


class InProcessClient:
    """What `rk setup` talks to (an RkClient), backed directly by the registry and the browser service."""

    def __init__(self, reg, service):
        self.reg, self.service = reg, service

    def call(self, name, args=None, *, write=False, **_):
        return self.reg.call(name, args or {}, CallContext(role="cli", actor="cli:user",
                                                           idempotency_key=uuid.uuid4().hex if write else None))

    def open_human(self, url):
        return self.service.session.new_tab(None, url).id


@pytest.mark.browser
def test_library_step_fake_sso_success(service, kernel, sites):
    reg = build_registry(kernel, browser=lambda: service)
    answers = iter(["Fixture University Library", sites.url("/human-login"),
                    sites.url("/proxy") + "?url={url}", "none", "", "10.5555/ok"])
    said: list[str] = []
    result = library_login_step(InProcessClient(reg, service), lambda q, d: next(answers), said.append)
    assert result["status"] == "ok" and result["origin"] == "library_browser"
    assert any("sign in" in m.lower() for m in said) and not any("password" in m.lower() and "enter" in m.lower()
                                                                for m in said)
    s = load_settings()
    assert (s.library_name, s.library_portal_url) == ("Fixture University Library", sites.url("/human-login"))
    assert "setup-check" in [p["slug"] for p in kernel.list_projects()]


@pytest.mark.browser
def test_library_step_reports_failure(service, kernel, sites):
    reg = build_registry(kernel, browser=lambda: service)
    answers = iter(["Fixture University Library", sites.url("/blog"), sites.url("/proxy") + "?url={url}", "none", "",
                    "10.5555/ok"])  # the 'portal' visit does not sign in: the proxy sends the browser to SSO
    result = library_login_step(InProcessClient(reg, service), lambda q, d: next(answers), lambda m: None)
    assert result["status"] == "failed" and "login_wait" in result["detail"]


def test_capabilities_lists_browser_channels(tmp_home):
    s = Settings(email="me@example.org", library_name="Uni", library_proxy="https://p.example/?url={url}")
    provider_checks = {"claude_cli": {"roles": ["lead"], "ok": True, "reason": None, "hint": None}}
    caps = capabilities(s, browser_running=True, providers=provider_checks)
    by_name = {(c["area"], c["name"]): c["status"] for c in caps}
    assert by_name[("full text", "built-in browser via Uni")] == "available"
    assert by_name[("sources", "Semantic Scholar")].startswith("pending")
    assert by_name[("providers", "claude_cli")] == "available (lead)"
    off = {(c["area"], c["name"]): c["status"] for c in capabilities(Settings(), browser_running=False, providers={})}
    assert off[("full text", "built-in browser")].startswith("not running")
    save_settings(s)
