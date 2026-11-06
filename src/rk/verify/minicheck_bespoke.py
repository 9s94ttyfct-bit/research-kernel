"""V-SUPPORT layer 1: Bespoke-MiniCheck-7B (Bespoke Labs; InternLM2-7B fine-tuned for MiniCheck), via local Ollama.

Licence: CC BY-NC 4.0 (non-commercial). rk is not commercialised (user decision, 2026-11-06); commercial use would
need a licence from Bespoke Labs.

Reproduces the official ``minicheck`` package (commit e9fd26ef, ``inference.py`` ``LLMCheck``), which runs the model
through vLLM (Linux/CUDA only); here the GGUF build ``bespoke-minicheck`` runs in Ollama on Apple silicon:
- prompt: the official system prompt and ``Document: …\\nClaim: …`` in the model's ChatML template;
- one generated token at temperature 0; P(supported) = sum of exp(logprob) over the top-5 first tokens whose text,
  lower-cased, is exactly ``yes``;
- the claim is split into sentences (``nltk.sent_tokenize``); every (document chunk, sentence) pair is scored; each
  sentence takes its best chunk and the claim score is the weakest sentence (official "SentenceFusion").
Deviation: official chunks are counted in model tokens (≈32k); here a conservative word budget (``CHUNK_WORDS``) keeps
every chunk inside ``NUM_CTX``. Abstract-length documents are one chunk either way.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from rk.config import rk_home
from rk.verify.minicheck import (
    Layer1Score,
    Layer1Unavailable,
    chunk_document,
    ensure_nltk,
)

SYSTEM_PROMPT = (
    "Determine whether the provided claim is consistent with the corresponding document. Consistency in this context "
    "implies that all information presented in the claim is substantiated by the document. If not, it should be "
    'considered inconsistent. Please assess the claim\'s consistency with the document by responding with either '
    '"Yes" or "No".'
)
NUM_CTX = 8192
CHUNK_WORDS = 4000  # ≈ 6k tokens for scientific English, leaving room for prompt and claim within NUM_CTX
TOP_LOGPROBS = 5


def build_prompt(doc: str, claim: str) -> str:
    """The HF chat template of bespokelabs/Bespoke-MiniCheck-7B with add_generation_prompt; the BOS token is added by
    the tokenizer."""
    return (f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\nDocument: {doc}\nClaim: {claim}<|im_end|>\n<|im_start|>assistant\n")


def support_prob(top: list[dict]) -> float:
    return sum(math.exp(t["logprob"]) for t in top if t.get("token", "").lower() == "yes")


def _nltk_sentences(text: str) -> list[str]:
    from nltk.tokenize import sent_tokenize

    return sent_tokenize(text)


class BespokeMiniCheck:
    def __init__(self, model_id: str, ollama_url: str, *, cache_dir: Path | None = None,
                 http: httpx.Client | None = None, workers: int = 2, split: Callable[[str], list[str]] | None = None):
        if not model_id.startswith("ollama:"):
            raise ValueError("Bespoke-MiniCheck model ids look like 'ollama:bespoke-minicheck:7b-q8_0'")
        self.model_id = model_id
        self.cache_dir = Path(cache_dir) if cache_dir else rk_home() / "models"
        self.ollama_model = model_id.removeprefix("ollama:")
        self.url = ollama_url.rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(600.0, connect=5.0))
        self.workers = workers
        self._split = split
        self._checked = False
        self._lock = threading.Lock()

    def _sentences(self, text: str) -> list[str]:
        if self._split is not None:
            return self._split(text)
        try:
            return _nltk_sentences(text)
        except ImportError as e:
            raise Layer1Unavailable(f"install the 'support' extra (uv sync --extra support): {e}") from e

    def _check(self) -> None:
        with self._lock:
            if self._checked:
                return
            try:
                r = self._http.get(f"{self.url}/api/tags", timeout=5.0)
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise Layer1Unavailable(f"ollama not reachable at {self.url}: {type(e).__name__}") from e
            names = {m.get("name") for m in r.json().get("models", [])}
            if self.ollama_model not in names:
                raise Layer1Unavailable(f"{self.ollama_model} not pulled (ollama pull {self.ollama_model})")
            if self._split is None:
                ensure_nltk(self.cache_dir)
            self._checked = True

    def prob(self, doc: str, claim_sentence: str) -> float:
        body = {"model": self.ollama_model, "prompt": build_prompt(doc, claim_sentence), "raw": True, "stream": False,
                "logprobs": True, "top_logprobs": TOP_LOGPROBS,
                "options": {"temperature": 0, "num_predict": 1, "num_ctx": NUM_CTX, "seed": 2024}}
        try:
            r = self._http.post(f"{self.url}/api/generate", json=body)
        except httpx.HTTPError as e:
            raise Layer1Unavailable(f"ollama request failed: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise Layer1Unavailable(f"ollama {r.status_code}: {r.text[:300]}")
        lp = r.json().get("logprobs") or []
        if not lp:
            raise Layer1Unavailable("ollama returned no logprobs (needs an Ollama version with logprobs support)")
        return support_prob(lp[0].get("top_logprobs") or [])

    def score_batch(self, pairs: list[tuple[str, str]]) -> list[Layer1Score]:
        self._check()
        jobs: list[tuple[int, int, int, str, str]] = []  # (pair, chunk, sentence, chunk text, sentence text)
        shapes: list[tuple[int, int]] = []
        for i, (doc, claim) in enumerate(pairs):
            chunks = chunk_document(doc, max_words=CHUNK_WORDS, split=self._split) or [""]
            sents = self._sentences(claim) or [claim]
            shapes.append((len(chunks), len(sents)))
            jobs += [(i, c, s, chunk, sent) for c, chunk in enumerate(chunks) for s, sent in enumerate(sents)]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            probs = list(ex.map(lambda j: self.prob(j[3], j[4]), jobs))
        grid: dict[tuple[int, int, int], float] = {(i, c, s): p for (i, c, s, _, _), p in zip(jobs, probs, strict=True)}
        out = []
        for i, (n_chunks, n_sents) in enumerate(shapes):
            best = [max(range(n_chunks), key=lambda c, s=s: grid[(i, c, s)]) for s in range(n_sents)]
            per_sentence = [grid[(i, best[s], s)] for s in range(n_sents)]
            weakest = min(range(n_sents), key=per_sentence.__getitem__)
            out.append(Layer1Score(p=round(per_sentence[weakest], 6), chunk_index=best[weakest], n_chunks=n_chunks,
                                   model=self.model_id))
        return out

    def score(self, doc: str, claim: str) -> Layer1Score:
        return self.score_batch([(doc, claim)])[0]
