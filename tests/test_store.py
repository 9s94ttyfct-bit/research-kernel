import sqlite3

import pytest

from rk.blobs import BlobStore
from rk.store.events import Event, NewEvent
from rk.store.project import ProjectStore
from rk.store.projections import projection, rebuild


@projection("test.tag")
def _test_tag(conn: sqlite3.Connection, ev: Event) -> None:
    conn.execute("INSERT OR IGNORE INTO tags(item_id, tag) VALUES (?, ?)", (ev.entity_id, ev.payload["tag"]))


def _ev(t, payload, entity=None):
    return NewEvent(type=t, entity_id=entity, payload=payload, actor_kind="kernel", actor="kernel")


def dump(conn, table):
    return sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {table}"))


def test_chain_and_tamper(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_X")
    for i in range(3):
        ps.apply(_ev("test.noop", {"i": i}))
    rep = ps.log.verify_chain()
    assert rep.ok and rep.checked == 3
    ps.db.execute("UPDATE events SET payload='{\"i\":9}' WHERE seq=2")
    rep = ps.log.verify_chain()
    assert not rep.ok and rep.first_bad_seq == 2


def test_rebuild_reproduces_projections(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_X")
    ps.apply(_ev("project.created", {"slug": "s", "title": "T"}))
    for tag in ("a", "b", "a"):
        ps.apply(_ev("test.tag", {"tag": tag}, entity="src_1"))
    before = (dump(ps.db, "tags"), dump(ps.db, "meta"))
    assert before[0] == [("src_1", "a"), ("src_1", "b")]
    assert rebuild(ps.db) == 4
    assert (dump(ps.db, "tags"), dump(ps.db, "meta")) == before


def test_failed_projection_rolls_back(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_X")

    @projection("test.boom")
    def _boom(conn, ev):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        ps.apply(_ev("test.boom", {}))
    assert ps.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_blob_write_once(tmp_path):
    b = BlobStore(tmp_path)
    h = b.put(b"x")
    assert b.put(b"x") == h and b.path(h).read_bytes() == b"x" and b.has(h)
