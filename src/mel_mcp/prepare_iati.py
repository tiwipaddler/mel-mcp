"""Build the indicator tracker and the report list from IATI activity files.

Writes data/indicators.csv (targets and actuals per period) and data/iati_documents.csv
(evaluation and results reports, for mel-download-docs). Each run replaces both files.
"""

import argparse
import csv
import hashlib
import html
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from mel_mcp.config import settings
from mel_mcp.countries import canonical_country

# IATI IndicatorMeasure codelist
MEASURES = {"1": "unit", "2": "percentage", "3": "nominal", "4": "ordinal", "5": "qualitative"}
COLUMNS = (
    "indicator_code",
    "indicator_name",
    "unit",
    "ascending",
    "project",
    "country",
    "baseline_year",
    "baseline_value",
    "period_start",
    "period_end",
    "target",
    "actual",
    "source",
)

# IATI DocumentCategory codes worth indexing, and the manifest source_type each maps to.
DOC_CATEGORIES = {
    "A07": ("Review of project performance and evaluation", "evaluation_report"),
    "A08": ("Results, outcomes and outputs", "donor_report"),
}
DOC_COLUMNS = (
    "activity_id",
    "project",
    "donor",
    "implementing_partner",
    "country",
    "category",
    "category_name",
    "source_type",
    "study_phase",
    "title",
    "document_date",
    "reporting_year",
    "year_source",
    "format",
    "language",
    "url",
    "file",
)
IMPLEMENTING_ROLE = "4"  # IATI OrganisationRole: 4 = Implementing

PHASE_PATTERNS = (  # first match wins; checked against the document title
    ("baseline", re.compile(r"\bbase[- ]?line\b", re.I)),
    ("midline", re.compile(r"\bmid[- ]?(line|term)\b", re.I)),
    ("ex-post", re.compile(r"\bex[- ]post\b", re.I)),
    (
        "endline",
        re.compile(
            r"\b(end[- ]?line|end[- ]?term|final evaluation|completion|terminal evaluation)\b", re.I
        ),
    ),
    ("formative", re.compile(r"\bformative\b", re.I)),
    # Periodic progress reports, e.g. World Bank "Implementation Status and Results Report"
    ("monitoring", re.compile(r"\b(status|progress|monitoring|ISR|annual review)\b", re.I)),
)

# Dates hidden in document URLs and titles, used when IATI gives no document-date.
DATE_IN_TEXT = re.compile(r"(?<!\d)(?:\d{1,2}[-_/.]\d{1,2}[-_/.])?((?:19|20)\d{2})(?!\d)")
# New World Bank document IDs start with 099 followed by the date as MMDDYY,
# e.g. .../curated/en/099120624085030097/... was filed on 06 Dec 2024.
WORLD_BANK_ID = re.compile(
    r"documents\.worldbank\.org/curated/[a-z]{2}/099(\d{2})(\d{2})(\d{2})\d+/"
)


def _text(element: ET.Element | None, path: str = "narrative") -> str:
    """First narrative text under an element (IATI 2.x), or its own text (1.x)."""
    if element is None:
        return ""
    narrative = element.find(path)
    text = narrative.text if narrative is not None else element.text
    return " ".join((text or "").split())


def _attr(element: ET.Element | None, name: str) -> str:
    return "" if element is None else (element.get(name) or "").strip()


def _plain(text: str, limit: int = 200) -> str:
    """Text without HTML tags or entities: '&lt;p&gt;Report&lt;/p&gt;' becomes 'Report'."""
    text = re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(text or "")))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# Words that only name a unit. Some publishers (AfDB, for example) put the unit in the
# indicator's title, leaving titles like "Ton" or "Number" that say nothing on their own.
UNIT_WORDS = {
    "ton",
    "tons",
    "tonne",
    "tonnes",
    "t",
    "mt",
    "kg",
    "g",
    "km",
    "m",
    "ha",
    "hectare",
    "hectares",
    "number",
    "numbers",
    "no",
    "nb",
    "nbr",
    "num",
    "count",
    "unit",
    "units",
    "value",
    "amount",
    "percentage",
    "percent",
    "pct",
    "%",
    "rate",
    "ratio",
    "usd",
    "us$",
    "$",
    "eur",
    "kes",
    "ua",
    "people",
    "persons",
    "person",
    "households",
    "yes",
    "dollars",
    "million",
    "thousand",
    "per",
    "day",
    "year",
    "years",
    "month",
    "months",
    "liters",
    "litres",
    "l",
    "index",
    "score",
}


