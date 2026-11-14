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
uv run rk project intake --audience "researchers in photovoltaics" --paper-type critical_review --mode d2 --production p2
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
| V-SUPPORT | Cloud judges only, no local model (user decision 2026-11-07). Three judge roles, `judge`, `judge_2` and `judge_3`, by default GPT-6 Luna (`omp_gateway:openai-codex/gpt-6-luna`), DeepSeek v4.1 flash (`ollama:deepseek-v4.1-flash:cloud`, hosted by Ollama Cloud) and Claude Haiku 5.5 (`omp_gateway:anthropic/claude-haiku-5-5`). A judge's vendor must be known, not human, and differ from the claim author's and from every other admitted judge's; a judge that fails this is skipped and does not vote (`same_or_unknown_vendor`, or `duplicate_vendor` when another admitted judge has its vendor). A judge that answers supported, partial or contradicted must quote the evidence verbatim; otherwise its opinion counts as uncertain. **supported**, **unsupported** or **contradicted** needs at least `support_min_judges` (default 2) valid judges that all give that label, and V-NUM gates **supported**. Everything else is **uncertain**: one dissent, a "partial" or a single valid judge. In collaborative mode that opens an `uncertain_support` checkpoint. Latest run: `docs/eval/support-2026-11-07-judges.md` (measured with the earlier judge drivers `codex_cli:gpt-6-luna` and `claude_cli:claude-haiku-5-5`, the same models). |
| V-NUM | Every number in a claim must occur, with the same value and unit, in its evidence. Formats are normalised: thousands separators, Unicode minus, `×10ⁿ`, `%`/percent and ranges. |
| V-STATS | A statistic or figure is made only by the kernel (`rk.stats`), never by a model. It recomputes, with the current `rk.stats` code, from the extraction rows it was made from (their hashes recorded), to 1e-9; a changed row, a different code version or a different extraction attempt is a mismatch. |

Hidden text is removed before anything enters a text layer:
- PDF: white, invisible-render-mode, sub-1 pt (font size or text-matrix scaling) and off-page glyphs
- HTML: `display:none`, `visibility:hidden`, `opacity:0`, zero font size (inline or via simple `<style>` rules,
  `!important` included), `hidden`, comments, scripts and zero-width characters

## Evaluation

The LitQA2 evaluation needs `--extra eval`. `rk eval support` asks each configured judge (or `--judges`) once per
SciFact dev pair and reports the verdicts production would give for claims written by `anthropic` (the headline),
`openai` and `human`. Successful opinions are cached under `$RK_HOME/eval` by prompt fingerprint, so a rerun pays only
for missing or failed calls; judge calls count against the same hourly call caps as `rk serve`.

```bash
rk eval support --limit 100         # V-SUPPORT judges on SciFact dev: verdict × gold matrix, coverage, precision
rk eval litqa2 --limit 20           # LitQA2 multiple choice with the Kernel's own search/full-text tools
rk eval deepfact --limit 10         # DeepFact-Bench claims: gather evidence, then V-SUPPORT (evaluation only)
rk eval reportbench --reports DIR   # citation precision/recall against ReportBench references
rk eval stats [--write-config]      # reproduce published meta-analyses with rk.stats; --write-config enables pooling
rk eval sr-repro [--entry ID]       # the systematic-review reproduction set: seed, run (in rk serve) and score
rk support check --text passage.txt --sources src_… --driver codex_cli --gold gold.json
```

- Results land in `docs/eval/`. The Markdown reports hold aggregate numbers. The JSON files also hold per-item dataset
  ids, gold and predicted labels, and scores, but never dataset text. Dataset sources and licences are listed in
  `src/rk/eval/LICENSES.md`.
- Evaluations run on a scratch kernel with chat-API drivers; the judges may also use the agentic CLIs, since they call
  no tools. `rk support check` runs inside `rk serve`, so it can use any driver.
- The systematic-review reproduction set (`rk eval sr-repro`) reproduces six published reviews, each in a P1
  project `srrepro-<id>` seeded with the review's question frozen as the RQ: intake, landscape, gaps, topics and
  question are recorded as passed by the seed (discovery does not run) and fixed, so no back-edge or rerun reopens
  them; one that would fails the run with "<stage> is fixed by the reproduction seed (its question is the published
  review's)". When the P1 RQ vote finds the main claim does not answer the RQ, argue goes back to synthesize, not to
  question, and fails once argue→synthesize has no use left (`docs/eval/seeded-fixed-2026-11-13.md`: going back to
  question re-ran the novelty check on a given question, which found a published review of it, in tfa the reproduced
  review itself, and the runs ended at gaps). The RQ judges see the main claim, its scope limits, the supported
  synthesis findings it argues on and the Arguer's self-check. A part of the question that no included study addresses,
  named as such in the claim, is an evidence gap and not a failure to answer; leaving out a part that a finding
  speaks to is a failure.

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

