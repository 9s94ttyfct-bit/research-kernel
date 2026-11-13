# Search scope: titles and abstracts — implementation plan

**Goal:** act on `docs/eval/search-scope-2026-11-12.md`: the kernel searches and counts OpenAlex in titles, abstracts
and keywords, and Europe PMC in titles and abstracts, everywhere it searches those sources; the protocol's
search-execution text, the rules sheet and the Scout's instructions say so.

**Branch:** `search-scope` from `main` `7753799`.

## Global constraints

- One convention everywhere the kernel queries these two sources (production search, the record-cap probe, discovery
  searches and OpenAlex year counts): no path keeps the full-text search.
- PubMed, arXiv, Semantic Scholar and Crossref unchanged.
- A query that already carries a Europe PMC field prefix is not wrapped again (the wrap is idempotent).
- Recorded HTTP fixtures used by tests may need the new request parameters; re-record or update them minimally and
  say so. No live network in tests.
- TDD; tests test behaviour (the request the source receives; the protocol text the judges see), not wording only.
- Docstrings in prose with "Calibration (search scope, 2026-11-12): …".

## Task 1: the two sources search titles and abstracts

**Files:** `src/rk/sources/openalex.py` (`search`, `count`, `year_counts`: `search.title_abstract_keywords` instead of
`search`), `src/rk/sources/europepmc.py` (`_query` wraps the query in `TITLE_ABS:( … )` unless it already starts with
or contains a Europe PMC field prefix such as `TITLE_ABS:`, `TITLE:`, `ABSTRACT:`), tests in the existing source test
files (`tests/test_sources_crossref_openalex.py`, `tests/test_sources_more.py` or wherever the request params are
checked).

**Tests:** OpenAlex search, count and year counts send `search.title_abstract_keywords=<query>` and no `search=`;
Europe PMC search and count send `TITLE_ABS:(<query>)` (with the year filter outside it) and leave a query that
already has `TITLE_ABS:` as it is.

## Task 2: the protocol says how the kernel searches

**Files:** `src/rk/production/protocol.py` (`search_execution_lines`, the rule in `KERNEL_RULES` that names how
queries go to the sources, `STRATEGY_TASK`'s "restrict fields (title or abstract) only in a syntax every chosen
source reads" → the kernel already searches titles and abstracts; do not add field tags), `src/rk/roles/scout.md`
line 19 likewise, README's search paragraph if it describes the search fields.

**Rule for the text:** "OpenAlex is searched in titles, abstracts and the keywords it tags works with; Europe PMC in
titles and abstracts; PubMed as given (its automatic term mapping applies); arXiv in all fields."

**Tests:** the rendered protocol (`render_protocol`) and the judges' rules sheet state the fields per source (one
behavioural test that the search-execution lines name the scope of each chosen source, not the exact wording).

## Task 3: docs

README, the protocol module docstring, `rk.sources` docstrings and the evidence doc's "Decision" match the code.

After all tasks: review, full suite on the final commit, path scan, fast-forward `main`, push; a fresh home, the six
entries again.
