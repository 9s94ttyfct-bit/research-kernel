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
included studies the full-text search finds (two lost: one tfa study, one prostate study), with 10 to 25 times fewer
records on each source. With the full-text search the same queries are far above the 3000-record cap on both sources
for four of the six reviews; scoped, none is.

## Decision

The kernel searches titles and abstracts: OpenAlex with `search.title_abstract_keywords` (titles, abstracts and the
keywords OpenAlex tags a work with; OpenAlex's own default on openalex.org) for searches, counts and year counts, and
Europe PMC with the query inside `TITLE_ABS:( … )`. PubMed (no full text; its automatic term mapping stays) and arXiv
are unchanged. The protocol's search-execution text says so, so the judges and the human see it.
