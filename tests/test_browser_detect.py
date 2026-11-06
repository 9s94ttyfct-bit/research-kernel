from rk.browser.detect import detect_block

ARTICLE = """<html><body><header><a href="/signin">Sign in</a></header><h1>Perovskite stability</h1>
<p>Abstract text about solar cells.</p></body></html>"""


def test_scholar_sorry_page():
    b = detect_block("https://www.google.com/sorry/index?continue=https://scholar.google.com/scholar%3Fq%3Dx", "")
    assert b and b.kind == "captcha"


def test_scholar_inline_captcha():
    b = detect_block("https://scholar.google.com/scholar?q=x", '<div id="gs_captcha_ccl"><form></form></div>')
    assert b and b.kind == "captcha"


def test_recaptcha_markup():
    html = '<html><body><div class="g-recaptcha" data-sitekey="k"></div></body></html>'
    assert detect_block("https://publisher.example/a", html).kind == "captcha"


def test_cloudflare_challenge():
    html = '<html><body><div id="cf-challenge-running"></div><div class="cf-turnstile"></div></body></html>'
    assert detect_block("https://publisher.example/a", html).kind == "captcha"


def test_unusual_traffic_text():
    html = "<html><body><p>Our systems have detected UNUSUAL TRAFFIC from your computer network.</p></body></html>"
    assert detect_block("https://scholar.google.com/scholar?q=x", html).kind == "captcha"


def test_scholar_automated_queries_page():
    # Served at the normal results URL (probed live 2026-11-06), no /sorry/ path and no CAPTCHA widget.
    html = ("<html><head><title>Sorry...</title></head><body><h1>We're sorry...</h1><p>... but your computer or "
            "network may be sending automated queries. To protect our users, we can't process your request right "
            "now.</p></body></html>")
    b = detect_block("https://scholar.google.com/scholar?hl=en&q=x", html)
    assert b and b.kind == "captcha"


def test_unusual_traffic_in_hidden_script_is_ignored():
    html = '<html><body><script>var msg = "unusual traffic";</script><p>Results</p></body></html>'
    assert detect_block("https://scholar.google.com/scholar?q=x", html) is None


def test_shibboleth_redirect():
    b = detect_block("https://idp.university.example/idp/profile/SAML2/Redirect/SSO?execution=e1s1", "<form></form>")
    assert b and b.kind == "login"


def test_openathens_redirect():
    assert detect_block("https://login.openathens.net/auth/example.edu/o/123", "").kind == "login"


def test_proxy_password_form():
    html = '<form action="/login"><input name="user"><input type="password" name="pass"></form>'
    b = detect_block("https://ezproxy.library.example/menu", html, proxy_host="ezproxy.library.example")
    assert b and b.kind == "login"


def test_article_with_signin_link_not_blocked():
    assert detect_block("https://publisher.example/article/123", ARTICLE) is None


def test_publisher_login_url_is_login():
    assert detect_block("https://publisher.example/action/showLogin?redirectUri=%2Fdoi%2F10.1000%2Fx", "").kind == "login"


def test_normal_results_page_not_blocked():
    html = '<div class="gs_r gs_or gs_scl"><h3 class="gs_rt"><a href="https://x.example/a">A paper</a></h3></div>'
    assert detect_block("https://scholar.google.com/scholar?q=perovskite", html) is None
