"""Download activities from the IATI Datastore API, then build the indicator tracker.

Needs a free API key from https://developer.iatistandard.org, set as IATI_API_KEY in .env.
The raw XML is kept in data/raw/, so the tracker can be rebuilt with mel-prepare-iati
without calling the API again.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mel_mcp import prepare_iati
from mel_mcp.config import settings

API_URL = "https://api.iatistandard.org/datastore/activity/select"
CALL_PAUSE_SECONDS = 13  # stays under the Exploratory limit of 5 calls a minute
USER_AGENT = "Mozilla/5.0 (compatible; mel-evidence-mcp; research use)"
XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>\s*")


def build_query(
    countries: list[str],
    sectors: list[str],
    since: str | None,
    doc_categories: list[str] | None = None,
) -> str:
    """Datastore (Solr) query, e.g. recipient_country_code:(KE) AND (sector_code:311*).

    doc_categories keeps only activities that link documents of those types,
    e.g. ["A07", "A08"] for evaluation and results reports.
    """
    parts = []
    if countries:
        parts.append(f"recipient_country_code:({' '.join(c.upper() for c in countries)})")
    if sectors:
        parts.append("(" + " OR ".join(f"sector_code:{s}*" for s in sectors) + ")")
    if since:
        parts.append(f"activity_date_iso_date:[{since}T00:00:00Z TO *]")
    if doc_categories:
        parts.append(
            "(" + " OR ".join(f"document_link_category_code:{c}" for c in doc_categories) + ")"
        )
    return " AND ".join(parts) or "*:*"


def fetch_activities(
    query: str,
    api_key: str,
    rows: int = 100,
    max_pages: int = 3,
    opener=None,
    pause: float | None = None,
) -> tuple[list[str], int]:
    """Return (activity XML strings, total matches). Each page is one API call."""
    opener = opener or urlopen
    pause = CALL_PAUSE_SECONDS if pause is None else pause
    activities, start, found = [], 0, 0
    for page in range(max_pages):
        if page:
            time.sleep(pause)
        params = urlencode(
            {
                "q": query,
                "fl": "iati_identifier,iati_xml",
                "rows": rows,
                "start": start,
                "wt": "json",
            }
        )
        request = Request(
            f"{API_URL}?{params}",
            headers={
                "Ocp-Apim-Subscription-Key": api_key,
                "Accept": "application/json",
                # Python's default label ("Python-urllib") is blocked by many APIs.
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with opener(request, timeout=60) as response:
                data = json.load(response)
        except HTTPError as exc:
            hints = {
                401: "check IATI_API_KEY",
                403: "access refused",
                429: "rate limit hit; wait a minute",
            }
            try:
                detail = exc.read()[:300].decode("utf-8", errors="replace").strip()
            except Exception:
                detail = ""
            raise SystemExit(
                f"IATI API error {exc.code}: {hints.get(exc.code, exc.reason)}"
                + (f"\nIATI said: {detail}" if detail else "")
            ) from exc

        found = data["response"]["numFound"]
        docs = data["response"]["docs"]
        for doc in docs:
            xml = doc.get("iati_xml")
            xml = xml[0] if isinstance(xml, list) else xml
            if xml:
                activities.append(XML_DECLARATION.sub("", xml))
        start += len(docs)
        print(f"Page {page + 1}: {len(docs)} activities ({start} of {found})")
        if not docs or start >= found:
            break
    return activities, found


def write_xml(activities: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<iati-activities version="2.03">\n')
        f.write("\n".join(activities))
        f.write("\n</iati-activities>\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Fetch IATI activities and build the indicator tracker."
    )
    parser.add_argument(
        "--country", action="append", default=[], help="ISO country code, e.g. KE (repeatable)"
    )
    parser.add_argument(
        "--sector",
        action="append",
        default=[],
        help="DAC sector code or prefix, e.g. 311 (repeatable)",
    )
    parser.add_argument("--since", help="Activities with dates from this day on, e.g. 2018-01-01")
    parser.add_argument(
        "--with-docs",
        action="store_true",
        help="Only activities that link evaluation (A07) or results (A08) reports",
    )
    parser.add_argument("--rows", type=int, default=100, help="Activities per call (max 1000)")
    parser.add_argument("--max-pages", type=int, default=3, help="Most API calls to make")
    parser.add_argument(
        "--out", type=Path, default=settings.data_dir / "raw" / "iati_activities.xml"
    )
    args = parser.parse_args(argv)

    api_key = os.getenv("IATI_API_KEY")
    if not api_key:
        raise SystemExit("Set IATI_API_KEY in .env (free key: https://developer.iatistandard.org).")

    query = build_query(
        args.country, args.sector, args.since, ["A07", "A08"] if args.with_docs else None
    )
    print(f"Query: {query}")
    activities, found = fetch_activities(query, api_key, min(args.rows, 1000), args.max_pages)
    if not activities:
        raise SystemExit("No activities matched the query.")
    if len(activities) < found:
        print(
            f"Fetched {len(activities)} of {found}; raise --max-pages or narrow the query for more."
        )

    write_xml(activities, args.out)
    print(f"Saved raw XML to {args.out}")
    prepare_iati.main([str(args.out)])


if __name__ == "__main__":
    main(sys.argv[1:])
