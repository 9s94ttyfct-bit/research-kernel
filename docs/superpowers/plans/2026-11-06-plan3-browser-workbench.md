# Plan 3 — Built-in Browser and Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rk serve` owns one Playwright Chromium window with a persistent profile. Its first tab is the Workbench. Agents search Google Scholar, PubMed, Europe PMC and the library discovery system at human pace, open articles, parse page metadata, and download PDFs through the library proxy or the publisher into the project library. CAPTCHAs and expired library logins go to you as checkpoints.

**Architecture:**
- `rk.browser` holds:
  - `session`: a dedicated Playwright thread and event loop, tab registry and Workbench isolation;
  - `pacing`: per-machine quotas in `kernel.sqlite`;
  - `detect`: CAPTCHA and SSO detection;
  - `metadata`: Highwire, Dublin Core, JSON-LD and COinS parsing;
  - `adapters`: pure HTML→hits parsers, one per site;
  - `service`: orchestration, browse log and ingestion.
- Tools `rk_browser_*` use the existing registry and role policy.
- `rk.ui` holds the Workbench: FastAPI routes under `/ui/api` plus one WebSocket, served to a static no-build frontend. A per-start session cookie authenticates it, separate from `serve.token`.
- `rk setup` gains the provider, library-login and capability steps (spec §8.8 4–6), which the web settings page shares.

**Tech Stack:** Plan 1/2 stack + `playwright>=1.64,<2` (Apache-2.0; Chromium build `chromium-1248` is already installed under `~/Library/Caches/ms-playwright`), `websockets>=15,<18` (BSD-3, needed by uvicorn for WS), `markdown-it-py>=4,<5` (MIT, already transitive; now direct).

**Spec:** `docs/superpowers/specs/2026-11-05-research-kernel-design.md` §6.0, §6.1 (`browser-profile/`, `projects/<slug>/browser/`), §6.3 (`browse_log`, `attachment.origin`), §8.1 (Scout/Reader/Lead tools), §8.4 (browser tools), §8.5, §8.6 ("save to library"), §8.7, §8.8, §9.3 (Workbench token, profile never exported), §11.1 (full-text order), §13 items 2–4 (defaults: conservative pacing; Primo first, then Summon; translation-server optional).

**Deviations (decided here, flagged to the user):**
1. **Run controls and the stage graph move to Plan 4.** The orchestrator they control does not exist yet. The "Stages & progress" page shows tasks, checkpoints and the project's event feed now. No placeholder buttons.
2. **Topic review cards move to Plan 4.** Checkpoints are answered through a generic options/free-text form.
3. **Draft preview reads `artifacts/drafts/<section>.v<N>.md` files.** Plan 5's `rk_draft_write` will create them. Marker resolution is implemented and tested now against claims, notes and items.
4. **The library login test (wizard step 5) cannot be completed by an agent.** The flow, `login_wait` and proxy handling are tested against a local fake SSO site. The real login is your step.

## Global Constraints

- All Plan 1/2 constraints. Git identity repo-local `9s94ttyfct-bit`; no personal paths or hostnames in commits or fixtures.
- **Browser:** `chromium.launch_persistent_context($RK_HOME/browser-profile, headless=<setting>, accept_downloads=True)`, owned only by `rk serve`. `browser_headless` defaults to `False`. Tests and agent smoke runs set `RK_BROWSER_HEADLESS=1`. The browser is never opened on the user's desktop during development.
- **Pacing defaults** (spec §8.5, settings key `browser_limits`):
  - Scholar: random gap 20–45 s, ≤ 60 queries/day.
  - PDF downloads per publisher domain: gap ≥ 30 s, ≤ 40/day.
  - All browser PDF downloads: ≤ 150/day.
  - Browser task concurrency: 1.
  - Other sites (PubMed, Europe PMC, Primo, Summon, generic open): gap ≥ 5 s, no daily cap.
  - `browser_max_wait_s = 120`: a call that would wait longer fails with `paced` and `retry_after_s`.
  - Quota days are local calendar days.
- **Never solve a CAPTCHA, never fill a login form, never store a library password.** A detected CAPTCHA or "unusual traffic" page pauses that site and opens a `captcha_wait` checkpoint. A redirect to SSO opens `login_wait`.
- **Workbench isolation:**
  - Agent tools see and act only on tabs opened for their own `task_id` (CLI/human sees all agent tabs).
  - Agent pages route-abort every request to the Workbench origin (`127.0.0.1:<port>`, `localhost:<port>`).
  - The Workbench tab is never listed or returned.
- **Workbench auth:**
  - A one-time login code is generated per start. `GET /ui/login?code=…` sets the cookie `rk_ui` (random, HttpOnly, SameSite=Strict, Path=/) and redirects to `/ui/`.
  - Codes are single-use with a 120 s TTL.
  - `/ui/api/*` and `/ui/ws` require the cookie. State-changing requests and WS upgrades also require `Origin` = the server origin.
  - The serve token never authenticates `/ui/*`; the UI cookie never authenticates `/api/*`. Workbench calls act as `CallContext(role="cli", actor="ui:user")`, which makes them human.
