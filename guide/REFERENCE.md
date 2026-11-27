# How MEL Evidence works

The server's tools, the rules built into the code, the drift agent, the commands and the project's layout.

## How it works

```
Claude Code / Claude Desktop (or any MCP client)
        │  MCP over stdio
        ▼
mel-evidence server ─────────────────────────────── audit log (who, what, outcome)
  ├─ Documents   search_reports
  │                Chroma vector search, filtered by phase, country, project, year;
  │                results carry citations and index freshness, and gaps are named
  └─ Data        list_datasets · describe_dataset · aggregate · compare_rounds
                 find_indicators · get_indicator
                   DuckDB over Parquet; aggregates only; small groups suppressed;
                   indicator figures checked for usability; queries time out
                      ▲
                      │ reports/  (read through mel://drift/latest)
Drift agent (LangGraph, scheduled) ── plan → detect → investigate → summarise → alert
```

## What the assistant can use

| Tool | What it does |
|---|---|
| `search_reports` | Finds report passages by meaning, filtered by phase, source type, country, project and years, with citations |
| `list_datasets`, `describe_dataset` | Lists survey datasets, and the variables the assistant is allowed to use |
| `aggregate` | Counts, means, medians and shares of a variable, optionally by group; small groups are suppressed |
| `compare_rounds` | Checks two survey rounds for renamed, recoded or removed variables before they are compared |
| `find_indicators` | Searches the indicator tracker and summarises which projects have usable figures |
| `get_indicator` | Targets, actuals and % of target per period, marked usable or not, with any cautions |

