# MEL Evidence MCP

An MCP server that lets an AI assistant such as Claude answer questions about a development programme's evidence: evaluation reports, survey data and indicator targets. Every claim comes with a citation, the assistant can only see totals and averages from survey data (never individual answers), and indicator figures are checked for data problems before they are used.

Built for monitoring, evaluation and learning (MEL) teams. Runs on your own computer, with your own files.

**What an answer looks like:** [examples/kenya-agriculture-delays.md](examples/kenya-agriculture-delays.md) answers *"What are the most common reasons Kenyan agriculture projects reported delays or missed targets since 2020, and do the indicators show the shortfalls?"* from public IATI data. It groups the reasons into themes with cited examples, shows which donors report each theme, checks every tracker figure against the reports, and grades how strong the evidence is.

## Quick start

On macOS or Linux, with Python 3.11 or 3.12 installed:

```bash
git clone https://github.com/tiwipaddler/mel-mcp.git
cd mel-mcp
./setup.sh --starter
```

This installs everything, runs the tests and loads the Kenya starter pack: 25 real reports from 12 Kenyan agriculture projects since 2020, downloaded from their publishers, and those projects' indicator data. The pack comes with the repo (`starter/kenya-agriculture`), so there is nothing extra to download first. As each report downloads, its link is shown, so you can open it at the source and check the answers against it. Indexing the reports takes from a few minutes on a recent laptop to about half an hour on an older one; the progress shows the time left.

