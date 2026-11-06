# Plan 1 — Foundation & Project Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `rk` foundation. A user can create a research project, search scholarly sources, add works to a per-project Zotero-like library, fetch open-access full text into a page-anchored text layer, write notes, and export the library to BibTeX/RIS/CSL-JSON. Every reference carries a reference/retraction verdict. Every quote note is verified verbatim, with a page and character locator. All of it runs through the `rk serve` single-writer daemon and the `rk` CLI.

**Architecture:**
- A Python package `rk` (src layout, uv).
- State lives in a per-project SQLite event log: a hash chain plus projection tables, all rebuildable from the log.
- Raw documents go into a content-addressed blob store; derived text goes into JSON text layers.
- Pure-function verifiers operate on text layers and resolved metadata.
- A tool registry defines every operation once. A FastAPI daemon (`rk serve`) is the only writer and exposes the registry over a token-authenticated localhost HTTP API. The `rk` CLI is a thin client that auto-starts the daemon.

**Tech Stack:**

| Area | Packages |
|---|---|
| Runtime | Python 3.11, uv 0.13 |
| Models and HTTP | pydantic 2.14, httpx 0.28 |
| Text and PDF | pypdfium2 5.14, trafilatura 2.3, lxml 6, rapidfuzz 3.14 |
| Service and CLI | fastapi 0.162, uvicorn 0.54, typer 0.27 |
| Utilities | python-ulid 4.1, tomli-w 1.2 |
| Bibliographic formats | bibtexparser 2.1, rispy 0.10 |
| Dev and test | pytest 9, respx 0.23, fpdf2 2.9 |

**Spec:** `docs/superpowers/specs/2026-11-05-research-kernel-design.md` (v2, approved 2026-11-05). This plan implements §12 "Plan 1 基础层与文献库", plus the parts of §6.0–6.3, §8.4, §8.6 (library core), §8.8 (CLI setup), §9.1 (V-REF, V-RETRACT, V-QUOTE, V-PARA, V-HEDGE), §10 (unit gold set, citation trap set) and §11 that this scope needs.

## Global Constraints

- Python `>=3.11,<3.13`; managed with `uv`. No Docker. macOS arm64 is the primary target.
- Package import name `rk`; console script `rk`. Source lives in `src/rk/`; tests in `tests/`.
- No dependency on `omp` anywhere in Plan 1.
- No AGPL or GPL runtime dependency (no `pymupdf`, no `marker`). `fpdf2` (LGPL) is a dev-only dependency, used to generate test fixtures.
- `RK_HOME` defaults to `~/ResearchKernel`; overridable with env `RK_HOME`.
- `config.toml` and `serve.token` are written with mode `0600`.
- IDs are `<prefix>_<ULID>`. The allowed prefixes are exactly the spec list: `prj, src, snp, nte, clm, evd, edg, obj, hyp, rq, tpc, gap, prt, dsg, fig, stt, chk, ver, drf, tsk, run, evt`, plus `att` (attachment) and `col` (collection), which this plan adds for library entities.
- Canonical JSON for hashing: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
- Event hash: `sha256((prev_hash or "") + canonical_json(row_without_hash))`, hex.
- Only `rk serve` opens project SQLite for writing. No CLI command opens SQLite directly.
- **Network etiquette:**
  - Every request carries `User-Agent: rk/<version> (mailto:<email>)` when an email is configured.
  - Crossref calls add `mailto=<email>`.
  - Minimum spacing between requests, per host:

    | Host | Min interval (s) |
    |---|---|
    | `api.crossref.org` | 0.35 |
    | `api.openalex.org` | 0.12 |
    | `www.ebi.ac.uk` | 0.12 |
    | `eutils.ncbi.nlm.nih.gov` | 0.34 |
    | `www.ncbi.nlm.nih.gov` (BioC) | 0.34 |
    | `export.arxiv.org` | 3.0 |
    | `arxiv.org` | 15.0 |
    | `api.semanticscholar.org` | 1.0 |
    | `api.biorxiv.org` | 0.5 |
    | default | 1.0 |
- Tests make **no network calls** unless marked `@pytest.mark.live`. Recorded HTTP fixtures live in `tests/fixtures/http/`.
- **Git identity is repo-local:** `user.name=9s94ttyfct-bit`, `user.email=273716724+9s94ttyfct-bit@users.noreply.github.com`. Never commit absolute home paths, emails other than the noreply address, or API keys.
- Verifier thresholds (spec §9.1, initial values):
  - V-PARA passes only if the longest common token run ≤ 8 **and** the 4-gram containment ≤ 0.35 **and** `token_set_ratio` ≤ 85.
  - V-REF passes only if title `token_set_ratio` ≥ 90 **and** the first-author family name matches (casefolded, accents stripped) **and** `|year diff|` ≤ 1.

## Review Focus

1. **PDF line-end hyphenation and ligatures inside quotes.** A quote copied from rendered text ("fact-checking", "evidence", "ﬁnding") must verify against the PDF layer whether the line-end hyphen is real or soft. The test lives in Task 10 (`test_quote_hyphen_variants`).
2. **The same work arriving under different identifiers.** The same paper may come in by DOI, by PMID, as an arXiv `v1`/`v2`, or with a DOI that has a URL prefix or different case. It must merge into one item, not duplicate. The test lives in Task 3 (`test_dedupe_identifier_variants`).
3. **API throttling.** A 429 with `Retry-After`, or an OpenAlex budget that reaches zero, must back off or raise `BudgetExhausted`, never silently return empty results. The test lives in Task 5 (`test_retry_after_and_budget`).
4. **Two CLI commands issued while the daemon is mid-write** must both succeed, with no SQLite lock error and the event chain intact. The test lives in Task 13 (`test_concurrent_cli_writes`).
5. **Hidden text injected into HTML or PDF** must never enter the text layer, and so can never be quoted or verified. HTML hiding means `display:none`, zero-width characters or comments; PDF hiding means white or tiny glyphs. The tests live in Task 8 (`test_pdf_hidden_text_removed`) and Task 9 (`test_html_hidden_text_removed`).

---

## File Structure

