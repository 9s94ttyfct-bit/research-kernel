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


def test_ordinary_prose_mentioning_bots_not_blocked():
    html = "<html><body><p>Ion migration is not a bottleneck; the robot arm is not a robotic failure.</p></body></html>"
    assert detect_block("https://publisher.example/article/9", html) is None


def test_invisible_recaptcha_on_results_page_not_blocked():
    # Summon results page (probed live 2026-11-06) loads reCAPTCHA v3: a script, an invisible anchor iframe and a
    # hidden g-recaptcha-response textarea. That is risk scoring, not a challenge.
    html = ('<html><head><script async src="https://www.gstatic.com/recaptcha/releases/x/recaptcha__en.js"></script>'
            '<script src="https://www.recaptcha.net/recaptcha/enterprise.js?render=k"></script></head><body>'
            '<div class="documentSummary document-grid"><div class="title"><h3>Perovskite stability</h3></div></div>'
            '<div><iframe title="reCAPTCHA" src="https://www.recaptcha.net/recaptcha/enterprise/anchor?ar=1&amp;k=k'
            '&amp;size=invisible"></iframe></div><textarea id="g-recaptcha-response-100000" '
            'name="g-recaptcha-response" class="g-recaptcha-response" style="display: none;"></textarea></body></html>')
    assert detect_block("https://ubc.summon.example/#!/search?q=x", html) is None


def test_cloudflare_interstitial():
    # Europe PMC served this to headless Chromium (probed live 2026-11-06).
    html = ('<html><head><title>Just a moment...</title></head><body><h2>Performing security verification</h2>'
            '<p>This website uses a security service to protect against malicious bots. This page is displayed while '
            'the website verifies you are not a bot.</p><div id="challenge-error-text"></div>'
            '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1?ray=1"></script></body></html>')
    assert detect_block("https://europepmc.example/search?query=x", html).kind == "captcha"


def test_visible_recaptcha_checkbox_iframe():
    html = '<iframe title="reCAPTCHA" src="https://www.google.com/recaptcha/api2/anchor?ar=1&amp;k=k&amp;size=normal">'
    assert detect_block("https://publisher.example/a", html).kind == "captcha"


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
