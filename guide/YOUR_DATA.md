# Your data

Loading reports, indicator data and surveys, building the search index and keeping it up to date. Install first: see [Setting up](SETUP.md).

## Load your data

Pick the parts you need. You can use your own files, public IATI data, or both.

If you loaded the starter pack, its files are in `docs/`, `data/` and `db/`. To add your own data alongside it, follow the steps below as they are. To start again with only your own data, delete those folders first with `rm -rf docs data db`.

### Reports (your own files)

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

### Reports and indicators from IATI (public data)

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

### Your own indicator tracker

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

### Survey data

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

**Optional: catching changes between survey rounds.** List which indicators or scripts read which variables in `data/variable_usage.csv` (template: `examples/variable_usage.example.csv`), then run `mel-drift`. It reports renamed, recoded or removed variables that would quietly break a comparison between rounds. See [The drift agent](REFERENCE.md#the-drift-agent).

## Build the search index

Reports must be indexed before they can be searched:

```bash
mel-ingest
```

It shows five numbered steps with progress and a time estimate. The first run downloads the search model (about 400 MB) and reads every report, so expect several minutes; about 1,000 passages took 15 minutes on an older laptop. After that, `mel-ingest` only processes new or changed files, and a change to the manifest alone takes seconds.

Survey data and the indicator tracker don't need indexing.

## Keeping it up to date

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