```
pyproject.toml                      deps, console script, pytest markers
.gitignore
README.md                           quickstart (rk setup / rk serve / rk project …)
src/rk/__init__.py                  __version__
src/rk/config.py                    Settings, load/save, rk_home()
src/rk/ids.py                       new_id, is_id, PREFIXES
src/rk/canon.py                     canonical_json, sha256_hex
src/rk/store/schema.sql             all tables (events, projections, fts, http cache, idempotency)
src/rk/store/db.py                  open_project_db(path) -> sqlite3.Connection (WAL, FKs, busy_timeout)
src/rk/store/events.py              EventLog: append, iter, verify_chain
src/rk/store/projections.py         projection registry + rebuild()
src/rk/store/project.py             ProjectStore: one project dir (db, blobs, text, library files)
src/rk/blobs.py                     BlobStore (content-addressed, write-once)
src/rk/library/models.py            ItemIn, Item, Creator, Ids, Attachment, Collection
src/rk/library/service.py           Library: add_item, get, tag, collections, search, attach
src/rk/library/formats.py           BibTeX / RIS / CSL-JSON import & export
src/rk/net/http.py                  HttpClient: cache, per-host pacing, retry, budget
src/rk/sources/crossref.py
src/rk/sources/openalex.py
src/rk/sources/europepmc.py
src/rk/sources/pubmed.py
src/rk/sources/arxiv.py
src/rk/sources/s2.py
src/rk/sources/biorxiv.py
src/rk/sources/identifiers.py       parse_identifier, normalize_doi/arxiv/pmid
src/rk/sources/resolver.py          resolve(identifier) -> ResolvedWork; search(query, source)
src/rk/text/layer.py                TextLayer, Page, NormKey, build_norm_keys
src/rk/text/pdf.py                  extract_pdf
src/rk/text/markup.py               jats_to_layer, bioc_to_layer, html_to_layer, sanitize_html
src/rk/fulltext.py                  fetch_fulltext pipeline
src/rk/verify/quote.py              V-QUOTE
src/rk/verify/paraphrase.py         V-PARA
src/rk/verify/hedge.py + hedge_lexicon.toml   V-HEDGE
src/rk/verify/ref.py                V-REF
src/rk/verify/retract.py            V-RETRACT
src/rk/notes.py                     add_note (immediate verification), text_read, text_find
src/rk/tools/registry.py            Tool, ToolRegistry, CallContext, PermissionDenied
src/rk/tools/builtin.py             registers all Plan-1 tools
src/rk/server/app.py                FastAPI app factory
src/rk/server/runtime.py            serve.lock / serve.port / serve.token handling
src/rk/cli/main.py                  typer app (all commands)
src/rk/cli/client.py                RkClient, ensure_server()
src/rk/cli/setup.py                 interactive setup wizard
scripts/record_fixtures.py          live: records HTTP fixtures used by tests
tests/conftest.py                   tmp RK_HOME, fixture HTTP router (respx)
tests/fixtures/http/*.json          recorded responses
tests/fixtures/docs/                generated PDFs, sample JATS/BioC/HTML
tests/gold/quote_cases.jsonl        generated V-QUOTE/V-PARA/V-HEDGE gold
tests/gold/trap_refs.json           citation trap set
tests/test_*.py
```

---

### Task 1: Scaffold, config, IDs, canonical JSON

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `src/rk/__init__.py`, `src/rk/config.py`, `src/rk/ids.py`, `src/rk/canon.py`, `tests/conftest.py`, `tests/test_config_ids.py`

**Interfaces:**
- Produces:
  - `rk.config.rk_home() -> Path`
  - `Settings(BaseModel)`, with fields:
    - `email: str | None = None`
    - `openalex_key: str | None = None`
    - `s2_key: str | None = None`
    - `unpaywall_email: str | None = None`
    - `current_project: str | None = None`
  - `load_settings() -> Settings`
  - `save_settings(s: Settings) -> Path` (writes `rk_home()/config.toml` with mode 0600)
  - `rk.ids.new_id(prefix: str) -> str`, `rk.ids.is_id(value: str, prefix: str | None = None) -> bool`, `PREFIXES: frozenset[str]`
  - `rk.canon.canonical_json(obj) -> str`, `rk.canon.sha256_hex(data: bytes | str) -> str`
  - Fixture `tmp_home` in `tests/conftest.py`: sets `RK_HOME` to a tmp dir.

- [ ] **Step 1: Write failing tests** (`tests/test_config_ids.py`)

```python
def test_settings_roundtrip_and_mode(tmp_home):
    p = save_settings(Settings(email="a@b.org", openalex_key="k"))
    assert p == rk_home() / "config.toml"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert load_settings().openalex_key == "k"

def test_new_id_format():
    i = new_id("src")
    assert i.startswith("src_") and len(i) == 4 + 26 and is_id(i, "src")
    with pytest.raises(ValueError):
        new_id("zzz")

def test_canonical_json_stable():
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'
```

- [ ] **Step 2:** `uv run pytest tests/test_config_ids.py -q`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement.**
  - `pyproject.toml`:
    - `[project.scripts] rk = "rk.cli.main:app"`
    - `[tool.pytest.ini_options] markers = ["live: hits real network"]`, `addopts = "-m 'not live'"`
  - `.gitignore`: `.venv/ __pycache__/ *.pyc .pytest_cache/ dist/ /tmp/ .DS_Store`
- [ ] **Step 4:** `uv sync && uv run pytest tests/test_config_ids.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: scaffold rk package with config, ids, canonical json`

---

### Task 2: Store — event log, projections, rebuild

**Files:**
- Create: `src/rk/store/schema.sql`, `src/rk/store/db.py`, `src/rk/store/events.py`, `src/rk/store/projections.py`, `src/rk/store/project.py`, `src/rk/blobs.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `new_id`, `canonical_json`, `sha256_hex`.
- Produces:
  - `open_project_db(path: Path) -> sqlite3.Connection`
    - Settings: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=10000`, `row_factory=sqlite3.Row`.
    - Applies `schema.sql` idempotently.
  - `NewEvent(BaseModel)`:
    - `type: str`, `entity_id: str | None`, `payload: dict`
    - `actor_kind: Literal["human","agent","kernel"]`, `actor: str`
  - `Event(NewEvent)`: adds `seq: int`, `evt_id: str`, `ts: str` (ISO UTC), `project_id: str`, `prev_hash: str | None`, `hash: str`.
  - `EventLog(conn, project_id)`:
    - `.append(e: NewEvent) -> Event`: inside the caller's transaction; computes the hash per Global Constraints.
    - `.iter() -> Iterator[Event]`
    - `.verify_chain() -> ChainReport(ok: bool, checked: int, first_bad_seq: int | None)`
  - Projections:
    - Decorator `projection(event_type: str)` registers `fn(conn, event: Event) -> None`.
    - `apply(conn, log, e: NewEvent) -> Event`: one transaction that appends the event and runs its projection; unknown types are log-only.
    - `rebuild(conn) -> int`: deletes all projection rows (tables listed in `PROJECTION_TABLES`), replays every event, returns the count.
  - `ProjectStore(root: Path, project_id: str)`:
    - Attributes: `.db`, `.log`, `.blobs: BlobStore`, `.text_dir`, `.files_dir`
    - Method: `.apply(e) -> Event`
  - `BlobStore(root)`:
    - `.put(data: bytes) -> str` (sha256; write-once; files at `blobs/sha256/<ab>/<hash>`)
    - `.path(sha) -> Path`, `.has(sha) -> bool`
- `schema.sql` tables:
  - `events`
  - `projects`
  - `items`
  - `item_ids` (kind, value) — unique index `(kind, value)`
  - `creators`
  - `attachments`
  - `collections`
  - `collection_items`
  - `tags`
  - `snapshots`
  - `notes`
  - `verifications`
  - `items_fts` — FTS5 over `item_id UNINDEXED, title, abstract, creators, notes, fulltext`
  - `http_cache` — global DB at `rk_home()/cache/http.sqlite`, same file format
  - `budgets`
  - `idempotency`

- [ ] **Step 1: Write failing tests**

