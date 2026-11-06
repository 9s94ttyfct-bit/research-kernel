# Research Kernel (`rk`)

A verifiable research-agent foundation:
- per-project, event-sourced state with a hash-chained audit log
- a Zotero-like project library
- scholarly source adapters
- page-anchored full text
- deterministic verifiers

The design lives in `docs/superpowers/specs/2026-11-05-research-kernel-design.md`, and the current scope in `docs/superpowers/plans/2026-11-05-plan1-foundation-library.md`.

## Quickstart

```bash
uv sync
uv run rk setup                      # email + free OpenAlex / Semantic Scholar keys (all optional)
uv run rk project create perovskite --title "Perovskite stability"
uv run rk search "perovskite moisture stability" --source europepmc --limit 5 --add
uv run rk add 10.7717/peerj.1364     # resolve + reference check + retraction check
uv run rk library list               # !RETRACTED / ref=… flags per item
uv run rk fetch <item_id>            # open-access full text -> page-anchored text layer
uv run rk find <item_id> "quotation errors"
uv run rk note add <item_id> --kind quote --role main --text "…"   # rejected unless verbatim in the source
uv run rk library export --format bibtex > refs.bib                 # drag into Zotero
uv run rk project status             # counts + event-chain integrity
```

Every command talks to `rk serve`, the local daemon. It is the only process that writes state, and the first command you run starts it automatically.

State lives in `$RK_HOME`, which defaults to `~/ResearchKernel`:
- `config.toml` (mode 0600)
- `projects/<slug>/rk.sqlite`, the event log plus projections
- `blobs/`, the original documents
- `text/`, the text layers
- `library/files/`, the PDFs

## What is verified

| Check | Rule |
|---|---|
| V-REF | The identifier resolves via Crossref, OpenAlex, PubMed or arXiv. Title (token-set ratio ≥ 90), first author and year (±1) all match. |
| V-RETRACT | Crossref `updated-by` notices (publisher and Retraction Watch) ∪ OpenAlex `is_retracted`. Retracted, withdrawn and EoC items are collected in "Retracted / EoC". |
| V-QUOTE | A quote note must occur verbatim in the source's text layer after normalisation: ligatures, quote and dash styles, line-end hyphenation and whitespace. It is stored with a page and character locator. |
| V-PARA | A paraphrase must not copy its source span. Limits: longest shared run ≤ 8 tokens, 4-gram containment ≤ 0.35, token-set ratio ≤ 85. This is a lexical flag only. |
| V-HEDGE | A claim may not be worded more strongly than its sources. |

Hidden text is removed before anything enters a text layer:
- PDF: white, sub-1 pt and invisible-render-mode glyphs
- HTML: `display:none`, `hidden`, comments, scripts and zero-width characters

## Tests

```bash
uv run pytest            # offline: recorded HTTP fixtures, generated PDFs, 691-case gold set
uv run pytest -m live    # also hits the real APIs (citation trap set)
uv run python scripts/record_fixtures.py   # re-record fixtures
uv run python scripts/make_gold.py         # regenerate the construction-labelled gold set
```