## Discovery (D1/D2)

Discovery goes from a field to one frozen research question in five stages: intake, landscape (clusters of verified
works), gaps, topics and question. Each stage has a gate. A rerun of a stage and the back-edges gaps→landscape,
topics→gaps and question→topics may each be used twice per project; after that P2 asks you (`stage_blocked`) and P1
degrades.

- **D1 (guided).** The Lead drives the stages with you in the Workbench Chat: it runs a stage, shows what it found with
  its evidence, records your feedback as an exploration round and moves on when you are satisfied. A stage call
  (`rk_stage_run`, `rk_stage_advance`) runs in the background and returns at once: a stage takes minutes. The Lead
  follows it with `rk_stage_status` (`d1.running`, then `d1.last` with the outcome and its reason) and never starts
  another while one runs. Stage calls are for D1 projects only, and no D2 run can start while one runs.
- **D2 (autonomous).** `uv run rk run` runs the stages in `rk serve` without the Lead and stops only at checkpoints. A
  run session has a 12 h wall clock and 40 role tasks per open stage (landscape, gaps, topics, protocol, argue,
  revise; search_screen and read_extract have no task budget, their work grows with the records). A check's task
  (the judges of V-SUPPORT, V-NOVELTY, already-done and the P1 votes; triage in gaps) runs but does not count. Hourly
  CLI caps pause the run until the next slot. Time spent waiting for your answer does not count: an answer that comes
  after the session's 12 h starts a new session. A source that fails for a while (HTTP 429/5xx) makes the run back off
  and try again; one work whose lookup fails is reported and skipped. `uv run rk run resume` on a failed or cancelled
  run starts a new run (and says so), which goes on from the stages that already passed.

```bash
uv run rk project intake --audience "…" --paper-type critical_review --mode d2 --production p2 --topics-n 6
uv run rk run perovskite --until question     # or --until topics; the query comes from the title and field
uv run rk run status                          # the run and its stages; also pause, resume, cancel
uv run rk stage status                        # status, attempts, waits and back-edges used per stage
uv run rk topic list                          # candidate topics with their gaps and closest works
uv run rk checkpoint list / uv run rk checkpoint answer <id> --json '{…}'
```

A run needs two judges of different known vendors, neither the vendor of the Scout, GapMiner or Ideator;
`uv run rk run` refuses to start without them (`judges_missing`).

The checkpoints you answer (only a human can, in the Workbench Checkpoints page or with `uv run rk checkpoint answer`):

| Checkpoint | When | Answers |
|---|---|---|
| `topic_review` | after the topics stage, in every mode | `approve` one topic, `revise` (topics or gaps, with a comment) or `reject` |
| `rq_approval` | the RQ was not found answered within the searched scope (D1; D2 with P2) | `approve` freezes the RQ; `revise` with a comment reruns the stage |
| `significance_novelty` | the two already-done judges split (P2) | `proceed` to `rq_approval`, or go `back` to topics |
| `stage_blocked` | a back-edge is at its limit (P2) | `{"decision": "allow_one_more"}` (optional `comment`: guidance for the stage that runs again, with the failures), or `stop` the run |

