import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from pydantic import BaseModel

from rk.cli.client import RkClient
from rk.library.models import Ids, ItemIn
from rk.library.service import Library
from rk.store.project import ProjectStore
from rk.tools.registry import CallContext, Tool, ToolRegistry


def test_parallel_add_of_same_work_creates_one_item(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_T")
    lib = Library(ps)
    item = ItemIn(title="Same work", year=2020, ids=Ids(doi="10.1/same"))
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda _: lib.add_item(item, added_by="agent", actor="t"), range(16)))
    assert len({r.item_id for r in results}) == 1 and sum(r.created for r in results) == 1
    assert ps.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert ps.log.verify_chain().ok


def test_reads_on_other_threads_never_see_uncommitted_writes(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_T")
    lib = Library(ps)
    stop = threading.Event()
    errors: list[BaseException] = []

    def reader():
        while not stop.is_set():
            try:
                n_items = ps.db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
                n_events = ps.db.execute("SELECT COUNT(*) FROM events WHERE type='item.added'").fetchone()[0]
                assert n_items <= n_events
            except BaseException as e:  # noqa: BLE001 - surfaced below
                errors.append(e)
                return

    t = threading.Thread(target=reader)
    t.start()
    for i in range(50):
        lib.add_item(ItemIn(title=f"Work {i}", year=2020), added_by="agent", actor="t")
    stop.set()
    t.join()
    assert not errors
    assert lib.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 50


class _In(BaseModel):
    pass


class _Out(BaseModel):
    n: int


def _registry(tmp_path, handler) -> ToolRegistry:
    reg = ToolRegistry(tmp_path / "idem.sqlite")
    reg.register(Tool(name="w", summary="", input_model=_In, output_model=_Out, roles=frozenset({"cli"}),
                      writes=True, handler=handler))
    return reg


def test_same_key_runs_once_different_keys_run_concurrently(tmp_path):
    calls = []
    barrier = threading.Barrier(2, timeout=5)

    def handler(ctx, _):
        calls.append(ctx.idempotency_key)
        if ctx.idempotency_key in ("a", "b"):
            barrier.wait()  # deadlocks (BrokenBarrierError) if writes with different keys are serialized
        return _Out(n=len(calls))

    reg = _registry(tmp_path, handler)
    with ThreadPoolExecutor(2) as ex:
        list(ex.map(lambda k: reg.call("w", {}, CallContext(role="cli", actor="t", idempotency_key=k)), ["a", "b"]))
    with ThreadPoolExecutor(4) as ex:
        outs = list(ex.map(lambda _: reg.call("w", {}, CallContext(role="cli", actor="t", idempotency_key="c")),
                           range(4)))
    assert calls.count("c") == 1 and len({o["n"] for o in outs}) == 1


def test_client_retries_transport_errors_with_same_key():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        if len(seen) == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"ok": True, "result": {"x": 1}})

    c = RkClient("http://rk.test", "tok")
    c._http = httpx.Client(base_url="http://rk.test", transport=httpx.MockTransport(handler))
    assert c.call("rk_x", {}, write=True) == {"x": 1}
    assert len(seen) == 2 and seen[0] == seen[1] and '"idempotency_key":' in seen[0]


def test_client_gives_up_with_rk_error():
    from rk.cli.client import RkError

    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    c = RkClient("http://rk.test", "tok")
    c._http = httpx.Client(base_url="http://rk.test", transport=httpx.MockTransport(handler))
    with pytest.raises(RkError) as ei:
        c.call("rk_x", {}, attempts=2)
    assert ei.value.type == "transport_error"
