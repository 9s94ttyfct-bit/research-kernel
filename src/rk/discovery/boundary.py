"""Error boundaries of the discovery stages.

``per_item`` is the boundary around one item's network work (a verification, a full text, a retraction lookup, a
cited work, a gap-check search): a source, IO or parse error there (``SOURCE_ERRORS``) is logged and returned as a
short reason, which the caller records (as a miner problem or a check's failure reason), and the stage goes on with
the next item instead of failing the run. A failed lookup never counts as "nothing found". Every other error (a
``TypeError``, ``KeyError`` or ``AttributeError`` in kernel code) is a bug and propagates, as do the run-control
exceptions (``BudgetExceeded``, ``RateCapped``) and an exhausted source API budget (``BudgetExhausted``): it pauses
the run (spec §11.3) instead of skipping the item.

``transient_source_error`` tells which errors outside such a boundary (e.g. a landscape search) are worth stepping the
run again after a backoff instead of failing it: an HTTP 429, a 5xx, a transport error or an unreadable 200 body."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

import httpx
import pypdfium2
from lxml import etree

from rk.net.http import HttpError
from rk.sources.s2 import NotConfigured

log = logging.getLogger(__name__)
T = TypeVar("T")
# what one item's source work raises when the source, the disk or the bytes fail: HTTP errors (``BadBody`` too),
# transport errors, OSError, ValueError (JSON, Unicode and pydantic validation errors), XML and PDF parse errors and
# an unconfigured source
SOURCE_ERRORS = (HttpError, httpx.HTTPError, OSError, ValueError, etree.LxmlError, pypdfium2.PdfiumError,
                 NotConfigured)


def error_text(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def per_item(what: str, call: Callable[[], T]) -> tuple[T | None, str | None]:
    """``(call(), None)``, or ``(None, reason)`` when it raised; ``what`` names the work in the reason and the log,
    e.g. ``"full text of itm_1"``."""
    try:
        return call(), None
    except SOURCE_ERRORS as e:  # one item's failure is recorded, the stage goes on
        log.warning("%s failed: %s", what, error_text(e), exc_info=e)
        return None, f"{what} failed ({error_text(e)})"


def transient_source_error(e: BaseException) -> bool:
    """A source error that may go away: HTTP 429, 5xx, a transport error (status 0) or an unreadable 200 body."""
    if isinstance(e, httpx.TransportError):
        return True
    return isinstance(e, HttpError) and (e.status in (0, 200, 429) or e.status >= 500)