It can also read four **resources** (the document list, valid filter values, data dictionaries and the latest drift report) and use the three **prompts** in [Using it](USING.md#ready-made-prompts). All tools are read-only.

## Design principles

| Principle | How it's built |
|---|---|
| **Fixed operations only** | The assistant never writes SQL. It picks a dataset, variable, statistic and breakdown, and a tested query does the rest |
| **No personal data returned** | The data dictionary is the access policy: `id` and `exclude` variables are never listed, queried or profiled, and groups under `MIN_CELL_SIZE` are suppressed without revealing their size |
| **Rules in code, not in instructions** | Dictionary roles, typed parameters and value checks are enforced by the server, whatever the assistant is told |
| **Citations in the tool output** | Title, page, phase, year and n come back with every passage, so the assistant has them to cite |
| **Only usable indicator data** | A period is usable only with a target above 0, a reported actual, real dates (IATI often uses 1900 as a placeholder), a % of target between 0 and 500, an actual that isn't just the baseline repeated, and whole numbers for counts. Usable figures can carry cautions (no published baseline, an actual of 0 or equal to the target, a wrong unit, figures copied between indicators, a period starting before the years asked about), and the assistant checks those against the reports |
| **One spelling per country** | `KENYA`, `Kenia` and `KE` are all stored as Kenya, so country filters find every donor's documents |
| **Logged** | Every call goes to `logs/audit.jsonl` with user, tool, arguments, outcome, result count and duration. Results themselves are not logged |
| **Protected** | Data queries stop after `QUERY_TIMEOUT_SECONDS`, and breakdowns are capped at 50 groups |
| **Freshness and gaps shown** | Search results say when the index was built, and an empty search says whether no documents match the filters or none were relevant |

These follow practices that teams running MCP in production describe, such as Jacaranda Health's talk at MCP Dev Summit Nairobi (November 2026), *Map the journey, not the API*.

**Why no LangChain in the server?** The MCP client is the agent: it decides which tools to call. The server only provides fixed tools, so it uses `chromadb`, `sentence-transformers` and `duckdb` directly, which keeps every step visible. LangGraph is used where an agent runs inside this project: the drift agent.

## The drift agent

A survey form changes between rounds: `yield_kg` becomes `maize_yield_kg`, "Female" becomes "F", land is recorded in square metres instead of acres. Nothing errors; the comparison is simply wrong.

```
plan ─► detect ─┬─ nothing changed ─► record_all_clear ─► END
                └─ drift found ─► investigate ─► summarise ─► alert ─► END
```

- **plan** pairs consecutive rounds of each project (baseline → midline → endline).
- **detect** finds possible renames, removed or added variables, recoded categories, type changes, likely unit changes (averages 5x apart) and jumps in missing answers.
- **investigate** looks up which indicators or scripts read each changed variable (`data/variable_usage.csv`) and raises the severity when something depends on it.
- **summarise** writes a plain-language alert with Claude if `ANTHROPIC_API_KEY` is set, otherwise with a template. Only findings are sent, never survey rows.
- **alert** writes `reports/drift_<time>.md` and `.json`; `reports/last_run.json` records when it last checked.

`mel-drift` exits with code 2 when high-severity drift is found, so a scheduler can react. To run it every hour with cron:

```
0 * * * * cd /FULL/PATH/TO/mel-mcp && venv/bin/mel-drift >> logs/drift.log 2>&1
```

## Commands

| Command | What it does |
|---|---|
| `mel-mcp-server` | Runs the MCP server (your assistant starts it for you) |
| `mel-ingest` | Builds or updates the report index; `--rebuild` starts over |
| `mel-search "query"` | Searches the index from the terminal (`--help` lists the filters) |
| `mel-prepare-survey` | Survey file (.dta, .csv or .parquet) → Parquet plus data dictionary |
| `mel-fetch-iati` | IATI Datastore → raw XML → indicator tracker and report list |
| `mel-prepare-iati` | IATI XML file → indicator tracker and report list |
| `mel-download-docs` | Downloads the listed reports (PDF and .odt) and adds them to the manifest |
| `mel-drift` | Runs the drift agent once |
| `mel-mcp-config <assistant>` | Prints the settings that connect an assistant ([Setting up](SETUP.md#connect-an-assistant)) |
| `mel-starter load` | Downloads the Kenya starter pack's reports and indexes them (`setup.sh --starter` runs it) |
| `mel-starter build` | Maintainers: checks every link in `starter/kenya-agriculture/reports.txt`, then rebuilds the pack from them |

Every command accepts `--help`.

## Project structure

```
src/mel_mcp/
├── config.py          settings from .env
├── countries.py       country names and codes, one spelling each
├── metadata.py        the document manifest and its vocabularies
├── reading.py         reading PDF and .odt pages, and chunking
├── documents.py       the Chroma index: updates, search, filters, citations
├── surveys.py         aggregate-only survey queries with small-group suppression
├── indicators.py      the indicator tracker and its data checks
├── tables.py          reading the data folder's CSV files
├── drift.py           comparing survey rounds
├── audit.py           audit log
├── server.py          the MCP server: tools and resources
├── prompts.py         the ready-made prompts
├── client_config.py   settings for connecting each assistant
├── starter.py         loading and building the starter pack
├── agents/
│   └── drift_agent.py LangGraph drift agent
└── ingest.py, search.py, prepare_survey.py, prepare_iati.py, fetch_iati.py,
    download_docs.py   commands
tests/                 tests on synthetic data only
starter/               the Kenya starter pack: report list and indicator data
setup.sh               one-step setup
examples/              templates and an example answer
guide/                 this manual
data_sources.md        public documents and datasets to try it with
```

## Tests

`pytest -q` runs the tests without API keys, model downloads or network access, using a fake search model and synthetic data with planted problems. They cover search filters and citations; that names, phone numbers and IDs can never be queried; small-group suppression; query timeouts; drift detection; the audit log; IATI fetching against a fake API; downloading reports; incremental indexing; OpenDocument files; country spellings; indicator data checks and cautions; and the server through a real MCP client session.

Code style is set by ruff. Before opening a pull request, run `ruff format` and `ruff check` from the project folder. GitHub runs the same checks and the tests on every push and pull request (`.github/workflows/tests.yml`).

## Known limitations

- **Answers vary between runs**, and there is no evaluation set yet to measure answer quality. This is the next step.
- **Differencing.** Comparing an overall result with visible groups can reveal something about a suppressed group. Production systems add rate limits on overlapping queries or statistical noise.
- **Country tags apply to whole documents**, so a multi-country report tagged Kenya also returns passages about other countries. The assistant labels them, but they take up space in results.
- **Drift heuristics** can mistake a real programme change for drift; the agent flags these for a person to check.
- **Single user.** The audit log records the local user; a shared deployment would need real authentication.
- **No OCR**, so scanned PDFs can't be searched.

## Roadmap

- [ ] Evaluation set with gold answers, run after every change
- [ ] Country tags per passage, not per document
- [ ] Hybrid keyword and semantic search, for indicator codes like "IR 2.1"
- [ ] FGD and KII transcripts chunked by speaker turn
- [ ] Personal-data screening when reports are added (e.g. Microsoft Presidio)
- [ ] Drift alerts to Slack or email
