"""Generate tests/gold/quote_cases.jsonl from offline fixture text layers.

Labels follow from how each case is constructed (never from a model): verbatim passages must verify; altered
passages must not; near-copies are "too close"; strengthened hedges are "overstated".
Run: uv run python scripts/make_gold.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rk.text.layer import build_norm_keys, normalize_query  # noqa: E402
from tests.gold_sources import SOURCES, layer  # noqa: E402

OUT = ROOT / "tests" / "gold" / "quote_cases.jsonl"
HEDGE_SWAPS = [(r"\bsuggest(s|ed)?\b", "prove"), (r"\bmay\b", "always"), (r"\bindicate(s|d)?\b", "establish"),
               (r"\blikely\b", "certainly")]
# Independent of the verifier's lexicon: a strengthened claim must not keep any weakening word.
RESIDUAL_HEDGE = re.compile(
    r"\b(may|might|could|possibly|perhaps|potentially|conceivably|plausibly|suggest\w*|indicat\w*|likely|unlikely|"
    r"appear\w*|seem\w*|associated with|probably|presumably|consistent with|preliminary|hypothes\w*|speculat\w*|"
    r"tends? to)\b", re.I)
CATEGORICAL = re.compile(
    r"\b(always|never|all|prove\w*|establish\w*|definitively|conclusively|undoubtedly|certainly|unequivocally)\b", re.I)


def passages(text: str) -> list[str]:
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    out = []
    for s in sents:
        s = s.strip()
        if 60 <= len(normalize_query(s)) <= 260 and re.search(r"[a-z]{4}", s):
            out.append(s)
    return out


def absent(q: str, page_text: str) -> bool:
    nq = normalize_query(q)
    return all(nq not in k.key for k in build_norm_keys(page_text))


def main() -> None:
    cases: list[dict] = []
    n_passages = 0
    for sid in SOURCES:
        lay = layer(sid)
        for page in lay.pages:
            ps = passages(page.text)
            step = max(1, len(ps) // 12)
            chosen = ps[::step][:12] if len(ps) > 3 else ps
            if sid.startswith("pdf:"):
                chosen = [page.text]  # generated PDFs: the whole page is one passage
            for k, p in enumerate(chosen):
                n_passages += 1
                cid = f"{sid}#p{page.n}.{k}"
                cases.append({"id": cid + ":exact", "kind": "quote", "source": sid, "text": p, "expected": True})
                words = p.split()
                if len(words) >= 6:
                    i = len(words) // 2
                    if words[i] != words[i + 1]:
                        sw = words[:i] + [words[i + 1], words[i]] + words[i + 2 :]
                        q = " ".join(sw)
                        if absent(q, page.text):
                            cases.append({"id": cid + ":swap", "kind": "quote", "source": sid, "text": q, "expected": False})
                    q = " ".join(words[: i] + ["not"] + words[i:])
                    if absent(q, page.text):
                        cases.append({"id": cid + ":insert", "kind": "quote", "source": sid, "text": q, "expected": False})
                m = re.search(r"\d", p)
                if m:
                    d = str((int(m.group()) + 1) % 10)
                    q = p[: m.start()] + d + p[m.end() :]
                    if absent(q, page.text):
                        cases.append({"id": cid + ":number", "kind": "quote", "source": sid, "text": q, "expected": False})
                if len(words) >= 10:
                    near = words.copy()
                    near[len(near) // 3] = "remarkably"
                    cases.append({"id": cid + ":para_close", "kind": "paraphrase", "source": sid, "text": " ".join(near),
                                  "span": p, "expected": False})
                for pat, repl in HEDGE_SWAPS:
                    if re.search(pat, p, re.I):
                        up = re.sub(pat, repl, p, count=1, flags=re.I)
                        if not RESIDUAL_HEDGE.search(up):
                            cases.append({"id": cid + ":hedge_same", "kind": "hedge", "claim": p, "sources": [p],
                                          "expected": True})
                            cases.append({"id": cid + ":hedge_up", "kind": "hedge", "claim": up, "sources": [p],
                                          "expected": False})
                        break
                cases.append({"id": cid + ":hedge_down", "kind": "hedge", "claim": "It may be that " + p,
                              "sources": [p], "expected": True})
                if not RESIDUAL_HEDGE.search(p) and not CATEGORICAL.search(p):
                    cases.append({"id": cid + ":hedge_overstate", "kind": "hedge",
                                  "claim": "It is conclusively proven that " + p, "sources": [p], "expected": False})
        # cross-passage splice
        all_ps = [pp for pg in lay.pages for pp in passages(pg.text)]
        for a, b in zip(all_ps[::5], all_ps[2::5], strict=False):
            q = " ".join(a.split()[:6] + b.split()[-6:])
            if all(absent(q, pg.text) for pg in lay.pages):
                cases.append({"id": f"{sid}:splice:{len(cases)}", "kind": "quote", "source": sid, "text": q, "expected": False})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    kinds = {}
    for c in cases:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"{len(cases)} cases from {n_passages} passages: {kinds}")


if __name__ == "__main__":
    main()
