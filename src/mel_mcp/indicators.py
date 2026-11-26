"""The indicator tracker (data/indicators.csv): targets and actuals per reporting period.

Every period is checked before its figures can be used as evidence. Issues make a period
unusable; cautions mean its figures should be checked against the reports first.
"""

import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from mel_mcp.countries import COUNTRY_NAMES, country_code
from mel_mcp.tables import DataError, modified, number, read_csv

MAX_INDICATORS = 25
MAX_PROJECTS = 40
# Above this, % of target usually means wrong units or cumulative totals.
MAX_PCT_OF_TARGET = 500
# IATI publishers often use 1900 as a placeholder date.
EARLIEST_PLAUSIBLE_YEAR = 1990

USABLE_RULE = (
    "A period is usable when it has a target above 0, a reported actual, real dates, a % of "
    f"target between 0 and {MAX_PCT_OF_TARGET}, an actual that isn't just the baseline repeated, "
    "and a whole number when the indicator counts things."
)
CAUTION_RULE = (
    "A usable period can still carry cautions (no published baseline, an actual of 0 or exactly "
    "equal to the target, a wrong unit, figures identical to another indicator, or a period that "
    "starts before year_from). Check those figures against the reports before citing them, and "
    "prefer the report's figure when the two disagree."
)

COUNT_WORDS = re.compile(
    r"\b(number|count|farmers|households|people|persons|beneficiaries|youth|women)\b"
)
NOT_A_COUNT = re.compile(
    r"\(\s*(ton|tons|ha|kg|%)"
    r"|\b(percentage|percent|proportion|share|rate|ratio|average|mean|per|index|score)\b|%"
)


def counts_things(name: str) -> bool:
    """Whether an indicator's actual has to be a whole number, e.g. 'Number of farmers'."""
    lower = name.lower()
    return bool(COUNT_WORDS.search(lower)) and not NOT_A_COUNT.search(lower)


def project_code(indicator_code: str) -> str:
    """The activity identifier an IATI indicator code starts with, e.g. 'XI-IATI-WBTF-P117635'."""
    return indicator_code.split("/R")[0] if "/R" in indicator_code else ""


def activity_page(source: str) -> str | None:
    """The d-portal page for an IATI activity, from a tracker source like 'IATI: <id>'."""
    if not source.startswith("IATI:") or not (activity := source[5:].strip()):
        return None
    return f"https://d-portal.iatistandard.org/ctrack.html#view=act&aid={quote(activity, safe='')}"


def project_matches(wanted: str, project: str, indicator_code: str) -> bool:
    """Match by full or partial project name, or by all or part of its IATI identifier."""
    wanted = wanted.strip().lower()
    return bool(wanted) and (
        wanted in project.lower() or wanted in project_code(indicator_code).lower()
    )


def _year(date: str) -> int | None:
    return int(date[:4]) if date[:4].isdigit() else None


def in_years(row: dict, year_from: int | None) -> bool:
    """Keep periods that end in or after year_from.

    A missing, impossible or placeholder end date counts as unknown, so the period is kept
    and later marked unusable. Otherwise a project with placeholder end dates would vanish
    instead of showing up as 'in the tracker, nothing usable'.
    """
    end = row.get("period_end", "")
    if not year_from or not _is_date(end) or _year(end) < EARLIEST_PLAUSIBLE_YEAR:
        return True
    return _year(end) >= year_from


def period_key(row: dict) -> tuple[str, str, str]:
    return (row.get("indicator_code", ""), row.get("period_start", ""), row.get("period_end", ""))