```python
def test_chain_and_tamper(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_X")
    for i in range(3):
        ps.apply(NewEvent(type="test.noop", entity_id=None, payload={"i": i}, actor_kind="kernel", actor="kernel"))
    assert ps.log.verify_chain().ok
    ps.db.execute("UPDATE events SET payload='{\"i\":9}' WHERE seq=2"); ps.db.commit()
    rep = ps.log.verify_chain()
    assert not rep.ok and rep.first_bad_seq == 2

def test_rebuild_reproduces_projections(tmp_path):
    ps = ProjectStore(tmp_path / "p", "prj_X")
    # uses a test-only projection registered in the test module writing to a table in PROJECTION_TABLES ("tags")
    ...
    before = dump_tables(ps.db); rebuild(ps.db); assert dump_tables(ps.db) == before

def test_blob_write_once(tmp_path):
    b = BlobStore(tmp_path); h = b.put(b"x"); assert b.put(b"x") == h and b.path(h).read_bytes() == b"x"
```

- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5: Commit** `feat(store): hash-chained event log, projections, rebuild, blob store`

---

### Task 3: Library core — items, identifiers, dedupe, tags, collections, FTS

**Files:**
- Create: `src/rk/library/models.py`, `src/rk/library/service.py`, `src/rk/sources/identifiers.py`, `tests/test_library.py`, `tests/test_identifiers.py`

**Interfaces:**
- Consumes: `ProjectStore`, `projection`, `new_id`.
- Produces:
  - `identifiers.py`:
    - `normalize_doi(s) -> str | None`: lowercase; strips `https://doi.org/`, `doi:`, whitespace; must match `^10\.\d{4,9}/\S+$`.
    - `normalize_arxiv(s) -> str | None`: strips the version suffix and the `arXiv:` prefix.
    - `normalize_pmid(s) -> str | None`
    - `parse_identifier(s) -> Identifier(kind: Literal["doi","pmid","pmcid","arxiv","openalex","isbn","url"], value: str)`; raises `ValueError` on unknown input.
  - `models.py`:
    - `Creator(family: str, given: str | None = None)`
    - `Ids(doi=None, pmid=None, pmcid=None, arxiv=None, openalex=None, s2=None, isbn=None, url=None)`
    - `ItemIn`, with fields:
      - `item_type: Literal["journalArticle","preprint","book","bookSection","conferencePaper","thesis","report","dataset","webpage"]`
      - `title: str`, `creators: list[Creator]`, `year: int | None`, `venue: str | None`, `abstract: str | None`
      - `ids: Ids`, `oa_urls: list[str] = []`
      - `discovered_via: Literal["search","backward","forward","snippet","browser","user","ai_suggested","import"]`
      - `csl: dict | None = None`
    - `Item(ItemIn)` adds:
      - `id: str`, `added_by: Literal["human","agent"]`
      - `src_type: Literal["primary","secondary","tertiary","unknown"] = "unknown"`
      - `stance: Literal["supports","challenges","neutral","unknown"] = "unknown"`
      - `retraction: Literal["none","retracted","withdrawn","eoc","corrected","unknown"] = "unknown"`
      - `ref_status: Literal["unverified","pass","mismatch","not_found","page_metadata_only"] = "unverified"`
      - `tags: list[str]`, `collection_ids: list[str]`
      - `read_status: Literal["unread","reading","read"] = "unread"`, `rating: int | None`
  - `Library(ps: ProjectStore)`:
    - `.add_item(item: ItemIn, *, added_by, actor) -> AddResult(item_id: str, created: bool)`
      - Dedupe in this order: any equal normalized identifier → same item. Otherwise, equal `normalize_title(title)` (casefold, alphanumerics only) **and** equal year → same item.
      - On merge, union the identifiers and `oa_urls` (event `item.merged`).
    - `.get(item_id) -> Item`, `.list(collection_id=None) -> list[Item]`
    - `.tag(item_id, add: list[str] = [], remove: list[str] = [], *, actor)`
    - `.create_collection(name, parent_id=None, query=None, *, actor) -> str`; with `query` set it is a smart collection (FTS query).
    - `.collect(item_id, collection_id, *, actor)`
    - `.search(query: str, limit=50) -> list[ItemHit(item_id, title, snippet, rank)]` (FTS5 bm25)
    - `.update(item_id, **fields)` for the `retraction`, `ref_status`, `stance`, `src_type`, `read_status` and `rating` fields; writes event `item.updated`.
    - `.ensure_default_collections(*, actor)`: creates the spec §8.6 collections named `Search hits`, `Included`, `Excluded`, `Counter-evidence`, `Retracted / EoC`, `Needs you`.

- [ ] **Step 1: Write failing tests**

```python
def test_dedupe_identifier_variants(lib):
    a = lib.add_item(item(doi="10.1000/ABC.1"), added_by="agent", actor="t")
    b = lib.add_item(item(doi="https://doi.org/10.1000/abc.1", pmid="123"), added_by="agent", actor="t")
    c = lib.add_item(item(arxiv="2401.00001v2"), added_by="agent", actor="t")
    d = lib.add_item(item(arxiv="arXiv:2401.00001"), added_by="agent", actor="t")
    assert a.item_id == b.item_id and not b.created
    assert lib.get(a.item_id).ids.pmid == "123"
    assert c.item_id == d.item_id

def test_title_year_dedupe(lib):
    x = lib.add_item(item(title="Deep  Learning: a Review", year=2020), added_by="agent", actor="t")
    y = lib.add_item(item(title="deep learning — A review", year=2020), added_by="agent", actor="t")
    assert x.item_id == y.item_id

def test_tags_collections_search(lib):
    i = lib.add_item(item(title="Perovskite stability under humidity"), added_by="human", actor="u").item_id
    lib.tag(i, add=["perovskite"], actor="u"); col = lib.create_collection("Included", actor="u"); lib.collect(i, col, actor="u")
    assert lib.get(i).tags == ["perovskite"] and col in lib.get(i).collection_ids
    assert lib.search("humidity")[0].item_id == i

def test_library_rebuild_equal(lib): ...   # rebuild() then compare items/tags/collections dumps
```

- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement. Every mutation goes through `ps.apply` and a projection.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5: Commit** `feat(library): items with identifier dedupe, tags, collections, FTS search`

---

### Task 4: Library import/export — BibTeX, RIS, CSL-JSON

**Files:**
- Create: `src/rk/library/formats.py`, `tests/test_formats.py`, `tests/fixtures/docs/sample.bib`, `tests/fixtures/docs/sample.ris`

**Interfaces:**
- Consumes: `ItemIn`, `Item`, `Creator`, `Ids`.
- Produces:
  - `to_csl(item: Item | ItemIn) -> dict` (CSL-JSON; `type` mapped from `item_type`)
  - `from_csl(d: dict) -> ItemIn`
  - `export_bibtex(items: list[Item]) -> str`
    - Citation keys: `{family}{year}{firstTitleWord}`, ASCII-folded and lowercased, with `a`/`b` suffixes on collisions.
    - `doi`, `pmid` and `eprint` (arXiv) go into fields.
  - `import_bibtex(text: str) -> list[ItemIn]` (`discovered_via="import"`)
  - `export_ris(items) -> str`, `import_ris(text) -> list[ItemIn]`
  - `export_csl_json(items) -> str`, `import_csl_json(text) -> list[ItemIn]`

