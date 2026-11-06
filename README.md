# Research Kernel (`rk`)

A verifiable research-agent foundation:
- per-project, event-sourced state with a hash-chained audit log
- a Zotero-like project library
- scholarly source adapters
- page-anchored full text
- deterministic verifiers

The design lives in `docs/superpowers/specs/2026-11-05-research-kernel-design.md`, and the implementation plans in `docs/superpowers/plans/`.

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
| V-SUPPORT | Layer 1 is the probability p from a local MiniCheck model, computed exactly as in the official package. The default is Bespoke-MiniCheck-7B in Ollama (`ollama pull bespoke-minicheck:7b-q8_0`; CC BY-NC 4.0, non-commercial use only). MiniCheck-Flan-T5-Large (MIT) is the alternative (`support_model`). Layer 2 is a set of LLM judges from vendors other than the claim's author; each must quote the evidence verbatim. **supported** needs ≥ `support_min_judges` (default 2) agreeing judges and p ≥ τ_hi, or p ≥ τ_hi_judged when at least two such judges agree. **unsupported**/**contradicted** mirror this with τ_lo / τ_lo_judged and need one shared label. All four thresholds come from `rk eval support` for the configured model; thresholds calibrated for a different model are refused. Everything else is **uncertain**, which in collaborative mode opens an `uncertain_support` checkpoint. |
| V-NUM | Every number in a claim must occur, with the same value and unit, in its evidence. Formats are normalised: thousands separators, Unicode minus, `×10ⁿ`, `%`/percent and ranges. |

Hidden text is removed before anything enters a text layer:
- PDF: white, invisible-render-mode, sub-1 pt (font size or text-matrix scaling) and off-page glyphs
- HTML: `display:none`, `visibility:hidden`, `opacity:0`, zero font size (inline or via simple `<style>` rules,
  `!important` included), `hidden`, comments, scripts and zero-width characters

## Evaluation

V-SUPPORT layer 1 defaults to Bespoke-MiniCheck-7B in a local Ollama: `ollama pull bespoke-minicheck:7b-q8_0`
(8.2 GB; it needs nltk from the `support` extra for sentence splitting). That tag is Ollama's 8-bit quantized build of
`bespokelabs/Bespoke-MiniCheck-7B`. It has not been checked against the full-precision model, so its thresholds come
from its own calibration. The Flan-T5 alternative also needs torch and transformers (`uv sync --extra support`) and
downloads once to `$RK_HOME/models`. Evaluation needs `--extra eval`.

```bash
rk eval support --write-config      # calibrate τ on SciFact train; confusion matrices on held-out SciFact dev
rk eval litqa2 --limit 20           # LitQA2 multiple choice with the Kernel's own search/full-text tools
rk eval deepfact --limit 10         # DeepFact-Bench claims: gather evidence, then V-SUPPORT (evaluation only)
rk eval reportbench --reports DIR   # citation precision/recall against ReportBench references
rk support check --text passage.txt --sources src_… --driver codex_cli --gold gold.json
```

- Results land in `docs/eval/`. The Markdown reports hold aggregate numbers. The JSON files also hold per-item dataset
  ids, gold and predicted labels, and scores, but never dataset text. Dataset sources and licences are listed in
  `src/rk/eval/LICENSES.md`.
- Evaluations run on a scratch kernel with chat-API drivers. `rk support check` runs inside `rk serve`, so it can
  use any driver.

## Built-in browser and Workbench

`rk serve` opens one Chromium window, which is the built-in browser. Its first tab is the Workbench, the local web page where you chat with the Lead agent and browse the library. You also answer checkpoints, watch progress, read drafts and change settings there. `rk open` prints a one-time link to the Workbench for another browser. The window keeps its profile in `$RK_HOME/browser-profile/`, so your library sign-in survives restarts.

```bash
uv run playwright install chromium         # once
uv run rk serve                            # browser window + Workbench (--no-browser to run without)
uv run rk setup                            # also: model providers, library sign-in and a test fetch
uv run rk browser search pubmed "perovskite encapsulation damp heat" --max 5
uv run rk browser search primo "perovskite stability"    # your library catalogue (Primo VE or Summon)
uv run rk browser fetch <item_id>          # PDF through your library proxy or the publisher page
uv run rk browser save [--collection ID]   # save the page you used last (metadata + PDF or snapshot)
uv run rk browser status                   # paused sites, today's quotas, open tabs
```