- **Metadata precedence** (spec §8.5):
  - API metadata (Crossref/OpenAlex/PubMed/arXiv single lookups) wins over page metadata, and differences are recorded in the `item.added` payload under `page_metadata_diff`.
  - With no resolvable identifier, page metadata is used and `ref_status = "page_metadata_only"`.
- **Attachment origins:** `library_browser` when the URL went through `library_proxy`, else `publisher_browser`. `access_note = "Obtained via <library_name or 'publisher site'> in the built-in browser; personal research use only"`.
- **Full-text order** (spec §11.1): the browser channel runs only after all API channels and only if the browser is running and `fulltext_browser_fallback` (default `True`) is set.
- Pytest marker `browser`: tests that launch Chromium. They are skipped (not failed) when the Playwright Chromium executable is missing and they run in the default suite. All site tests use synthetic fixture pages served by a local `http.server`; there is no network. `live` stays opt-in.

## Review Focus

1. **An agent tab tries to reach the Workbench** (navigate to or `fetch` `http://127.0.0.1:<port>/ui/api/...`). The request is aborted, and `rk_browser_tabs` never lists the Workbench tab. Test: Task 4 `test_agent_tab_cannot_reach_workbench`.
2. **Scholar serves a CAPTCHA in the middle of a session.** The search returns `human_required` with a `captcha_wait` checkpoint id. Later Scholar calls fail fast with `site_paused` until the checkpoint is answered, and the page is never interacted with. Test: Task 6 `test_captcha_pauses_site_until_answered`.
3. **The library session expired:** the proxied DOI redirects to an SSO page. The fetch opens `login_wait`, attaches nothing, and the SSO HTML is never stored as a PDF. Test: Task 6 `test_sso_redirect_opens_login_wait`.
4. **The "PDF" link returns HTML** (paywall or interstitial). Nothing is attached and the result is `pdf_not_obtained` with the final URL. Test: Task 6 `test_pdf_link_returning_html_not_attached`.
5. **A cap or long gap is reached, then `rk serve` restarts.** Counters persist, so the 61st Scholar query of the day fails `daily_cap_reached` after a restart, and a gap longer than `browser_max_wait_s` fails `paced` instead of blocking. Test: Task 1 `test_quota_survives_restart` / `test_wait_beyond_max_is_paced_error`.

---

## File Structure

```
src/rk/browser/__init__.py        BrowserUnavailable, HumanRequired, SitePaused errors
src/rk/browser/pacing.py          Limits, Pacer (kernel.sqlite tables browser_quota, browser_pauses)
src/rk/browser/log.py             browse_log projection + record_browse()
src/rk/browser/metadata.py        PageMetadata, parse_page_metadata(), to_item(), merge_with_api()
src/rk/browser/detect.py          detect_block(url, html) -> Block|None
src/rk/browser/session.py         BrowserSession (Playwright thread), Tab
src/rk/browser/adapters/__init__.py  SearchHit, Adapter protocol, ADAPTERS
src/rk/browser/adapters/{scholar,pubmed,europepmc,primo,summon}.py
src/rk/browser/adapters/publisher.py  find_pdf_url(landing_url, html) -> str|None
src/rk/browser/translation.py     optional Zotero translation-server client
src/rk/browser/service.py         BrowserService (search/open/snapshot/act/fetch/save_page/request_human)
src/rk/browser/tools.py           register_browser_tools(registry, kernel, service_getter)
src/rk/ui/__init__.py
src/rk/ui/auth.py                 UiAuth (login codes, session cookie, origin check)
src/rk/ui/api.py                  ui_router(kernel, registry, chats, browser, auth) -> APIRouter
src/rk/ui/drafts.py               list_drafts(), render_draft() with marker resolution
src/rk/ui/static/{index.html,app.js,style.css}
src/rk/cli/setup.py               + steps 4–6 (providers, library login, capabilities)
src/rk/server/app.py              mount ui router + static, lifespan-free (serve wires browser)
src/rk/cli/main.py                serve: start/stop browser, open Workbench; rk open; rk browser …
tests/test_browser_pacing.py, test_browser_metadata.py, test_browser_detect.py,
tests/test_browser_adapters.py, test_browser_session.py, test_browser_service.py,
tests/test_ui_api.py, test_ui_e2e.py, test_setup_wizard.py
tests/fixtures/browser/           synthetic HTML pages + tiny PDFs (generated by make_pdfs.py)
tests/browser_sites.py            local fixture HTTP server (scholar, pubmed, publisher, sso, captcha)
```

---

### Task 1: Settings, pacing and the browse log

**Files:**
- Create: `src/rk/browser/__init__.py`, `src/rk/browser/pacing.py`, `src/rk/browser/log.py`
- Modify: `src/rk/config.py` (fields), `src/rk/ids.py` (`brw`), `src/rk/store/schema.sql`, `src/rk/store/projections.py`
- Test: `tests/test_browser_pacing.py`