- [ ] **Step 1: Write failing tests:**
  - A round-trip for each format preserves `title`, creator families, `year`, `doi` and `item_type`.
  - Citation-key collision yields `smith2020deep` and `smith2020deepa`.
  - Importing `sample.bib` (5 entries: an article, a book, inproceedings, a misc arXiv entry, and one with LaTeX accents `{\"u}`) gives correct types and unicode names.
- [ ] **Step 2:** FAIL. **Step 3:** Implement with `bibtexparser` v2 (`bibtexparser.parse_string`, `bibtexparser.write_string`) and `rispy`. **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(library): BibTeX/RIS/CSL-JSON import and export`

---

### Task 5: HTTP client — cache, per-host pacing, retry, budgets

**Files:**
- Create: `src/rk/net/http.py`, `tests/test_http.py`

**Interfaces:**
- Produces:
  - `HttpClient(cache_db: Path, *, user_agent: str, min_intervals: dict[str, float] | None = None, sleep=time.sleep, now=time.monotonic)`
    - `.get(url, params=None, *, ttl_s: int = 7 * 86400, headers=None, budget_key: str | None = None) -> HttpResult(status: int, headers: dict, body: bytes, from_cache: bool, url: str)`
    - `.get_json(...) -> (dict | list | None, HttpResult)`
    - `HttpResult.json()`
  - Behaviour:
    - **Caching:** only `GET` responses with status 200 are cached. The cache key is `sha256(url + canonical_json(sorted params))`. Secrets must not appear in cache keys or stored URLs: the `api_key` and `key` query parameters are stripped before keying. A fresh cache hit makes no network call.
    - **Pacing:** each host waits until `now - last_request[host] >= min_interval[host]`, using the Global Constraints table.
    - **Retry:** on 429 or 5xx, retry at most 4 times. The delay is `Retry-After` seconds when present, else `2**attempt`. After that, raise `HttpError(status, url)`.
    - **Budgets:** when the response carries `x-ratelimit-remaining`, store it under `budget_key`. A later call with the same `budget_key` raises `BudgetExhausted(budget_key)` when the stored value is ≤ 0 and its `x-ratelimit-reset` time has not passed. 404 returns the result (no raise).

- [ ] **Step 1: Write failing tests** (respx, with injected fake `sleep`/`now`)

```python
def test_cache_hit_no_network(...): two gets -> respx route called once; second from_cache True
def test_api_key_not_in_cache_key(...): get(url, params={"api_key":"SECRET"}); assert "SECRET" not in cache rows
def test_retry_after_and_budget(...):
    # 1st call 429 Retry-After: 2 then 200 with x-ratelimit-remaining: 0, x-ratelimit-reset: 3600
    # assert fake sleep received 2; next call with same budget_key raises BudgetExhausted
def test_per_host_pacing(...): two calls to export.arxiv.org -> fake sleep called with >= 3.0 - elapsed
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement (the cache table lives in `rk_home()/cache/http.sqlite`; the tests pass `tmp_path`). **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(net): cached, paced HTTP client with retry and budget tracking`

---

### Task 6: Sources — Crossref and OpenAlex

**Files:**
- Create: `src/rk/sources/crossref.py`, `src/rk/sources/openalex.py`, `scripts/record_fixtures.py`, `tests/fixtures/http/` (recorded), `tests/test_sources_crossref_openalex.py`

**Interfaces:**
- Consumes: `HttpClient`, `ItemIn`, identifier normalizers.
- Produces:
  - `crossref.get_work(http, doi: str, mailto: str | None) -> CrossrefWork | None`
    - `CrossrefWork(item: ItemIn, updates: list[Update], raw: dict)`; `Update(type: str, source: str, doi: str | None, date: str | None)` from `updated-by[]`.
    - `item_type` mapping: `journal-article`→journalArticle, `posted-content`→preprint, `proceedings-article`→conferencePaper, `book`→book, `book-chapter`→bookSection, otherwise report.
  - `crossref.csl(http, doi, mailto) -> dict | None`: `/works/{doi}/transform/application/vnd.citationstyles.csl+json`
  - `openalex.get_work(http, ident: Identifier, api_key: str | None) -> OpenAlexWork | None`
    - DOI: `/works/https://doi.org/<doi>`; PMID: `/works/pmid:<id>`; OpenAlex: `/works/<W…>`.
    - `OpenAlexWork(item: ItemIn, is_retracted: bool, referenced_works: list[str], cited_by_count: int, oa_pdf_urls: list[str], raw: dict)`
    - The abstract is rebuilt from `abstract_inverted_index`.
  - `openalex.search(http, query, *, api_key, per_page=25, filters: dict | None = None) -> list[ItemIn]`
  - `openalex.cited_by(http, openalex_id, *, api_key, per_page=25) -> list[ItemIn]` (`filter=cites:<id>`)
  - `openalex.referenced(http, work: OpenAlexWork, *, api_key, limit=50) -> list[ItemIn]`: batched `filter=openalex:W1|W2…` (≤ 50 per call)
- `scripts/record_fixtures.py` (live; run once by the implementer) records:
  - Crossref for the Wakefield DOI `10.1016/S0140-6736(97)11096-0`, a normal DOI `10.7717/peerj.1364`, and a non-existent DOI `10.9999/rk-does-not-exist-2026`
  - OpenAlex for the same DOIs plus one search (`"quotation accuracy"`, `per_page=5`)

  Each fixture is written as `{url, params, status, headers, body}`. Requests use `mailto` from the env var `RK_FIXTURE_MAILTO` (default `rk-fixtures@users.noreply.github.com`).

- [ ] **Step 1: Run the recorder.** `RK_FIXTURE_MAILTO=… uv run python scripts/record_fixtures.py crossref openalex`. Verify the files exist.
- [ ] **Step 2: Write failing tests** (respx serves the fixtures)

```python
def test_wakefield_has_retraction_update(): w = crossref.get_work(http, "10.1016/S0140-6736(97)11096-0", None); assert any(u.type == "retraction" for u in w.updates)
def test_missing_doi_none(): assert crossref.get_work(http, "10.9999/rk-does-not-exist-2026", None) is None
def test_openalex_peerj(): w = openalex.get_work(...); assert w.item.year == 2015 and w.item.ids.pmid == "26528420" and w.item.abstract
def test_openalex_wakefield_retracted(): assert openalex.get_work(...wakefield...).is_retracted
```

- [ ] **Step 3:** FAIL. **Step 4:** Implement. **Step 5:** PASS.
- [ ] **Step 6: Commit** `feat(sources): Crossref and OpenAlex adapters with recorded fixtures`

---

### Task 7: Sources — Europe PMC, PubMed/BioC, arXiv, Semantic Scholar, bioRxiv, resolver