def is_unit_only(name: str) -> bool:
    """True for titles such as 'Ton', 'Number', 'Ton/Ha', '(%)' or 'Yes/No'."""
    words = re.findall(r"[a-z]+|[%$]", name.lower())
    return bool(words) and all(w in UNIT_WORDS for w in words)


def indicator_name(indicator: ET.Element, result: ET.Element) -> str:
    """The best available name: indicator title, then indicator description,
    then the result's title, then the result's description (publishers often
    leave titles blank). A title that only names a unit ('Ton') is combined with
    the first real description: 'Fertiliser distributed (Ton)'."""
    candidates = [
        _plain(_text(element.find(path)))
        for element, path in (
            (indicator, "title"),
            (indicator, "description"),
            (result, "title"),
            (result, "description"),
        )
    ]
    candidates = [c for c in candidates if c]
    if not candidates:
        return ""
    first = candidates[0]
    if is_unit_only(first):
        better = next((c for c in candidates[1:] if not is_unit_only(c)), None)
        if better:
            return f"{better} ({first})"
    return first


def activity_rows(activity: ET.Element, source: str) -> list[dict]:
    activity_id = (activity.findtext("iati-identifier") or "").strip()
    project = _text(activity.find("title"))
    country = ";".join(c.get("code", "") for c in activity.findall("recipient-country"))
    rows = []
    for r_i, result in enumerate(activity.findall("result"), 1):
        for i_i, indicator in enumerate(result.findall("indicator"), 1):
            base = {
                "indicator_code": f"{activity_id}/R{r_i}/I{i_i}",
                "indicator_name": indicator_name(indicator, result),
                "unit": MEASURES.get(_attr(indicator, "measure"), ""),
                "ascending": _attr(indicator, "ascending") or "1",  # IATI default: higher is better
                "project": project,
                "country": country,
                "baseline_year": _attr(indicator.find("baseline"), "year"),
                "baseline_value": _attr(indicator.find("baseline"), "value"),
                "source": source,
            }
            periods = indicator.findall("period")
            if not periods:
                rows.append(
                    {**base, "period_start": "", "period_end": "", "target": "", "actual": ""}
                )
            for period in periods:
                rows.append(
                    {
                        **base,
                        "period_start": _attr(period.find("period-start"), "iso-date"),
                        "period_end": _attr(period.find("period-end"), "iso-date"),
                        "target": _attr(period.find("target"), "value"),
                        "actual": _attr(period.find("actual"), "value"),
                    }
                )
    return rows


def guess_phase(title: str) -> str:
    for phase, pattern in PHASE_PATTERNS:
        if pattern.search(title or ""):
            return phase
    return "other"


