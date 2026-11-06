"""V-SUPPORT layer 1: MiniCheck-Flan-T5-Large (Tang, Laban & Durrett 2024), run locally.

Reproduces the official ``minicheck`` package (commit e9fd26ef, ``minicheck/inference.py``):
- documents are split into sentences (``\\n`` blocks, then ``nltk.sent_tokenize``) and packed greedily into chunks of
  at most 500 whitespace words, never splitting a sentence;
- each chunk is scored as ``"predict: " + chunk + "</s>" + claim`` (max 2048 tokens), with one decoder step from
  ``decoder_input_ids = 0``; P(supported) = softmax over vocab ids [3, 209], index 1;
- the document score is the max over chunks.
Requires the optional ``support`` extra (torch, transformers, nltk, sentencepiece).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

MODEL_ID = "lytang/MiniCheck-Flan-T5-Large"
CHUNK_WORDS = 500
MAX_TOKENS = 2048
NO_SUPPORT_ID, SUPPORT_ID = 3, 209


class Layer1Unavailable(Exception):
    """torch/transformers/nltk missing, or the model could not be loaded."""


class Layer1Score(BaseModel):
    p: float
    chunk_index: int
    n_chunks: int
    model: str


def _nltk_split(text: str) -> list[str]:
    from nltk.tokenize import sent_tokenize

    return sent_tokenize(text)


def sentences_with_newlines(text: str, split: Callable[[str], list[str]]) -> list[str]:
    out: list[str] = []
    for block in text.split("\n"):
        out.extend(split(block))
        out.append("\n")
    return out[:-1]


def chunk_document(doc: str, max_words: int = CHUNK_WORDS, split: Callable[[str], list[str]] | None = None
                   ) -> list[str]:
    sents = sentences_with_newlines(doc, split or _nltk_split) or [""]
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for s in sents:
        n = len(s.split())
        if count + n > max_words:  # upstream behaviour: may emit an empty chunk, removed below
            chunks.append(" ".join(current))
            current, count = [s], n
        else:
            current.append(s)
            count += n
    if current:
        chunks.append(" ".join(current))
    chunks = [c.replace(" \n ", "\n").strip() for c in chunks]
    return [c for c in chunks if c]


def ensure_nltk(cache_dir: Path) -> None:
    """Make nltk's sentence splitter data (punkt_tab) available from ``<cache_dir>/nltk``, downloading it once."""
    try:
        import nltk
    except ImportError as e:
        raise Layer1Unavailable(f"install the 'support' extra (uv sync --extra support): {e}") from e
    nltk_dir = Path(cache_dir) / "nltk"
    if str(nltk_dir) not in nltk.data.path:
        nltk.data.path.insert(0, str(nltk_dir))
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        nltk.download("punkt_tab", download_dir=str(nltk_dir), quiet=True)


class MiniCheck:
    def __init__(self, cache_dir: Path, model_id: str = MODEL_ID, device: str | None = None, batch_size: int = 8):
        self.cache_dir, self.model_id, self.batch_size = Path(cache_dir), model_id, batch_size
        self._device = device
        self._model = None
        self._tok = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError as e:
            raise Layer1Unavailable(f"install the 'support' extra (uv sync --extra support): {e}") from e
        ensure_nltk(self.cache_dir)
        device = self._device or ("mps" if torch.backends.mps.is_available() else "cpu")
        hf = self.cache_dir / "hf"

        def load(local_only: bool):
            tok = AutoTokenizer.from_pretrained(self.model_id, cache_dir=hf, local_files_only=local_only)
            mdl = AutoModelForSeq2SeqLM.from_pretrained(self.model_id, cache_dir=hf, dtype=torch.float32,
                                                        local_files_only=local_only)
            return tok, mdl

        try:
            try:
                self._tok, model = load(True)  # cached: no network round trip, works offline
            except OSError:
                self._tok, model = load(False)  # first use: download once
        except Exception as e:  # network, disk and format problems all mean "layer 1 unavailable"
            raise Layer1Unavailable(f"cannot load {self.model_id}: {type(e).__name__}: {e}") from e
        self._model = model.to(device).eval()
        self._device = device

    def _probs(self, docs: list[str], claims: list[str]) -> list[float]:
        import torch

        texts = ["predict: " + self._tok.eos_token.join([d, c]) for d, c in zip(docs, claims, strict=True)]
        out: list[float] = []
        for i in range(0, len(texts), self.batch_size):
            enc = self._tok(texts[i:i + self.batch_size], max_length=MAX_TOKENS, truncation=True, padding=True,
                            return_tensors="pt").to(self._device)
            with torch.no_grad():
                dec = torch.zeros((enc["input_ids"].size(0), 1), dtype=torch.long, device=self._device)
                logits = self._model(input_ids=enc["input_ids"], attention_mask=enc["attention_mask"],
                                     decoder_input_ids=dec).logits.squeeze(1)
                probs = torch.softmax(logits[:, [NO_SUPPORT_ID, SUPPORT_ID]].float().cpu(), dim=-1)[:, 1]
            out.extend(probs.tolist())
        return out

    def score_batch(self, pairs: list[tuple[str, str]]) -> list[Layer1Score]:
        """Score (document, claim) pairs; chunks of all pairs are batched together."""
        with self._lock:
            self._load()
            flat_docs, flat_claims, owner = [], [], []
            for i, (doc, claim) in enumerate(pairs):
                for chunk in chunk_document(doc) or [""]:
                    flat_docs.append(chunk)
                    flat_claims.append(claim)
                    owner.append(i)
            probs = self._probs(flat_docs, flat_claims)
        results = []
        for i in range(len(pairs)):
            mine = [p for p, o in zip(probs, owner, strict=True) if o == i]
            best = max(range(len(mine)), key=mine.__getitem__)
            results.append(Layer1Score(p=round(mine[best], 6), chunk_index=best, n_chunks=len(mine),
                                       model=self.model_id))
        return results

    def score(self, doc: str, claim: str) -> Layer1Score:
        return self.score_batch([(doc, claim)])[0]


_instances: dict[tuple[str, ...], object] = {}
_instances_lock = threading.Lock()


def get_minicheck(cache_dir: Path, model_id: str = MODEL_ID):
    """One layer-1 scorer per process and model. ``ollama:…`` ids (Bespoke-MiniCheck-7B) run in local Ollama; other
    ids are Hugging Face checkpoints of MiniCheck-Flan-T5 run in-process."""
    if model_id.startswith("ollama:"):
        from rk.config import load_settings
        from rk.verify.minicheck_bespoke import BespokeMiniCheck

        url = load_settings().ollama_url
        key: tuple[str, ...] = (str(cache_dir), model_id, url)
        make = lambda: BespokeMiniCheck(model_id, url, cache_dir=cache_dir)
    else:
        key = (str(cache_dir), model_id)
        make = lambda: MiniCheck(cache_dir, model_id)
    with _instances_lock:
        if key not in _instances:
            _instances[key] = make()
        return _instances[key]