**Files:**
- Create: `src/rk/sources/europepmc.py`, `pubmed.py`, `arxiv.py`, `s2.py`, `biorxiv.py`, `resolver.py`; extend `scripts/record_fixtures.py`; `tests/test_sources_more.py`, `tests/test_resolver.py`

**Interfaces:**
- Produces:
  - **Europe PMC**
    - `europepmc.search(http, query, *, open_access_only=False, page_size=25) -> list[ItemIn]` (`resultType=core`)
    - `europepmc.fulltext_xml(http, pmcid) -> bytes | None` (`/{pmcid}/fullTextXML`)
  - **PubMed**
    - `pubmed.esearch(http, term, retmax=50) -> list[str]`
    - `pubmed.esummary(http, pmids: list[str]) -> list[ItemIn]`
    - `pubmed.bioc_json(http, pmcid) -> dict | None`: `https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{pmcid}/unicode`
  - **arXiv**
    - `arxiv.query(http, *, search_query: str | None = None, id_list: list[str] | None = None, max_results=25) -> list[ItemIn]` (Atom parsed with lxml)
    - `arxiv.html_url(arxiv_id) -> str`: `https://arxiv.org/html/<id>`
    - `arxiv.pdf_url(arxiv_id) -> str`
  - **Semantic Scholar**
    - `s2.search(http, query, *, api_key, limit=20) -> list[ItemIn]`
    - `s2.snippet_search(http, query, *, api_key, limit=10) -> list[Snippet(paper_ids: Ids, text: str, section: str | None)]`
    - Both raise `NotConfigured("s2_key")` when `api_key` is None.
  - **bioRxiv**: `biorxiv.details(http, doi) -> ItemIn | None` (`/details/biorxiv/{doi}`, then medrxiv as a fallback)
  - **Resolver**
    - `resolver.resolve(ctx: SourceContext, ident: Identifier) -> ResolvedWork | None`
      - `SourceContext(http, settings)`
      - `ResolvedWork(item: ItemIn, retraction_updates: list[Update], openalex: OpenAlexWork | None, crossref: CrossrefWork | None, sources_used: list[str])`
      - Routing:
        - DOI: Crossref + OpenAlex, merging the identifiers; if Crossref returns None and the DOI prefix is `10.1101`, try bioRxiv.
        - PMID / PMCID: OpenAlex `pmid:`; on miss, PubMed esummary.
        - arXiv: arXiv API + OpenAlex by DOI `10.48550/arXiv.<id>`.
        - OpenAlex ID: OpenAlex.
    - `resolver.search(ctx, query, source: Literal["openalex","europepmc","pubmed","arxiv","s2"], limit=25) -> list[ItemIn]`

- [ ] **Step 1:** Record fixtures:
  - Europe PMC search `"quotation accuracy"` (open access, 3 results), plus fullTextXML for `PMC4627914`
  - PubMed esearch/esummary for PMID `26528420`, plus BioC for `PMC4627914`
  - arXiv `id_list=2404.10774`
  - bioRxiv `10.1101/2024.12.31.630767`
  - S2 search: recorded only if `S2_API_KEY` is set; otherwise its test asserts `NotConfigured`
- [ ] **Step 2: Write failing tests.**
  - Each adapter returns an `ItemIn` with the expected title fragment and identifiers.
  - `resolve(parse_identifier("10.7717/peerj.1364"))` merges the PMID from OpenAlex and has `sources_used == ["crossref","openalex"]`.
  - `resolve` on the arXiv id returns `item_type == "preprint"`.
  - `s2.search(api_key=None)` raises `NotConfigured`.
- [ ] **Step 3:** FAIL. **Step 4:** Implement. **Step 5:** PASS.
- [ ] **Step 6: Commit** `feat(sources): Europe PMC, PubMed/BioC, arXiv, S2, bioRxiv adapters and resolver`

---

### Task 8: Text layer, normalization keys, PDF extraction

**Files:**
- Create: `src/rk/text/layer.py`, `src/rk/text/pdf.py`, `tests/fixtures/docs/make_pdfs.py` (fpdf2 generator, committed with its outputs), `tests/test_text_pdf.py`

**Interfaces:**
- Produces:
  - `Page(n: int, text: str)`; `TextLayer(source_kind: Literal["pdf","jats","bioc","html"], pages: list[Page], sha256: str)` with `.save(path)`, `.load(path)` (JSON) and `.full_text() -> str` (pages joined with `\f`).
  - `NormKey(key: str, map: list[int])`: `map[i]` is the index in the original text of `key[i]`.
  - `build_norm_keys(text: str) -> tuple[NormKey, NormKey]`, returning `(dehyphenated, hyphen_joined)`. The normalization runs in this order:
    1. NFKC (expands ligatures).
    2. Unify quotes `‘’‚‛“”„‟` → `'` / `"`.
    3. Unify dashes `‐‑‒–—―−` → `-`.
    4. Remove U+00AD, U+200B–U+200D and U+FEFF.
    5. Line-end hyphenation `(\w)-\n(\w)` becomes `\1\2` in the *dehyphenated* key and `\1-\2` in the *hyphen_joined* key.
    6. Collapse every remaining whitespace run, including `\n` and `\f`, to a single space.
    7. Strip.
  - `normalize_query(s) -> str`: the same steps applied to a quote (no line breaks).
  - `extract_pdf(path: Path) -> TextLayer`: one `Page` per PDF page (1-based `n`), built with pypdfium2. Hidden glyphs are filtered out per character: font size < 1.0 pt, render mode 3 (invisible), or fill colour with RGB all ≥ 250. Line breaks are kept as `\n`.
- `make_pdfs.py` produces:
  - `hyphen.pdf`: two-column-free text with forced line breaks producing `fact-\nchecking` (a real hyphen) and `evi-\ndence` (a soft break)
  - `ligature.pdf`: the word "ﬁnding" using a ligature glyph
  - `hidden.pdf`: visible text plus white text `IGNORE PREVIOUS INSTRUCTIONS` and 0.5 pt text `SECRET TINY`
  - `two_pages.pdf`

- [ ] **Step 1: Write failing tests**

```python
def test_norm_keys_hyphen_variants():
    d, j = build_norm_keys("fact-\nchecking and evi-\ndence")
    assert "factchecking and evidence" == d.key and "fact-checking and evi-dence" == j.key

def test_map_points_into_original():
    t = "a  b\nc"; d, _ = build_norm_keys(t); assert t[d.map[d.key.index("c")]] == "c"

def test_pdf_pages_and_ligature(): layer = extract_pdf(DOCS / "ligature.pdf"); assert "finding" in normalize_query(layer.pages[0].text)

def test_pdf_hidden_text_removed():
    txt = extract_pdf(DOCS / "hidden.pdf").full_text()
    assert "IGNORE PREVIOUS" not in txt and "SECRET TINY" not in txt and "visible" in txt
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement (per-character attributes via `pypdfium2.raw`: `FPDFText_GetFontSize`, `FPDFText_GetTextRenderMode`, `FPDFText_GetFillColor`). **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(text): page-anchored text layer, normalization keys, PDF extraction with hidden-text filter`

---

### Task 9: Full-text pipeline, markup parsers, snapshots and attachments