def infer_year(
    document_date: str, title: str, url: str, latest: int | None = None
) -> tuple[str, str]:
    """The document's year and where it came from: the IATI document-date,
    a World Bank document ID, or a date in the URL or title. ("", "") if unknown."""
    latest = latest or datetime.now().year + 1
    if document_date[:4].isdigit():
        return document_date[:4], "document-date"
    match = WORLD_BANK_ID.search(url or "")
    if match:
        month, day, yy = (int(g) for g in match.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return str(2000 + yy), "world-bank-id"
    years = [int(y) for y in DATE_IN_TEXT.findall(f"{title} {url}") if 1990 <= int(y) <= latest]
    if years:
        return str(max(years)), "url-or-title"
    return "", ""


ODT_FORMAT = "application/vnd.oasis.opendocument.text"


def file_extension(fmt: str, url: str) -> str:
    """'odt' for OpenDocument text links (e.g. UK FCDO documents), otherwise 'pdf'.
    mel-download-docs checks the real content and corrects this if needed."""
    path = (url or "").lower().split("?")[0]
    return "odt" if (fmt or "").lower() == ODT_FORMAT or path.endswith(".odt") else "pdf"


def suggested_file(activity_id: str, title: str, url: str, extension: str = "pdf") -> str:
    """Readable, stable file name; the URL hash keeps it unique."""
    slug = re.sub(r"[^a-z0-9]+", "_", f"{activity_id} {title}".lower()).strip("_")[:70]
    return f"{slug}_{hashlib.md5(url.encode()).hexdigest()[:6]}.{extension}"


def activity_documents(activity: ET.Element, categories: tuple[str, ...]) -> list[dict]:
    """Document links in the chosen categories, anywhere in the activity (including results)."""
    activity_id = (activity.findtext("iati-identifier") or "").strip()
    # Every country the activity covers: multi-country programmes (e.g. FCDO's CASA)
    # must show up when someone filters by any one of them.
    names = []
    for element in activity.findall("recipient-country"):
        # The code is reliable; narratives vary ("KENYA", "Kenia"), so use them only without a code.
        name = canonical_country(_attr(element, "code") or _text(element))
        if name and name not in names:
            names.append(name)
    country = "; ".join(names)
    base = {
        "activity_id": activity_id,
        "project": _text(activity.find("title")),
        "donor": _text(activity.find("reporting-org")),
        "implementing_partner": "; ".join(
            _text(org)
            for org in activity.findall("participating-org")
            if org.get("role") == IMPLEMENTING_ROLE and _text(org)
        ),
        "country": country,
    }

    docs, seen = [], set()
    for link in activity.iter("document-link"):
        url = _attr(link, "url")
        codes = [c.get("code", "") for c in link.findall("category")]
        category = next((c for c in codes if c in categories), None)
        if not url or not category or url in seen:
            continue
        seen.add(url)
        title = _text(link.find("title"))
        date = _attr(link.find("document-date"), "iso-date")
        year, year_source = infer_year(date, title, url)
        name, source_type = DOC_CATEGORIES[category]
        docs.append(
            {
                **base,
                "category": category,
                "category_name": name,
                "source_type": source_type,
                "study_phase": guess_phase(title),
                "title": title,
                "document_date": date,
                "reporting_year": year,
                "year_source": year_source,
                "format": _attr(link, "format"),
                "language": _attr(link.find("language"), "code"),
                "url": url,
                "file": suggested_file(
                    activity_id, title, url, file_extension(_attr(link, "format"), url)
                ),
            }
        )
    return docs


def parse_file(
    path: Path, keep: list[str], doc_categories: tuple[str, ...] = tuple(DOC_CATEGORIES)
) -> tuple[list[dict], list[dict]]:
    """Return (indicator rows, document rows) for the activities in one XML file."""
    rows, docs = [], []
    # iterparse keeps memory low on large publisher files.
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != "iati-activity":
            continue
        activity_id = (element.findtext("iati-identifier") or "").strip()
        if not keep or any(k in activity_id for k in keep):
            rows.extend(activity_rows(element, f"IATI: {activity_id}"))
            docs.extend(activity_documents(element, doc_categories))
        element.clear()
    return rows, docs


def _write(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build the indicator tracker and document list from IATI XML."
    )
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument(
        "--activity",
        action="append",
        default=[],
        help="Keep only activities whose IATI identifier contains this text (repeatable)",
    )
    parser.add_argument(
        "--doc-category",
        action="append",
        choices=sorted(DOC_CATEGORIES),
        help="Document categories to list (repeatable). Default: A07 and A08",
    )
    args = parser.parse_args(argv)
    doc_categories = tuple(args.doc_category or DOC_CATEGORIES)

    rows, docs = [], []
    for path in args.files:
        found, found_docs = parse_file(path, args.activity, doc_categories)
        print(f"{path}: {len(found)} indicator period(s), {len(found_docs)} document(s)")
        rows.extend(found)
        docs.extend(found_docs)
    if not rows and not docs:
        raise SystemExit(
            "No result indicators or documents found. Not every IATI publisher reports them."
        )

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    out = settings.data_dir / "indicators.csv"
    _write(out, COLUMNS, rows)
    n_indicators = len({r["indicator_code"] for r in rows})
    print(f"Wrote {n_indicators} indicator(s), {len(rows)} row(s) to {out}")

    doc_out = settings.data_dir / "iati_documents.csv"
    _write(doc_out, DOC_COLUMNS, docs)
    print(f"Wrote {len(docs)} document link(s) to {doc_out}")
    if docs:
        print(
            "Next: mel-download-docs --dry-run to see them, "
            "then mel-download-docs to fetch the PDFs."
        )


if __name__ == "__main__":
    main(sys.argv[1:])