- **Human pace.** Google Scholar: 20–45 s between queries, at most 60 a day. Publisher PDFs: 30 s per domain, at most 40 per domain and 150 in total a day. Other sites: 5 s. One browser task runs at a time, and the counters survive restarts. Limits are in `config.toml` (`browser_limits`).
- **You stay in charge of logins and CAPTCHAs.** A CAPTCHA, an "unusual traffic" page or a sign-in page pauses that site and opens a `captcha_wait` or `login_wait` checkpoint. You handle the page in the window and press Done in the Workbench. The Kernel never solves CAPTCHAs, never fills in a password field and never stores your library password.
- **Agents reach only the public web.** Agent tabs cannot open the Workbench or any loopback, private or link-local address, and every redirect hop is checked before it is sent. Frames from other hosts are not loaded, and service workers are blocked. An intranet host your library needs can be allowed in `browser_allow_hosts`. Your own tabs are not restricted.
- **Agents read, they do not post.** Outside the search sites, an agent cannot submit a form: a submit button or Enter in a form field fails with `forbidden_action`, and every POST (or similar) request from an agent's tab is refused, including one sent by a page script. So agents make no purchases, posts or account changes. A page an agent asks you to handle (`rk_browser_request_human`) opens under the same checks and then becomes your tab, where you can sign in. Each agent keeps at most 5 tabs.
- **Metadata.** Pages are read through Highwire `citation_*`, JSON-LD, Dublin Core and COinS. If a DOI, PMID or arXiv id resolves through Crossref, OpenAlex or PubMed, the API record wins and the differences are logged. Otherwise the item is marked `page_metadata_only`.
- **Full-text order.** Open-access channels come first; the browser comes last (`fulltext_browser_fallback`). A PDF fetched this way is labelled `library_browser` or `publisher_browser` with an access note, and stays in the project.
- **Optional.** A Zotero translation-server (`translation_server_url`) improves metadata for pages without good tags.

## Drivers and roles

Role tasks run on a configurable driver per role (`drivers` in `config.toml`; see `rk providers`):

| Driver | How it runs | Kernel tools reach it through |
|---|---|---|
| `claude_cli` | `claude -p`, all built-in tools off (`--tools ""`), user settings off (`--setting-sources ""`) | `rk mcp` (stdio MCP proxy) with a one-time task token |
| `codex_cli` | `codex exec`, read-only sandbox, shell/apps/browser features off, `--ignore-user-config` | `rk mcp`, tools pre-approved for that server only |
| `anthropic_api:<model>`, `openai_api:<model>` | HTTP API with your key (`config.toml` or `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) | the in-process tool loop |
| `ollama:<model>` | local `ollama serve` (cloud models via `:cloud`; output validated client-side) | the in-process tool loop |
| `omp_gateway:<provider/model>` | optional, off by default (`omp_gateway = true`); `omp auth-gateway stdio` | the in-process tool loop |
| `human` | turns the task into a checkpoint for you | — |

- Every task is bound to one project and to its role's tools (`src/rk/roles/policy.py`). Agents can never answer
  checkpoints, attach host files or manage tokens.
- Text from sources reaches models inside `untrusted` data blocks. Agents report embedded instructions with
  `rk_flag_injection`.
- Every attempt (input, output, model, duration) is an event in the project log. A task gets at most 3 attempts.
  Subscription CLIs are capped per hour (`calls_per_hour`, default claude 30, codex 20).
- Generator and reviewer must come from different model developers (`src/rk/llm/vendors.toml`).

```bash
rk providers                                   # driver per role, vendor, availability
rk task run --role reader --driver codex_cli --instructions "…"   # one recorded role task
rk chat --driver claude_cli --role lead        # multi-turn chat with the role's tools
rk checkpoint list / rk checkpoint answer <id> --json '{…}'       # questions only you can answer
rk token create --name my-client               # lead token for an external MCP client (RK_TASK_TOKEN=… rk mcp)
```

## Tests

Browser tests (`-m browser`, included by default) drive headless Chromium against local fixture sites; they are skipped when Chromium is not installed.

```bash
uv run pytest                     # offline: recorded HTTP fixtures, generated PDFs, gold set, fake CLIs
uv run pytest -m live             # also hits the real APIs (citation trap set)
uv run pytest -m "live and llm"   # real models: claude, codex, and omp_gateway openai-codex/gpt-6-luna (test model)
uv run python scripts/record_fixtures.py   # re-record fixtures
uv run python scripts/make_gold.py         # regenerate the construction-labelled gold set
```