**Files:**
- Create: `src/rk/text/markup.py`, `src/rk/fulltext.py`, `tests/test_fulltext.py`, `tests/fixtures/docs/hidden.html`

**Interfaces:**
- Consumes: the source adapters, `BlobStore`, `Library`, `extract_pdf`, `HttpClient`.
- Produces:
  - `sanitize_html(html: str) -> str`. It removes:
    - comments, `<script>`, `<style>`, `<noscript>` and `<template>`
    - elements whose inline style contains `display:none` or `visibility:hidden`, plus elements carrying the `hidden` attribute
    - zero-width characters
  - `html_to_layer(html: str, sha: str) -> TextLayer`: `sanitize_html` → `trafilatura.extract(..., include_comments=False, include_tables=True)` → a single page.
  - `jats_to_layer(xml: bytes, sha) -> TextLayer`: title, abstract and body `<p>` elements, with section titles as their own lines; one page.
  - `bioc_to_layer(doc: dict, sha) -> TextLayer`: passages joined by `\n\n` in document order; one page.
  - `fetch_fulltext(ps: ProjectStore, lib: Library, ctx: SourceContext, item_id: str, *, actor: str) -> FulltextResult(snapshot_sha: str, kind: str, origin: str, page_count: int) | None`
    - Fetch order (spec §11.1, API part):
      1. Europe PMC fullTextXML (pmcid)
      2. BioC (pmcid)
      3. arXiv HTML
      4. OA PDF URLs (`item.oa_urls` ∪ the OpenAlex `oa_pdf_urls`)

      Any download whose content type isn't PDF/XML/HTML is skipped.
    - Side effects, all written as events:
      - the raw blob is stored
      - the `TextLayer` is saved to `text/<sha>.json`
      - event `snapshot.created` `{snapshot_sha, item_id, kind, url}`
      - for PDFs, an attachment: a human-readable copy at `library/files/<item_id>/<FirstAuthor> <year> - <short title>.pdf`, event `attachment.added` `{origin: "oa_api"}`
    - The fulltext text goes into `items_fts.fulltext`.
  - `Library.attach(item_id, path: Path, *, origin: Literal["user_upload","import"], actor) -> str`: a user-supplied PDF → blob + attachment + snapshot via `extract_pdf`.

- [ ] **Step 1: Write failing tests** (served by respx from Task 7 fixtures plus a small generated PDF)

```python
def test_jats_first(...): r = fetch_fulltext(...PMC4627914 item...); assert r.kind == "jats" and "quotation errors" in layer_text(r)
def test_pdf_fallback_creates_attachment(...): item with only oa_urls -> kind == "pdf"; attachment file exists under files_dir/item_id
def test_html_hidden_text_removed(): t = html_to_layer(open(DOCS/"hidden.html").read(), "x").full_text(); assert "IGNORE PREVIOUS" not in t
def test_no_source_returns_none(...)
def test_user_attach_pdf(...): attach(...) -> snapshot kind "pdf", fts finds a word from the pdf
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(fulltext): JATS/BioC/HTML/PDF pipeline with snapshots and library attachments`

---

### Task 10: Verifiers — V-QUOTE, V-PARA, V-HEDGE, and the unit gold set

**Files:**
- Create: `src/rk/verify/quote.py`, `src/rk/verify/paraphrase.py`, `src/rk/verify/hedge.py`, `src/rk/verify/hedge_lexicon.toml`, `scripts/make_gold.py`, `tests/gold/quote_cases.jsonl`, `tests/test_verify_text.py`

**Interfaces:**
- Consumes: `TextLayer`, `build_norm_keys`, `normalize_query`.
- Produces:
  - `verify_quote(quote: str, layer: TextLayer) -> QuoteResult(ok: bool, page: int | None, start: int | None, end: int | None, best_score: float, best_snippet: str)`
    - `ok` is True only when `normalize_query(quote)` is a substring of either norm key of some page. `start` and `end` are offsets into that page's original text, taken via the key map.
    - Otherwise `rapidfuzz.fuzz.partial_ratio_alignment` gives the best snippet and score, and `ok=False`.
  - `check_paraphrase(paraphrase: str, source_span: str) -> ParaResult(ok: bool, longest_common_run: int, ngram4_containment: float, token_set_ratio: float)`
    - Tokens are lowercase `\w+`.
    - `ngram4_containment` = |4-grams(paraphrase) ∩ 4-grams(source)| / |4-grams(paraphrase)|, or 0 when the paraphrase has fewer than 4 tokens.
    - Thresholds per Global Constraints.
    - This is **only a lexical-closeness flag** (spec §9.1); semantic fidelity is Plan 2's V-SUPPORT.
  - `hedge_level(text: str) -> int`. Lexicon levels:
    - 0: speculative, e.g. may, might, could, possibly, perhaps, potentially, conceivably
    - 1: tentative, e.g. suggest(s/ed), indicate(s/d), likely, appear(s), seem(s), associated with
    - 2: assertive, e.g. show(s/ed), find/found, demonstrate(s/d), observe(d)
    - 3: categorical, e.g. prove(s/d), establish(es/ed), always, never, all, definitively, conclusively, undoubtedly

    Rule: if any level-0 term is present → 0; elif any level-1 term → 1; elif any level-3 term → 3; else 2. Matching is word-boundary and case-insensitive.
  - `check_hedge(claim: str, sources: list[str]) -> HedgeResult(ok: bool, claim_level: int, source_level: int)`, where `source_level = max(hedge_level(s) for s in sources)` and `ok = claim_level <= source_level`.
- `scripts/make_gold.py` generates `tests/gold/quote_cases.jsonl` (≥ 120 cases) from the fixture text layers:
  - three generated PDFs plus the Europe PMC JATS and BioC layers of PMC4627914 plus the arXiv HTML of 2404.10774
  - ≥ 30 passages
  - Each passage yields cases of four kinds: `exact` (expect ok), `word_swap`, `number_change`, `cross_page_splice` (expect not ok), plus paraphrase cases `copy_with_one_word_changed` (expect too_close) and hedge cases `strengthened` (expect overstated)

  Labels follow from how each case is constructed, never from a model (spec §10).

- [ ] **Step 1: Write failing tests**

