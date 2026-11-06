import builtins
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from rk.verify.minicheck import Layer1Unavailable, MiniCheck, chunk_document


def regex_split(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def test_chunking_greedy_whole_sentences():
    sents = [f"Sentence number {i} has exactly seven words." for i in range(200)]  # 7 words each
    chunks = chunk_document(" ".join(sents), max_words=50, split=regex_split)
    assert all(len(c.split()) <= 50 for c in chunks)
    assert " ".join(chunks) == " ".join(sents)  # order preserved, nothing lost, nothing split
    assert len(chunks) == 29  # 7 sentences (49 words) per chunk: ceil(200 / 7)


def test_chunking_long_sentence_and_newlines():
    long = " ".join(["word"] * 80) + "."
    chunks = chunk_document(f"Short one.\n{long}\nTail.", max_words=50, split=regex_split)
    assert chunks[0] == "Short one." and len(chunks[1].split()) == 80 and chunks[-1].endswith("Tail.")
    assert chunk_document("", split=regex_split) == []


def test_layer1_unavailable_without_torch(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "torch" or name.startswith("transformers"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(Layer1Unavailable, match="support"):
        MiniCheck(tmp_path).score("doc", "claim")


# ---------------- with the real model ----------------
CACHE = Path(os.environ.get("RK_HOME", Path.home() / "ResearchKernel")) / "models"
DOC = "The patient received 50 mg of drug X daily for two weeks."


@pytest.fixture(scope="module")
def mc():
    return MiniCheck(CACHE)


@pytest.mark.model
def test_minicheck_known_pairs(mc):
    yes, no = mc.score_batch([(DOC, "The patient took drug X."), (DOC, "The patient never received drug X.")])
    assert yes.p > 0.5 > no.p and yes.n_chunks == 1


@pytest.mark.model
def test_support_in_last_chunk(mc):
    filler = " ".join(f"Unrelated filler sentence {i} talks about the weather in a distant town." for i in range(120))
    r = mc.score(filler + " " + DOC, "The patient took drug X.")
    assert r.n_chunks >= 3 and r.chunk_index == r.n_chunks - 1 and r.p > 0.5


PAIRS = [
    (DOC, "The patient took drug X."),
    (DOC, "The patient never received drug X."),
    ("Copper catalysts reduce CO2 to ethylene at high current densities.", "Copper catalysts convert CO2."),
    ("The trial enrolled 120 adults. Mortality did not differ between groups.", "The drug lowered mortality."),
    ("Quotation errors occurred in 25% of the sampled articles.", "A quarter of articles had quotation errors."),
]


@pytest.mark.model
@pytest.mark.live
def test_parity_with_official(mc, tmp_path):
    ours = [s.p for s in mc.score_batch(PAIRS)]
    script = tmp_path / "official.py"
    script.write_text(
        "import json, sys\nfrom minicheck.minicheck import MiniCheck\n"
        f"pairs = json.loads({json.dumps(json.dumps(PAIRS))})\n"
        f"m = MiniCheck(model_name='flan-t5-large', cache_dir={str(CACHE / 'hf')!r})\n"
        "_, probs, _, _ = m.score(docs=[d for d, _ in pairs], claims=[c for _, c in pairs])\n"
        "print(json.dumps([float(p) for p in probs]))\n")
    out = subprocess.run(
        ["uv", "run", "--no-project", "--python", "3.11", "--with",
         "minicheck @ git+https://github.com/Liyan06/MiniCheck.git@e9fd26ef8e542ac4021fa9d4493bf915b2bd68dc",
         "--with", "accelerate", "--with", "torch", "--with", "transformers", "--with", "nltk", "--with",
         "sentencepiece", "python", str(script)], capture_output=True, text=True, timeout=1800, check=False,
        env={**os.environ, "NLTK_DATA": str(CACHE / "nltk")})
    assert out.returncode == 0, out.stderr[-2000:]
    official = json.loads(out.stdout.strip().splitlines()[-1])
    assert len(official) == len(ours)
    for a, b in zip(ours, official, strict=True):
        assert abs(a - b) < 1e-3, (ours, official)
