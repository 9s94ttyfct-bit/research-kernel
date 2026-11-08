"""Subscription usage limits of the agentic CLIs (spec §7.2): a reply saying the account hit its session, weekly or
usage limit is no failed task but an outage of the driver until the limit resets. The CLI drivers report it as
``SESSION_LIMITED`` with the seconds to wait (``usage_limit_wait``); ``run_task`` does not retry it and blocks the
driver in ``CallCaps`` for that long, and ``KernelExecutor`` raises ``SessionLimited`` (a ``RateCapped``), so a run
waits (``waiting_rate``) instead of spending a rerun or a back-edge.

The wording, as the CLIs print it (checked against their binaries):

- ``claude`` (Claude Code 2.1): ``You've hit your session limit · resets 3pm (Europe/Berlin)`` (also ``weekly limit``,
  ``Opus limit``, ``monthly spend limit``...; the date is given when the reset is not today: ``resets Nov 10, 3pm``),
  and the older ``Claude AI usage limit reached|<unix time>``;
- ``codex`` (0.16x): ``You’ve hit your usage limit. ... or try again at 3:04 PM.`` (``try again at Nov 10th, 2026
  3:04 PM`` when not today; local time), ``... try again in 2 hours 5 minutes.``, and ``Usage limit reached. You've
  reached your usage limit.``

A reset time is read in the zone the reply names, else in local time; a reply without one waits ``DEFAULT_WAIT_S``."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SESSION_LIMITED = "session_limited"  # TaskResult.error of a usage-limit reply
DEFAULT_WAIT_S = 1800.0  # no reset time given: try again in half an hour
MIN_WAIT_S = 60.0
MAX_WAIT_S = 8 * 24 * 3600.0  # a weekly limit at most, with a day to spare

_LIMIT = re.compile(r"(?:you['’]ve|you have) (?:hit|reached) your\b[^\n.!]*?\b(?:limit|budget)\b"
                    r"|\busage limit reached\b", re.IGNORECASE)
_EPOCH = re.compile(r"limit reached\|(\d{9,11})\b", re.IGNORECASE)
_UNITS = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600,
          "d": 86400, "day": 86400}
_IN = re.compile(r"\b(?:try again|resets?) in ((?:\d+(?:\.\d+)?\s*[a-z]+[\s,]*(?:and\s+)?)+)", re.IGNORECASE)
_SPAN = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)", re.IGNORECASE)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_AT = re.compile(
    r"\b(?:resets|reset at|try again at)\s+(?:at\s+)?"
    r"(?:(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?"
    r"(?:\s+(?P<year>\d{4}))?,?\s+(?:at\s+)?)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]\.?m\.?)?"
    r"(?:\s*\((?P<zone>[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)*)\))?", re.IGNORECASE)


def usage_limit_wait(text: str, now: float) -> float | None:
    """Seconds from ``now`` (a unix time) until the usage limit ``text`` reports resets; ``DEFAULT_WAIT_S`` when it
    gives no reset time; ``None`` when ``text`` is no usage-limit reply. Kept within ``MIN_WAIT_S``..``MAX_WAIT_S``."""
    if not _LIMIT.search(text):
        return None
    wait = _wait(text, now)
    return DEFAULT_WAIT_S if wait is None else min(max(wait, MIN_WAIT_S), MAX_WAIT_S)


def _wait(text: str, now: float) -> float | None:
    if m := _EPOCH.search(text):
        return float(m.group(1)) - now
    if m := _IN.search(text):
        spans = [(float(n), _UNITS.get(u.lower()) or _UNITS.get(u.lower().rstrip("s")))
                 for n, u in _SPAN.findall(m.group(1))]
        if spans and all(unit for _, unit in spans):
            return sum(n * unit for n, unit in spans)
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
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return None


def _next(m: re.Match, now: float) -> float | None:
    """The unix time of the reset ``m`` names: that date (this year when none is given; next year when it passed), or
    the next such time of day."""
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
            if not m.group("year") and _ts(at) <= now:
                at = at.replace(year=at.year + 1)
        else:
            at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if _ts(at) <= now:
                at += timedelta(days=1)
    except ValueError:  # no such date
        return None
    return _ts(at)


def _ts(at: datetime) -> float:
    """A zoned time's unix time; a naive one is local time."""
    return at.timestamp() if at.tzinfo else at.astimezone().timestamp()