```python
def test_quote_hyphen_variants():
    L = extract_pdf(DOCS/"hyphen.pdf")
    assert verify_quote("fact-checking", L).ok and verify_quote("evidence", L).ok
    r = verify_quote("fact checking evidence base", L); assert not r.ok and r.best_snippet

def test_quote_locator_points_to_text():
    L = extract_pdf(DOCS/"two_pages.pdf"); r = verify_quote(PHRASE_ON_PAGE2, L)
    assert r.page == 2 and normalize_query(L.pages[1].text[r.start:r.end]) == normalize_query(PHRASE_ON_PAGE2)

def test_paraphrase_thresholds():
    src = "Quotation errors are common in medical journal articles and readers should be aware of them."
    assert not check_paraphrase("Quotation errors are common in medical journal articles, readers should know.", src).ok
    assert check_paraphrase("Medical papers frequently cite sources inaccurately.", src).ok

def test_hedge_monotonic():
    assert check_hedge("X may reduce Y", ["X reduced Y in mice"]).ok
    assert not check_hedge("X proves that Y", ["results suggest X affects Y"]).ok

def test_gold_set_100_percent():
    for case in load_jsonl(GOLD/"quote_cases.jsonl"):
        assert run_case(case) == case["expected"], case["id"]
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement the verifiers, then run `uv run python scripts/make_gold.py`. **Step 4:** PASS (the gold accuracy must be 100%).
- [ ] **Step 5: Commit** `feat(verify): V-QUOTE, V-PARA, V-HEDGE with generated gold set`

---

### Task 11: Verifiers — V-REF, V-RETRACT, and the citation trap set

**Files:**
- Create: `src/rk/verify/ref.py`, `src/rk/verify/retract.py`, `tests/gold/trap_refs.json`, `tests/test_verify_refs.py`

**Interfaces:**
- Consumes: `resolver.resolve`, `CrossrefWork.updates`, `OpenAlexWork.is_retracted`.
- Produces:
  - `CitedRef(identifier: str | None, title: str, first_author: str | None, year: int | None)`
  - `verify_reference(ctx, cited: CitedRef) -> RefResult(verdict: Literal["pass","mismatch","not_found","page_metadata_only"], resolved: ItemIn | None, details: dict)`
    - With no identifier → `page_metadata_only`.
    - When the identifier resolves to nothing → `not_found`.
    - Otherwise compare against the Global Constraints thresholds; `details` records each comparison.
  - `retraction_status(resolved: ResolvedWork) -> RetractionInfo(status: Literal["none","retracted","withdrawn","eoc","corrected","unknown"], sources: list[str], notice_doi: str | None)`
    - It is the union of Crossref `updated-by` types and OpenAlex `is_retracted`. Priority: retracted > withdrawn > eoc > corrected > none. `unknown` when neither source responded.
  - `verify_item(ps, lib, ctx, item_id, *, actor) -> tuple[RefResult, RetractionInfo]`: updates `ref_status` and `retraction` via `Library.update`, writes a `verifications` row through the event `verification.recorded`, and adds retracted/EoC items to the "Retracted / EoC" collection.
- `trap_refs.json` cases, each with its expected result. These run offline on fixtures, and additionally `@pytest.mark.live` against the real APIs:

  | Case | Expected |
  |---|---|
  | `10.9999/rk-does-not-exist-2026` (fabricated) | `not_found` |
  | `10.7717/peerj.1364` cited with the title "Citation accuracy in surgical journals" | `mismatch` |
  | `10.7717/peerj.1364` with the correct title, author Jergas, year 2015 | `pass` |
  | Wakefield `10.1016/S0140-6736(97)11096-0` | ref `pass` + retraction `retracted` |
  | Surgisphere `10.1016/S0140-6736(20)31180-6` | `retracted` |
  | one EoC DOI and one corrected DOI, picked by the recorder from Crossref `filter=update-type:expression_of_concern` / `update-type:correction` and stored in the fixture | `eoc` / `corrected` |

- [ ] **Step 1:** Extend the recorder for the Surgisphere DOI and the EoC/correction samples; record them.
- [ ] **Step 2: Write failing tests:** every trap case yields its expected verdict and retraction status (detection rate 100%), and `verify_item` puts Wakefield into the "Retracted / EoC" collection.
- [ ] **Step 3:** FAIL. **Step 4:** Implement. **Step 5:** PASS. **Step 6:** `uv run pytest -m live tests/test_verify_refs.py -q`. Expected PASS; record the result in the commit body.
- [ ] **Step 7: Commit** `feat(verify): V-REF and V-RETRACT with citation trap set`

---

### Task 12: Notes with immediate verification, text read/find

**Files:**
- Create: `src/rk/notes.py`, `tests/test_notes.py`

**Interfaces:**
- Consumes: `verify_quote`, `check_paraphrase`, `hedge_level`, `TextLayer.load`, `ProjectStore`, `Library`.
- Produces:
  - `NoteIn`, with fields:
    - `source_id: str`, `kind: Literal["quote","paraphrase","summary","own"]`, `text: str`
    - `locator: Locator | None = None`, where `Locator(page: int, start: int, end: int)`
    - `role_in_source: Literal["main","sub","concession","qualification","summary_of_other"] | None`
    - `keywords: list[str] = []`, `source_span: str | None = None` (required for paraphrase)
  - `add_note(ps, lib, note: NoteIn, *, actor: str) -> NoteResult(note_id: str, verified: dict)`
    - `quote`: requires a snapshot of the source; runs `verify_quote` against the latest snapshot. On failure, raise `NoteRejected(reason="quote_not_found", best_snippet=…, best_score=…)` and store nothing. On success, set the locator from the result.
    - `paraphrase`: requires `source_span`, which must itself pass `verify_quote` (otherwise `NoteRejected("span_not_found")`). It must also pass `check_paraphrase` (otherwise `NoteRejected("too_close", …)`).
    - `role_in_source` is required for `quote` and `paraphrase`.
    - `hedge_level` is stored for every non-`own` note.
    - Event `note.added`; the note text goes into `items_fts.notes`.
  - `text_read(ps, source_id, page: int | None = None, start: int | None = None, end: int | None = None) -> TextSlice(page, start, end, text, snapshot_sha)`
  - `text_find(ps, source_id, query: str, limit=10) -> list[TextMatch(page, start, end, snippet)]`: case-insensitive search over the norm keys, with snippets of ±80 chars of original text.

- [ ] **Step 1: Write failing tests:**
  - A verbatim quote is stored with a page locator.
  - A misquote raises `NoteRejected` with `best_snippet`, and the `notes` table is unchanged.
  - A too-close paraphrase is rejected.
  - A quote without `role_in_source` raises `ValueError`.
  - `text_find` returns locators that `text_read` reproduces.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(notes): immediately verified quote/paraphrase notes and text read/find`

---

### Task 13: Tool registry, `rk serve` daemon, CLI, setup wizard, end-to-end smoke

