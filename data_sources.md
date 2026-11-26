# Public Data Sources

This project uses only public data. None of it is stored in this repository
(`docs/` and `data/` are gitignored): download each file from its source,
check its licence, and cite it as the source asks.

The suggested set below sits on one theme, **smallholder agriculture in Kenya
and Malawi**, so that documents, survey data and indicators can answer
questions together.

## Evaluation reports (→ `docs/`, described in `docs/manifest.csv`)

| Document | Why it's useful | Link |
|---|---|---|
| Ndegwa, Ward, Shee and You (2025), *Rural credit, food security, and resilience: An empirical evaluation from Kenya*, IFPRI Discussion Paper 2351 | Randomised trial in Machakos County; pairs with the panel data below | https://hdl.handle.net/10568/175990 |
| 3ie Impact Evaluation Report 138: *An impact assessment of EAMDA's banana initiative to increase technology adoption by smallholder farmers in Kenya* (2022) | Technology adoption by Kenyan smallholders | https://www.3ieimpact.org/sites/default/files/2023-06/IE138-TW4.1027-EAMDA-Kenya.pdf |
| 3ie Impact Evaluation Report 122: Maertens, Mhango and Michelson (2020), demonstration plots and warehouse receipts in Malawi | Soil fertility adoption, yields and income | https://doi.org/10.23846/TW4IE122 |
| One Acre Fund insights and data library | Midline and endline reports of RCTs in Kenya and Rwanda | https://oneacrefund.org (Insights and data library) |

## Survey datasets (→ `mel-prepare-survey`)

| Dataset | Phases | Link |
|---|---|---|
| Replication data for *Rural credit, food security, and resilience* (IFPRI, 2025) | Baseline and midline (face to face), endline (phone), 2017 to 2020 | IFPRI Africa datasets page: https://africa.ifpri.info/datasets/ |
| Malawi Africa RISING Baseline Evaluation Survey (IFPRI) | Baseline | https://doi.org/10.7910/DVN/28557 |

Clean the data first (missing-value codes, labels, merges), for example with
the R workflow project, then convert one file per survey round:

Give every round of the same study the same `--project`, so the drift agent
compares them in order (baseline → midline → endline):

```bash
mel-prepare-survey endline_clean.dta --name kenya_credit_endline \
    --title "Rural credit RCT, endline household survey" --phase endline \
    --project "Rural credit RCT" --country Kenya --year 2020 \
    --url https://africa.ifpri.info/datasets/
```

**Review the generated `data/<name>.dictionary.csv` before starting the server.**
Even public datasets can contain fields that identify people in small groups.

## Indicator tracker (→ `mel-fetch-iati` or `mel-prepare-iati`)

IATI activity files publish projects' results frameworks: indicators with a
baseline and a target and actual for each period. Not every publisher reports
results, so some activities will add nothing to the tracker.

**Through the API** (free key from https://developer.iatistandard.org; the
Exploratory tier allows 5 calls a minute and 100 a week):

```bash
mel-fetch-iati --country KE --sector 311 --since 2018-01-01   # 311 = agriculture
```

The raw XML is saved in `data/raw/`, so you can rebuild the tracker later with
`mel-prepare-iati` without calling the API again.

The same step lists each project's **evaluation (A07) and results (A08)
reports** in `data/iati_documents.csv`. IATI only holds links to these: the
PDFs live on the publishers' own websites. To download them and add them to
your manifest:

```bash
mel-download-docs --dry-run --since-year 2020     # see what's listed
mel-download-docs --since-year 2020 --limit 10    # PDF and .odt files; web pages are skipped
mel-ingest
```

Then check the new rows in `docs/manifest.csv`: the study phase is guessed
from each title, and sample sizes have to be read from the reports.

Multi-country programmes list every country they cover, for example
`Uganda; Kenya`, and a country filter finds them under each one. For manifest
rows added before this, run `mel-download-docs --update-countries` once, then
`mel-ingest`. It changes only the country, keeping your other edits.

Many publishers (the World Bank, for example) give no document dates in IATI.
The year is then worked out from the World Bank document ID or a date in the
URL or title, and `year_source` in `data/iati_documents.csv` says which.
Documents whose year can't be worked out are skipped by `--since-year` unless
you add `--include-undated`.

Some publishers use OpenDocument text (`.odt`) instead of PDF; the UK
government's FCDO publishes its annual reviews, project completion reviews and
evaluations this way. These are downloaded and indexed too, with tables kept
row by row. An `.odt` file has no fixed pages unless it was saved with page
breaks, so citations to it may name the document without a page number.

**By hand:** use IATI Datastore Search
(https://iatistandard.org/en/iati-tools-and-resources/iati-datastore/), download
activities as **XML**, then:

```bash
mel-prepare-iati downloaded_activities.xml
```
