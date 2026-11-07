import io
import json
import tarfile

import httpx
import pytest

from rk.eval.data import (
    fetch,
    load_deepfact,
    load_litqa2,
    load_reportbench_gt,
    load_scifact,
)

CORPUS = [{"doc_id": 11, "title": "Copper study", "abstract": ["Copper reduces CO2.", "Rates were high."],
           "structured": False},
          {"doc_id": 22, "title": "Nickel study", "abstract": ["Nickel was inert."], "structured": False}]
CLAIMS_DEV = [
    {"id": 1, "claim": "Copper reduces CO2.", "evidence": {"11": [{"sentences": [0], "label": "SUPPORT"}]},
     "cited_doc_ids": [11]},
    {"id": 2, "claim": "Nickel is very active.", "evidence": {"22": [{"sentences": [0], "label": "CONTRADICT"}]},
     "cited_doc_ids": [22]},
    {"id": 3, "claim": "Zinc is cheap.", "evidence": {}, "cited_doc_ids": [11, 99]},  # 99 is not in the corpus
]


def _tarball(tmp_path):
    p = tmp_path / "data.tar.gz"
    with tarfile.open(p, "w:gz") as tar:
        for name, rows in [("data/corpus.jsonl", CORPUS), ("data/claims_dev.jsonl", CLAIMS_DEV)]:
            raw = "\n".join(json.dumps(r) for r in rows).encode()
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return p


def test_scifact_label_mapping(tmp_path):
    pairs = load_scifact(tarball=_tarball(tmp_path))  # the dev split: the only one with labels that rk evaluates on
    assert [(p.id, p.label) for p in pairs] == [("1:11", "supported"), ("2:22", "contradicted"), ("3:11", "unsupported")]
    assert pairs[0].evidence == "Copper study\nCopper reduces CO2. Rates were high."


def test_deepfact_fields(tmp_path):
    p = tmp_path / "test.json"
    p.write_text(json.dumps([{"sampled": "sampled", "sentence": "A claim.", "sentence_idx": 4, "report_id": "env_X",
                              "domain": "environment", "agent_verdict": "supported", "human_verdict": "Inconclusive",
                              "split": "test"}]))
    (c,) = load_deepfact(path=p)
    assert (c.id, c.gold, c.domain) == ("env_X:4", "inconclusive", "environment")


def test_litqa2_fields(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    p = tmp_path / "litqa.parquet"
    pq.write_table(pa.Table.from_pylist([{
        "id": "q1", "question": "Which?", "ideal": "A", "distractors": ["B", "C"], "canary": "x", "source": None,
        "tag": "litqa", "version": "1.1", "sources": ["https://doi.org/10.1/x"], "is_opensource": True,
        "subtask": "litqa-v2-public", "key-passage": "..."}]), p)
    (q,) = load_litqa2(path=p)
    assert (q.ideal, q.distractors, q.sources, q.is_opensource) == ("A", ["B", "C"], ["https://doi.org/10.1/x"], True)


def test_reportbench_gt(tmp_path):
    p = tmp_path / "2003.00653.jsonl"
    p.write_text('{"bib_id": "a", "title": "First Paper", "author": "X"}\n{"bib_id": "b", "title": "Second"}\n')
    assert load_reportbench_gt("2003.00653", path=p) == ["First Paper", "Second"]


def test_fetch_caches_and_records_sha(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url)
        return httpx.Response(200, content=b"payload")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    a = fetch("https://example.org/f.json", "x/f.json", http=http, root=tmp_path)
    b = fetch("https://example.org/f.json", "x/f.json", http=http, root=tmp_path)
    assert a == b and a.read_bytes() == b"payload" and len(calls) == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["x/f.json"]["sha256"] == "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5"


@pytest.mark.live
def test_real_downloads_parse():
    dev = load_scifact()
    assert len(dev) > 300 and {p.label for p in dev} == {"supported", "contradicted", "unsupported"}
    assert len(load_deepfact()) == 621
    assert len(load_litqa2()) == 199
