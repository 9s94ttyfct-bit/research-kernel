# Search fields: titles and abstracts (2026-11-12)

## Question

The kernel sends a strategy's queries to OpenAlex with `search=` and to Europe PMC as the bare query. OpenAlex
documents that `search=` on works matches the title, the abstract, the **full text** and the work's keywords
(<https://help.openalex.org/api/searching/>); a bare Europe PMC query also matches the full text of open-access
articles. Systematic-review searches are run on titles and abstracts. Does searching only titles and abstracts lose
the reviews' included studies, and how many records does it save?

## Measurement

For each of the six reproduction-set reviews, the selection's own Boolean query (its Europe PMC query with the
`TITLE_ABS:` wrapper removed), run in the review's publication years, counting the records and the review's included
studies found by DOI (86 of the 89 included studies have a DOI). Scripts: the scope and union experiments run on
2026-11-12 against the live APIs.

Records found:

| review | OpenAlex `search` | OpenAlex `search.title_and_abstract` | OpenAlex `search.title_abstract_keywords` | Europe PMC bare | Europe PMC `TITLE_ABS:` |
|---|---|---|---|---|---|
| cade-colonoscopy | 1268 | 72 | 74 | 296 | 14 |
| ozone-bleaching | 1087 | 33 | 34 | 174 | 27 |
| fructose-rats-sbp | 20943 | 837 | 880 | 7051 | 456 |
| tfa-heated-oils | 34722 | 1226 | 1315 | 5811 | 372 |
| ai-pulmonary-hypertension | 13960 | 820 | 882 | 6662 | 371 |
| ai-prostate-mri | 11326 | 622 | 688 | 4195 | 345 |

Included studies found by the two sources together (of those with a DOI):

| review | now (OpenAlex `search` + Europe PMC bare) | OpenAlex title/abstract/keywords + Europe PMC bare | OpenAlex title/abstract/keywords + Europe PMC `TITLE_ABS:` | OpenAlex title/abstract + Europe PMC `TITLE_ABS:` |
|---|---|---|---|---|
| cade-colonoscopy (6) | 6 | 6 | 6 | 6 |
| ozone-bleaching (3) | 3 | 3 | 3 | 3 |
| fructose-rats-sbp (22) | 22 | 22 | 22 | 21 |
| tfa-heated-oils (33) | 28 | 27 | 27 | 26 |
| ai-pulmonary-hypertension (12) | 12 | 12 | 12 | 12 |
| ai-prostate-mri (10) | 10 | 10 | 9 | 9 |
| **total (86)** | **81** | **80** | **79** | **77** |

Searching OpenAlex in titles, abstracts and keywords and Europe PMC in titles and abstracts finds 79 of the 81
included studies the full-text search finds (two lost: one tfa study, one prostate study), with about 6 to 32 times
fewer records on each source (mostly 12 to 26). With the full-text search the same queries are far above the
3000-record cap on both sources for four of the six reviews; scoped, none is.

### The strategies the runs froze

The same comparison on the strategies two projects froze in a P1 rerun on `7753799` (written and narrowed under the
full-text search; OpenAlex, Europe PMC and PubMed, PubMed unchanged in both), counting the review's included studies
any source found:

| review | included studies found, full text | included studies found, titles and abstracts |
|---|---|---|
| cade-colonoscopy (6) | 5 | 4 |
| tfa-heated-oils (33) | 5 | 3 |

Two things follow. These strategies lose recall when moved to titles and abstracts, for two reasons. cade's long
unquoted queries find almost nothing there (`deep learning colonoscopy adenoma detection rate randomized controlled`:
601 OpenAlex records in full text, 6 in titles, abstracts and keywords; 268 in Europe PMC, 0 with `TITLE_ABS:`).
tfa's four frozen queries already are AND-of-OR groups with quoted phrases, yet Europe PMC with `TITLE_ABS:` returns
0 for all four (31, 26, 75 and 5 in full text). Europe PMC does not stem quoted phrases (measured, no years:
`TITLE_ABS:"trans fatty acid"` 1074 records, `TITLE_ABS:"trans fatty acids"` 2679, the two OR'd 3286;
`TITLE_ABS:("trans fatty acid" AND frying)` 50, `TITLE_ABS:("trans fatty acids" AND frying)` 112), so the singular
phrase the queries use misses the titles and abstracts that say "acids"; their other concepts may also contribute to
the zeros (not measured one by one). And their recall is poor
in either scope (tfa: 5 of 33), far below the selection query's 28 of 33: narrowing a strategy until its full-text
count fits the cap cuts the included studies out. The change is therefore tested by its effect on the runs: the next
rerun reports each entry's recall against the 0.85 target, and its frozen strategies are measured in both scopes
again to separate the effect of the scope from that of the Scout's instructions.

## Decision

The kernel searches titles and abstracts: OpenAlex with `search.title_abstract_keywords` (titles, abstracts and the
keywords OpenAlex tags a work with; OpenAlex's own default on openalex.org) for searches, counts and year counts, and
Europe PMC with the query inside `TITLE_ABS:( … )` for searches and counts. A Europe PMC query that already names a
search field (`TITLE_ABS:`, `TITLE:`, `ABSTRACT:`, `KW:`, `MESH:`, `AUTH:`) is sent as written, so the wrap is
idempotent and a query written with its own fields (the reproduction set's Europe PMC queries) keeps them. One
convention everywhere: production searches, the record-cap probe, discovery searches and year counts all go through
`rk.sources.openalex` and `rk.sources.europepmc`. PubMed (no full text; its automatic term mapping stays), arXiv
(all fields) and Semantic Scholar are unchanged.

The protocol's search-execution text and the judges' rules sheet name the fields each source searches
(`SEARCHED_IN` in `rk.production.protocol`), so the judges and the human see it. Strategies written for the full-text
search lose recall in titles and abstracts (a long unquoted query found no Europe PMC record; strategies narrowed to
fit the cap under the full-text search dropped included studies), so the Scout's instructions (`STRATEGY_TASK`,
`src/rk/roles/scout.md`) ask for queries of a few concepts joined by AND, each an OR group of synonyms and spelling
variants, with only the concepts every eligible record names in its title or abstract, and for narrowing by
tightening a concept or dropping the broadest query, never by adding a concept.