**Interfaces:**
- Produces:
  - `Settings` fields:
    - `browser_enabled: bool = True`, `browser_headless: bool = False` (env `RK_BROWSER_HEADLESS=1` overrides);
    - `browser_limits: dict[str, dict] = DEFAULT_BROWSER_LIMITS`, `browser_max_wait_s: float = 120`, `browser_max_steps: int = 30`;
    - `library_name: str | None`, `library_portal_url: str | None`;
    - `library_proxy: str | None` (template containing `{url}`), `library_discovery: Literal["primo","summon"] | None`;
    - `primo_base: str | None`, `primo_vid: str | None`, `primo_tab: str = "Everything"`, `primo_scope: str = "MyInst_and_CI"`, `summon_base: str | None`;
    - `translation_server_url: str | None`, `fulltext_browser_fallback: bool = True`.
  - `DEFAULT_BROWSER_LIMITS = {"scholar": {"gap": [20, 45], "per_day": 60}, "pdf_domain": {"gap": [30, 30], "per_day": 40}, "pdf_total": {"gap": [0, 0], "per_day": 150}, "default": {"gap": [5, 5], "per_day": None}}`.
  - `class Pacer(db_path: Path, limits: dict, *, max_wait_s: float, clock=time.time, sleep=time.sleep, rand=random.uniform)`:
    - `acquire(scopes: list[str]) -> None`: atomically checks every scope's daily cap and gap, sleeps the longest required wait (≤ max), then records all.
    - `pause(site, reason, project, checkpoint_id)`, `paused(site) -> dict | None`, `unpause(site)`, `status() -> dict`.
  - Scope names: `site:<name>`, `pdf:<registrable domain>`, `pdf:*`. Limits key lookup: `site:scholar`→`scholar`, `pdf:<d>`→`pdf_domain`, `pdf:*`→`pdf_total`, otherwise `default`.
  - Errors (in `rk/browser/__init__.py`, all `ToolError` subclasses):
    - `Paced(retry_after_s)` (429, type `paced`);
    - `DailyCapReached(scope, reset_at)` (429);
    - `SitePaused(site, checkpoint_id)` (409);
    - `HumanRequired(kind, checkpoint_id, url)` (409, type `human_required`);
    - `BrowserUnavailable(reason)` (503).
  - `record_browse(ps, *, task_id, site, action, url, query=None, result_count=None, screenshot_path=None, actor) -> str`: id `brw_…`, event `browse.logged`, table `browse_log(id, task_id, site, action, url, query, result_count, screenshot_path, ts)`. `action` must be one of `search|open|download|login_wait|captcha_wait`.

- [ ] **Step 1: Write the failing tests**

```python
def test_scholar_gap_is_random_between_20_and_45(tmp_path): ...   # rand stub records (20, 45); sleep called with 20..45 - elapsed
def test_daily_cap(tmp_path):                                       # 60 acquires ok, 61st raises DailyCapReached(scope="site:scholar")
def test_quota_survives_restart(tmp_path):                          # 60 acquires, new Pacer on same db, 61st raises
def test_wait_beyond_max_is_paced_error(tmp_path):                  # max_wait_s=10, gap 30 → Paced(retry_after_s≈30), nothing recorded
def test_multi_scope_acquire_is_atomic(tmp_path):                   # pdf:* at cap → pdf:<domain> count unchanged
def test_pause_and_unpause(tmp_path):                               # paused("scholar") returns checkpoint id; unpause clears
def test_new_day_resets_count(tmp_path):                            # clock moved past local midnight → allowed
def test_browse_log_projection(project):                            # record_browse → row; rebuild reproduces it; bad action → ValueError
```

- [ ] **Step 2: Run** `uv run pytest tests/test_browser_pacing.py -q`. Expected: import errors.
- [ ] **Step 3: Implement.** The pacer tables live in `kernel.sqlite` because limits are per machine/IP, not per project. Wrap check+record in `BEGIN IMMEDIATE`, and hold a process-wide `threading.Lock` while sleeping so concurrency stays 1.
- [ ] **Step 4: Run** the tests plus the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(browser): human-pace quotas, site pauses and browse log`

### Task 2: Page metadata

**Files:** Create `src/rk/browser/metadata.py`. Test `tests/test_browser_metadata.py` with fixtures `tests/fixtures/browser/meta_*.html`.

**Interfaces:**
- Produces:
  - `PageMetadata(title, creators: list[Creator], year, venue, doi, pmid, arxiv, isbn, pdf_url, abstract, url, item_type, sources: list[str])`;
  - `parse_page_metadata(html: str, url: str) -> PageMetadata`;
  - `to_item(meta, *, discovered_via="browser") -> ItemIn`;
  - `merge_with_api(meta, resolved: ResolvedWork | None) -> tuple[ItemIn, dict]` returns the item to add and the diff `{field: {"page":…, "api":…}}`.
- Precedence inside a page: Highwire `citation_*` > JSON-LD (`ScholarlyArticle|Article|Book|Chapter`) > Dublin Core (`DC.*`/`dc.*`/`dcterms.*`) > COinS (`span.Z3988[title]`, OpenURL `rft.*`) > `<title>`.
- `citation_author` values in either "Last, First" or "First Last" form become `Creator(family, given)`. `citation_date`/`citation_publication_date`/`citation_online_date` become year. `citation_pdf_url` is resolved against the page URL. A DOI is also taken from `citation_doi`, `dc.identifier` (`doi:`/`https://doi.org/`) or JSON-LD `@id`/`sameAs`.

