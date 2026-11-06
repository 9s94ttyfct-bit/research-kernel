# Path C — Research Kernel: technical due diligence (fetched 2026-11-05)

Conventions: every fact has its source URL. **[live]** = I observed it today with curl or a throwaway script in `/tmp` (no repo changes, nothing installed globally). **[unverified]** = I could not confirm it from a primary source today. PyPI facts (version, upload date, license) come from `https://pypi.org/pypi/<pkg>/json`, queried today.

---

## A. Scholarly data APIs

### A1. OpenAlex
- **Auth (2026):** A key is optional but strongly encouraged. Without a key, all callers on one IP share a **$0.10/day** budget. A free key gives **$1/day**, and only a keyed caller can page with a cursor past the first page. Send the key as `api_key=` or `Authorization: Bearer`. https://help.openalex.org/api/authentication.md
  - Free key: sign up at openalex.org, or `POST https://api.openalex.org/api-keys {"email": …}`. The key works only after the user clicks the emailed activation link. Placeholder or agent-named addresses are refused, so it needs the user's real email. Same page.
  - New accounts made after 3 Nov 2026 spend their first $1 at no more than $0.10 per 5 min. Same page.
- **Cost per endpoint** (https://help.openalex.org/access/example-costs.md):

  | Operation | Cost per 1,000 calls |
  |---|---|
  | Get single entity (ID or DOI) | **Free** |
  | List + filter | $0.10 (1 credit) |
  | Search | $1 (10 credits) |
  | Search + `rerank=true` | $2 |
  | Semantic search | $1 |
  | Content (cached PDF) download | $10 |

  - $1/day buys about 10k filter calls, about 1k searches, or 100 PDFs. Single lookups are unlimited.
- **Rate limits:** 10 req/s with no key, 30 req/s with a free key. Semantic search is limited to 1 req/s. Searches with more than 5 boolean operators are limited to 1 req/s (no key) or 5 req/s (free key). `per_page` max is 100; OR-filter max is 100 values; basic paging stops at 10k results. Same auth page.
- **[live] Single-work fetch by DOI, no key:** `GET https://api.openalex.org/works/doi:10.1016/S0140-6736(97)11096-0` → 200.
  - Headers: `x-ratelimit-limit: 1000`, `x-ratelimit-remaining: 1000`, `x-ratelimit-credits-used: 0`, `x-ratelimit-cost-usd: 0`, `x-ratelimit-limit-usd: 0.1`, `x-ratelimit-remaining-usd: 0.1`, `x-ratelimit-reset: 2643` (seconds to midnight UTC), `x-ratelimit-prepaid-remaining-usd: 0`.
  - The record had `is_retracted: true`, the title "RETRACTED: …", and `referenced_works` with 25 IDs.
  - A follow-up `filter=cites:W2117847125` call cost 1 credit ($0.0001). A `search=` call cost 10 credits ($0.001), which matches the price table.
- **Search:** `GET /works?search=<q>` (keyword search over title, abstract and fulltext). Semantic search is a separate surface: https://help.openalex.org/api/semantic-search.md.
- **Citation expansion:**
  - Backward (what a work cites): read the work's `referenced_works` (list of OpenAlex IDs), then batch-resolve them with `filter=openalex:W1|W2|…` (up to 100 IDs per call).
  - Forward (what cites a work): `GET /works?filter=cites:Wxxxx` [live].
  - `cited_by_api_url` came back **null** in today's record, so do not rely on it; build the `cites:` filter yourself.
  - Field definitions: https://help.openalex.org/data/works/attributes.md
- **Retraction:** `is_retracted` (Boolean) is "per the Retraction Watch database". https://help.openalex.org/data/works/attributes.md
- **OA / full-text fields:**
  - `open_access{is_oa, oa_status, oa_url, any_repository_has_fulltext}`, `best_oa_location`, `primary_location`, `locations[]` (each with version and license).
  - `has_content{pdf, grobid_xml}` and `content_urls{pdf, grobid_xml}`: OpenAlex-cached PDF and **GROBID TEI** for more than 60M OA works. Downloading needs a key and costs $10 per 1,000. Same page, plus https://help.openalex.org/access/fulltext.md.
- **Recommendation:** Make OpenAlex the primary discovery, citation-graph and retraction source. Get the user a free key, because the shared-IP budget is $0.10/day. Use singleton DOI lookups wherever possible (they are free). Use `content_urls.grobid_xml` as a cheap structured full-text source, since it avoids running GROBID ourselves.
- **Risks:**
  - Pricing changed in 2025–26 and may change again. The verifier must read the `x-ratelimit-*` headers and handle 429.
  - `is_retracted` depends on RW→OpenAlex sync lag.
  - The keyless budget is shared per IP.

### A2. Crossref REST
- **Pools** (https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/):

  | Pool | How you join | Rate | Concurrency |
  |---|---|---|---|
  | Public | default | 5 req/s | 1 |
  | Polite | `mailto=` parameter or `agent` header | 10 req/s | 3 |
  | Metadata Plus | paid key | 150 req/s | none |

- **Change of 21 Jul 2026:** limits now depend on request type. Single-record requests stay at 5/s (public) and 10/s (polite). **List/query requests drop to 1/s (public) and 3/s (polite).** A new header, `x-rate-limit-type`, reports the type. Polite traffic is now **rate-limited per email address**, and repeatedly using an invalid email can get you blocked. https://community.crossref.org/t/refining-rest-api-limits-for-improved-stability-and-reliability/16137
- **[live]** `GET https://api.crossref.org/works/10.1016/S0140-6736(97)11096-0?mailto=…` → 200 with `x-api-pool: polite-single`, `x-rate-limit-limit: 10`, `x-rate-limit-interval: 1s`, `x-concurrency-limit: 3`.
- **Where the retraction shows [live]:** on the Wakefield record it is in **`updated-by`**:
  ```json
  [{"DOI":"10.1016/s0140-6736(10)60175-4","type":"retraction","label":"Retraction","source":"retraction-watch","updated":{"date-parts":[[2010,2,6]]},"record-id":"4036"},
   {"DOI":"10.1016/s0140-6736(04)15715-2","type":"correction","source":"retraction-watch","record-id":"17269"}]
  ```
  - `update-to` is null and `relation` is `{}` on the original article.
  - The retraction notice (10.1016/s0140-6736(10)60175-4) carries the reverse link in **`update-to`** (type `retraction`, source `retraction-watch`) [live].
  - `record-id` is the Retraction Watch DB ID.
- **RW integration status:**
  - RW data has been in the production schema since Jan 2025.
  - **The Labs API RW annotations were deprecated on 29 May 2026; no new updates go there.** Check retractions through production `updated-by`.
  - The full RW CSV is on GitLab: https://gitlab.com/crossref/retraction-watch-data.
  - Sources: https://community.crossref.org/t/deprecating-retraction-watch-annotations-in-the-labs-api/15884 and https://www.crossref.org/labs/retraction-watch/
- **Recommendation:** Use Crossref as the authoritative retraction/correction signal. Treat any `updated-by[].type ∈ {retraction, withdrawal, expression_of_concern, correction}`, from either `publisher` or `retraction-watch` source, as a flag. Cross-check against OpenAlex `is_retracted`. Optionally mirror the RW CSV locally for offline checks. Always send a **real** user email in `mailto`.
- **Risks:**
  - The 3 req/s list limit makes Crossref unsuitable for bulk search; use it for DOI lookups.
  - A community report says RW and publisher-deposited retractions sometimes disagree (https://community.crossref.org/t/rwdb-record-62686-10-33263-briac94-979986-retraction-recorded-in-retraction-watch-but-absent-from-crossref-metadata/16586). Union the sources rather than trusting one.
  - My live calls used a placeholder `example.org` mailto; production must not.

### A3. Semantic Scholar Graph API
- **Limits:** unauthenticated callers share **1000 req/s across everyone** and may be throttled further. A key's introductory limit is **1 RPS on all endpoints**. Some endpoints require a key. Apply via the form at https://www.semanticscholar.org/product/api#api-key-form (the key arrives by email). Source: same page.
- **Endpoints** (from today's swagger, https://api.semanticscholar.org/graph/v1/swagger.json):
  - `GET /graph/v1/paper/DOI:{doi}?fields=…`
  - `GET /paper/{id}/references`
  - `GET /paper/{id}/citations`
  - `POST /paper/batch`
  - `GET /paper/search`, `GET /paper/search/bulk`, `GET /paper/search/match`
  - `GET /snippet/search?query=…&limit=` (about 500-word snippets from title, abstract and body; max limit 1000)
- **[live]**
  - The DOI lookup returned 200. The record has **no retraction field**, and the title is *not* prefixed "RETRACTED" (`publicationTypes: ["JournalArticle","Review"]`).
  - Unauthenticated `/snippet/search` returned **429** on the first try.
- **Recommendation:** Request a key now. Use S2 for **snippet search** (full-text passage retrieval, which OpenAlex lacks) and as a second citation-graph source. Never use it for retraction status.
- **Risks:** 1 RPS keyed, and the shared pool is unreliable (429 on the first call). There is no retraction signal.

### A4. arXiv
- **API:** `http://export.arxiv.org/api/query` (Atom XML). Legacy APIs (API, OAI-PMH, RSS): **one request every 3 s, one connection at a time, counted across all your machines**. Metadata is CC0. You may retrieve and store e-print content "for your own personal use, or for research purposes", but must not redistribute or serve it. https://info.arxiv.org/help/api/tou.html
- **[live]**
  - `id_list=2404.10774` returned Atom with abs and pdf links.
  - `https://arxiv.org/html/2404.10774` → 200 HTML (576 KB).
  - `https://arxiv.org/pdf/2404.10774` → 200 PDF.
  - `https://ar5iv.labs.arxiv.org/html/2404.10774` → 200.
- **robots.txt [live]:** `Crawl-delay: 15`. `/pdf`, `/html` and `/abs` are allowed; `/api`, `/search` and `/e-print` are disallowed on arxiv.org. The robots file says "Indiscriminate automated downloads … not permitted". https://arxiv.org/robots.txt
- **Recommendation:**
  - Query metadata through export.arxiv.org at ≤1 request per 3 s.
  - For full text, prefer `arxiv.org/html/<id>` (structured HTML; not every paper has it), then the PDF.
  - Fetch at most one paper per 15 s and cache forever, keyed by version (`vN`).
- **Risks:** HTML is missing for older or non-LaTeX papers. Rate limits make bulk ingestion slow; for bulk, use OpenAlex `content_urls` or the S3 bulk data.

### A5. Unpaywall and PubMed/PMC
- **Unpaywall:**
  - It now runs on the OpenAlex database ("a legacy-compatible format over it", moved late 2025).
  - Call shape: `api.unpaywall.org/v2/{DOI}?email=…`. It covers Crossref DOIs only.
  - `z_authors` is deprecated.
  - Source: https://help.openalex.org/access/unpaywall.md
  - [live] `GET https://api.unpaywall.org/v2/10.1038/nature12373?email=…` → 200 with `best_oa_location.url_for_pdf`; `evidence`/`updated` show "deprecated".
  - The daily rate limit could not be confirmed, because the product page renders client-side (historically 100k/day) **[unverified]**.
- **E-utilities:**
  - [live] `esummary` returned the header `x-ratelimit-limit: 3`, i.e. 3 req/s without a key.
  - 10 req/s with an NCBI API key, per https://www.ncbi.nlm.nih.gov/books/NBK25497/ (key limit not re-fetched today **[unverified-today]**).
  - Since Feb 2026, PMC ESearch can reach only the first 10,000 records, and EPost takes at most 10k IDs. https://ncbiinsights.ncbi.nlm.nih.gov/2026/01/06/updated-pmc-e-utilities/
- **PMC full text — changed in 2026:**
  - The **PMC OA Web Service is gone** (as of 25 Aug 2026), and **legacy FTP/Cloud article-dataset files were removed the week of 24 Aug 2026**. https://pmc.ncbi.nlm.nih.gov/tools/oa-service/ and https://pmc.ncbi.nlm.nih.gov/tools/textmining/
  - Allowed automated routes are now **only** the PMC Cloud Service (AWS), OAI-PMH, E-utilities and the **BioC API**. All other systematic retrieval is prohibited. Same textmining page.
- **BioC API:**
  - Endpoint: `https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_{xml|json}/{PMID|PMCID}/{unicode|ascii}`, covering the OA Subset and author manuscripts. https://ncbi.nlm.nih.gov/research/bionlp/APIs/BioC-PMC/
  - [live] `BioC_json/PMC1790863/unicode` → 200 (71 KB).
- **Recommendation:**
  - For biomedical full text, use BioC JSON. It gives passages with offsets and section types, which suit exact-quote locators.
  - Use `efetch db=pmc` (JATS) as a fallback.
  - Use Unpaywall/OpenAlex only to find OA PDF URLs.
- **Risks:** PMC distribution changed twice in 2026; build adapters behind an interface. BioC covers OA/author-manuscript articles only.

### A6. DOI content negotiation
- **[live] Blocked from this machine:** `doi.org` returned **HTTP 403 with a Cloudflare "Security Challenge" page** to curl. The same happened with a descriptive User-Agent, and on `/api/handles/…`. The crosscite docs (now `citation.doi.org/docs.html`) also return 403.
- **[live] Working equivalents:**
  - Crossref transform: `GET https://api.crossref.org/works/{doi}/transform/application/vnd.citationstyles.csl+json` → CSL-JSON (200).
  - DataCite (e.g. arXiv DOIs): `GET https://api.datacite.org/dois/{doi}` with `Accept: application/vnd.citationstyles.csl+json` → CSL-JSON (200).
  - `data.crossref.org` → 503.
- **Recommendation:** Do not depend on doi.org content negotiation. Route by registration agency (`https://api.crossref.org/works/{doi}/agency`, cited in https://help.openalex.org/access/unpaywall.md): Crossref transform for Crossref DOIs, DataCite REST for DataCite DOIs. Use doi.org only as an opportunistic fallback.
- **Risks:** The Cloudflare challenge may be IP- or time-dependent **[unverified whether global]**. If it persists, doi.org can't be used for headless resolution either.

**Section A Recommendation:**
- OpenAlex (free key) is the backbone.
- Crossref `updated-by` plus OpenAlex `is_retracted` form the retraction gate.
- S2 (key) supplies snippet search.
- arXiv HTML, PMC BioC and OpenAlex GROBID-TEI supply full text.
- Agency-routed CSL-JSON is the metadata fallback.

The reason: each is the cheapest primary source for its job, and the retraction signal is cross-validated across two of them.

**Section A Risks:**
- Three providers changed limits or pricing in 2025–26 (OpenAlex budgets, Crossref list limits and per-email throttling, PMC services).
- The keyless S2 pool is effectively unusable.
- doi.org is behind a bot challenge.
- Every adapter needs header-driven throttling, a persistent HTTP cache and graceful degradation.

---

## B. Python libraries
All Python 3.11-compatible unless noted (from `requires_python` on PyPI).

### B7. MCP SDK
- **`mcp` 2.3.0** (2026-10-02, MIT, `>=3.10`). Release notes: https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0 and https://github.com/modelcontextprotocol/python-sdk/releases
- **v2.0.0 (2026-07-28)** implements the **2026-07-28 protocol revision** (stateless, no handshake) and still serves older clients. v1.x is security-fix-only; pin `mcp>=1.28,<2` if not migrating.
- **Breaking changes in v2** (https://py.sdk.modelcontextprotocol.io/migration/index.md):
  - **`FastMCP` renamed to `MCPServer`**; `mcp.server.fastmcp` no longer exists.
  - The `httpx`/`httpx-sse` dependency is replaced by **`httpx2`** (pydantic's fork, 2.16.0, BSD-3).
  - `streamablehttp_client` and the WebSocket transport are removed.
  - Roots, Sampling and Logging are deprecated (SEP-2577).
  - Types moved to `mcp-types`.
- **API** (https://py.sdk.modelcontextprotocol.io/servers/structured-output/index.md, https://py.sdk.modelcontextprotocol.io/run/index.md):
  - `from mcp.server import MCPServer; mcp = MCPServer("x")`, then `@mcp.tool()`.
  - **The return annotation becomes `outputSchema`.** A Pydantic model return fills `structured_content`. Scalars are wrapped as `{"result": …}`.
  - `mcp.run()` defaults to **stdio**.
- **`fastmcp` 4.1.6** (2026-11-03, Apache-2.0, PrefectHQ). It depends on `mcp-types>=2` and, through its client extra, `mcp>=2.3`. It is a higher-level framework (auth, proxying, composition, apps). https://github.com/PrefectHQ/fastmcp
- **Recommendation: the official `mcp` 2.x `MCPServer`.** The kernel needs only stdio tools with Pydantic-typed structured output, which the official SDK provides. That gives one fewer fast-moving dependency (fastmcp shipped 4.1.5, 4.1.6 and 4.2.0a1–a3 in the last 10 days).
- **Risks:**
  - v2 is three months old.
  - Claude Code and Codex speak MCP, but whether their clients have adopted the 2026-07-28 revision is **[unverified]**; v2 serves legacy clients automatically.

### B8. PDF / HTML text extraction

| Lib | Version (date) | License | Notes |
|---|---|---|---|
| `docling` | 2.148.0 (2026-11-05) | MIT | Layout models via torch. **[live]** 8 pages of an arXiv paper took 39.8 s, plus 19.7 s init, on the M4 Pro (~5 s/page; one run, first-run cache effects possible). Gives `prov[].page_no`, a `bbox` per item and `charspan`, which suits locators. Exports Markdown. |
| `docling-parse` | 7.24.2 | MIT | Docling's text-with-coordinates backend; usable alone. |
| `pymupdf` / `pymupdf4llm` / `pymupdf-layout` | 2.1 (2026-11-02) | **AGPL-3.0** (or commercial) | Layout went from PolyForm-NC to AGPL on 2026-08-10: https://pymupdf.io/blog/open-source-all-the-way-down-pymupdf4llm-goes-fully-agpl. **[live]** 30 pages in 0.49 s; arm64 wheels. |
| `pypdf` | 6.21.4 (2026-10-28) | BSD-3 | Pure Python. **[live]** 30 pages in 0.64 s; plain text per page. |
| `pypdfium2` | 5.14.0 (2026-10-04) | BSD-3 / Apache-2.0 | PDFium. **[live]** 30 pages in 0.33 s (fastest). Has char-box APIs and `\r\n` line ends; macOS arm64 wheel. |
| `marker-pdf` | 2.1.0 (2026-10-29) | **Code Apache-2.0** (no longer GPL); **weights: modified OpenRAIL-M**, free only for research, personal use or <$5M orgs | https://github.com/datalab-to/marker. Pulls in `torch`, `surya-ocr`, and the `anthropic`/`openai`/`google-genai` SDKs. |
| GROBID | 0.9.1 (2026-08-04) | Apache-2.0 | Needs **JDK 21** + Gradle build without Docker (`brew install openjdk@21`): https://github.com/grobidOrg/grobid/blob/master/doc/Install-Grobid.md. OpenAlex already serves GROBID TEI for 60M+ OA works (A1). |
| `trafilatura` | 2.3.1 (2026-10-06) | Apache-2.0 | HTML main-text extraction. https://github.com/adbar/trafilatura |

- **Recommendation:** **two-tier extraction, with the canonical text layer on `pypdfium2`.**
  - `pypdfium2` (permissive, fastest, per-page and per-char boxes) produces the canonical page-anchored text used for quote verification.
  - **`docling`** (MIT) runs only when structure is needed: sections, tables, reading order. Keep its `page_no`/`bbox`/`charspan`.
  - Prefer publisher structure when it exists: arXiv HTML, PMC BioC, OpenAlex GROBID-TEI.
  - Use `trafilatura` for web pages.
  - Avoid PyMuPDF: AGPL would infect a distributable kernel.
  - Avoid marker: its weights license is restrictive.
- **Risks:**
  - docling is about 5 s/page; batch ingestion needs a queue.
  - The two layers' text will differ, so locators must reference the canonical pdfium layer only.
  - Scanned PDFs need OCR (docling has it), which is slower.

### B9. Quote verification and overlap
- **`rapidfuzz` 3.14.6** (2026-08-30, MIT, macOS arm64 wheel). `fuzz.partial_ratio_alignment` returns the score **and** the matched span `dest_start`/`dest_end`. **[live]** A verbatim quote scored 99.3, span 365–510 on page 1. A paraphrase scored 54.5.
- **Normalisation pitfall [live]:**
  - Neither raw nor naively de-hyphenated text contained the quote verbatim. The PDF has "fact-\nchecking" (a *real* hyphen at line end) and "evi-\ndence" (a *soft* break), so neither "keep" nor "drop" hyphen-at-EOL is always right.
  - A **hyphen/whitespace-insensitive key** (`NFKC` → strip `[\s\-\u00AD]` → lowercase) found the exact match.
  - Build the matcher on that key **with an offset map back to the raw text**.
  - `unicodedata.normalize("NFKC")` turns ligatures into letters ("ﬁ"→"fi") [live].
  - `ftfy` 6.3.1 (Apache-2.0) fixes mojibake.
  - **Avoid `unidecode` (GPL-2+).**
- **n-gram overlap:**
  - A word 5-gram Jaccard/containment needs only stdlib. **[live]** Verbatim containment was 0.70; paraphrase 0.00.
  - For ROUGE-style scores, `rouge-score` (Apache-2.0) **[version unverified today]** or `nltk` 3.10.4 (Apache-2.0).
- **Recommendation:**
  - Exact quote: our own normaliser + offset map, with `rapidfuzz` as the fuzzy fallback; record a threshold and the alignment span.
  - Paraphrase ceiling: stdlib n-gram containment plus `rapidfuzz.fuzz.token_set_ratio`.
- **Risks:**
  - Two-column layouts and footnotes can interleave text; anchor matching per page or block.
  - Thresholds need calibration on a gold set.

### B10. Claim-support / NLI verifiers (local, Apple Silicon)
LLM-AggreFact averages are from the leaderboard fetched today: https://llm-aggrefact.github.io/

| Model | Size | License | AggreFact avg | Runs locally how |
|---|---|---|---|---|
| `lytang/MiniCheck-Flan-T5-Large` | 0.8B (783M params [live]) | MIT (HF card) | **75.0** | transformers + MPS. **[live]** torch 2.15 + transformers 5.22 on M4 Pro MPS: load 3.7 s warm (85 s first, incl. download); scored a supported vs an unsupported claim at **0.981 vs 0.009** in 0.48 s / 0.03 s. Uses MiniCheck's label-token trick (decoder start 0, logits for token IDs 3/209 = "0"/"1", per `minicheck/inference.py`). |
| `yaxili96/FactCG-DeBERTa-v3-Large` | 0.4B | MIT | **75.6** | transformers + MPS |
| `lytang/MiniCheck-DeBERTa-v3-Large` / `-RoBERTa-Large` | 0.4B | MIT | (in MiniCheck paper) | transformers |
| `bespokelabs/Bespoke-MiniCheck-7B` | 7.7B | **CC BY-NC 4.0** (https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B) | **77.4** | **Ollama `bespoke-minicheck`** (https://ollama.com/library/bespoke-minicheck). The MiniCheck package's 7B path needs vLLM, Linux only (https://github.com/Liyan06/MiniCheck). |
| Ollama `nimble` (Bespoke Labs decision model; leaderboard lists nimble-v4 9B) | 9B | Apache-2.0 (per Ollama page) | **77.8** (nimble-v4) | Ollama `/v1/systemone` typed-question API (https://docs.ollama.com/api/systemone.md); needs Ollama ≥0.35. Whether the Ollama `nimble` tag equals leaderboard "nimble-v4" is **[unverified]**. |
| Ollama `clef` (Cloudflare) | 27B | [unverified] | **79.0** | Ollama cloud/systemone (https://ollama.com/library/clef) |
| Granite Guardian 3.3 | 8B | Apache-2.0 (3.0 Ollama page) | **76.5** | Ollama `granite3-guardian` exists for 3.0 (https://ollama.com/library/granite3-guardian); 3.3 availability **[unverified]** |
| `MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli` | 435M | MIT | not on board | transformers; generic NLI baseline |
| AlignScore (`yuh-zha/AlignScore`) | 355M (RoBERTa-L) | code MIT; weights license **[unverified]** | not on today's board | Not on PyPI. Repo last pushed 2024-03 and pins old deps **[unverified]**. https://github.com/yuh-zha/AlignScore |
| DeepFact-Eval (ACL 2026, https://aclanthology.org/2026.acl-long.1586/) | agent | **repo has no LICENSE file** | n/a | https://github.com/kkkevinkkkkk/DeepFact. The "Lite" agent by default needs **OpenAI + Serper keys**. Not a local NLI model. |

- **PyPI name collision:** PyPI `minicheck` 0.4.0 is **an unrelated model checker**. Install MiniCheck from git: `pip install "minicheck @ git+https://github.com/Liyan06/MiniCheck.git@main"` (code Apache-2.0).
- **[live] MPS caveat:** torch logs "Redirects are currently not supported in MacOs" (harmless). The MiniCheck package hard-codes `CUDA_VISIBLE_DEVICES` in its README example, so call the HF model directly (as above) or pass the device explicitly.
- **Input format:** my smoke test used a `premise: … hypothesis: …` prompt. The official input is `doc + tokenizer.eos_token + claim`, with documents chunked by whitespace (`batch_tokenize` in https://github.com/Liyan06/MiniCheck/blob/main/minicheck/inference.py). The kernel must reproduce that format exactly.
- **Recommendation:** run a **two-tier support checker**.
  - (1) MiniCheck-Flan-T5-L or FactCG-DeBERTa-L (MIT, <1B, MPS) as the fast deterministic-ish gate on every claim–evidence pair.
  - (2) A second opinion from a different vendor through Ollama, `bespoke-minicheck` or `nimble`, for disagreements and high-stakes claims.
  - Use DeepFact's AtS idea (an audit with rationale) as design inspiration, not as a dependency.
- **Risks:**
  - Bespoke-7B is non-commercial.
  - Inputs beyond about 500 tokens need chunking (MiniCheck chunks internally).
  - AggreFact scores are on summarisation/RAG grounding, not scholarly argument. Calibrate on our own gold set.

### B11. PaperQA2 (`paper-qa`)
- **2026.8.12** (2026-08-12, Apache-2.0, `>=3.11`). Repo: https://github.com/Future-House/paper-qa (last pushed 2026-10-26, about 9.5k stars).
- **Library use:** usable as a library over a local corpus: `Settings(agent={"index": {"paper_directory": "my_papers"}})`.
- **Models:** LLMs and embeddings via **LiteLLM** (through `fhlmi`). Ollama is supported (`llm="ollama/llama3.2"`, `embedding="ollama/mxbai-embed-large"`, `api_base=http://localhost:11434`), as are local sentence-transformers embeddings (`paper-qa[local]`). README: https://github.com/Future-House/paper-qa
- **Defaults and dependencies:**
  - It defaults to OpenAI models and calls **Crossref, Semantic Scholar and Unpaywall** for metadata; it recommends `CROSSREF_API_KEY` and `SEMANTIC_SCHOLAR_API_KEY`.
  - It uses **tantivy** for full-text indexing.
  - Core dependencies include `fhaviary`, `fhlmi`, `tantivy`, `pybtex`, `tiktoken`.
- **Recommendation:** **borrow the ideas, don't depend on it.** The ideas worth taking: chunk → embed → retrieve → per-chunk *contextual summary with relevance score* (RCS) → answer with citations; tantivy for BM25.
  - It owns its own state, LLM loop and metadata clients, which duplicates and conflicts with the kernel's typed ledger and with driving LLMs through Claude Code/Codex subscriptions (which are not LiteLLM endpoints).
  - It is acceptable as an **eval baseline** on LitQA2 using Ollama.
- **Risks:** CalVer releases with sparse cadence; the FutureHouse-internal `fh*` dependencies; quality with small local models is unknown.

### B12. Durable workflow / state

| Option | Version | License | Infra | Fit |
|---|---|---|---|---|
| (a) SQLite event log + own FSM | stdlib `sqlite3` (+`sqlite-utils` 4.2.1, Apache-2.0) | — | none | Full control, append-only audit log = provenance |
| (b) LangGraph checkpointing | `langgraph` 1.2.20 (2026-11-05), `langgraph-checkpoint-sqlite` 3.1.3 | MIT | none | Couples state to the LangGraph graph runtime; we don't run the LLM loop in-process |
| (c) DBOS Transact | `dbos` 3.4.0 (2026-10-28) | MIT | **SQLite supported** (`system_database_url="sqlite:///…"`, default `sqlite:///<app>.sqlite`; Postgres recommended for prod) — https://docs.dbos.dev/python/reference/configuration | `@DBOS.workflow`/`@DBOS.step` give durable, resumable steps in-process; but `psycopg[binary]` + SQLAlchemy are hard dependencies |
| (d) Temporal | `temporalio` 1.35.0 | MIT | Needs a server; `temporal server start-dev --db-filename …` (no Docker, dev-only) — https://docs.temporal.io/cli/server | Overkill for single-user |

- **Recommendation: (a)**, a SQLite (WAL) append-only event log plus explicit state machines, with **(c) DBOS kept as the escape hatch** if long multi-step pipelines need crash-resume.
  - The kernel is a state + verifier server; the agent loop lives in external drivers.
  - An event log *is* the research audit trail (claim ledger history, verifier verdicts).
- **Risks:**
  - Hand-rolled retries and idempotency need discipline: use idempotency keys per tool call.
  - Concurrent MCP clients on one SQLite file need WAL mode + `busy_timeout`.

### B13. Other libraries

| Lib | Version (date) | License | Note |
|---|---|---|---|
| `pydantic` | 2.14.3 (2026-11-05) | MIT | model layer + MCP output schemas |
| `httpx` | 0.28.1 (**2024-12-06**, stale) | BSD-3 | `mcp` 2.x moved to **`httpx2` 2.16.0** (2026-10-21, BSD-3, https://github.com/pydantic/httpx2) |
| `tenacity` | 9.2.1 (2026-10-07) | Apache-2.0 | retries/backoff |
| `hishel` | 2.0.0 (2026-11-05, released today) | BSD | RFC 9111 HTTP cache with SQLite storage; has extras for both `httpx` and **`httpx2`** |
| `diskcache` | 5.6.3 (**2023-08-31**) | Apache-2.0 | unmaintained-looking |
| `sqlite-utils` | 4.2.1 (2026-08-13) | Apache-2.0 | ergonomic SQLite |
| `sqlmodel` | 0.0.48 (2026-10-06) | MIT | still 0.0.x |
| `citeproc-py` | 0.12.1 (2026-10-30) | BSD-2-Clause-Views | CSL processor; CSL styles repo is **CC BY-SA 3.0** (https://github.com/citation-style-language/styles) |
| `pyalex` 0.21, `habanero` 2.9.2, `semanticscholar` 0.12.0 | — | MIT | thin clients; optional |

- **Recommendation:**
  - `pydantic` 2.14 + `httpx2` (to align with `mcp` 2.x and avoid two HTTP stacks) + `hishel[httpx2]` + `tenacity` + stdlib `sqlite3`/`sqlite-utils`.
  - `citeproc-py` with CSL-JSON from A6.
  - Skip `diskcache` and `SQLModel`.
  - Write our own thin API clients so headers, budgets and caching stay under our control.
- **Risks:** `httpx2` and `hishel` 2.0 are both new. If either breaks, pin `httpx` 0.28 + `hishel[httpx]`.

---

## C. Driving agents without API keys

### C14. Claude Code headless (`claude -p`)
Installed locally: Claude Code 2.1.309. Flags are from https://code.claude.com/docs/en/cli-reference.md and https://code.claude.com/docs/en/headless.md.

- **Flags:**
  - `-p/--print`
  - `--output-format text|json|stream-json`, `--input-format stream-json`
  - **`--json-schema '<schema>'`**: the result lands in a `structured_output` field. It is absent if the run ends without valid output, and the `format` keyword is not enforced.
  - `--mcp-config <files|json>` (with `-p`, it waits for servers up to `MCP_TIMEOUT`, 30 s by default) and `--strict-mcp-config`
  - `--allowedTools`, `--disallowedTools`, `--tools`
  - `--append-system-prompt[-file]`, `--system-prompt[-file]`
  - `--max-turns`, `--max-budget-usd`
  - `--permission-mode default|acceptEdits|plan|auto|dontAsk|bypassPermissions`
  - `--model`, `--fallback-model`
  - `--no-session-persistence`, `--session-id`, `--resume`
  - `--settings`, `--bare`
- **Critical — `--bare`:**
  - "In bare mode, Claude Code **never reads OAuth credentials or the system keychain**"; it needs `ANTHROPIC_API_KEY` or `apiKeyHelper`.
  - The docs also say `--bare` "**will become the default for `-p` in a future release**". https://code.claude.com/docs/en/headless.md
  - So with subscription login we must run **non-bare** `-p` and isolate with `--strict-mcp-config --mcp-config …`, `--tools`/`--allowedTools`, `--settings`, and a clean cwd.
  - Non-bare `-p` runs the hooks in a project's `.claude/settings.json` and connects its `.mcp.json` servers without asking for workspace trust (same page).
- **Long-lived subscription token:** `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`, "for CI pipelines and scripts". https://code.claude.com/docs/en/authentication.md
- **Policy:** https://code.claude.com/docs/en/legal-and-compliance.md
  - OAuth is "intended exclusively for purchasers of … subscription plans and is designed to support ordinary use of Claude Code".
  - "Advertised usage limits … assume ordinary, individual usage of Claude Code and the Agent SDK."
  - Developers *building products* must use API keys. Third parties may not offer claude.ai login or route requests through subscription credentials *on behalf of their users*.
  - The policy explicitly does **not** prevent "an end user from signing in to the **unmodified Claude Code binary** with their own Claude subscription".
  - Agent SDK docs: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK." https://code.claude.com/docs/en/agent-sdk/overview.md
- **Recommendation:**
  - The user's own machine drives the **unmodified `claude` CLI** with their own login (or a `setup-token` token): `-p --output-format json --json-schema … --strict-mcp-config --mcp-config kernel.json --allowedTools "mcp__kernel__*" --max-turns N`.
  - Do **not** embed the Agent SDK with OAuth.
  - Do not ship this as a product to others.
- **Risks:**
  - The interpretation is ours: personal automation via the unmodified CLI looks permitted, but heavy unattended loops may exceed "ordinary, individual usage" **[policy-interpretation risk]**.
  - The future `--bare` default would break subscription auth for `-p`. Pin the CLI version and watch the changelog.

### C15. Codex CLI (`codex exec`)
Installed locally: codex-cli 0.166.0-alpha.6. Flags are from `codex exec --help` [live] and https://developers.openai.com/codex/noninteractive.md.

- **Flags:**
  - `--json`: JSONL events (`thread.started`, `turn.*`, `item.*`, `error`)
  - **`--output-schema <file>`**: final response conforms to the schema
  - `-o/--output-last-message <file>`
  - `-s/--sandbox read-only|workspace-write|danger-full-access`; read-only is the default for exec
  - `--approve-for-me`
  - `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, `--skip-git-repo-check`
  - `-C`, `-m`, `--oss --local-provider ollama`
  - `-c key=value`, `-p/--profile`
- **MCP config:** in `~/.codex/config.toml`, `[mcp_servers.<id>]` with `command`, `args`, `env`, `cwd`, `url`, `enabled`, `required`, `startup_timeout_sec`, … https://developers.openai.com/codex/config-reference.md
  - Can be overridden per run with `-c 'mcp_servers.kernel.command="…"'` [inferred from the `-c` semantics].
- **Auth policy:** https://developers.openai.com/codex/noninteractive.md and https://developers.openai.com/codex/auth.md
  - `codex exec` "reuses saved CLI authentication by default".
  - The docs call API keys "the right default for automation". ChatGPT-managed auth in CI/CD is an "advanced" path "if you need … ChatGPT/Codex rate limits instead of API key usage", with warnings to protect `~/.codex/auth.json` and not to use it in public repos.
- **Recommendation:** use the local ChatGPT-login `codex exec --json --output-schema` as the second LLM driver for cross-vendor review, with `--sandbox read-only` and `--ephemeral`.
- **Risks:**
  - The installed build is an **alpha** and flags drift.
  - Whether the OpenAI Terms of Use restrict automated use of consumer-plan output: openai.com returned 403 to fetches, so **[unverified]**.
  - Subscription rate limits are opaque.

### C16. Ollama (local 0.40.14 installed; Cloud)
- **Cloud API:**
  - `https://ollama.com/api/chat` with `Authorization: Bearer $OLLAMA_API_KEY`.
  - OpenAI-compatible base: `https://ollama.com/v1`. The cloud lacks stateful Responses and custom tool-call replay.
  - Anthropic-compatible API also offered.
  - Sources: https://docs.ollama.com/cloud.md and https://docs.ollama.com/api/openai-compatibility.md
- **[live] Cloud model list** (`GET https://ollama.com/api/tags`, 26 models): `gemma4:31b, gpt-oss:120b/20b, deepseek-v4.1-flash(:1017), deepseek-v4-pro, kimi-k3, kimi-k3.1-pro, kimi-k2.6/2.7-code, glm-5.2/5.3(-flash), minimax-m3(.1-flash)/m2.7, mistral-large-3/4, nemotron-3-nano/super/ultra, mimo-v2.6-pro/flash, clef:27b, beam, muse-spark-open`.
- **Structured outputs:**
  - Locally: the `format` field accepts a JSON schema (`Model.model_json_schema()`).
  - **"Ollama's cloud currently does not support structured outputs."** https://docs.ollama.com/capabilities/structured-outputs.md
  - System One (`/v1/systemone`) returns typed answers (choice, yes/no, score) locally and in the cloud. Cloud needs a paid plan or credits. https://docs.ollama.com/api/systemone.md
- **Local models observed [live `ollama list`]:** `gemma4:31b-cloud`, `deepseek-v4.1-flash:cloud`, `embeddinggemma-2`, `lfm3*`, `bonsai-2`. **`gemma4:12b-it-qat` is not currently pulled.**
- **Recommendation:**
  - Use Ollama as the **cross-vendor second opinion**.
  - Locally: schema-constrained JSON (`format`) for gemma4 12B / bespoke-minicheck / nimble verdicts.
  - In the cloud: a large open model (e.g. `gpt-oss:120b`, `deepseek-v4.1-flash`) with prompt-level JSON plus Pydantic validation and a retry, or System One typed questions.
- **Risks:**
  - The missing cloud `format` support means cloud verdicts need parse-and-retry.
  - Cloud model retirements are announced only in the settings UI.
  - Cloud prompts leave the machine (not used for training, per the docs).

**Section C Recommendation:**
- The kernel is an MCP stdio server. Drivers are `claude -p` (primary), `codex exec` (second vendor) and Ollama (third vendor / local).
- All three return schema-validated JSON (`--json-schema`, `--output-schema`, `format`) that the kernel re-validates with Pydantic.

**Section C Risks:**
- Subscription automation rests on "ordinary individual use" language.
- Claude's planned `--bare` default and Codex alpha builds can break drivers. Pin versions and keep the API-key path as a fallback.

---

## D. Evaluation datasets

| Benchmark | Availability / license | Size | Scoring | Judge needed |
|---|---|---|---|---|
| **LitQA2** (LAB-Bench) | HF `futurehouse/lab-bench` config `LitQA2`, **CC-BY-SA-4.0**, ungated (https://huggingface.co/datasets/futurehouse/lab-bench); repo https://github.com/Future-House/LAB-Bench (CC-BY-SA-4.0) | **199** questions [live datasets-server] | Multiple choice with an "insufficient information" option → accuracy/precision, computed deterministically | **None** |
| **ScholarQABench** (OpenScholar) | https://github.com/AkariAsai/ScholarQABench (MIT); HF `OpenScholar/ScholarQABench` returned an auth error to anonymous API calls | SciFact, PubmedQA, QASA, CS, Multi, Bio, Neuro subsets | SciFact/PubmedQA: accuracy + citation metrics. QASA: ROUGE-L. CS: rubric eval (`rubric_eval.py`). Multi: **Prometheus** (local vLLM) | Citation-precision/recall and rubric judges need an LLM (the paper used a proprietary judge **[unverified]**). Prometheus runs via vLLM (no macOS) |
| **DeepResearch Bench** | https://github.com/Ayanami0730/deep_research_bench (Apache-2.0); HF `muset-ai/DeepResearch-Bench-Dataset` (Apache-2.0) | **100** PhD-level tasks (50 zh / 50 en) | RACE (reference-based report quality) + FACT (citation accuracy) | **Official evaluator switched to GPT-5.5 (May 2026)**, replacing Gemini-2.5-Pro (deprecated 17 Jun 2026); cleaning model GPT-5.6 Luna → **paid API** |
| **ResearchRubrics** (Scale AI) | HF `ScaleAI/researchrubrics` (MIT), `processed_data.jsonl`; paper https://arxiv.org/abs/2511.07685 | **101** prompts, 2,500+ expert rubrics | Rubric compliance via LLM judge | LLM judge (any strong model; absolute scores judge-dependent) |
| **DRACO** (Perplexity) | HF `perplexity-ai/draco` (MIT), `test.jsonl`; paper https://arxiv.org/abs/2602.11685 | **100** tasks, 3,934 weighted criteria (415 negative) | Per-criterion binary MET/UNMET by LLM judge, weighted | "Capable judge model with low temperature"; rankings stable across judges |
| **DeepFact-Bench** (ACL 2026) | HF `kkkevinkkk/DeepFactBench` (MIT); code https://github.com/kkkevinkkkkk/DeepFact (no license file) | 1K–10K claims (HF size category; exact **[unverified]**) | Claim labels SUPPORTED / CONTRADICTORY / INCONCLUSIVE; DeepFact score | Default eval agent = **OpenAI GPT-4.1 + Serper search** |
| **ReportBench** (ByteDance) | HF `ByteDance-BandAI/ReportBench` (**CC0-1.0**); code https://github.com/ByteDance-BandAI/ReportBench (Apache-2.0) | **100** survey-derived prompts | Citation precision/recall vs survey references; non-cited claims fact-checked | **Web-connected Gemini Pro + Flash voting** for factual checks; citation matching is deterministic |

- **Runs fully locally or on subscriptions:**
  - **LitQA2** needs no judge and is fully local.
  - **ReportBench citation precision/recall** is deterministic matching.
  - **ScholarQABench SciFact/PubmedQA** are accuracy-scored; QASA uses ROUGE.
  - **ResearchRubrics and DRACO** are judge-agnostic by design, so they can run with `claude -p --json-schema` or `codex exec --output-schema` as judge. Scores are then not comparable to published leaderboards.
- **Need paid API judges for leaderboard-comparable numbers:**
  - DeepResearch Bench (GPT-5.5 official)
  - DeepFact-Eval (GPT-4.1 + Serper)
  - ReportBench factual path (Gemini web-grounded)
  - ScholarQABench rubric/citation judges **[judge model unverified]**
- **Recommendation:**
  - Dev loop: LitQA2 (offline accuracy, no judge) + ReportBench citation metrics + DeepFact-Bench labels used as a **gold set for our claim-support verifier** (MiniCheck/Ollama, scored against human/AtS labels; no LLM judge needed).
  - Periodic quality checks: ResearchRubrics + DRACO with a **subscription-CLI judge** run at temperature as low as the CLI allows, reported as internal scores.
- **Risks:**
  - Using subscription judges at scale may hit the "ordinary use" limits (C14).
  - Contamination: LitQA2 questions are public since 2024.
  - DRB's evaluator change splits historical comparisons.

---

## Summary table

| Component | Choice | Version | License | Reason |
|---|---|---|---|---|
| Discovery + citation graph | OpenAlex REST (free key) | API (2026 pricing) | Data CC0 (per OpenAlex) **[license not re-fetched]** | Free DOI lookups, `cites:` filter, `referenced_works`, `is_retracted`, GROBID-TEI full text |
| Retraction gate | Crossref `updated-by` / `update-to` (RW source) ∪ OpenAlex `is_retracted` | REST (polite pool) | Metadata open | Production RW data; Labs API deprecated |
| Passage search | Semantic Scholar `/snippet/search` (key) | Graph v1 | S2 API terms | Only full-text snippet API; 1 RPS keyed |
| Full text | arXiv HTML → PMC BioC → OpenAlex TEI → PDF | — | per-source | Structured text first, PDF last |
| Metadata fallback | Crossref transform / DataCite CSL-JSON | — | — | doi.org CN blocked by a challenge [live] |
| MCP server | `mcp` (official) `MCPServer` | 2.3.0 | MIT | Typed structured output, stdio default, fewer dependencies than fastmcp |
| Canonical PDF text | `pypdfium2` | 5.14.0 | BSD-3/Apache-2.0 | Fastest [live], per-page, permissive |
| Structured PDF parse | `docling` | 2.148.0 | MIT | page/bbox/charspan provenance; ~5 s/page [live] |
| Web text | `trafilatura` | 2.3.1 | Apache-2.0 | Standard main-content extractor |
| Quote match | own normaliser + `rapidfuzz` | 3.14.6 | MIT | Alignment spans; hyphen-insensitive key needed [live] |
| Claim support (tier 1) | MiniCheck-Flan-T5-L / FactCG-DeBERTa-L | HF (2024–25) | MIT | 75.0 / 75.6 AggreFact, <1B, MPS |
| Claim support (tier 2) | Ollama `bespoke-minicheck` or `nimble` | Ollama 0.40.x | CC BY-NC 4.0 / Apache-2.0 | 77.4 / 77.8 AggreFact, local, other vendor |
| RAG ideas | PaperQA2 (borrow, don't depend) | 2026.8.12 | Apache-2.0 | RCS + tantivy design; avoids an LLM-loop clash |
| State / durability | SQLite WAL event log + FSM (DBOS as escape hatch) | stdlib / dbos 3.4.0 | — / MIT | Audit trail, zero infra |
| Models / HTTP | `pydantic` + `httpx2` + `hishel[httpx2]` + `tenacity` | 2.14.3 / 2.16.0 / 2.0.0 / 9.2.1 | MIT / BSD-3 / BSD / Apache-2.0 | Aligns with mcp 2.x stack; RFC 9111 cache |
| Citations | `citeproc-py` + CSL styles | 0.12.1 | BSD-2 / CC BY-SA 3.0 | CSL-JSON native |
| Driver 1 | `claude -p` (non-bare, own login) | 2.1.309 | proprietary | `--json-schema`, `--strict-mcp-config`; policy allows unmodified CLI with own sub |
| Driver 2 | `codex exec` (ChatGPT login) | 0.166.0-alpha.6 | Apache-2.0 (https://github.com/openai/codex) | `--output-schema`, `--json`, read-only sandbox |
| Driver 3 / 2nd opinion | Ollama local (`format` schema) + Cloud | 0.40.14 | MIT | Cross-vendor; cloud lacks structured outputs |
| Eval | LitQA2 + ReportBench (citations) + DeepFact-Bench (verifier gold) ; ResearchRubrics/DRACO with CLI judge | HF snapshots | CC-BY-SA-4.0 / CC0 / MIT / MIT / MIT | Judge-free or judge-agnostic |
