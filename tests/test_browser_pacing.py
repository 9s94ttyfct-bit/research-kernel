import datetime as dt

import pytest

from rk.browser import DailyCapReached, Paced
from rk.browser.log import list_browse, record_browse
from rk.browser.pacing import DEFAULT_BROWSER_LIMITS, Pacer
from rk.store.project import ProjectStore
from rk.store.projections import rebuild


class Clock:
    def __init__(self, t: float):
        self.t = t
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def noon() -> float:
    return dt.datetime(2026, 11, 6, 12, 0).timestamp()


def make(tmp_path, clock, *, max_wait_s=120.0, rand=None, limits=None):
    return Pacer(tmp_path / "kernel.sqlite", limits or DEFAULT_BROWSER_LIMITS, max_wait_s=max_wait_s, clock=clock,
                 sleep=clock.sleep, rand=rand or (lambda lo, hi: lo))


def test_scholar_gap_is_random_between_20_and_45(tmp_path):
    clock, seen = Clock(noon()), []

    def rand(lo, hi):
        seen.append((lo, hi))
        return 33.0

    p = make(tmp_path, clock, rand=rand)
    p.acquire(["site:scholar"])
    assert clock.slept == []  # first query of the day: no wait
    clock.t += 3
    p.acquire(["site:scholar"])
    assert (20, 45) in seen
    assert clock.slept == [pytest.approx(30.0)]  # 33 s gap minus the 3 s already elapsed


def test_daily_cap(tmp_path):
    clock = Clock(noon())
    p = make(tmp_path, clock)
    for _ in range(60):
        p.acquire(["site:scholar"])
    with pytest.raises(DailyCapReached) as e:
        p.acquire(["site:scholar"])
    assert e.value.details["scope"] == "site:scholar"


def test_quota_survives_restart(tmp_path):
    clock = Clock(noon())
    p = make(tmp_path, clock)
    for _ in range(60):
        p.acquire(["site:scholar"])
    p.close()
    again = make(tmp_path, clock)
    with pytest.raises(DailyCapReached):
        again.acquire(["site:scholar"])


def test_wait_beyond_max_is_paced_error(tmp_path):
    clock = Clock(noon())
    p = make(tmp_path, clock, max_wait_s=10)
    p.acquire(["pdf:example.org"])
    with pytest.raises(Paced) as e:
        p.acquire(["pdf:example.org"])
    assert e.value.details["retry_after_s"] == pytest.approx(30.0)
    assert clock.slept == []
    assert p.status()["quotas"]["pdf:example.org"]["count"] == 1  # the refused call recorded nothing


def test_multi_scope_acquire_is_atomic(tmp_path):
    clock = Clock(noon())
    limits = {**DEFAULT_BROWSER_LIMITS, "pdf_total": {"gap": [0, 0], "per_day": 1}}
    p = make(tmp_path, clock, limits=limits)
    p.acquire(["pdf:a.org", "pdf:*"])
    with pytest.raises(DailyCapReached):
        p.acquire(["pdf:b.org", "pdf:*"])
    assert "pdf:b.org" not in p.status()["quotas"]


def test_pause_and_unpause(tmp_path):
    p = make(tmp_path, Clock(noon()))
    assert p.paused("scholar") is None
    p.pause("scholar", "captcha", project="demo", checkpoint_id="chk_X")
    assert p.paused("scholar")["checkpoint_id"] == "chk_X"
    assert p.status()["paused_sites"][0]["site"] == "scholar"
    p.unpause("scholar")
    assert p.paused("scholar") is None


def test_new_day_resets_count(tmp_path):
    clock = Clock(noon())
    p = make(tmp_path, clock)
    for _ in range(60):
        p.acquire(["site:scholar"])
    clock.t += 13 * 3600  # past local midnight
    p.acquire(["site:scholar"])


def test_browse_log_projection(tmp_path):
    ps = ProjectStore(tmp_path / "proj", "prj_T")
    bid = record_browse(ps, task_id="tsk_1", site="pubmed", action="search", url="https://pubmed.example/?term=x",
                        query="x", result_count=10, actor="agent:scout")
    rows = list_browse(ps)
    assert [(r["id"], r["site"], r["action"], r["result_count"]) for r in rows] == [(bid, "pubmed", "search", 10)]
    rebuild(ps.db)
    assert [r["id"] for r in list_browse(ps)] == [bid]
    with pytest.raises(ValueError):
        record_browse(ps, task_id=None, site="x", action="click", url="https://x", actor="a")
