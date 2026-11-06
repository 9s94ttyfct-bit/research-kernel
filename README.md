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
- PDF: white, invisible-render-mode, sub-1 pt (font size or text-matrix scaling) and off-page glyphs
- HTML: `display:none`, `visibility:hidden`, `opacity:0`, zero font size (inline or via simple `<style>` rules,
  `!important` included), `hidden`, comments, scripts and zero-width characters

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

```bash
uv run pytest                     # offline: recorded HTTP fixtures, generated PDFs, gold set, fake CLIs
uv run pytest -m live             # also hits the real APIs (citation trap set)
uv run pytest -m "live and llm"   # real models: claude, codex, and omp_gateway openai-codex/gpt-6-luna (test model)
uv run python scripts/record_fixtures.py   # re-record fixtures
uv run python scripts/make_gold.py         # regenerate the construction-labelled gold set
```
