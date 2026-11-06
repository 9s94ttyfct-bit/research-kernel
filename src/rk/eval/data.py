"""Download-once loaders for the evaluation datasets (licences: ``LICENSES.md`` next to this file).

Files are cached under ``$RK_HOME/eval`` with their SHA-256 in ``manifest.json``; nothing from these datasets is
committed to the repository."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel

from rk.config import rk_home

SCIFACT_URL = "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz"
DEEPFACT_URL = "https://huggingface.co/datasets/kkkevinkkk/DeepFactBench/resolve/main/test.json"
LITQA2_INDEX = "https://huggingface.co/api/datasets/futurehouse/lab-bench/parquet/LitQA2/train"
REPORTBENCH_GT = "https://raw.githubusercontent.com/ByteDance-BandAI/ReportBench/main/ReportBench_v1.1_GT/{}.jsonl"


def eval_dir() -> Path:
    d = rk_home() / "eval"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch(url: str, name: str, *, http: httpx.Client | None = None, root: Path | None = None) -> Path:
    """Download ``url`` to ``<eval dir>/<name>`` once; record its SHA-256 and source in manifest.json."""
    root = root or eval_dir()
    dest = root / name
    if dest.exists():
        return dest
    client = http or httpx.Client(timeout=120.0, follow_redirects=True)
    try:
        r = client.get(url)
        r.raise_for_status()
    finally:
        if http is None:
            client.close()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest[name] = {"url": url, "sha256": hashlib.sha256(r.content).hexdigest(), "bytes": len(r.content),
                      "fetched_at": datetime.now(UTC).isoformat(timespec="seconds")}
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return dest


# ---------------- SciFact ----------------
class SupportPair(BaseModel):
    id: str
    claim: str
    evidence: str
    label: Literal["supported", "contradicted", "unsupported"]


def _tar_member(tar_path: Path, name: str) -> bytes:
    with tarfile.open(tar_path, "r:gz") as tar:
        member = tar.getmember(name)
        if not member.isfile():
            raise ValueError(f"{name} is not a regular file in {tar_path}")
        return tar.extractfile(member).read()  # read into memory; nothing is written from the archive


def load_scifact(split: Literal["train", "dev"], *, tarball: Path | None = None,
                 http: httpx.Client | None = None) -> list[SupportPair]:
    """One pair per (claim, cited abstract). SUPPORT → supported, CONTRADICT → contradicted, a cited abstract without
    rationale → unsupported (SciFact's NOT ENOUGH INFO). The test split has no labels."""
    if split not in ("train", "dev"):
        raise ValueError("SciFact test split is unlabeled; use train (calibration) or dev (evaluation)")
    tar = tarball or fetch(SCIFACT_URL, "scifact/data.tar.gz", http=http)
    corpus = {}
    for line in _tar_member(tar, "data/corpus.jsonl").decode().splitlines():
        d = json.loads(line)
        corpus[d["doc_id"]] = d["title"] + "\n" + " ".join(d["abstract"])
    pairs = []
    for line in _tar_member(tar, f"data/claims_{split}.jsonl").decode().splitlines():
        c = json.loads(line)
        for doc_id in c.get("cited_doc_ids", []):
            ev = c.get("evidence", {}).get(str(doc_id))
            label = "unsupported" if not ev else ("supported" if ev[0]["label"] == "SUPPORT" else "contradicted")
            if doc_id in corpus:
                pairs.append(SupportPair(id=f"{c['id']}:{doc_id}", claim=c["claim"], evidence=corpus[doc_id],
                                         label=label))
    return pairs


# ---------------- DeepFact-Bench ----------------
class DeepFactClaim(BaseModel):
    id: str
    sentence: str
    report_id: str
    domain: str
    gold: Literal["supported", "contradictory", "inconclusive"]


def load_deepfact(*, path: Path | None = None, http: httpx.Client | None = None) -> list[DeepFactClaim]:
    p = path or fetch(DEEPFACT_URL, "deepfact/test.json", http=http)
    return [DeepFactClaim(id=f"{r['report_id']}:{r['sentence_idx']}", sentence=r["sentence"], report_id=r["report_id"],
                          domain=r["domain"], gold=r["human_verdict"].lower())
            for r in json.loads(Path(p).read_text())]


# ---------------- LitQA2 ----------------
class LitQAItem(BaseModel):
    id: str
    question: str
    ideal: str
    distractors: list[str]
    sources: list[str]
    is_opensource: bool | None = None


def load_litqa2(*, path: Path | None = None, http: httpx.Client | None = None) -> list[LitQAItem]:
    import pyarrow.parquet as pq

    if path is None:
        client = http or httpx.Client(timeout=60.0, follow_redirects=True)
        try:
            shards = client.get(LITQA2_INDEX).raise_for_status().json()
        finally:
            if http is None:
                client.close()
        path = fetch(shards[0], "litqa2/train-0.parquet", http=http)
    rows = pq.read_table(io.BytesIO(Path(path).read_bytes())).to_pylist()
    return [LitQAItem(id=r["id"], question=r["question"], ideal=r["ideal"], distractors=list(r["distractors"]),
                      sources=list(r.get("sources") or []), is_opensource=r.get("is_opensource")) for r in rows]


# ---------------- ReportBench ----------------
def load_reportbench_gt(arxiv_id: str, *, path: Path | None = None, http: httpx.Client | None = None) -> list[str]:
    p = path or fetch(REPORTBENCH_GT.format(arxiv_id), f"reportbench/{arxiv_id}.jsonl", http=http)
    return [json.loads(line)["title"] for line in Path(p).read_text().splitlines() if line.strip()]
