"""Recognise pages that need you: CAPTCHAs / "unusual traffic" walls and library or publisher sign-in pages (spec §8.5).
Detection only; the Kernel never interacts with these pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from lxml import html as lhtml

from rk.text.markup import sanitize_html

# Challenge widgets and interstitials only. reCAPTCHA v3 / invisible widgets (a script, a size=invisible anchor iframe,
# a hidden g-recaptcha-response textarea) score traffic silently on normal pages, so they are not blocks.
_CAPTCHA_MARKUP = re.compile(
    r"""(?:id|class)\s*=\s*["'](?:[^"']*\s)?(?:gs_captcha_ccl|captcha-form|challenge-form|challenge-error-text|"""
    r"""cf-chl-widget[\w-]*|cf-challenge[\w-]*|g-recaptcha|h-captcha|cf-turnstile)["'\s]"""
    r"""|src\s*=\s*["'][^"']*/cdn-cgi/challenge-platform/[^"']*chl_page"""
    r"""|src\s*=\s*["'][^"']*recaptcha/(?:api2|enterprise)/anchor[^"']*size=(?:normal|compact)""",
    re.IGNORECASE)
_CAPTCHA_TEXT = re.compile(r"\b(?:unusual traffic|not a (?:ro)?bot|verify (?:that )?you are (?:a )?human|"
                           r"are you a robot|sending automated queries|performing security verification)\b",
                           re.IGNORECASE)
_LOGIN_HOST = re.compile(r"(?:^|\.)(?:login|signin|sso|idp|auth|wayf|shibboleth)\.|openathens", re.IGNORECASE)
_LOGIN_PATH = re.compile(
    r"(?:^|[/?&=])(?:log-?in|sign-?in|sso|shibboleth(?:\.sso)?|cas/login|adfs|wayf|showlogin|saml2?)(?:$|[/?&.=_-])",
    re.IGNORECASE)
_PASSWORD = re.compile(r"""<input[^>]*type\s*=\s*["']?password""", re.IGNORECASE)


@dataclass(frozen=True)
class Block:
    kind: Literal["captcha", "login"]
    reason: str


def _visible_text(html: str) -> str:
    if not html.strip():
        return ""
    try:
        return lhtml.fromstring(sanitize_html(html)).text_content()
    except (ValueError, lhtml.etree.ParserError):
        return ""


def detect_block(url: str, html: str, *, proxy_host: str | None = None) -> Block | None:
    parts = urlsplit(url)
    host, path = parts.hostname or "", parts.path + ("?" + parts.query if parts.query else "")
    if "/sorry/" in parts.path or (host == "ipv4.google.com" and parts.path.startswith("/sorry")):
        return Block("captcha", "Google 'unusual traffic' page")
    if _CAPTCHA_MARKUP.search(html or ""):
        return Block("captcha", "CAPTCHA or bot-check widget on the page")
    if _CAPTCHA_TEXT.search(_visible_text(html or "")):
        return Block("captcha", "page asks to prove you are human")
    if _LOGIN_HOST.search(host) or _LOGIN_PATH.search(path):
        return Block("login", f"redirected to a sign-in page ({host})")
    if proxy_host and (host == proxy_host or host.endswith("." + proxy_host)) and _PASSWORD.search(html or ""):
        return Block("login", f"library proxy {proxy_host} asks you to sign in")
    return None
