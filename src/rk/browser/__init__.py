"""The built-in browser (spec §8.5): a Playwright Chromium window owned by ``rk serve``, used by agents at human pace
and handed to you for logins and CAPTCHAs."""

from __future__ import annotations

from rk.tools.registry import ToolError


class Paced(ToolError):
    """The next allowed slot is further away than ``browser_max_wait_s``."""

    def __init__(self, scope: str, retry_after_s: float):
        super().__init__("paced", f"{scope}: next request allowed in {retry_after_s:.0f} s",
                         {"scope": scope, "retry_after_s": round(retry_after_s, 1)}, status=429)


class DailyCapReached(ToolError):
    def __init__(self, scope: str, per_day: int, reset_at: str):
        super().__init__("daily_cap_reached", f"{scope}: daily limit of {per_day} reached; resets at {reset_at}",
                         {"scope": scope, "per_day": per_day, "reset_at": reset_at}, status=429)


class SitePaused(ToolError):
    def __init__(self, site: str, reason: str, checkpoint_id: str | None):
        super().__init__("site_paused", f"{site} is paused ({reason}) until checkpoint {checkpoint_id} is answered",
                         {"site": site, "reason": reason, "checkpoint_id": checkpoint_id}, status=409)


class HumanRequired(ToolError):
    """A login or CAPTCHA page: the browser task stops and you handle it in the browser window, in ``tab``."""

    def __init__(self, kind: str, checkpoint_id: str, url: str, reason: str, tab: str | None = None):
        super().__init__("human_required", f"{kind}: {reason} — handle it in the browser window ({url})",
                         {"kind": kind, "checkpoint_id": checkpoint_id, "url": url, "reason": reason, "tab": tab},
                         status=409)


class BrowserUnavailable(ToolError):
    def __init__(self, reason: str):
        super().__init__("browser_unavailable", reason, status=503)