def copied_figures(rows: list[dict]) -> dict[tuple[str, str, str], list[str]]:
    """Map each period (see period_key) to other indicators in the same project with the
    same period, target and non-zero actual. Identical figures usually mean one was copied."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        target, actual = number(r.get("target", "")), number(r.get("actual", ""))
        if target in (None, 0) or actual in (None, 0):
            continue
        key = (
            project_code(r.get("indicator_code", "")),
            r.get("period_start"),
            r.get("period_end"),
            target,
            actual,
        )
        groups.setdefault(key, []).append(r)

    copies: dict[tuple[str, str, str], list[str]] = {}
    for same in groups.values():
        if len({r["indicator_code"] for r in same}) < 2:
            continue
        for r in same:
            others = {
                f"{o.get('indicator_name')} ({o['indicator_code']})"
                for o in same
                if o["indicator_code"] != r["indicator_code"]
            }
            copies[period_key(r)] = sorted(others)
    return copies


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value[:10])
    except ValueError:
        return False
    return True


def _issues(row: dict, target, actual, achieved, baseline) -> list[str]:
    issues = []
    start, end = row.get("period_start", ""), row.get("period_end", "")
    for label, value in (("start", start), ("end", end)):
        if value and not _is_date(value):
            issues.append(
                f"The period {label} date '{value}' isn't a real date. "
                "Correct it in the tracker (YYYY-MM-DD)."
            )
        elif (year := _year(value)) and year < EARLIEST_PLAUSIBLE_YEAR:
            issues.append(
                f"A period {label} date of {year} looks like a placeholder, not a real date. "
                "Find the period's dates in the reports, or leave the period out."
            )
    if not start and not end:
        issues.append(
            "A period has no dates, so it can't be placed in time. Find its dates in the reports."
        )
    elif not end:
        issues.append(
            "A period has no end date, so it can't be placed in time. Find the end date in the "
            "reports, or leave the period out."
        )

    for field, parsed in (("target", target), ("actual", actual)):
        raw = row.get(field, "")
        if raw and parsed is None:
            issues.append(
                f"The {field} '{raw}' isn't a number the tracker can use. Correct it in the "
                "tracker, or take the figure from the reports."
            )
    if target is None and actual is None and not (row.get("target") or row.get("actual")):
        issues.append(
            "Neither a target nor an actual is reported for this period. Look for both in the "
            "reports, or leave the period out."
        )
    elif target not in (None, 0) and actual is None and not row.get("actual"):
        issues.append(
            "No actual is reported for this period, so progress can't be measured. Find the "
            "result in the reports, or say that none has been reported."
        )
    if target is not None and target < 0:
        issues.append(
            "The target is negative, so % of target can't be read. Check its sign in the tracker."
        )
    if target == 0 and actual in (None, 0):
        issues.append(
            "Target and actual are both 0 or missing: probably nothing was reported. Leave the "
            "period out, and look for the figures in the reports."
        )
    elif target == 0:
        issues.append(
            "The target is 0, so % of target can't be calculated. Find the target in the reports."
        )
    if target is None and actual is not None:
        issues.append(
            "An actual is reported without a target. Ask the publisher for a target, or find it "
            "in the reports."
        )
    if achieved is not None and not 0 <= achieved <= MAX_PCT_OF_TARGET:
        issues.append(
            f"% of target is {achieved}: check the units, and whether a figure is a cumulative "
            "total, in the reports."
        )
    if baseline not in (None, 0) and actual == baseline:
        issues.append(
            "The actual equals the baseline, so it may be the baseline repeated, not a result. "
            "Find the period's result in the reports."
        )
    small_percentage = row.get("unit") == "percentage" and max(target or 0, actual or 0) <= 100
    if (
        actual is not None
        and actual != int(actual)
        and counts_things(row.get("indicator_name", ""))
        and not small_percentage
    ):
        issues.append(
            f"The actual {actual} isn't a whole number, but this indicator counts things: "
            "the figure is probably estimated or miscalculated. Find the figure in the reports."
        )
    return issues


def _cautions(row: dict, target, actual, copies, year_from) -> list[str]:
    cautions = []
    if target not in (None, 0) and actual is not None:
        if number(row.get("baseline_value", "")) is None:
            cautions.append(
                "No baseline is published, so % of target assumes the starting point was 0. "
                "If this measures something that existed before the project, find the baseline "
                "in the reports: progress may be much smaller."
            )
        if actual == target:
            cautions.append(
                "The actual exactly equals the target. Sometimes the target is copied into the "
                "actual: confirm in the reports."
            )
        if actual == 0:
            cautions.append(
                "The actual is 0, which can mean nothing was achieved or nothing was reported. "
                "Confirm in the reports."
            )
        if row.get("unit") == "percentage" and max(target, actual) > 100:
            cautions.append(
                "Coded as a percentage, but the figures are above 100, so they are probably "
                "counts. Treat the unit as unknown."
            )
    if copies:
        cautions.append(
            "Same period, target and actual as "
            + "; ".join(copies)
            + ": one may have been copied from the other. Check which the reports support."
        )
    start_year = _year(row.get("period_start", ""))
    if year_from and start_year and EARLIEST_PLAUSIBLE_YEAR <= start_year < year_from:
        cautions.append(
            f"This period starts in {start_year}, before {year_from}, so the figure includes "
            "earlier results."
        )
    return cautions


def assess_period(
    row: dict,
    baseline: float | None,
    copies: list[str] | None = None,
    year_from: int | None = None,
) -> dict:
    target, actual = number(row.get("target", "")), number(row.get("actual", ""))
    achieved = None
    if target not in (None, 0) and actual is not None:
        achieved = round(100 * actual / target, 1)
    issues = _issues(row, target, actual, achieved, baseline)
    usable = target is not None and target > 0 and actual is not None and not issues
    return {
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "target": target,
        "actual": actual,
        "pct_of_target": achieved,
        "usable": usable,
        "issues": issues,
        "cautions": _cautions(row, target, actual, copies, year_from) if usable else [],
    }


def _codes(row: dict) -> list[str]:
    return [c.strip().upper() for c in row.get("country", "").split(";") if c.strip()]


def _countries(row: dict) -> list[str]:
    return [COUNTRY_NAMES.get(c, c) for c in _codes(row)]


def _evidence_rule(periods: list[dict]) -> str:
    usable = sum(p["usable"] for p in periods)
    if usable == len(periods):
        rule = "All periods are usable as evidence."
    elif usable:
        rule = (
            f"Only the {usable} period(s) marked usable may be used as evidence. Mention the "
            "others only to explain gaps."
        )
    else:
        return (
            "No period is usable: don't use these figures as evidence or work out a shortfall "
            "from them. Say the tracker has no usable figures for this indicator and why."
        )
    if any(p["cautions"] for p in periods):
        rule += (
            " Some usable periods carry cautions: check those figures against the reports "
            "before citing them, and use the report's figure if they disagree."
        )
    return rule


class IndicatorTracker:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "indicators.csv"

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            raise DataError("No indicator tracker found (data/indicators.csv).")
        return read_csv(self.path)

    def find(
        self,
        search: str = "",
        project: str | None = None,
        country: str | None = None,
        include_unusable: bool = False,
        year_from: int | None = None,
    ) -> dict:
        """Indicators matching the search, plus a summary of which projects have usable data.

        Only usable indicators are listed unless include_unusable is set. The project summary
        counts every match, so it also shows projects with no usable data at all.
        """
        rows = self._rows()
        copies = copied_figures(rows)
        wanted_country = country_code(country) if country else None
        terms = search.lower().split()
        found: dict[str, dict] = {}

        for row in rows:
            code = row.get("indicator_code", "")
            if not in_years(row, year_from):
                continue
            if project and not project_matches(project, row.get("project", ""), code):
                continue
            if wanted_country and wanted_country not in _codes(row):
                continue
            if not all(t in f"{code} {row.get('indicator_name', '')}".lower() for t in terms):
                continue

            entry = found.setdefault(
                code,
                {
                    **{k: row.get(k, "") for k in ("indicator_code", "indicator_name", "unit")},
                    "project": row.get("project", ""),
                    "activity_page": activity_page(row.get("source", "")),
                    "countries": _countries(row),
                    "periods": 0,
                    "periods_with_actuals": 0,
                    "usable_periods": 0,
                    "issues": set(),
                    "cautions": set(),
                },
            )
            if any(row.get(k) for k in ("period_start", "period_end", "target", "actual")):
                entry["periods"] += 1
            target, actual = number(row.get("target", "")), number(row.get("actual", ""))
            if actual is not None and not (actual == 0 and target in (None, 0)):
                entry["periods_with_actuals"] += 1
            period = assess_period(
                row,
                number(row.get("baseline_value", "")),
                copies.get(period_key(row)),
                year_from,
            )
            entry["usable_periods"] += period["usable"]
            entry["issues"].update(period["issues"])
            entry["cautions"].update(period["cautions"])

        indicators = list(found.values())
        for e in indicators:
            e["usable"] = e["usable_periods"] > 0
            e["issues"] = sorted(e["issues"])
            e["cautions"] = sorted(e["cautions"])
            e["check_against_reports"] = bool(e["cautions"])

        projects = _project_summary(indicators)
        if include_unusable:
            shown = sorted(indicators, key=lambda e: not e["usable"])
        else:
            shown = [e for e in indicators if e["usable"]]

        out = {
            "total_matches": len(shown),
            "shown": min(len(shown), MAX_INDICATORS),
            "indicators": shown[:MAX_INDICATORS],
            "projects": projects[:MAX_PROJECTS],
            "projects_with_usable_data": sum(p["usable_indicators"] > 0 for p in projects),
            "usable_rule": USABLE_RULE,
            "caution_rule": CAUTION_RULE,
        }
        if year_from:
            out["year_from"] = year_from
        notes = []
        if hidden := len(indicators) - len(shown):
            notes.append(
                f"{hidden} matching indicator(s) have no usable period and are left out. Don't "
                "use them as evidence; pass include_unusable=true only to explain why a project "
                "has no usable figures."
            )
        if len(shown) > MAX_INDICATORS:
            notes.append(
                f"Only the first {MAX_INDICATORS} of {len(shown)} are shown. "
                "Narrow the search with words, project or country to see the rest."
            )
        if len(projects) > MAX_PROJECTS:
            notes.append(
                f"Only {MAX_PROJECTS} of {len(projects)} projects are summarised; "
                "filter by country or project to see the rest."
            )
        if notes:
            out["note"] = " ".join(notes)
        return out

    def get(self, code: str, period: str | None = None, year_from: int | None = None) -> dict:
        """Baseline, targets and actuals for one indicator, each period checked for usability."""
        all_rows = self._rows()
        rows = [r for r in all_rows if r.get("indicator_code") == code]
        if not rows:
            raise DataError(
                f"No indicator with code '{code}'. Use find_indicators to search by name."
            )
        if period:
            rows = [
                r
                for r in rows
                if r.get("period_start", "").startswith(period)
                or r.get("period_end", "").startswith(period)
            ]
            if not rows:
                raise DataError(f"Indicator '{code}' has no period matching '{period}'.")
        if year_from:
            rows = [r for r in rows if in_years(r, year_from)]
            if not rows:
                raise DataError(f"Indicator '{code}' has no period ending in or after {year_from}.")

        first = rows[0]
        descending = first.get("ascending", "").lower() in ("0", "false", "no")
        baseline = number(first.get("baseline_value", ""))
        copies = copied_figures(all_rows)
        periods = [
            assess_period(r, baseline, copies.get(period_key(r)), year_from)
            for r in sorted(rows, key=lambda r: r.get("period_start", ""))
        ]
        issues = sorted({i for p in periods for i in p["issues"]})
        return {
            "indicator_code": code,
            "indicator_name": first.get("indicator_name"),
            "unit": first.get("unit"),
            "project": first.get("project"),
            "project_code": project_code(code),
            "countries": _countries(first),
            "baseline": {"year": first.get("baseline_year") or None, "value": baseline},
            "direction": "lower is better" if descending else "higher is better",
            "periods": periods,
            "usable": any(p["usable"] for p in periods),
            "usable_periods": sum(p["usable"] for p in periods),
            "evidence_rule": _evidence_rule(periods),
            "check_against_reports": any(p["cautions"] for p in periods),
            "source": first.get("source"),
            "activity_page": activity_page(first.get("source", "")),
            "tracker_updated": modified(self.path),
            "data_quality": issues or ["No problems detected."],
            "note": (
                "For a 'lower is better' indicator, an actual below target is good, "
                "so pct_of_target under 100 is not a shortfall."
                if descending
                else None
            ),
        }


def _project_summary(indicators: list[dict]) -> list[dict]:
    """One entry per activity. Two activities can share a name, so group by the IATI
    identifier and fall back to the name only when an indicator code has none."""
    projects: dict[str, dict] = {}
    for e in indicators:
        p = projects.setdefault(
            project_code(e["indicator_code"]) or e["project"],
            {
                "project": e["project"],
                "project_code": project_code(e["indicator_code"]),
                "activity_page": e["activity_page"],
                "countries": e["countries"],
                "usable_indicators": 0,
                "unusable_indicators": 0,
            },
        )
        p["usable_indicators" if e["usable"] else "unusable_indicators"] += 1
    return sorted(projects.values(), key=lambda p: (-p["usable_indicators"], p["project"]))