- [ ] **Step 1: Tests**

```python
def test_highwire_full_record(): ...          # title, 3 authors in order, year 2024, venue, doi normalized, pdf_url absolute
def test_jsonld_when_no_highwire(): ...
def test_dublin_core_and_coins(): ...
def test_precedence_highwire_over_jsonld(): ...
def test_no_identifier_gives_page_metadata_only():  # merge_with_api(meta, None) → item.ids empty but url set; caller sets ref_status
def test_api_wins_and_diff_recorded():         # page title differs → item.title == api title; diff["title"] == {"page":…, "api":…}
def test_hidden_meta_text_ignored():           # sanitize: zero-width chars stripped from values
```

- [ ] **Step 2–4:** Run, implement with `lxml.html`, run. Values pass through `rk.text.markup` zero-width stripping.
- [ ] **Step 5: Commit** `feat(browser): page metadata parser (Highwire, JSON-LD, Dublin Core, COinS)`

### Task 3: Block detection

**Files:** Create `src/rk/browser/detect.py`. Test `tests/test_browser_detect.py`.

**Interfaces:** `Block(kind: Literal["captcha","login"], reason: str)`; `detect_block(url: str, html: str, *, proxy_host: str | None = None) -> Block | None`.
- CAPTCHA:
  - URL path contains `/sorry/`, or host `ipv4.google.com` with `/sorry`;
  - HTML has `#gs_captcha_ccl`, `#captcha-form`, `g-recaptcha`, `h-captcha`, `cf-turnstile`, `cf-challenge`, or the visible text "unusual traffic", "not a robot", "verify you are human" (case-insensitive).
- Login:
  - host or path matches `login|signin|sso|idp|shibboleth|openathens|wayf|cas/login|adfs|Shibboleth.sso`;
  - or the proxy host serves a page with a password `<input type=password>`.
- A publisher article page that merely contains a "Sign in" link is **not** a login block. It needs a URL match or a password field on a proxy or SSO host.

- [ ] Tests:
  - `test_scholar_sorry_page`, `test_recaptcha_markup`, `test_unusual_traffic_text`;
  - `test_shibboleth_redirect`, `test_proxy_password_form`;
  - `test_article_with_signin_link_not_blocked`, `test_normal_results_page_not_blocked`.
- [ ] Run (fail) → implement → run (pass) → commit `feat(browser): CAPTCHA and SSO detection`

### Task 4: Browser session

**Files:**
- Create: `src/rk/browser/session.py`, `tests/browser_sites.py` (fixture server)
- Modify: `pyproject.toml` (`playwright>=1.64,<2`, marker `browser`)
- Test: `tests/test_browser_session.py`

**Interfaces:**
- `BrowserSession(profile_dir: Path, *, headless: bool, workbench_origin: str | None)`:
  - `start()`, `close()`, `running -> bool`;
  - `open_workbench(url)`;
  - `new_tab(owner: str, url: str | None) -> Tab`, `tabs(owner: str | None) -> list[Tab]` (`owner=None`: all agent tabs, i.e. human view), `get_tab(tab_id, owner) -> Tab` (an unknown or foreign tab raises `ToolError("tab_not_found", 404)`);
  - `goto(tab_id, owner, url, *, wait="domcontentloaded", timeout_s=45) -> PageState`;
  - `content(tab_id, owner) -> PageState`;
  - `snapshot(tab_id, owner, *, screenshot_to: Path | None) -> dict` returns `{url, title, ax: str (aria snapshot via locator("body").aria_snapshot()), screenshot_path}`;
  - `act(tab_id, owner, action: Action) -> PageState`;
  - `fetch_bytes(tab_id, owner, url, *, referer) -> tuple[int, str, bytes, str]` (status, content_type, body, final_url) via `context.request`, sharing cookies;
  - `click_download(tab_id, owner, selector, *, timeout_s=60) -> bytes | None`;
  - `active_human_tab() -> Tab | None` (the most recently focused non-Workbench tab, including tabs the user opened);
  - `call(fn, timeout_s)`: runs a coroutine on the session thread.
- `PageState(url, title, html, status)`.
- `Action = {"kind": "click"|"fill"|"press"|"scroll"|"back", "selector"?: str, "text"?: str, "key"?: str}`. `fill` is refused when the selector resolves to `input[type=password]`, which raises `ToolError("forbidden_action")`.
- Tab ids: `tab_<n>`.
- Isolation: every agent tab installs `page.route` aborting requests whose origin equals `workbench_origin` or `http://localhost:<same port>`.

