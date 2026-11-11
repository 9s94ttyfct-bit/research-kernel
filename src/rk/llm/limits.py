"""Usage limits of the model drivers (spec §7.2): a reply saying the account hit its session, weekly, usage or spend
limit is no failed task but an outage of the driver until the limit resets. The driver's task result is
``SESSION_LIMITED`` with the seconds to wait (``usage_limit_wait``); ``run_task`` does not retry it and blocks the
driver in ``CallCaps`` for that long, and ``KernelExecutor`` raises ``SessionLimited`` (a ``RateCapped``), so a run
waits (``waiting_rate``) instead of spending a rerun, a back-edge or a conservative vote.

The block is per provider (``claude_cli``, ``codex_cli``, ``ollama``, ...), not per model: a subscription's limit
covers every model of it, and the call caps count per provider too. ``rk limits`` lists the blocks and
``rk limits clear <provider>`` lifts one (e.g. after buying more usage).

The wording, as the drivers give it (the CLIs' checked against their binaries):

- ``claude`` (Claude Code 2.1): ``You've hit your session limit · resets 3pm (Europe/Berlin)`` (also ``weekly limit``,
  ``Opus limit``, ``monthly spend limit``...; the date is given when the reset is not today: ``resets Nov 10, 3pm``),
  ``You're out of usage credits``, ``Your org is out of usage``, ``spend limit reached (daily; resets 2026-08-08
  00:00 UTC)``, and the older ``Claude AI usage limit reached|<unix time>``;
- ``codex`` (0.16x): ``You’ve hit your usage limit. ... or try again at 3:04 PM.`` (``try again at Nov 10th, 2026
  3:04 PM`` when not today; local time), ``... try again in 2 hours 5 minutes.``, and ``Usage limit reached. You've
  reached your usage limit.``
- a chat API (``http_limit``): an HTTP 429, with its ``Retry-After`` or a body in the wording above (e.g. Ollama's
  ``you have reached your monthly usage limit``);
- a gateway (``retry_after``): a transient rate limit in another status with a ``retry-after-ms=<ms>`` (the omp
  auth-gateway gives Codex's ``Codex is at capacity`` as a 502). ``run_task`` waits it out in the task when it is no
  longer than ``RETRY_WAIT_MAX_S`` and tries again; a longer one, or one the last attempt still gives, is a usage
  limit.

A reset time is read in the zone the reply names, else in local time. A reset time that already passed (the reply is
stale: a time up to ``PASSED_S`` ago, a past date with its year, or one without a year less than ``ROLLOVER_S`` ago)
waits ``MIN_WAIT_S``; a reply without one waits
``DEFAULT_WAIT_S``."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SESSION_LIMITED = "session_limited"  # TaskResult.error of a usage-limit reply
DEFAULT_WAIT_S = 1800.0  # no reset time given: try again in half an hour
MIN_WAIT_S = 60.0
MAX_WAIT_S = 8 * 24 * 3600.0  # a weekly limit at most, with a day to spare
PASSED_S = 6 * 3600.0  # a time of day passed less than this long ago is a stale reset, not tomorrow's
ROLLOVER_S = 183 * 24 * 3600.0  # a date without a year passed more than half a year ago is next year's

_LIMIT = re.compile(r"(?:you['’]ve|you have) (?:hit|reached) your(?:\s+[\w'’-]+){0,3}?\s+(?:limit|budget)\b"
                    r"|\busage limit reached\b|\bspend limit reached\b|\byou['’]re out of usage\b"
                    r"|\byour org is out of usage\b", re.IGNORECASE)
_EPOCH = re.compile(r"limit reached\|(\d{9,11})\b", re.IGNORECASE)
_UNITS = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600,
          "d": 86400, "day": 86400}
_IN = re.compile(r"\b(?:try again|resets?) in ((?:\d+(?:\.\d+)?\s*[a-z]+[\s,]*(?:and\s+)?)+)", re.IGNORECASE)
_SPAN = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)", re.IGNORECASE)
_ISO = re.compile(r"\bresets?\s+(?:at\s+)?(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*"
                  r"(Z|UTC|[+-]\d{2}:?\d{2})?", re.IGNORECASE)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_AT = re.compile(
    r"\b(?:resets|reset at|try again at)\s+(?:at\s+)?"
    r"(?:(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?"
    r"(?:\s+(?P<year>\d{4}))?,?\s+(?:at\s+)?)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]\.?m\.?)?"
    r"(?:\s*\((?P<zone>[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)*)\))?", re.IGNORECASE)
_HTTP_429 = re.compile(r"\bHTTP 429\b(?: \(retry-after (\d+(?:\.\d+)?)\))?")
# a rate limit a gateway passes on in another status (the omp auth-gateway gives Codex's "at capacity" as a 502); a
# driver that clips an error keeps this hint (``rk.llm.chat.omp_gateway``)
RETRY_AFTER_MS = re.compile(r"\bretry-after-ms=(\d+)\b", re.IGNORECASE)
RETRY_WAIT_MAX_S = 120.0  # a retry-after up to this long is waited out in the task; a longer one is a usage limit


def usage_limit_wait(text: str, now: float) -> float | None:
    """Seconds from ``now`` (a unix time) until the usage limit ``text`` reports resets; ``DEFAULT_WAIT_S`` when it
    gives no reset time; ``None`` when ``text`` is no usage-limit reply. Kept within ``MIN_WAIT_S``..``MAX_WAIT_S``."""
    if not _LIMIT.search(text):
        return None
    wait = _wait(text, now)
    return DEFAULT_WAIT_S if wait is None else min(max(wait, MIN_WAIT_S), MAX_WAIT_S)


def http_limit(error: str, now: float) -> tuple[bool, float] | None:
    """A chat API's HTTP 429 (``error`` as ``rk.llm.chat.base.http_error_message`` gives it): ``(True, wait)`` when its
    ``Retry-After`` or a usage-limit body says how long, so it is a usage limit at once; ``(False, DEFAULT_WAIT_S)``
    for a plain 429, which is retried first; ``None`` when it is no 429."""
    m = _HTTP_429.search(error)
    if m is None:
        return None
    if m.group(1) is not None:
        return True, min(max(float(m.group(1)), MIN_WAIT_S), MAX_WAIT_S)
    if (wait := usage_limit_wait(error, now)) is not None:
        return True, wait
    return False, DEFAULT_WAIT_S


def retry_after(error: str) -> float | None:
    """Seconds a failed reply asks to wait before trying again in a ``retry-after-ms`` (a transient rate limit a
    gateway passes on in another status than 429); ``None`` when it gives none."""
    m = RETRY_AFTER_MS.search(error)
    return None if m is None else int(m.group(1)) / 1000


def _wait(text: str, now: float) -> float | None:
    if m := _EPOCH.search(text):
        return float(m.group(1)) - now
    if m := _IN.search(text):
        spans = [(float(n), _UNITS.get(u.lower()) or _UNITS.get(u.lower().rstrip("s")))
                 for n, u in _SPAN.findall(m.group(1))]
        if spans and all(unit for _, unit in spans):
            return sum(n * unit for n, unit in spans)
    if m := _ISO.search(text):
        zone = (m.group(5) or "Z").upper().replace("UTC", "Z").replace("Z", "+00:00")
        try:
            at = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4) or '00'}{zone}")
        except ValueError:
            at = None
        if at is not None:
            return max(at.timestamp() - now, MIN_WAIT_S)
    for m in _AT.finditer(text):
        if m.group("minute") is None and m.group("ampm") is None:
            continue  # a bare number is no time of day
        at = _next(m, now)
        if at is not None:
            return at - now
    return None


def _zone(name: str | None) -> tzinfo | None:
    """The zone a reply names; ``None`` (local time) when it names none or one this system does not know."""
    if name:
        if name.upper() == "UTC":
            return UTC
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return None


def _next(m: re.Match, now: float) -> float | None:
    """The unix time of the reset ``m`` names: that date (without a year: this year's, or next year's when this year's
    passed more than ``ROLLOVER_S`` ago), or the next such time of day. A reset that already passed (a date with its
    year, a date without one passed less than ``ROLLOVER_S`` ago, or a time up to ``PASSED_S`` ago) is ``MIN_WAIT_S``
    from now."""
    hour, minute = int(m.group("hour")), int(m.group("minute") or 0)
    if ampm := (m.group("ampm") or "").lower().replace(".", ""):
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    zone = _zone(m.group("zone"))
    current = datetime.fromtimestamp(now, zone) if zone else datetime.fromtimestamp(now)
    try:
        if m.group("mon"):
            month, day = _MONTHS.index(m.group("mon").lower()[:3]) + 1, int(m.group("day"))
            at = current.replace(year=int(m.group("year") or current.year), month=month, day=day, hour=hour,
                                 minute=minute, second=0, microsecond=0)
            if not m.group("year") and now - ROLLOVER_S >= _ts(at):  # no year and long past: next year's
                at = at.replace(year=at.year + 1)
        else:
            at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now - PASSED_S >= _ts(at):
                at += timedelta(days=1)
    except ValueError:  # no such date
        return None
    return max(_ts(at), now + MIN_WAIT_S)


def _ts(at: datetime) -> float:
    """A zoned time's unix time; a naive one is local time."""
    return at.timestamp() if at.tzinfo else at.astimezone().timestamp()
