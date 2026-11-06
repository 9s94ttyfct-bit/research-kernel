import json
import math
import re
from pathlib import Path

import httpx
import pytest

from rk.verify.minicheck import Layer1Unavailable, get_minicheck
from rk.verify.minicheck_bespoke import (
    SYSTEM_PROMPT,
    BespokeMiniCheck,
    build_prompt,
    support_prob,
)

MODEL = "ollama:bespoke-minicheck:7b-q8_0"


def split(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def fake_ollama(probs: dict[tuple[str, str], float], seen: list, models=("bespoke-minicheck:7b-q8_0",)):
    """Answers /api/generate with P(Yes) looked up by (chunk start, claim sentence)."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        body = json.loads(req.content)
        seen.append(body)
        doc = body["prompt"].split("Document: ", 1)[1].split("\nClaim: ", 1)[0]
        claim = body["prompt"].split("\nClaim: ", 1)[1].split("<|im_end|>", 1)[0]
        p = probs[(doc[:10], claim)]
        top = [{"token": "Yes", "logprob": math.log(p)}, {"token": "No", "logprob": math.log(1 - p)}]
        return httpx.Response(200, json={"response": "Yes", "logprobs": [{"token": "Yes", "logprob": math.log(p),
                                                                          "top_logprobs": top}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_prompt_matches_official_template():
    p = build_prompt("Doc text.", "A claim.")
    assert p == ("<|im_start|>system\n" + SYSTEM_PROMPT + "<|im_end|>\n<|im_start|>user\nDocument: Doc text.\n"
                 "Claim: A claim.<|im_end|>\n<|im_start|>assistant\n")
    assert SYSTEM_PROMPT.endswith('responding with either "Yes" or "No".')


def test_support_prob_sums_yes_variants_only():
    top = [{"token": "Yes", "logprob": math.log(0.6)}, {"token": "yes", "logprob": math.log(0.1)},
           {"token": " Yes", "logprob": math.log(0.05)}, {"token": "No", "logprob": math.log(0.25)}]
    assert support_prob(top) == pytest.approx(0.7)  # " Yes" (leading space) does not count, as upstream


def test_sentence_fusion_max_over_chunks_min_over_sentences():
    doc = " ".join(["filler words here."] * 1500) + " Copper reduces CO2."  # > CHUNK_WORDS: two chunks
    m = BespokeMiniCheck(MODEL, "http://ollama.test", split=split, workers=1)
    calls = []

    def prob(doc, sent):
        calls.append((doc.endswith("Copper reduces CO2."), sent))
        return {(True, "Copper reduces CO2."): 0.9, (False, "Copper reduces CO2."): 0.2,
                (True, "It is cheap."): 0.4, (False, "It is cheap."): 0.35}[(doc.endswith("Copper reduces CO2."), sent)]

    m.prob = prob
    m._checked = True
    r = m.score(doc, "Copper reduces CO2. It is cheap.")
    assert len(calls) == 4 and r.n_chunks == 2
    assert r.p == 0.4 and r.chunk_index == 1  # weakest sentence ("It is cheap.") at its best chunk (the last)


def test_request_shape_and_end_to_end(tmp_path):
    seen: list = []
    m = BespokeMiniCheck(MODEL, "http://ollama.test", split=split, workers=1)
    m._http = fake_ollama({("The patien", "The patient took drug X."): 0.97}, seen)
    r = m.score("The patient received drug X daily.", "The patient took drug X.")
    assert r.p == pytest.approx(0.97) and r.model == MODEL
    body = seen[0]
    assert body["raw"] is True and body["logprobs"] is True and body["top_logprobs"] == 5
    assert body["options"]["temperature"] == 0 and body["options"]["num_predict"] == 1
    assert body["model"] == "bespoke-minicheck:7b-q8_0"


def test_unavailable_when_model_not_pulled_or_ollama_down():
    m = BespokeMiniCheck(MODEL, "http://ollama.test", split=split)
    m._http = fake_ollama({}, [], models=("lfm3:latest",))
    with pytest.raises(Layer1Unavailable, match="not pulled"):
        m.score("d", "c")

    def down(req):
        raise httpx.ConnectError("refused", request=req)

    m2 = BespokeMiniCheck(MODEL, "http://ollama.test", split=split)
    m2._http = httpx.Client(transport=httpx.MockTransport(down))
    with pytest.raises(Layer1Unavailable, match="not reachable"):
        m2.score("d", "c")


def test_get_minicheck_dispatch(tmp_home):
    assert isinstance(get_minicheck(Path(tmp_home), MODEL), BespokeMiniCheck)
    assert type(get_minicheck(Path(tmp_home), "lytang/MiniCheck-Flan-T5-Large")).__name__ == "MiniCheck"


@pytest.mark.model
def test_bespoke_known_pairs_real():
    m = BespokeMiniCheck(MODEL, "http://127.0.0.1:11434")
    doc = "The patient received 50 mg of drug X daily for two weeks."
    yes, no = m.score_batch([(doc, "The patient took drug X."), (doc, "The patient never received drug X.")])
    assert yes.p > 0.5 > no.p