- [ ] **Step 1: Tests** (`@pytest.mark.browser`, headless, against `browser_sites` server):

```python
def test_tabs_are_owned(session, sites):            # owner A cannot get B's tab; human view lists both; workbench absent
def test_agent_tab_cannot_reach_workbench(session, sites):  # goto(workbench_url) → error/blocked; in-page fetch rejected
def test_snapshot_has_aria_and_screenshot(session, sites, tmp_path)
def test_act_click_and_fill(session, sites)
def test_password_fill_forbidden(session, sites)
def test_fetch_bytes_shares_cookies(session, sites)  # site sets cookie on page visit; PDF route requires it
def test_profile_persists_cookie(tmp_path, sites)    # close + reopen same profile → cookie still present
```

- [ ] **Step 2–4:** Run (fail), implement (dedicated `threading.Thread` running `asyncio` loop with `async_playwright`; `call` = `run_coroutine_threadsafe(...).result(timeout)`), run (pass).
- [ ] **Step 5: Commit** `feat(browser): Playwright session with owned tabs and Workbench isolation`

### Task 5: Site adapters

**Files:** Create `src/rk/browser/adapters/*.py`. Test `tests/test_browser_adapters.py` with fixtures `tests/fixtures/browser/{scholar,pubmed,europepmc,primo,summon}_results.html` and `publisher_*.html`.

**Interfaces:**
- `SearchHit(title, url, authors: str | None, venue_year: str | None, snippet: str | None, doi, pmid, pdf_url, source_site)`.
- Adapter protocol:
  - `name: str`;
  - `search_url(query: str, settings: Settings, page: int = 0) -> str`;
  - `ready_selector: str` (what to wait for after navigation);
  - `parse(html: str, base_url: str) -> list[SearchHit]`;
  - `valid(hits) -> bool`: every hit has a title and an http(s) URL, and the parse matched at least one result container when the page says it has results.
- `ADAPTERS = {"scholar","pubmed","europepmc","primo","summon"}`.
- Selectors are pinned from a live probe on implementation day and recorded with the date in each module docstring:
  - PubMed: `article.full-docsum`, `a.docsum-title`, `.docsum-authors.full-authors`, `.docsum-journal-citation.full-journal-citation`, `.docsum-pmid` (probed 2026-11-06).
  - Scholar: `div.gs_r.gs_or.gs_scl`, `h3.gs_rt a`, `div.gs_a`, `div.gs_rs`, `div.gs_or_ggsm a`.
  - Europe PMC, Primo VE (`/discovery/search?query=any,contains,<q>&vid=<vid>&tab=<tab>&search_scope=<scope>`) and Summon: probed live.
- `publisher.find_pdf_url(landing_url, html) -> str | None`:
  1. `citation_pdf_url`;
  2. then known patterns (ScienceDirect `pdfft`, Springer `/content/pdf/<doi>.pdf`, Wiley `/doi/pdfdirect/`, ACS/T&F `/doi/pdf/`, RSC `/articlepdf/`, Nature `.pdf` link, IEEE `stamp.jsp`, ACM `/doi/pdf/`);
  3. then the first `a[href$=".pdf"]` whose text contains "PDF".
- `primo`/`summon` `search_url` raise `ToolError("not_configured", …)` when `primo_base`/`primo_vid` (or `summon_base`) is unset.

- [ ] Tests:
  - per site, `test_<site>_parses_fixture` (count, first hit fields, DOI/PMID extraction);
  - `test_<site>_invalid_markup_falls_back` (`valid` is false for a page whose containers were renamed);
  - `test_primo_requires_config`;
  - `test_find_pdf_url_patterns` (parametrized, one per publisher pattern).
- [ ] Run (fail) → implement (lxml) → run (pass) → commit `feat(browser): site adapters and publisher PDF link resolution`

### Task 6: Browser service, tools and the full-text channel

**Files:**
- Create: `src/rk/browser/service.py`, `src/rk/browser/tools.py`, `src/rk/browser/translation.py`
- Modify:
  - `src/rk/fulltext.py`: add `attach_pdf_bytes`, and make the browser channel the last step via an optional `browser` parameter;
  - `src/rk/tools/builtin.py`: register the browser tools, and pass the service to `rk_source_fetch_fulltext`;
  - `src/rk/roles/policy.py`.
- Test: `tests/test_browser_service.py` (browser marker, fixture sites), `tests/test_roles.py`