Then connect your assistant ([step 6](#6-connect-an-assistant)) and ask:

> What are the most common reasons Kenyan agriculture projects reported delays or missed targets since 2020, and do the indicators show the shortfalls?

When you're ready for your own data, see [step 3](#3-load-your-data).

---

## Contents

1. [Before you start](#1-before-you-start)
2. [Install](#2-install)
3. [Load your data](#3-load-your-data)
4. [Build the search index](#4-build-the-search-index)
5. [Check that it works](#5-check-that-it-works)
6. [Connect an assistant](#6-connect-an-assistant)
7. [Ask questions](#7-ask-questions)
8. [Keeping it up to date](#8-keeping-it-up-to-date)
9. [Troubleshooting](#9-troubleshooting)
10. [How it works](#10-how-it-works)

---

## 1. Before you start

You need:

- **Python 3.11 or 3.12.** Not 3.13: PyTorch, which the search model needs, doesn't support it on every machine. Check with `python3.11 --version`.
- **Git**, to download the project.
- **An MCP client:** [Claude Code](https://docs.claude.com/en/docs/claude-code) (recommended) or Claude Desktop.
- **Node.js** (optional), only for the MCP Inspector test in step 5.
- **About 2 GB of disk space** for the Python packages and the search model.

Tested on macOS (Intel) and Linux.

You can load any of three kinds of evidence, and you don't need all of them:

| Evidence | What you provide | What the assistant can do with it |
|---|---|---|
| **Reports** | PDF or OpenDocument (.odt) files, plus a spreadsheet describing each one | Search them by meaning and quote them with page citations |
| **Indicator tracker** | A CSV of targets and actuals, or public IATI data | Compare targets with actuals and flag doubtful figures |
| **Survey data** | Cleaned Stata (.dta), CSV or Parquet files, one per survey round | Calculate totals, averages and shares by group, with small groups hidden |

> **Privacy.** The server never shows individual survey answers, but it does show report text and indicator figures. Only load files you are allowed to share with the AI service you connect.

---

## 2. Install

From a terminal on macOS or Linux:

```bash
git clone https://github.com/tiwipaddler/mel-mcp.git
cd mel-mcp
./setup.sh
```

`setup.sh` finds Python 3.11 or 3.12, creates a private Python environment in a folder called `venv`, installs the packages, creates your settings file (`.env`), runs the tests and downloads the search model (about 400 MB). If anything is missing, it stops and says what to install. It is safe to run again, and it never replaces your `.env` or your data. Add `--starter` to also load the Kenya starter pack.

After setup, **in every new terminal window**, activate the environment before using any `mel-*` command:

```bash
cd mel-mcp
source venv/bin/activate
```

Your prompt shows `(venv)` when it is active. The rest of this guide assumes you are in the `mel-mcp` folder with the environment active; check with `pwd`, which should end in `/mel-mcp`.

On Windows, use WSL (Windows Subsystem for Linux) and run the same commands there.

<details>
<summary>Installing by hand instead</summary>

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt     # the exact versions this was tested with
pip install -e . --no-deps          # adds the mel-* commands
cp .env.example .env
pytest -q
```

</details>

### Settings (`.env`)

Everything has a working default. The settings you are most likely to change:

| Setting | Default | Change it when |
|---|---|---|
| `DOCS_DIR` | `docs` | Your reports live somewhere else, such as a shared or cloud drive |
| `MANIFEST_FILE` | `docs/manifest.csv` | Your report list lives somewhere else |
| `DATA_DIR` | `data` | Your indicator and survey files live somewhere else |
| `IATI_API_KEY` | empty | You want to fetch public IATI data (free key, see step 3) |
| `MIN_CELL_SIZE` | `10` | Your rules require a different minimum group size for survey results |

Paths can be relative to the project folder or absolute. **Put paths that contain spaces in double quotes**:

```
DOCS_DIR="/Users/me/Google Drive/My Drive/mel-docs"
MANIFEST_FILE="/Users/me/Google Drive/My Drive/mel-docs/manifest.csv"
```

Never put real API keys in `.env.example`, which is shared on GitHub. `.env` is not shared.

---

## 3. Load your data

Pick the parts you need. You can use your own files, public IATI data, or both.

If you loaded the starter pack, its files are in `docs/`, `data/` and `db/`. To add your own data alongside it, follow the steps below as they are. To start again with only your own data, delete those folders first with `rm -rf docs data db`.

### 3a. Reports (your own files)

1. Put the files in your docs folder (`docs/` unless you changed `DOCS_DIR`). Subfolders are fine.
   - **PDFs must contain text.** Scanned PDFs (pictures of pages) need OCR first; otherwise nothing can be found in them.
   - OpenDocument text files (.odt) work too, including their tables.
2. Describe each file in the manifest (`docs/manifest.csv`). Start from the template:

   ```bash
   cp examples/manifest.example.csv docs/manifest.csv
   ```

   Then edit it in Excel, Numbers, LibreOffice or a text editor, keeping one row per file:

   | Column | Required | What to put |
   |---|---|---|
   | `file` | yes | The path inside the docs folder, e.g. `kenya/endline_report.pdf` |
   | `title` | yes | The report's title, as it should appear in citations |
   | `study_phase` | yes | One of `formative`, `baseline`, `midline`, `endline`, `ex-post`, `monitoring`, `other` |
   | `source_type` | yes | One of `evaluation_report`, `learning_brief`, `donor_report`, `survey_report`, `fgd_transcript`, `kii_transcript`, `logframe`, `policy_brief`, `other` |
   | `project` | | The project name. Use the same spelling everywhere, so filters work |
   | `donor`, `implementing_partner` | | Organisation names |
   | `reporting_year` | | e.g. `2023` |
   | `country` | | Names or ISO codes. Separate several with `;`, e.g. `Kenya; Uganda`. Spellings such as `KENYA`, `Kenia` and `KE` are all read as Kenya |
   | `sample_size` | | The number of respondents (n), for surveys and evaluations. Leave blank otherwise |
   | `url` | | Where the report can be found |

   Files that aren't in the manifest are still indexed, with only their file name as a title, so filters and citations will be poor. Save the manifest as CSV (UTF-8).

### 3b. Reports and indicators from IATI (public data)

[IATI](https://iatistandard.org) is where donors publish their projects' results and reports. Every IATI project can give you an indicator tracker, and many link to their evaluation (A07) and results (A08) reports.

1. Get a free API key at <https://developer.iatistandard.org> and add it to `.env`. To keep it out of your terminal history, type `read -s IATI_KEY` (macOS and Linux), paste the key and press Enter, then run:

   ```bash
   echo "IATI_API_KEY=$IATI_KEY" >> .env
   ```

2. Fetch the projects. This example takes Kenyan agriculture projects (DAC sector 311) that link reports:

   ```bash
   mel-fetch-iati --country KE --sector 311 --with-docs --out data/raw/iati_kenya_agri.xml
   ```

   This saves the raw data and builds `data/indicators.csv` (the tracker) and `data/iati_documents.csv` (the list of linked reports). If you already have an IATI XML file, run `mel-prepare-iati yourfile.xml` instead.

3. See which reports are available, then download them:

   ```bash
   mel-download-docs --dry-run --since-year 2020
   mel-download-docs --since-year 2020 --limit 50
   ```

   Files go into `docs/iati/` and are added to the manifest for you, with project, donor, phase, year and country filled in. Check the new manifest rows; you can correct anything, and your edits are kept on later downloads. `--limit` sets how many to download per run, so you can run it again for more.

### 3c. Your own indicator tracker

If you track targets and actuals yourself, save them as `data/indicators.csv` with **one row per indicator per reporting period**. Start from the template:

```bash
cp examples/indicators.example.csv data/indicators.csv
```

| Column | What to put |
|---|---|
| `indicator_code` | A unique code for the indicator, the same on each of its rows, e.g. `PRJ-01/R1/I1` |
| `indicator_name` | What it measures, with the unit, e.g. `Farmers trained (Number)` |
| `unit` | `unit` for counts and amounts, `percentage` for percentages |
| `ascending` | `1` if higher is better, `0` if lower is better (e.g. post-harvest loss) |
| `project`, `country` | The project name, and ISO country codes separated by `;` |
| `baseline_year`, `baseline_value` | The starting point. Leave blank if unknown; the figure is then marked "check against the reports" |
| `period_start`, `period_end` | Dates as `YYYY-MM-DD` |
| `target`, `actual` | Numbers only, without commas or units |
| `source` | Where the figures come from |

`mel-prepare-iati` and `mel-fetch-iati` **replace** `data/indicators.csv`. If you combine your own tracker with IATI data, keep a copy of yours and add its rows back after each IATI update.

### 3d. Survey data

Convert each cleaned survey round, giving all rounds of the same study the same `--project`:

```bash
mel-prepare-survey baseline_clean.dta --name credit_baseline --title "Rural credit study, baseline" \
    --phase baseline --project "Rural credit study" --country Kenya --year 2017
mel-prepare-survey endline_clean.dta --name credit_endline --title "Rural credit study, endline" \
    --phase endline --project "Rural credit study" --country Kenya --year 2020
```

`--name` takes lowercase letters, digits and `_` only. The `\` at the end of a line continues the command on the next line.

**Then review `data/<name>.dictionary.csv` before connecting an assistant.** Its `role` column decides what the assistant can see:

| Role | Meaning |
|---|---|
| `group` | Can be used to break results down or filter (e.g. gender, county) |
| `measure` | Can be summarised (e.g. yield, income) |
| `id` | Never shown |
| `exclude` | Never shown |

The roles are first guesses. Anything that could identify a person (names, phone numbers, GPS points, village names in small samples, free-text answers) must be set to `exclude`. Results for groups smaller than `MIN_CELL_SIZE` are never shown.

**Optional: catching changes between survey rounds.** List which indicators or scripts read which variables in `data/variable_usage.csv` (template: `examples/variable_usage.example.csv`), then run `mel-drift`. It reports renamed, recoded or removed variables that would quietly break a comparison between rounds. See [The drift agent](#the-drift-agent).

---

## 4. Build the search index

Reports must be indexed before they can be searched:

```bash
mel-ingest
```

It shows five numbered steps with progress and a time estimate. The first run downloads the search model (about 400 MB) and reads every report, so expect several minutes; about 1,000 passages took 15 minutes on an older laptop. After that, `mel-ingest` only processes new or changed files, and a change to the manifest alone takes seconds.

Survey data and the indicator tracker don't need indexing.

---

## 5. Check that it works

1. **Run the tests** (about 20 seconds, no network or API keys needed):

   ```bash
   pytest -q
   ```

   Expect `125 passed`.

2. **Search from the terminal**, to check your reports were indexed:

   ```bash
   mel-search "reasons for delays" --country Kenya --year-from 2020
   ```

   You should see passages with their title, page, phase and year.

3. **Optional: try the tools by hand** in the MCP Inspector, a web page that lists the server's tools and lets you call them (needs Node.js):

   ```bash
   npx @modelcontextprotocol/inspector venv/bin/mel-mcp-server
   ```

   Click **Connect**, open **Tools**, and try `list_datasets` or `find_indicators` with `country` set to `Kenya`.

---

## 6. Connect an assistant

The server works with any assistant that can start a local MCP server on your computer (a "stdio" server). Each assistant keeps its settings in a different place, so the project includes a command that prints the right settings for yours, with your paths already filled in:

```bash
mel-mcp-config cursor
```

It prints where the settings go, then the settings to copy. Use the name for your assistant:

| Assistant | Name to use | What you do with the output |
|---|---|---|
| Claude Code | `claude-code` | Run the printed command |
| Codex CLI and the ChatGPT desktop app (they share settings) | `codex` | Run the printed command |
| Claude Desktop | `claude-desktop` | Paste into its config file, then restart it |
| Cursor | `cursor` | Paste into `~/.cursor/mcp.json` |
| VS Code (Copilot Chat, agent mode) | `vscode` | Paste into `.vscode/mcp.json` in the folder you open |
| Gemini CLI | `gemini` | Paste into `~/.gemini/settings.json` |
| LM Studio (models running on your computer) | `lm-studio` | Paste into its `mcp.json` (Program tab, Edit mcp.json) |
| Anything else | `other` | Give your assistant the command and `MEL_HOME` setting shown |

If a settings file already lists other servers, add the `mel-evidence` entry next to them rather than replacing the file. After changing settings, restart the assistant or start a new session.

**Start coding assistants from a separate folder.** Claude Code, Codex, Gemini CLI, Cursor and VS Code can read the files in the folder they are opened in. Open them in an empty folder, not in `mel-mcp`, so they can only reach your data through the server's tools, which apply the privacy and data-quality rules:

```bash
mkdir -p ~/mel-chat
cd ~/mel-chat
claude          # or codex, gemini; or open this folder in Cursor or VS Code
```

**Check it's connected.** In Claude Code, Codex and Gemini CLI, type `/mcp` to see the server and its tools. In the other apps, look for `mel-evidence` in the tools or MCP list. If it shows as failed, see [Troubleshooting](#9-troubleshooting).

**Which model to use.** The answers depend on the model following several steps and long tool descriptions. It was built and tested with Claude. Other strong tool-using models should work. Small local models often skip steps or run out of context; in LM Studio, choose a model that supports tool use and give it a long context (at least 16,000 tokens).

**Web chat apps can't use it yet.** claude.ai, ChatGPT on the web and similar apps can only connect to servers hosted on the internet, and this server runs on your computer. Use one of the apps above.

---

## 7. Ask questions

Just ask in plain language. Name the place and years when they matter, so the assistant filters by them:

- *"What reasons did Kenyan agriculture projects give for delays since 2020?"*
- *"Compare what the baseline and endline reports say about access to credit."*
- *"What share of households used fertiliser at endline, by gender?"*
- *"Can I compare yields between baseline and endline?"* (the assistant checks for renamed or recoded variables first)
- *"How is the maize yield indicator doing against target, and do the reports explain why?"*

### Ready-made prompts

The server includes three prompts that set out a thorough method step by step:

| Prompt | Use it for | Arguments |
|---|---|---|
| `evidence_summary` | A full evidence review: themes with cited examples, a donor-by-theme matrix, tracker figures checked against the reports, and a grade for the evidence | country, first year |
| `compare_phases` | What changed between two study phases on a topic | topic, two phases |
| `indicator_report` | One indicator against its targets, with explanations from the reports | indicator code |

In Claude Code, prompts are slash commands. **Claude Code splits arguments at spaces, so type single words only**, then give your question when Claude asks for it:

```
/mcp__mel-evidence__evidence_summary Kenya 2020
```

Typing `/` shows the available prompts. In Claude Desktop, choose prompts from the + menu and fill in each argument.

Not every assistant shows MCP prompts. If yours doesn't, the tools still work: ask in plain language, or paste the steps from `EVIDENCE_STEPS` in `src/mel_mcp/prompts.py` before your question.

### Reading the answers

- **Citations** give the title, page (where the file has page numbers), study phase, year and sample size (n), and the report's link when the manifest has one. For a PDF the link opens at the cited page in most browsers. Reports from IATI usually have no sample size.
- **Indicator figures from IATI** come with a link to the activity's page on d-portal, where the published targets and results can be checked.
- **Your own reports and figures** have no web link. A report without a link in the manifest is cited by its file in your documents folder (for example `documents folder: surveys/endline.pdf`), and a figure from your own tracker by the tracker and the row's `source`.
- **Indicator figures** are only used when they pass data checks (a real target, a reported actual, real dates, a plausible % of target, whole numbers for counts). Figures that pass but look odd, such as having no baseline or an actual exactly equal to the target, are checked against the reports, and the report's figure is used if they disagree.
- **Gaps are stated.** If the evidence doesn't cover a question, the answer should say so, rather than guess.
- **Answers vary between runs**, like any AI answer. For important questions, use the `evidence_summary` prompt, and check key figures against the cited pages.

Every tool call is logged in `logs/audit.jsonl` (who asked, which tool, with which filters, how many results). To see the latest calls:

```bash
tail -20 logs/audit.jsonl
```

---

## 8. Keeping it up to date

| You changed | Run | Then |
|---|---|---|
| Added, replaced or deleted reports | `mel-ingest` | Start a new assistant session |
| Edited the manifest | `mel-ingest` (seconds: only details are updated) | Start a new session |
| Updated IATI data | `mel-fetch-iati ...` or `mel-prepare-iati file.xml`, then `mel-download-docs --since-year 2020` and `mel-ingest` | Start a new session |
| Changed countries for IATI reports | `mel-download-docs --update-countries`, then `mel-ingest` | Start a new session |
| Added a survey round | `mel-prepare-survey ...`, review its dictionary, optionally `mel-drift` | Start a new session |
| Edited `data/indicators.csv` | Nothing | It's read fresh on every question |
| Changed `EMBEDDING_MODEL`, `CHUNK_SIZE` or `CHUNK_OVERLAP` | `mel-ingest` (it rebuilds everything automatically) | Start a new session |
| Updated this project's code (`git pull`) | `./setup.sh` | Start a new session |

**Why a new session?** Each assistant session starts its own copy of the server, which keeps the search index it opened at the start. Close the session and start a new one to see changes.

---

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `pip install` fails on `torch` | Use Python 3.11 or 3.12: `python3.11 -m venv venv`. On Intel Macs, keep `torch==2.2.2` and `numpy==1.26.4` from `requirements.txt` |
| `command not found: mel-ingest` | Activate the environment: `source venv/bin/activate` (from the project folder) |
| `manifest.csv is missing column(s)` | The manifest needs at least `file`, `title`, `study_phase` and `source_type` |
| `study_phase '...' is not one of ...` | Use one of the listed values exactly, e.g. `endline`, not `Endline report` |
| A folder path with spaces isn't found | Put it in double quotes in `.env` |
| `IATI API error 401` or `403` | Check `IATI_API_KEY` in `.env` and that your IATI subscription is active |
| `Too many open files` when running tests (macOS) | Run `ulimit -n 1024` in that terminal, then `pytest -q` again |
| A report never comes up in searches | It may be a scanned PDF with no text, or missing from the manifest. Check with `mel-search` and its title |
| The assistant can't see new reports or fixes | Start a new assistant session (see step 8) |
| The assistant shows the server as failed | Run `mel-mcp-config <assistant>` again and check your settings match it exactly, including the full path to `venv/bin/mel-mcp-server`. Check that `pytest -q` passes |
| The assistant never uses the tools | Name them in your question ("Using the mel-evidence tools, ..."). With a local model, choose one that supports tool use |
| A prompt got only part of your question | Claude Code splits prompt arguments at spaces. Type single words (`Kenya 2020`) and give the question when asked |

---

## 10. How it works

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

### What the assistant can use

| Tool | What it does |
|---|---|
| `search_reports` | Finds report passages by meaning, filtered by phase, source type, country, project and years, with citations |
| `list_datasets`, `describe_dataset` | Lists survey datasets, and the variables the assistant is allowed to use |
| `aggregate` | Counts, means, medians and shares of a variable, optionally by group; small groups are suppressed |
| `compare_rounds` | Checks two survey rounds for renamed, recoded or removed variables before they are compared |
| `find_indicators` | Searches the indicator tracker and summarises which projects have usable figures |
| `get_indicator` | Targets, actuals and % of target per period, marked usable or not, with any cautions |

It can also read four **resources** (the document list, valid filter values, data dictionaries and the latest drift report) and use the three **prompts** in step 7. All tools are read-only.

### Design principles

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

### The drift agent

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

### Commands

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
| `mel-mcp-config <assistant>` | Prints the settings that connect an assistant (step 6) |
| `mel-starter load` | Downloads the Kenya starter pack's reports and indexes them (`setup.sh --starter` runs it) |
| `mel-starter build` | Maintainers: checks every link in `starter/kenya-agriculture/reports.txt`, then rebuilds the pack from them |

Every command accepts `--help`.

### Project structure

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
tests/                 125 tests, synthetic data only
starter/               the Kenya starter pack: report list and indicator data
setup.sh               one-step setup
examples/              templates and an example answer
data_sources.md        public documents and datasets to try it with
```

### Tests

`pytest -q` runs 125 tests without API keys, model downloads or network access, using a fake search model and synthetic data with planted problems. They cover search filters and citations; that names, phone numbers and IDs can never be queried; small-group suppression; query timeouts; drift detection; the audit log; IATI fetching against a fake API; downloading reports; incremental indexing; OpenDocument files; country spellings; indicator data checks and cautions; and the server through a real MCP client session.

Code style is set by ruff. Before opening a pull request, run `ruff format` and `ruff check` from the project folder.

### Known limitations

- **Answers vary between runs**, and there is no evaluation set yet to measure answer quality. This is the next step.
- **Differencing.** Comparing an overall result with visible groups can reveal something about a suppressed group. Production systems add rate limits on overlapping queries or statistical noise.
- **Country tags apply to whole documents**, so a multi-country report tagged Kenya also returns passages about other countries. The assistant labels them, but they take up space in results.
- **Drift heuristics** can mistake a real programme change for drift; the agent flags these for a person to check.
- **Single user.** The audit log records the local user; a shared deployment would need real authentication.
- **No OCR**, so scanned PDFs can't be searched.

### Roadmap

- [ ] Evaluation set with gold answers, run after every change
- [ ] Country tags per passage, not per document
- [ ] Hybrid keyword and semantic search, for indicator codes like "IR 2.1"
- [ ] FGD and KII transcripts chunked by speaker turn
- [ ] Personal-data screening when reports are added (e.g. Microsoft Presidio)
- [ ] Drift alerts to Slack or email

## Sources and licence

[data_sources.md](data_sources.md) lists public reports and datasets to try the tool with, and where to download them. MIT licence: see [LICENSE](LICENSE).