**Files:**
- Create: `src/rk/tools/registry.py`, `src/rk/tools/builtin.py`, `src/rk/server/runtime.py`, `src/rk/server/app.py`, `src/rk/cli/client.py`, `src/rk/cli/main.py`, `src/rk/cli/setup.py`, `tests/test_tools.py`, `tests/test_server_cli.py`, `README.md` (quickstart)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - **Registry**
    - `Tool(name: str, summary: str, input_model: type[BaseModel], output_model: type[BaseModel], roles: frozenset[str], writes: bool, handler: Callable[[CallContext, BaseModel], BaseModel])`
    - `CallContext(role: str, actor: str, project_slug: str | None, idempotency_key: str | None)`
    - `ToolRegistry.register(tool)`, `.list(role: str) -> list[Tool]`, `.call(name, args: dict, ctx) -> dict`
      - Validates the input model.
      - Raises `PermissionDenied` when `ctx.role` is not in `tool.roles`.
      - For `writes=True` tools, requires `idempotency_key` and returns the stored result when that `(tool, key)` pair was seen before (table `idempotency`).
  - **Plan-1 tools** in `builtin.py`. The role set for Plan 1 is `{"lead","cli"}`; Plan 2 adds the others.

    | Tool | Notes |
    |---|---|
    | `rk_project_create(slug, title, field?, paper_type?)` | |
    | `rk_project_list` | |
    | `rk_project_status(slug)` | counts of items, snapshots, notes, verifications; `verify_chain` result |
    | `rk_search_works(query, source, limit)` | |
    | `rk_get_work(identifier)` | |
    | `rk_source_add(identifier \| item, discovered_via, stance?, verify: bool = True)` | runs `verify_item` when `verify` is set |
    | `rk_expand_citations(source_id, direction: backward\|forward, limit)` | |
    | `rk_source_fetch_fulltext(source_id)` | |
    | `rk_text_read` | |
    | `rk_text_find` | |
    | `rk_note_add` | |
    | `rk_verify(target_type="item", target_id)` | |
    | `rk_library_search` | |
    | `rk_library_tag` | |
    | `rk_library_collect` | |
    | `rk_library_import(format, text)` | |
    | `rk_library_export(format, collection_id?)` | |
    | `rk_library_attach(source_id, path)` | |
  - **Runtime**
    - `acquire_serve_lock(home) -> ServeRuntime(port, token)`. It writes `serve.lock` (pid), `serve.port`, and `serve.token` (0600, `secrets.token_urlsafe(32)`). It raises `AlreadyRunning(pid)` when the existing lock's pid is alive; a stale lock is taken over.
    - `release(runtime)`
  - **App**
    - `create_app(registry, home) -> FastAPI`
    - `GET /healthz` → `{"ok": true, "version"}` (no auth)
    - `GET /api/tools` → the tool list for role `cli`
    - `POST /api/tools/{name}` with body `{"args": {...}, "idempotency_key": str | null}`, header `Authorization: Bearer <token>`, and a server-side role of `cli`. It returns `{"ok": true, "result": …}` or `{"ok": false, "error": {"type", "message", "details"}}` with status 4xx.
    - All `writes=True` calls run under one process-wide `threading.Lock` (the single writer). Reads run concurrently.
    - Binds `127.0.0.1:<free port>`.
  - **Client**
    - `RkClient.from_home(home)`; `ensure_server(home, timeout_s=15) -> RkClient` spawns `sys.executable -m rk.cli.main serve --detach` when `/healthz` fails, then polls.
    - `RkClient.call(name, args, idempotency_key=None) -> dict` raises `RkError(type, message, details)`.
  - **CLI (typer):**
    - `rk serve [--detach]`
    - `rk project create|list|use|status`
    - `rk search <query> [--source openalex|europepmc|pubmed|arxiv|s2] [--limit N] [--add]`
    - `rk add <identifier>...`
    - `rk fetch <item_id>`
    - `rk read <item_id> [--page N]`
    - `rk find <item_id> <query>`
    - `rk note add <item_id> --kind quote|paraphrase|summary|own --text … [--role main|…] [--span …] [--keyword …]`
    - `rk verify <item_id>`
    - `rk library list|search|tag|collect|import|export|attach`
    - `rk setup`

    Write commands generate an `idempotency_key` per invocation (`uuid4`). Output is human-readable by default, with `--json` for raw output.
  - **`rk setup`** (CLI version of spec §8.8, steps 1–3 and 6). It prompts for:
    - an email, checked with Crossref `/works/10.7717/peerj.1364?mailto=…` (expect 200)
    - an OpenAlex key, after printing `https://openalex.org/settings/api` and the instructions; checked with `/works/W2741809807?api_key=…` and by reporting the `x-ratelimit-*` budget
    - an S2 key (optional; prints `https://www.semanticscholar.org/product/api#api-key-form`; when left empty, it is recorded as pending)

    Settings are saved with mode 0600, and a capability table (source → available/limited/missing) is printed. All prompts accept Enter to skip.

- [ ] **Step 1: Write failing tests**

```python
def test_registry_permission_and_idempotency(reg, ctx_cli):
    with pytest.raises(PermissionDenied): reg.call("rk_project_create", {...}, CallContext(role="skeptic", actor="x", project_slug=None, idempotency_key="k"))
    a = reg.call("rk_project_create", {"slug": "p1", "title": "T"}, ctx_cli("k1")); b = reg.call(...same..., ctx_cli("k1"))
    assert a == b and len(reg.call("rk_project_list", {}, ctx_cli(None))["projects"]) == 1

def test_server_auth(tmp_home):  # TestClient
    assert client.post("/api/tools/rk_project_list", json={"args": {}}).status_code == 401

def test_concurrent_cli_writes(tmp_home, live_server):
    # 2 threads x 10 rk_library_tag calls on same item via RkClient -> all ok, verify_chain ok, 20 tag events

def test_setup_wizard_with_mocks(monkeypatch, tmp_home):
    # feeds inputs: email, openalex key, empty s2 -> config saved 0600; capability table mentions s2 "pending"
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** `uv run pytest -q` (full offline suite). Expected: all PASS.
- [ ] **Step 5: Live smoke** (real network, a temporary `RK_HOME`). Steps:

  ```bash
  export RK_HOME=$(mktemp -d)
  uv run rk project create smoke --title "Smoke"
  uv run rk add 10.1016/S0140-6736(97)11096-0 10.7717/peerj.1364
  uv run rk library list            # Wakefield shows retraction=retracted, ref=pass
  uv run rk fetch <peerj item_id>   # kind=jats
  uv run rk note add <peerj item_id> --kind quote --role main --text "<a sentence copied from the article>"   # verified, page locator printed
  uv run rk note add <peerj item_id> --kind quote --role main --text "Quotation errors are rare in medicine."  # rejected with best snippet
  uv run rk library export --format bibtex > /tmp/smoke.bib  # parses back via import
  uv run rk project status smoke    # chain ok
  ```

  Expected: each comment holds. Paste the outputs into the commit body.
- [ ] **Step 6: Commit** `feat: tool registry, rk serve single-writer daemon, CLI and setup wizard`

---

## Self-Review Notes

**Spec coverage (Plan 1 row of §12):**

| Requirement | Task |
|---|---|
| Skeleton, config | 1 |
| Event log, hash chain, projections, rebuild | 2 |
| Library core (items, attachments, collections, tags, FTS5) | 3, 9 |
| BibTeX/RIS/CSL | 4 |
| Data sources | 6, 7 |
| HTTP cache and budget | 5 |
| Full-text pipeline | 8, 9 |
| V-REF / V-RETRACT | 11 |
| V-QUOTE / V-PARA / V-HEDGE | 10 |
| `note_add` immediate verification | 12 |
| Tool registry, CLI incl. `rk setup` | 13 |
| Unit gold set and trap set | 10, 11 |
| §6.0 single writer | 13 |
| §11.1 API part of the full-text order | 9 |

The browser part of §11.1 belongs to Plan 3.

**Deferred by design:** LLM providers, MCP, roles beyond `lead`/`cli` (Plan 2); browser and Workbench (Plan 3); the orchestrator and stages (Plans 4–5). The registry already carries `roles` and `writes`, so Plan 2 extends it without rewriting.
