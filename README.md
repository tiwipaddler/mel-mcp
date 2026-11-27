# MEL Evidence MCP

[![Tests](https://github.com/tiwipaddler/mel-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/tiwipaddler/mel-mcp/actions/workflows/tests.yml)

**Ask your programme's evidence a question. Get an answer you can check.**

MEL Evidence connects an AI assistant such as Claude to your evaluation and progress reports, your indicator tracker and your survey data. Every claim comes back with its source, and a link that opens the report at the page cited. Indicator figures are checked against the reports before they are used. Survey data is only ever seen as totals and averages.

Built for monitoring, evaluation and learning (MEL) teams. It runs on your own computer, with your own files.

<!-- Demo GIF goes here: an answer in Claude Code with its citation links and the tracker-against-report table. Save it as guide/demo.gif and add: <p align="center"><img src="guide/demo.gif" alt="..." width="800"></p> -->

## Why MEL teams need it

How long a portfolio question takes depends on where the evidence sits. If the figure is already tracked and cleaned, pulling it out is quick. If it needs new data collection or a formal evaluation, it takes months, and no tool changes that. Most questions land in between: the evidence already exists, spread across projects' progress reports, completion reports and indicator trackers, and it has to be read, reconciled and checked before anyone can rely on it.

That checking is harder than it looks. Norad's [practical guide to portfolio-level MEL](https://norad.no/contentassets/edb6d72917a84b70bf1bed3283586efc/evaluation_brief_a-practical-guide-to-monitoring-evaluation-and-learning-at-a-portfolio-level.pdf) (2024) notes that "it's challenging to use standardised indicators effectively" and that "the evidence may be contradictory or ambiguous". In practice:

- **Indicator trackers are often wrong in ways nobody notices.** A target gets copied into the actual column, dates are left as placeholders, or a count of registered farmers is reported as beneficiaries. The tracker says 100% and the report says 0%.
- **General AI chat tools answer fluently but can't be checked.** They don't show where a figure came from, they mix up projects and study phases, and they shouldn't be handed raw survey data.

MEL Evidence works on that middle ground. It reads across the reports, checks the tracker against them, and shows you every source.

## What it found in 25 public reports

The [Kenya starter pack](starter/kenya-agriculture/README.md) loads 25 public reports from 12 agriculture projects, with the projects' indicator data from IATI. Asked why the projects were delayed or missed targets, and whether the indicators show it, the assistant [answered with themes and cited examples](examples/kenya-agriculture-delays.md), then checked the tracker against the reports. Some of what it caught:

| The indicator tracker says | The reports say |
|---|---|
| Enable Youth: 1,250 of 1,250 trained in business incubation, and $50m of $50m leveraged through the Risk Guarantee Fund (both 100%) | 0% progress on both ([progress report, June 2025, p. 4](https://www.afdb.org/sites/default/files/documents/projects-and-operations/kenya-_enable_youth_kenya-_p-ke-aaz-014-ipr-june_2025.pdf#page=4)) |
| AEFPF-K: 3,228,491 farmers received certified seed (248% of target) | That is the number registered on the e-voucher system. 389,080 beneficiaries are attributed to the project ([completion report, p. 5](https://www.afdb.org/sites/default/files/documents/projects-and-operations/kenya-_african_emergency_food_production_facility-kenya_aefpf-k-_p-ke-a00-002-pcr-november_2024.pdf#page=5)) |
| AEFPF-K: 293,442 t of fertiliser distributed (49% of target) | The final figure is 341,651 t, 57% ([completion report, p. 5](https://www.afdb.org/sites/default/files/documents/projects-and-operations/kenya-_african_emergency_food_production_facility-kenya_aefpf-k-_p-ke-a00-002-pcr-november_2024.pdf#page=5)) |
| Kenya Water Security and Climate Resilience Project: 25 indicators | None can be used: every period "ends" in 1900 and most targets are 0. The results are in the [completion report](https://documents.worldbank.org/curated/en/099122425102515384/pdf/BOSIB-24e62aba-6021-41bf-82e8-bc2d425ba9ef.pdf#page=17) instead |

Each link opens the report at the page cited, so you can check a finding yourself in a few seconds.

## Try it on the starter pack

On macOS or Linux, with Python 3.11 or 3.12:

```bash
git clone https://github.com/tiwipaddler/mel-mcp.git
cd mel-mcp
./setup.sh --starter
```

This installs everything, runs the tests, downloads the 25 reports from their publishers (each report's link is shown as it downloads) and indexes them. Indexing takes from a few minutes on a recent laptop to about half an hour on an older one. On Windows, see [Windows](guide/SETUP.md#windows-not-yet-tested), which hasn't been tested yet.

Then [connect your assistant](guide/SETUP.md#connect-an-assistant) and ask:

> What are the most common reasons Kenyan agriculture projects reported delays or missed targets since 2020, and do the indicators show the shortfalls?

Click any citation in the answer and check the page for yourself. When you're ready for your own data, see [Your data](guide/YOUR_DATA.md).

## What it does for you

- **Does the tracker match the reports?** Each figure is checked. Copied targets, missing baselines, impossible dates and implausible percentages are flagged, and the report's figure is used when the two disagree.
- **Why did our projects miss their targets?** Themes across projects and study phases, each with cited examples.
- **What do we already know about this?** A quick, cited answer for a donor, a new colleague or a programme manager.
- **Can we compare these two survey rounds?** Totals, averages and shares by group, after a check for variables that were renamed, recoded or measured in different units.
- **Where did this number come from?** Every figure carries its report, page and link, or the file in your own documents folder.

## What it will not do

- **Show anyone's individual survey answers.** The assistant only gets totals, averages and shares, and groups smaller than ten people are hidden. Names, phone numbers and IDs are marked in the data dictionary and can't be queried at all.
- **Write its own database queries.** It picks a dataset, a variable and a statistic, and a tested query does the rest (`surveys.py`).
- **Use a tracker figure without checking it.** A period counts only with a real target, a reported actual, real dates and a plausible result (`indicators.py`).
- **Make up a source.** Citations come from the tools, and the instructions forbid inventing a link. A report with no link is cited by its file.
- **Send your files anywhere.** It runs on your computer. Only the passages and figures the assistant asks for go to the AI service you connect, and every request is logged in `logs/audit.jsonl`.

It doesn't replace an evaluator's judgement. It finds, checks and cites the evidence, and deciding what that evidence means is still your job. Answers vary a little between runs, so check key figures against the cited pages. The [known limitations](guide/REFERENCE.md#known-limitations) list the rest.

## Guide

- [Setting up](guide/SETUP.md): what you need, installing on macOS, Linux or Windows, connecting an assistant, troubleshooting
- [Your data](guide/YOUR_DATA.md): loading reports, indicator data and surveys, building the search index, keeping it up to date
- [Using it](guide/USING.md): asking questions, the ready-made prompts, reading the answers
- [How it works](guide/REFERENCE.md): the tools, the rules built into the code, the drift agent, commands, tests, limitations and roadmap

## Contributing

Issues and pull requests are welcome. Issues labelled [good first issue](https://github.com/tiwipaddler/mel-mcp/labels/good%20first%20issue) are small and clearly described, and [help wanted](https://github.com/tiwipaddler/mel-mcp/labels/help%20wanted) marks the ones that need knowledge from outside the project, such as Windows. Every pull request runs the tests and lint, and gets a CodeRabbit review.

## Sources and licence

[data_sources.md](data_sources.md) lists public reports and datasets to try the tool with, and where to download them. MIT licence: see [LICENSE](LICENSE).
