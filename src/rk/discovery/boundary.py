"""Error boundaries of the discovery stages.

``per_item`` is the boundary around one item's network work (a verification, a full text, a retraction lookup, a
cited work, a gap-check search): an error there is logged and returned as a short reason, which the caller records (as
a miner problem or a check's failure reason), and the stage goes on with the next item instead of failing the run. A
failed lookup never counts as "nothing found". The run-control exceptions (``BudgetExceeded``, ``RateCapped``) pass
through.

``transient_source_error`` tells which errors outside such a boundary (e.g. a landscape search) are worth stepping the
run again after a backoff instead of failing it: an HTTP 429, a 5xx, a transport error or an unreadable 200 body."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

import httpx

from rk.discovery.executor import BudgetExceeded, RateCapped
from rk.net.http import HttpError

log = logging.getLogger(__name__)
T = TypeVar("T")
CONTROL = (BudgetExceeded, RateCapped)  # stop the attempt; never caught by a per-item boundary


def error_text(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def per_item(what: str, call: Callable[[], T]) -> tuple[T | None, str | None]:
    """``(call(), None)``, or ``(None, reason)`` when it raised; ``what`` names the work in the reason and the log,
    e.g. ``"full text of itm_1"``."""
    try:
        return call(), None
    except CONTROL:
        raise
    except Exception as e:  # one item's failure is recorded, the stage goes on
        log.warning("%s failed: %s", what, error_text(e), exc_info=e)
        return None, f"{what} failed ({error_text(e)})"


def transient_source_error(e: BaseException) -> bool:
    """A source error that may go away: HTTP 429, 5xx, a transport error (status 0) or an unreadable 200 body."""
    if isinstance(e, httpx.TransportError):
        return True
    return isinstance(e, HttpError) and (e.status in (0, 200, 429) or e.status >= 500)