- **A verified gap means its evidence chain was checked, not that it is new.** The kinds are contradictions (two
  verbatim quotes judged "contradicted" by V-SUPPORT), citation distortions (a citing quote judged unsupported by
  passages the Kernel picks from the cited work), stated limitations (a verbatim quote in the last 40 % of the text
  that Triage finds no later work addressing), evidence-map holes (a cell of a map that placed at least half of the
  works, next to placed works, with no work in it; Triage placed none of the targeted search's works) and retracted
  works still cited. Absence is always worded "not found within the searched scope".
- **V-NOVELTY is a reference only.** Each topic shows its closest works so you can judge overlap yourself; it never
  stops a topic. The kernel refuses a novelty claim ("novel", "first study to", "never been studied", …) in every text
  the Ideator writes for a topic or an RQ, and in the judges' differences: such an output is rerun.
- **The already-done check gates the RQ** (unlike V-NOVELTY): each judge whose vendor differs from the RQ author's
  judges the closest works, quoting their title or abstract. A work that two valid judges judge as answering the RQ
  sends the project back to topics; a split asks you (P2) or counts as answered (P1). Only an RQ not found answered
  within the searched scope can be approved.
- **Models are configurable per role** (`drivers` in `config.toml`, `rk driver set` or Settings → Models): the Scout,
  GapMiner and Ideator generate; `judge`, `judge_2` and `judge_3` (V-SUPPORT, V-NOVELTY, the already-done check) and
  `triage` check, from other vendors.
- A recorded run: `docs/demo/plan4-discovery.md`.

## Evidence pipeline (protocol, screening, extraction)

After the research question is frozen, three stages turn it into checked evidence: `protocol` (the review protocol,
frozen), `search_screen` (search, deduplication, title/abstract and full-text screening, PRISMA) and `read_extract`
(reading and extraction with verified quotes). The paper type of the intake decides how strict each one is.

- **Runs.** From protocol on, a run drives the later stages of the paper type in D1 and D2 projects alike
  (`uv run rk run <project> --until protocol|search_screen|read_extract`). A D2 run may go from intake straight past
  question (it freezes the RQ on the way); a run that starts after question passed needs a frozen RQ
  (`rq_not_frozen`). Once question passed, or once production began (even if protocol→question reopens question), the
  Lead only reads and guided stage calls are refused. A stage runs only when it has a worker; the others show as "not
  yet available" and a run cannot end at or pass them (`stage_unavailable`). The back-edges protocol→question,
  search_screen→protocol and read_extract→search_screen follow the same limit of two uses per project.
- **Protocol.** The Ideator drafts the eligibility criteria, the extraction schema (text, number or category fields),
  the synthesis plan and, for a systematic review, the PRISMA-P items, written to agree with the protocol's languages
  (English: records in another language are excluded by rule, so no criterion admits one); the Scout drafts the
  search strategy (at least 3 queries on at least 2 sources, years), each query a few concepts joined by AND, each
  concept an OR group of its synonyms. The queries together find every record that may meet the eligibility criteria
  (screening applies the criteria): an outcome is ANDed only when it is the subject of the question, and then with its
  broader names; a concept naming a class lists its common members; a context such as the setting or the delivery route is not
  ANDed unless a criterion requires it; narrowing keeps the population and the intervention concepts
  (`docs/eval/search-screen-2026-11-12.md`). Every query runs on every source of the strategy as written, so a field tag one
  source understands (PubMed `[tiab]`, Europe PMC `TITLE_ABS:`) breaks the query on the other. The kernel picks the
  fields: OpenAlex is searched in titles, abstracts and the keywords it tags works with, Europe PMC in titles and
  abstracts, PubMed as given (its automatic term mapping applies), arXiv in all fields
  (`docs/eval/search-scope-2026-11-12.md`: the reproduction set's selection queries find 79 of the 81 included
  studies the full-text search found, with about 6 to 32 times fewer records, mostly 12 to 26; strategies written for
  the full-text search lost included studies when scoped, cade 5 to 4 of 6 and tfa 5 to 3 of 33, so the next rerun
  measures recall). The kernel checks the draft (its free texts may not claim novelty, the same rule as for topics
  and RQs) and sizes it: the
  record-cap probe counts each query on each source that reports counts (OpenAlex, Europe PMC, PubMed) within the strategy's publication years. The probe
  is advisory and gives two bounds on the records: the sum of the counts (an upper bound: duplicates count again) and
  the largest single count (nearly always a lower bound). A draft that fails the checks is drafted again within the
  attempt (up to `protocol_tries`, default 3, versions; only the role whose part failed is asked), and a probe above
  the record cap sends the strategy back to the Scout to narrow, with the counts (up to `protocol_narrow_tries`,
  default 3, tries; a strategy that does not lower the total is a failed try, and so is one whose largest single count
  is above the cap when the best version's is within it); the version with the lowest total goes
  on. The kernel writes what only it knows: for a systematic or scoping review the PRISMA-P title (the review type
  and the frozen question; the rendered protocol opens with it), the contributors (drafted by the kernel's agent
  roles, frozen in P1 only when every judge role the vote admits approves it, in P2 by you; no human author or
  contact address is recorded; a version drafted before you change the production mode goes to the vote or your
  approval as a copy with the current mode's text) and the
  administrative items (registration, amendments, support, competing interests), the language exclusion `E-lang` and the search execution (publication years written out,
  each query sent as written to each source and the fields each source searches); the checks also reject
  placeholders, a missing PRISMA-P item of a systematic review, prose naming sources or searches the strategy does not have, missing outcomes or study
  identifier field, a meta-analysis of diagnostic accuracy, unbalanced or AND/OR-mixed queries, and a redraft that
  drops a PRISMA-P item no open point and no revise comment of yours asked to remove. In P2 you approve, revise or go
  back in a `protocol_approval` checkpoint (a revise, like
  checks still failing after the tries, reruns the stage and uses one of its two reruns); in P1 a two-vendor vote
  approves or asks for a revision, and a version whose probe shows it above the record cap (its largest single count
  is above the cap) is not voted on; between the bounds it is voted on, the judges see both bounds, and
  search_screen's deduplicated count holds the cap. P1 judges answer with
  numbered points (blocking or advisory) kept in a ledger; the next draft answers each open point it owns (changed
  or rebutted; the Scout answers the search points, the Ideator the others, and an answer to another author's point
  is dropped), only the judge that raised a point closes it (a mark whose id differs from one of the judge's own
  points only in its separators, `p1.judge.2.1` for `p1.judge_2.1`, is that point; a mark that matches none is
  ignored and listed), and an approve with an open blocking point of the judge's own counts as revise. Each judge
  sees every open point with the judge that owns it and votes on its own: it answers revise only for a blocking
  point of its own, never because another judge's point is open (that judge's vote already holds it). A judge that
  approves and leaves some of its own open blocking points unmarked is asked once more, in the same vote, to mark
  exactly those (resolved or not, with why); its marks then apply as any other (the opinion records them as
  `repaired`), a point it leaves unmarked or marks unresolved still counts as open, and a resumed vote asks nothing
  again. At the
  protocol's rerun limit a P1 protocol that still converges gets another round: when the last vote closed at least
  one blocking point and left no more open than before it, the kernel allows one more `protocol->protocol` use
  (recorded as `edge.allowed` by kernel, with the reason), at most twice per project; otherwise the P1 run fails at
  the limit as before, and P2 still asks you. Approving freezes the protocol (`frozen_hash`, the sha256 of its
  canonical JSON);
  only you set the record cap (default 3000), with the approval. A frozen protocol is never edited: a later frozen
  version records a deviation from it. For systematic and scoping reviews the freeze counts as preregistration
  (G-PREREG); critical reviews and proposals freeze for traceability and need only the question, search and
  selection principles.
- **Screening (search_screen).** The frozen search strategy runs and the records are deduplicated (above the record
  cap the stage goes back to protocol; so does a systematic or scoping review whose search a source could not
  finish, while a critical review or proposal goes on and records it as a limitation). A later hit of a work already
  in the library fills what the item lacks, its abstract included. Records in other languages are
  excluded ("language"). Before title/abstract screening, once per attempt, a record still without an abstract gets
  one from PubMed (`efetch`, by PMID) or else Europe PMC (by DOI); a failed lookup is the record's problem, never
  fatal. screener_a, and screener_b where dual screening applies (systematic reviews, the scoping
  review's 10% sample), decide on titles and abstracts in batches of 25; a decision without the reason it needs is
  asked again. In both phases a screener decides "no" only when the text shows the record fails a criterion, naming
  it and pointing to the words; a criterion the text shown says nothing about is "unclear" (an exclusion criterion
  phrased as an absence is met only when the text shows the absence). Dual screening needs two screeners of
  different vendors: a run that would reach search_screen without them is refused at start (`screeners_unusable`).
  At title/abstract both screeners unclear sends the record to the full text. Other disagreements go to you in
  `screening_disagreements` checkpoints of up to 25 records (P2) or to the two-vendor vote (P1): at title/abstract
  the judges are asked whether the record may meet the criteria, and when they disagree it goes to the full text; at
  full text they include a record when the text shown establishes every criterion, exclude it when it shows one
  fails, and otherwise exclude it marking the criterion unsettled, and the vote excludes when they disagree
  (`docs/eval/search-screen-2026-11-12.md`). Such an exclusion, and a both-unclear pair's, says "eligibility not
  established from the text shown (abstract only / sections / whole text)"; it is an exclusion like any other,
  counted apart in the stage's summary, the PRISMA flow and notes, and the Writer's limitations
  (`docs/eval/ft-screening-2026-11-13.md`). The full text of every include is then sought (open-access
  channels: by PMCID Europe PMC's XML and PMC's BioC, an item without a PMCID looking it up by DOI or PMID first;
  then the browser). An article outside PMC's open-access subset is not fetched from PMC's web site, whose terms
  forbid automated downloading: it stays abstract-only. A record without a full text is assessed on its abstract,
  its problem naming each source tried and why it gave none, and the same screeners
  decide again on up to 12000 characters of the full text's abstract, methods and results (results the largest
  share; a structured abstract's own sub-headings stay in the abstract). V-PRISMA checks that the flow adds up and
  that every exclusion has a reason (systematic and scoping reviews); a critical review or proposal keeps the
  selection reason of every record instead. An attempt interrupted by an hourly cap is resumed: the next attempt
  reuses the decisions already made under the same frozen protocol.
- **Reading and extraction (read_extract).** For every study included at full text the retraction status is
  checked (V-RETRACT; a retracted or withdrawn study is flagged, not extracted, and listed for the limitations), then
  the reader extracts each protocol field with a quote the kernel verifies (V-QUOTE, against the full text, or the
  abstract for a study with no full text: those values carry `sufficient=low`). A field marked `source: record` in
  the schema (where the study was published, its type) is quoted from the study's bibliographic record instead
  (title, authors, venue, year, publication type), which a paper's text rarely states; every other field only
  from the text, and an effect's numbers never from the record; a record value must be what its quoted line says. A
  value whose quote is not verbatim is kept as a problem, never as evidence; a field the study does not report is
  `not_reported` with a quote of the section searched. The gate names each study and field without a verified
  value, and the rerun reads only those fields again (a study with another problem in full; everything else keeps its
  extraction); at the edge limit P2 asks (`stage_blocked`) and P1 marks those values missing as a limitation. An
  attempt interrupted by an hourly cap goes on from the studies already read.

```bash
uv run rk run perovskite --until read_extract   # or --until protocol / search_screen
uv run rk protocol show [ID]                    # the protocol in force (else the latest draft), probe, versions, deviations
uv run rk prisma [--attempt N]                  # PRISMA flow + V-PRISMA + notes; selection reasons for critical reviews
uv run rk screening list [--phase title_abstract|full_text] [--final]   # each screener's decisions, or the finals
uv run rk extraction show [ITEM]                # usable values with their quote notes; set-aside rows with their problem
```

| Checkpoint | When | Answers |
|---|---|---|
| `protocol_approval` | a protocol draft passed its checks (P2) | `{"decision": "approve"}` (optional `"record_cap": N`) freezes it; `revise` with a `comment` reruns the stage; `back` (optional `comment`) returns to question |
| `screening_disagreements` | the screeners disagree on up to 25 records, in either phase (P2) | `{"decisions": {record_id: "include"\|"exclude"}}`, one per record of the checkpoint |

In the Workbench the protocol and disagreement checkpoints have their own cards (every protocol section; each record
with its abstract or full-text excerpt and both screeners' reasons). The Progress page shows the PRISMA flow with its
V-PRISMA result and notes (or the selection reasons), and each library item shows its extraction: the values with links
to their quote notes, and apart from them the rows that are not evidence.

A recorded run: `docs/demo/plan5a-evidence.md`.

## Synthesis, argument and design

After read_extract, `synthesize` turns the extracted evidence into findings, `argue` builds and attacks an argument on
them, and, for a research proposal, `design` ties falsifiable hypotheses to a method design. Their back-edges
(synthesize→read_extract, argue→synthesize, argue→question, design→argue, design→question) follow the same limit of
two uses per project. A stage a project fixed (a reproduction's seeded discovery stages, see Evaluation) is never
reopened: a back-edge to it fails the run, in P1 and P2.

- **Synthesize.** The Arguer scores every study on seven evidence criteria and drafts the synthesis; the kernel
  computes every number. A systematic review whose frozen protocol pre-specifies a meta-analysis (typed: effect
  measure RR, OR, RD, MD or SMD, `pool_model` REML, DL or FE, HKSJ, prediction-interval df, and each outcome mapped to
  its extraction fields) gets a pooling plan; you approve each comparable group in `pooling_approval` (P1: a vote).
  With a random-effects model (REML or DL) the planner and the P1 vote are told that differences in dose, route,
  population, duration or setting are heterogeneity the model and its pre-specified subgroup and sensitivity analyses
  address, not grounds for not pooling (`docs/eval/synthesis-2026-11-13.md`).
  The kernel pools it with `rk.stats` (by default REML, and HKSJ when τ² > 0 and k > 2, as in the Cochrane Handbook
  6.5.1), draws the forest plot and writes the `stat_result` claim itself; V-STATS recomputes each from its
  extraction rows. Pooling stays off until `rk eval stats --write-config` has validated the current `rk.stats`
  (`docs/eval/stats-2026-11-08.md`: 180/180 published values reproduced). Every study with a computable effect is in
  its group; a group that is not comparable, has fewer than two computable studies or lacks a field falls back to the
  narrative synthesis, and every fallback is an analysis deviation from the frozen protocol and a limitation. A study
  of a group missing a value its effect measure needs sends synthesize back to read_extract once (per study and field
  set, per protocol version), and read_extract reads only that study again. A scoping review gets an evidence map.
  In a systematic review, whatever its method, an outcome may map a field pair (`intervention` and `comparator`
  fields; a meta-analysis outcome's means or arm risks), and the Ideator is told to map one for each outcome. For each
  outcome with a pair, the kernel counts the studies whose values are higher, lower or the same with the
  intervention: vote counting by direction of effect (SWiM), a `stat_result` claim checked by V-STATS and V-NUM,
  never a pooled estimate or a significance test, and a table in the exported paper. An outcome without a pair gets
  no count. A study whose values are not two numbers is left out of the count with the reason (a limitation). The
  Arguer writes a finding about several studies citing every study it counts (or worded for the studies it cites) and
  puts statements about the evidence itself (sparse, not pooled, heterogeneous) in its scope limits; past 400 notes it
  is given an equal share of each study's notes, every study still listed with its values.
  Each finding is a claim with its evidence, checked by V-SUPPORT (its judges read the passages grouped by study, each
  headed by the study's label, title, year, design and arms; what they quote must come from the passages, not the
  headings), V-NUM (which also accepts a count of the studies the claim cites) and V-HEDGE; every claim that fails the
  claim gate goes to you in `uncertain_support` (P2): withdraw it, or call it supported when the judges were its only
  problem. Both synthesize cards also offer "Back to read_extract", naming the studies to read again. A new synthesize
  attempt (a rerun or argue→synthesize) keeps, word for word with its evidence, every finding of the attempt before
  that still passes the claim gate and cites only usable studies; the Arguer writes only the others, and the kept
  findings are verified again as new claims. To change or drop a kept finding, tick it under "Findings to write
  again" on synthesize's rerun or argue's "Back to synthesize" (`"revise": [finding ids]`): the next attempt gives it
  to the Arguer to write again as your comment says. Nothing else releases one, and P1 keeps them all.
- **Argue.** The Arguer states the main claim (a new claim, or a supported synthesis finding reused by id), its
  reasons and the warrant of each reason; the Skeptic (another vendor) raises objections, tests every warrant and
  names conflicts between supported claims, round after round, until no new objection of priority 1 or 2 arrives. The
  Arguer answers each objection (a new claim, a concession or
  an invalid-objection reason). A conflict goes to you in `evidence_conflict` (P1: a vote); a claim with a conceded
  priority-1 objection or a failed check is narrowed or withdrawn, a conceded priority-1 objection on a reason link
  drops the link instead, and the limitations list the withdrawn and narrowed claims, the conceded objections and the
  dropped links. The kernel's `stat_result` findings (pooled results, direction counts) are reused by id, never
  restated; a claim may say how many of the studies it cites it rests on.
  At the end you say in `uncertain_support` whether the main claim answers the frozen RQ (P1: the judges; when they
  say it does not, argue goes back to question, or to synthesize when the question is fixed, with the Arguer's
  synthesis requests as guidance), and you withdraw or support each claim (the findings included) that fails the
  argument's gate. Before that, P1 judges decide whether the Arguer's synthesis requests go back to synthesize, shown
  how many included studies have usable values for each field and outcome. When every finding is withdrawn, argue
  goes back to synthesize.
- **Design (research proposals).** The Ideator ties hypotheses (each with what would falsify it) to design items and
  measurements and states its feasibility claims; the kernel checks the links both ways, verifies the feasibility
  claims and asks for your `design_approval` (P1: a vote). The approved design is frozen and pre-registers the
  hypotheses it tests. When the Ideator says the research question is untestable, that is only its input: your
  `design_approval` shows its reason and offers back to the question (P1: the judges vote testable or untestable).
  A draft whose hypothesis references cannot be kept is refused and the design reruns; refusals stay on
  design→design, so at its limit you are asked (`stage_blocked`; P1 degrades) instead of reopening argue.

```bash
uv run rk run melatonin --until argue           # or --until synthesize / design (research proposals)
uv run rk synthesis show [--attempt N]          # assessments, groups, statistics and figures with V-STATS, findings, fallbacks
uv run rk argument show [--claim ID]            # claims (role, status, hedge), warrants and tests, objections, conflicts
uv run rk design show [--attempt N]             # the design draft or the frozen design, hypotheses, feasibility claims
uv run rk eval stats --write-config             # validate rk.stats against published meta-analyses; enables pooling
```

| Checkpoint | When | Answers |
|---|---|---|
| `pooling_approval` | comparable pooling groups (P2) | `{"decisions": {"<group>": "pool"\|"no_pool"}}`, one per group |
| `uncertain_support` | the judges disagree on synthesis findings (P2) | `{"claims": {claim_id: "supported"\|"withdraw"}}`, or `{"decision": "rerun", "comment", "revise"?: [finding id]}` (reruns synthesize only; `revise`: kept findings to write again) |
| `uncertain_support` | the end of argue (P2) | `{"answers_rq": "yes"\|"no", "claims": {…}}`, `{"decision": "rerun", "comment"}`, or, when the Arguer asked synthesize for findings, `{"decision": "back", "to": "synthesize", "comment"?, "revise"?: [finding id]}` (a comment is required with `revise`) |
| `evidence_conflict` | two supported claims contradict each other (P2) | `{"decision": "a_holds"\|"b_holds"\|"both_contested"}` |
| `design_approval` | a design passed its checks (P2) | `{"decision": "approve", "claims": {…}}`, `revise` with a `comment`, or `back` |
| `design_approval` | the Ideator says the RQ is untestable (P2) | `{"decision": "question"}` (optional `comment`), `revise` with a `comment`, or `approve` when its draft passes its checks |

In the Workbench these checkpoints have their own cards, and the Argument page shows the claims, warrants, objections
and conflicts. A recorded run: `docs/demo/plan5b-synthesis-argument.md` (it reached synthesize, not argue; design was
shown only by the tests).

## Draft, revise, audit, export

After argue (and design, for a research proposal), four stages turn the argument into a manuscript you can submit:
`draft → revise → audit → export`.

- **Draft.** The Writer drafts each required section of the paper type, and the kernel writes the `disclosure`:
  - systematic and scoping reviews: abstract, introduction, methods, results, discussion, limitations, conclusion;
  - critical review: an introduction, one section per synthesis theme, gaps and agenda, a conclusion;
  - research proposal: background, gap, questions and hypotheses, methods, expected outcomes, risks.

  Every sentence ends with source markers:
  - `[[clm:clm_…]]`: a supported claim;
  - `[[obj:obj_…]]`: what the source of one of the argument's objections says (an outside work, such as a benchmark
    a Skeptic objection came from); it is exported as a citation of that source, which G-REF checks. The objection's
    text is the Skeptic's account of the source, never checked against it: it is paraphrased, never quoted, and the
    disclosure says so for each objection cited;
  - `[[run:evt_…]]` or `[[run:prt_…]]`: what the kernel ran (a search, the PRISMA counts, the frozen protocol);
  - `[[fig:…]]`, `[[stat:…]]`: a figure or statistic;
  - `[[plan:…]]`: the frozen design or a hypothesis (proposals only);
  - `[[inf]]`: an inference, worded tentatively;
  - `[[ck]]`: common knowledge, at most 1 sentence in 20.

  The kernel verifies every sentence. V-COVER checks the markers; V-NUM checks that every number is in a cited
  target, the protocol's own numbers included; V-HEDGE checks that a sentence is no stronger than the claims and
  objections it cites.
  A `[[…]]` that is not a marker fails its sentence and names the form to write. A failing section is retried, and a
  draft rerun keeps the sections that passed. When a section needs a claim the argument lacks, draft goes back to
  argue once; after that, a later request is refused and the gap is disclosed as a limitation. In P2 you decide first
  (`argue_request`): back to argue, or write the section with the gap as a limitation.
- **Revise.** The SimReader (another vendor) reads the manuscript as your audience and asks questions with a
  priority. The Writer revises a sentence, answers with a note, or declines; a revision is kept only if the section
  still verifies. Revise passes when every priority-1 question is revised or answered.
- **Audit.** These hard gates run on the whole manuscript:
  - G-REF (references and retractions), G-QUOTE (every quote note), G-COVER (every sentence);
  - V-BALANCE (contested claims and answered objections are shown), G-SUPPORT (every cited claim is supported) and
    G-HEDGE;
  - G-DISCLOSE: the disclosure lists the models, every human decision and vote, what no human reviewed, the
    limitations and the protocol deviations;
  - G-PREREG (systematic and scoping reviews), V-PRISMA where the paper type reports it, and V-STATS.

  A failure goes back to the stage that owns it (draft, argue or read_extract). A failure no stage can mend fails the
  run and names its owner. When every gate passes, you read the manuscript in `final_review` (P1: a vote).
- **Export.** Only an audited manuscript that you approved exports. pandoc, citeproc and tectonic write
  `paper.pdf`, `paper.docx`, `paper.md` and `paper.tex` with a bibliography; claim markers become citations of their
  evidence. The reproducible package (`package-<hash>.zip`) holds:
  - a `MANIFEST.json` with the SHA-256 of every file and the event-log head;
  - the audit report and the disclosure;
  - `extraction.csv` and `protocol.json`.

```bash
uv run rk run melatonin --until export          # draft, revise and audit run in rk serve; you answer final_review
uv run rk draft show [--section S --version N]  # every sentence with its verdict and problems
uv run rk revise show                           # the reader's questions and how each was handled
uv run rk audit show [--version N]              # the hard gates, the final review, whether it can export
uv run rk export [--open]                       # export the approved manuscript (the export stage runs in rk serve)
```

| Checkpoint | When | Answers |
|---|---|---|
| `argue_request` | a section needs claims the argument lacks (P2, once per section) | `{"decision": "back"}` to argue or `{"decision": "write"}` (the gap stated as a limitation); optional `comment` |
| `final_review` | the audit passed every hard gate (P2) | `{"decision": "approve"}`, `revise` with a `comment`, or `back` to draft, argue or read_extract (optional `comment`) |
| `stage_blocked` | a back-edge reached its limit | `{"decision": "allow_one_more"}` (an optional `comment` is guidance for the reopened stage) or `stop` |

Recorded runs are in `docs/demo/plan5c-manuscript.md`. Two projects exported there on 2026-11-09, with Claude Haiku 5.5
and GPT-6 Luna as the main generators (other models also answered; see the deviations there) and a stand-in
answering every checkpoint:
- the melatonin systematic review: 16-page PDF, 15 draft and 7 revise attempts;
- a research proposal for a trial on the same question: 19 pages.

In both runs the stand-in caught facts the gates cannot see, such as a misread denominator and a misstated power.

## Drivers and roles

Role tasks run on a configurable driver per role (`drivers` in `config.toml`; see `rk providers`):

| Driver | How it runs | Kernel tools reach it through |
|---|---|---|
| `claude_cli` | `claude -p`, all built-in tools off (`--tools ""`), user settings off (`--setting-sources ""`) | `rk mcp` (stdio MCP proxy) with a one-time task token |
| `codex_cli` | `codex exec`, read-only sandbox, shell/apps/browser features off, `--ignore-user-config` | `rk mcp`, tools pre-approved for that server only |
| `anthropic_api:<model>`, `openai_api:<model>` | HTTP API with your key (`config.toml` or `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) | the in-process tool loop |
| `ollama:<model>` | local `ollama serve` (cloud models via `:cloud`; output validated client-side) | the in-process tool loop |
| `omp_gateway:<provider/model>` | the default; `omp auth-gateway stdio` with omp's own logins (`rk models` lists its models). The gateway runs with omp's model fallback (`retry.modelFallback`) off, so a failing model fails instead of being answered by your `retry.fallbackChains`; a reply from another model than the one asked for is still refused, so a role never silently changes vendor | the in-process tool loop |
| `human` | turns the task into a checkpoint for you | — |

- Every task is bound to one project and to its role's tools (`src/rk/roles/policy.py`). Agents can never answer
  checkpoints, attach host files or manage tokens.
- Text from sources reaches models inside `untrusted` data blocks. Agents report embedded instructions with
  `rk_flag_injection`.
- Every attempt (input, output, model, duration) is an event in the project log. A task gets at most 3 attempts.
  Subscription CLIs, when chosen, are capped per hour (`calls_per_hour`, default claude 30, codex 20).
- Generator and reviewer must come from different model developers (`src/rk/llm/vendors.toml`). A model whose vendor
  is unknown (for example an `omp_gateway` model no vendor rule matches) can generate but never review; `rk models`
  and Settings → Models say so.

**Choosing models.** `rk models` lists every model each driver can run, with its vendor and whether it is usable now;
`rk driver set <role> <driver>` saves one (the vendor rules are checked first), and Settings → Models in the
Workbench does the same. Every model role defaults to `omp_gateway` (user decision 2026-11-09: omp's logins, not the
Claude Code / Codex CLI usage limits): the eight Claude roles (lead, scout, screener_a, reader, gapminer, ideator,
arguer, writer) `omp_gateway:anthropic/claude-haiku-5-5`; screener_b, skeptic and simreader
`omp_gateway:openai-codex/gpt-6.1-sol`; the judges as above; triage `ollama:deepseek-v4.1-flash:cloud`. So the
defaults need `omp` installed and logged in (`claude_cli` / `codex_cli` remain selectable), and no local model.

```bash
rk providers                                   # driver per role, vendor, availability, rule problems/warnings
rk models                                      # the models each driver can run, with their vendors
rk driver set judge omp_gateway:openai-codex/gpt-6-luna   # choose a role's model (vendor rules checked)
rk task run --role reader --driver omp_gateway:anthropic/claude-haiku-5-5 --instructions "…"   # one recorded role task
rk chat --driver claude_cli --role lead        # multi-turn chat with the role's tools (any driver)
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
