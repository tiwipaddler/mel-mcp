"""Prompts: step-by-step methods the user can pick, e.g. as slash commands in Claude Code."""

from mcp.server.fastmcp import FastMCP

from mel_mcp.countries import COUNTRY_NAMES, canonical_country, country_code

EVIDENCE_STEPS = """\
1. Search the reports from several angles (at least four search_reports calls).{filters} Keep \
examples from the country asked about; label any passage about another country, or leave it out.
2. Group the findings into themes. For each, give two or three examples with citations (title, \
page where available, phase, year, n).
3. Show a matrix: one row per theme, one column per donor, marking which donors' documents \
report it.
4. Indicator check. Call find_indicators for every project you cited (by project name, or by \
country) and read its 'projects' summary. For EVERY cited project that has usable indicators, \
call get_indicator on the ones related to your themes. Never use periods that aren't usable, \
and never work out a shortfall from them.
5. Reconcile before presenting. For each usable figure, search the reports for the same \
indicator; do this always for figures marked check_against_reports. Then show ONE table per \
project with columns: indicator | tracker target, actual, % | report figure (cited) | match? | \
figure used. Where they disagree, use the report's figure and say why. Don't show tracker \
figures anywhere else first. If reports give different figures for the same indicator, use the \
latest: the completion report first, then the latest progress report, and name the version \
used. For figures with a 'no baseline' caution, look for the report's own baseline or % rating \
and use it.
6. List the cited projects with no usable indicator figures. From the 'projects' summary, say \
whether each is missing from the tracker or is in it with no usable figures, and why (call \
find_indicators with include_unusable=true for the reasons). For those, run one more \
search_reports for headline results (for example 'achieved against target', 'hectares', \
'beneficiaries reached') and give any figures the reports state, cited to the report, not the \
tracker.
7. Say how many projects had usable tracker data and how many tracker figures the reports \
confirmed. Grade the evidence separately for the reasons (themes) and for the size of the \
shortfalls (strong, moderate, limited), one line each. Note gaps: places, phases, donors or \
groups the evidence doesn't cover.
Do not present qualitative quotes as prevalence, or a single project as a pattern."""


def _argument_checks(country: str, year_from: str, question: str):
    """Claude Code splits prompt arguments at spaces, so check each one looks like what it is.

    Returns the problems to raise with the user, and the country and year that are valid.
    """
    problems = []
    if country and country_code(country) not in COUNTRY_NAMES:
        problems.append(
            f"'{country}' isn't a country name, so the arguments were probably split at spaces. "
            "Ask the user which country they mean."
        )
        country = ""
    year = year_from.strip()
    since = int(year) if year.isdigit() and len(year) == 4 else None
    if year_from and since is None:
        problems.append(
            f"'{year_from}' isn't a year, so the arguments were probably split at spaces. "
            "Ask the user which year the evidence should start from, if any."
        )
    if len(question.split()) < 4:
        problems.append(
            "The question is missing or looks cut off (some apps pass only the first word of "
            "each argument). Before any search, ask the user to type their full question, then "
            "follow the steps below."
        )
    return problems, country, since


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt()
    def compare_phases(
        topic: str, earlier_phase: str = "baseline", later_phase: str = "endline"
    ) -> str:
        """Compare what the evidence says about a topic at two study phases."""
        return (
            f"Compare the evidence on '{topic}' between the {earlier_phase} and the "
            f"{later_phase}.\n\n"
            f"1. Call search_reports separately with study_phase='{earlier_phase}' and "
            f"study_phase='{later_phase}'.\n"
            "2. Call list_datasets. If datasets for both phases cover this topic, call "
            "compare_rounds on them first, then describe_dataset and aggregate for the numbers.\n"
            "3. Present a short comparison: what changed, what stayed the same, and where "
            "evidence is missing for one phase.\n"
            "4. Cite every point with title, page, phase and n. Flag renamed or recoded "
            "variables and differences in sample or method that weaken the comparison."
        )

    @mcp.prompt()
    def indicator_report(indicator_code: str) -> str:
        """Report an indicator's performance against target, with explanations."""
        return (
            f"Report on indicator {indicator_code}.\n\n"
            "1. Call get_indicator. If no period is usable, say so and why (data_quality), "
            "and don't report the figures as results.\n"
            "2. Call search_reports with the indicator's name and project. Look for "
            "explanations of under- or over-performance, and check the figures of every "
            "period with cautions. Where a report gives a different figure, use the report's.\n"
            "3. Show baseline, target, actual and % of target for each usable period in a "
            "table, with the source of each figure (tracker, or report title and page). "
            "Respect the indicator's direction.\n"
            "4. Summarise in three to five sentences, citing sources, and say clearly when the "
            "reports don't explain a result. Note how recently the tracker was updated."
        )

    @mcp.prompt()
    def evidence_summary(country: str = "", year_from: str = "", question: str = "") -> str:
        """Answer a question from reports and indicators, with a donor-by-theme matrix and
        one table per cited project reconciling tracker and report figures. In Claude Code,
        type single words only (/mcp__mel-evidence__evidence_summary Kenya 2020) and give
        the question when asked."""
        problems, country, since = _argument_checks(country, year_from, question)
        filters = []
        if country:
            filters.append(f"country='{canonical_country(country)}'")
        if since:
            filters.append(f"year_from={since}")
        filter_text = (
            f" Pass {' and '.join(filters)} to every search_reports, find_indicators and "
            "get_indicator call that accepts them."
            if filters
            else ""
        )
        first = "".join(f"FIRST: {p}\n" for p in problems)
        steps = EVIDENCE_STEPS.format(filters=filter_text)
        return f"{first}Question: {question or '(not given yet)'}\n\n{steps}"