**Interfaces:**
- `BrowserService(kernel: Kernel, session: BrowserSession, pacer: Pacer)`, where `owner = ctx.task_id or ctx.actor`. Methods:
  - `search(ctx, project, site, query, max_results=10) -> dict`:
    1. Check `paused`.
    2. `pacer.acquire([f"site:{site}"])`.
    3. Navigate, wait for `ready_selector` (falling back to `domcontentloaded`), then `detect_block`.
    4. Parse with the adapter.
    5. If the hits are invalid, return `{fallback: "generic", tab, snapshot}`.
    6. Record a `search` browse event with `result_count` and return `{site, query, tab, hits: [...]}`.
  - `open(ctx, project, url, tab=None) -> {tab, url, title, metadata: PageMetadata | None}` logs `open`.
  - `snapshot(ctx, project, tab)` saves the screenshot under `projects/<slug>/browser/<brw>.png`.
  - `act(ctx, project, tab, action)` counts steps per owner and raises `ToolError("step_limit")` above `browser_max_steps`.
  - `fetch_fulltext(ctx, project, item_id) -> FulltextResult`:
    1. Build the landing URL from DOI (`https://doi.org/<doi>`), else PMID/arXiv/url. Wrap it with `library_proxy` if set.
    2. Acquire `site:publisher`, navigate, detect blocks.
    3. Parse metadata, then `find_pdf_url`.
    4. Acquire `[pdf:<domain>, pdf:*]`.
    5. `fetch_bytes` with the landing page as referer. If the result is not `%PDF-`, try `click_download` on the PDF link.
    6. Attach with `attach_pdf_bytes` and the origin/`access_note` from the Global Constraints. Log `download`.
    - Fails with `pdf_not_obtained` (404) when no PDF bytes are obtained, with details `{final_url, landing_url}`.
  - `save_page(ctx, project, tab=None) -> {item_id, created, attachment: str | None}`:
    - With `translation_server_url` set, the metadata comes from translation-server; otherwise from `parse_page_metadata`.
    - Metadata goes through `merge_with_api(resolve(...))`, then `lib.add_item(added_by="human")` and `ref_status` (`page_metadata_only` when unresolved).
    - Then a PDF via `find_pdf_url`, else an `html_snapshot` attachment (`html_to_layer`). The item is collected into the current or `Search hits` collection.
  - `request_human(ctx, project, reason, url=None) -> {checkpoint_id}` (kind `login_wait`).
  - `status() -> {running, paused_sites, quotas, tabs}`.
- Blocks: a CAPTCHA pauses the site and creates a `captcha_wait` checkpoint (options `["done"]`, prompt names the site and URL); login creates `login_wait`. Both log the matching browse action and raise `HumanRequired`. When a site is paused and its checkpoint has been answered, the next call unpauses it.
- `attach_pdf_bytes(ps, lib, item_id, raw: bytes, *, origin, actor, actor_kind, access_note, url) -> FulltextResult`. `attach_pdf` delegates to it.
- `translation.py`: `TranslationServer(url, http)`, `.web(url) -> list[dict] | None` (POST `/web`, `text/plain`), `.to_csl(items) -> list[dict]` (POST `/export?format=csljson`). Unreachable → `None`, and the caller falls back.
- Tools:

  | Tool | Input | writes | Roles (`cli` always) |
  |---|---|---|---|
  | `rk_browser_search` | project, site, query, max_results ≤ 20 | yes (replay avoids re-querying) | lead, scout |
  | `rk_browser_open` | project, url, tab? | no | lead, scout, reader |
  | `rk_browser_snapshot` | project, tab | no | lead, scout, reader |
  | `rk_browser_act` | project, tab, action | no | lead, scout, reader |
  | `rk_browser_tabs` | project | no | lead, scout, reader |
  | `rk_browser_fetch_fulltext` | project, source_id | yes | lead, reader |
  | `rk_browser_request_human` | project, reason, url? | yes | lead, scout, reader |
  | `rk_browser_status` | — | no | lead, scout, reader |
  | `rk_browser_save_page` | project, tab? | yes | human only |

  With no browser running, every browser tool raises `BrowserUnavailable("rk serve was started without the browser")`.
- `rk_source_fetch_fulltext`: after the API channels fail, it calls `service.fetch_fulltext` when the browser is running and `fulltext_browser_fallback` is set. A `HumanRequired` from the browser propagates.

- [ ] **Step 1: Tests** (fixture sites: `scholar` results, `scholar` CAPTCHA mode, `publisher` landing with `citation_pdf_url`, `proxy` that 302s to `sso` when the session cookie is missing, `paywall` whose PDF link returns HTML):

```python
def test_search_pubmed_fixture_logs_browse(service, sites, project)
def test_invalid_markup_returns_generic_fallback(service, sites, project)
def test_captcha_pauses_site_until_answered(service, sites, project)
def test_sso_redirect_opens_login_wait(service, sites, project)
def test_fetch_via_proxy_attaches_library_browser_pdf(service, sites, project)  # origin, access_note, text layer, browse download row
def test_pdf_link_returning_html_not_attached(service, sites, project)
def test_save_page_page_metadata_only(service, sites, project)                  # no DOI → ref_status page_metadata_only, html_snapshot
def test_save_page_doi_api_wins(service, sites, project, replay_http)           # diff recorded
def test_step_limit(service, sites, project)
def test_source_fetch_fulltext_falls_back_to_browser(live_server_with_browser, sites)
def test_browser_tools_unavailable_without_browser(live_server)
```

  Plus in `test_roles.py`: policy entries match the table above; reader cannot `rk_browser_search`; scout cannot `rk_browser_fetch_fulltext`; `rk_browser_save_page` is human only.
