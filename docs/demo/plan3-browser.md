# Plan 3 demo: built-in browser and Workbench (2026-11-06)

Recorded on macOS arm64 with a scratch `RK_HOME` and `RK_BROWSER_HEADLESS=1`. Headless is used here only so the demo
does not open a window on the desktop; a normal `rk serve` shows the window.

## 1. `rk serve` opens the Workbench as the window's first tab

```
$ RK_BROWSER_HEADLESS=1 rk serve
Workbench open in the built-in browser: Research Kernel — Workbench (http://127.0.0.1:<port>/ui/)
```

The one-time login link is used once (a second use returns 403). The serve token does not open `/ui`, and the
Workbench cookie does not open `/api`.

## 2. PubMed search in the browser (live)

```
$ rk browser search pubmed "perovskite solar cell stability" --max 3 --project perovskite
- Single-Crystal Perovskite for Solar Cell Applications.
  https://pubmed.ncbi.nlm.nih.gov/39301993/
  doi:10.1002/smll.202402759  pmid:39301993
- Big data driven perovskite solar cell stability analysis.
  https://pubmed.ncbi.nlm.nih.gov/36496471/
  doi:10.1038/s41467-022-35400-4  pmid:36496471
- Improvement Strategies for Stability and Efficiency of Perovskite Solar Cells.
  https://pubmed.ncbi.nlm.nih.gov/36234422/
  doi:10.3390/nano12193295  pmid:36234422
```

The Primo VE adapter was checked the same way against a public university catalogue (10 hits, adapter self-check
passed).

## 3. Publisher PDF through the browser, then a verified quote (live)

An item that only has the article page URL. The API channels have nothing, so the browser channel fetches the PDF that
the page names in `citation_pdf_url`:

```
rk_browser_fetch_fulltext -> {'kind': 'pdf', 'origin': 'publisher_browser', 'page_count': 10,
                              'url': 'https://www.nature.com/articles/s41467-022-35400-4.pdf'}
rk_note_add (quote taken from page 1) -> verified: {'quote': {'ok': True, 'page': 1}}
```

## 4. The Lead agent uses the browser (live, test model `omp_gateway:openai-codex/gpt-6-luna`)

Prompt: "Use the built-in browser to search PubMed for 'perovskite encapsulation damp heat' (at most 3 results), then
add the first result to the project library by its DOI."

```
TOOL rk_browser_search {'site': 'pubmed', 'query': 'perovskite encapsulation damp heat', 'max_results': 3}
TOOL rk_source_add {'identifier': '10.1038/s41467-023-36918-x', 'discovered_via': 'browser', 'verify': True}
TOOL rk_library_list {}
TEXT Added "Room temperature nondestructive encapsulation via self-crosslinked fluorosilicone polymer enables damp
     heat-stable sustainable perovskite solar cells."
```

## 5. Hand-offs to you

- **Google Scholar.** Headless Chromium got Scholar's "automated queries" page, served at the normal results URL. The
  detector recognises it, pauses Scholar and opens a `captcha_wait` checkpoint, so later Scholar calls fail fast with
  `site_paused` until you press Done. Europe PMC and doi.org answer headless Chromium with a Cloudflare challenge, which
  is detected the same way.
- **Library sign-in.** An expired proxy session (redirect to an SSO page) opens `login_wait` and attaches nothing.
  After you sign in, the same fetch attaches the PDF as `library_browser`, with an access note. This runs in
  `tests/test_browser_service.py` against a local fake proxy and SSO site, because signing in to your real library is
  your step (`rk setup`, step 5).
- **Private network.** Agent tabs cannot reach loopback, private or link-local addresses. That includes redirect chains
  that start on a public page, cross-site frames and popups. The private test server receives zero requests
  (`tests/test_browser_session.py`).

## 6. Workbench pages (checked visually in the real surface)

- **Chat:** the Lead agent's answers, with tool calls folded.
- **Library:** search within a collection, badges (reference check, retraction, full text, verified quotes), the PDF
  link and notes.
- **Checkpoints:** a CAPTCHA card with a Done button; the count in the rail updates live.
- **Progress:** tasks, open checkpoints and the event feed.
- **Drafts:** source markers as chips, with the claim, its quote and page in the margin note; a marker without a target
  shows red.
- **Settings:** keys are never shown back. The setup checks include "Show what works", the capability table.