- [ ] **Step 2–4:** Run (fail), implement, run (pass, full suite).
- [ ] **Step 5: Commit** `feat(browser): browser tools, human hand-off, library/publisher PDF ingestion`

### Task 7: Serve integration and Workbench auth

**Files:**
- Create: `src/rk/ui/__init__.py`, `src/rk/ui/auth.py`
- Modify:
  - `src/rk/server/app.py`: `create_app(..., browser: BrowserService | None = None, ui: UiAuth | None = None)`, mounting the UI router and static files when `ui` is given;
  - `src/rk/cli/main.py`: `serve --no-browser` starts the session after the socket is bound, opens the Workbench tab with a login code, and closes the browser in `finally`; `rk open` asks the server for a login link and prints it;
  - `pyproject.toml`: `websockets`.
- Test: `tests/test_ui_api.py`

**Interfaces:**
- `UiAuth(origin: str)`: `new_login_code() -> str`, `redeem(code) -> str | None` (session id), `check_cookie(value) -> bool`, `check_origin(header) -> bool`.
- `GET /ui/login?code=` sets the cookie and redirects to `/ui/`.
- `POST /api/ui/link` (serve token, `cli` role only) → `{url}`, a one-time login URL. This is what `rk open` uses.
- `POST /api/browser/open-human` (serve token, `cli` role only; body `{url}`) opens a tab that belongs to the human, not to an agent, and returns `{tab}`; with no browser running it returns 503. `rk setup` uses it for the library login step.
- Dependency `ui_ctx(request) -> CallContext(role="cli", actor="ui:user")`: 401 without a valid cookie; 403 on POST/PUT/DELETE/WS with a missing or foreign `Origin`.

- [ ] Tests:
  - `test_login_code_single_use_and_expires`;
  - `test_ui_api_requires_cookie`;
  - `test_serve_token_rejected_on_ui` and `test_ui_cookie_rejected_on_api`;
  - `test_cross_origin_post_rejected`;
  - `test_rk_open_link` (`live_server`).
- [ ] Run (fail) → implement → run (pass) → commit `feat(ui): Workbench session auth; serve owns the browser and opens the Workbench`

### Task 8: Workbench API

**Files:** Create `src/rk/ui/api.py`, `src/rk/ui/drafts.py`. Test `tests/test_ui_api.py`.

**Interfaces:** `ui_router(kernel, registry, chats, browser, auth) -> APIRouter`. All routes sit under `/ui/api` and are thin wrappers that call `registry.call(name, args, ui_ctx)` wherever a tool exists, so permissions and idempotency stay in one place. Write calls get `idempotency_key = "ui:" + uuid4`.
- `GET /projects`, `POST /projects` (slug, title, field?, paper_type?), `POST /projects/current` (slug).
- `GET /library?project&collection&q`, `GET /library/item/{id}` returns the item plus badges:
  - `ref_status`, `retraction`;
  - `fulltext: bool` (snapshot exists);
  - `quotes_verified: int` (notes of kind quote whose V-QUOTE passed);
  - attachments.
- `GET /files/{project}/{att_id}` streams the attachment (with the right content type, so PDFs open in Chromium's viewer).
- `POST /library/tag`, `POST /library/collect`, `POST /library/import` (multipart file), `GET /library/export?format`.
- `POST /library/save-page` (project, tab?) calls `rk_browser_save_page`.
- `GET /checkpoints?project&status`, `POST /checkpoints/{id}/answer` (`answered_by="human"`).
- `GET /progress?project`: tasks table, open checkpoints, last 200 events (type, actor, ts, entity).
- `GET /drafts?project` and `GET /drafts/{section}?project&version`:
  - `render_draft(ps, lib, markdown) -> {html, markers: [{kind, id, label, target}]}`.
  - Markers `[[kind:id]]` become `<span class="mk" data-kind data-id>` before rendering with markdown-it (`html=False`).
  - `clm` resolves to the claim text plus its evidence notes and their source locations; `inf`/`ck` are labels only; unknown ids are flagged `missing`.
- `GET /settings` (no key values, only `set: bool`), `POST /settings` (email, keys, library_* fields, translation_server_url), `POST /setup/check/{step}` (shares the step functions from Task 10).
- `GET /browser/status`.
- `WS /ui/ws?project=`:
  - Server pushes `{"type":"event", ...}` for new project events (polls `max(seq)` every 1 s), `{"type":"checkpoint", ...}` and `{"type":"browser", ...}` when the status changes.
  - Client `{"type":"chat.open","driver"}` / `{"type":"chat.send","chat_id","text"}` streams ChatEvents back as `{"type":"chat", ...}`.

- [ ] Tests (TestClient with a cookie):
  - `test_projects_crud`;
  - `test_library_item_badges`;
  - `test_file_route_serves_pdf_and_rejects_foreign_project`;
  - `test_checkpoint_answer_is_human`;
  - `test_settings_never_returns_secrets`;
  - `test_render_draft_markers` (claim, missing id, `[[inf]]`, script tag escaped);
  - `test_ws_pushes_new_events`;
  - `test_ws_chat_with_fake_driver` (ChatManager with a fake chat provider from Plan 2A tests).
- [ ] Run (fail) → implement → run (pass) → commit `feat(ui): Workbench API and WebSocket`

### Task 9: Workbench frontend

**Files:** Create `src/rk/ui/static/{index.html,app.js,style.css}`. Test `tests/test_ui_e2e.py` (browser marker).

- One page, hash routes `#/projects`, `#/chat`, `#/library`, `#/progress`, `#/checkpoints`, `#/drafts`, `#/settings`. ES modules, no build step, no CDN (everything is served locally).
- **Chat:** messages stream from WS; `tool_call` and `tool_result` render as collapsed `<details>`.
- **Library:** collections sidebar, FTS search box, item list with badges, an item panel that has attachments opening in a new tab, and a "Save current page" button (shortcut `Alt+S`).
- **Checkpoints:** cards with prompt, options buttons and free-text answer. A `login_wait`/`captcha_wait` card shows the site and URL with the text "Handle it in the browser tab, then press Done".
- **Settings:** sections mirror wizard steps 1–6; each section has a "Check" button that shows the result.
- **Progress:** tasks, open checkpoints, live event feed.
- **Drafts:** section list, rendered draft, marker hover cards.
- Text is in English. The UI says what each control does in plain words, and errors show the server's message.

- [ ] Tests (headless Chromium against `live_server` + UI):
  - `test_login_and_navigate_all_pages` (each route renders its heading, no console errors);
  - `test_answer_checkpoint_from_ui`;
  - `test_library_search_and_badges`;
  - `test_chat_roundtrip_fake_driver`.
- [ ] Verify the real surface visually: start `RK_BROWSER_HEADLESS=1 rk serve` in a scratch `RK_HOME`, open the `rk open` link in the omp built-in browser, and screenshot each page.
- [ ] Commit `feat(ui): Workbench frontend`

### Task 10: Setup wizard steps 4–6

**Files:** Modify `src/rk/cli/setup.py`, `src/rk/cli/main.py`. Test `tests/test_setup_wizard.py`.

**Interfaces:**
- `check_providers(settings) -> dict[str, dict]` uses `available_all`, with a hint per unavailable driver (`claude` → "run `claude` in a terminal and log in", `codex` → "`codex login`").
- `library_login_step(settings, browser_client, ask, say) -> dict`:
  1. Ask for `library_name`, `library_portal_url`, `library_proxy` (validated: contains `{url}` and is https) and the discovery system (primo/summon/none plus base/vid).
  2. Open the portal in the built-in browser through `POST /api/browser/open-human`, say "log in in the browser window, then press Enter", and wait.
  3. Test with `rk_browser_fetch_fulltext` on a scratch item for a paywalled DOI you enter.
  4. Report ok/fail with the final URL. Never ask for the password.
- `capabilities(settings, http, browser_running) -> list[{area, name, status, quota}]`.
- `run_setup` runs steps 1–6. The web settings page calls the same functions through `/ui/api/setup/check/{step}`.

- [ ] Tests:
  - `test_providers_step_hints`;
  - `test_library_step_validates_proxy_template`;
  - `test_library_step_fake_sso_success` (browser marker, fake proxy/SSO site where "login" = visiting the SSO page sets the cookie);
  - `test_capabilities_lists_browser_channels`.
- [ ] Run (fail) → implement → run (pass) → commit `feat(setup): provider, library login and capability steps`

### Task 11: CLI, demo and docs

**Files:**
- Modify: `src/rk/cli/main.py`, `README.md`, spec §8.5 (selectors probe date, deviations), `docs/superpowers/plans/` (this plan, ticked)
- Create: `docs/demo/plan3-browser.md` (demo transcript)

- CLI: `rk browser search SITE QUERY [--max N]`, `rk browser open URL`, `rk browser fetch SOURCE_ID`, `rk browser status`, `rk browser save [--tab]`. All are serve clients.
- Demo:
  1. Run `RK_BROWSER_HEADLESS=1 rk serve`.
  2. Live: `rk browser search pubmed "perovskite solar cell stability"`, then add a hit and `rk fetch` it through the API channels.
  3. Live: `rk browser fetch` on an OA publisher DOI (citation_pdf_url path), then verify a quote from it with `rk note add --kind quote`.
  4. Fixture sites: the CAPTCHA and SSO hand-offs as checkpoints shown in the Workbench.
  5. Lead chat in the Workbench (`omp_gateway:openai-codex/gpt-6-luna` test driver) asked to search PubMed in the browser and save the first result.
  - Scholar live: at most 1 query, only if not blocked. A CAPTCHA there is shown as the hand-off working.
- [ ] Run the full suite, the browser-marked tests and the demo. Record outputs in `docs/demo/plan3-browser.md` with no personal paths.
- [ ] Commit `docs: Plan 3 demo, README for the browser and Workbench` and push.
