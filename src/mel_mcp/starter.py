"""The Kenya starter pack: real IATI reports and indicators to try the server with.

starter/kenya-agriculture/ lists the reports, with their source links and details, and holds
the indicator tracker. The reports themselves are downloaded from their sources, because most
publishers don't allow them to be redistributed.

Run:  mel-starter load    # download the reports, then build the search index
      mel-starter build   # maintainers: make the pack from your own manifest and tracker
"""

import argparse
import csv
import shutil
import time
from pathlib import Path

from mel_mcp import ingest
from mel_mcp.config import ROOT, Settings, settings
from mel_mcp.countries import canonical_countries, canonical_country, country_code
from mel_mcp.documents import DocumentIndex
from mel_mcp.download_docs import SkipDocument, download_document
from mel_mcp.metadata import MANIFEST_COLUMNS, append_to_manifest, load_manifest
from mel_mcp.tables import read_csv

PACK = ROOT / "starter" / "kenya-agriculture"
PAUSE_SECONDS = 1  # between downloads, to be polite to the publishers' servers


def load(
    s: Settings = settings, pack: Path = PACK, opener=None, pause: float = PAUSE_SECONDS
) -> int:
    """Download the pack's reports into the docs folder and copy its tracker. Returns the
    number of reports now available. Refuses to touch a manifest or tracker that exists."""
    tracker = s.data_dir / "indicators.csv"
    if existing := [str(p) for p in (s.manifest_file, tracker) if p.exists()]:
        raise SystemExit(
            f"Not loading the starter pack: {' and '.join(existing)} already exist. "
            "The starter pack is for a fresh copy of the project; your own data is left as is."
        )

    if not (pack / "manifest.csv").exists():
        raise SystemExit(f"The starter pack is missing from this copy ({pack}).")
    rows = read_csv(pack / "manifest.csv")
    entries = []
    for i, row in enumerate(rows, 1):
        target = s.docs_dir / row["file"]
        if not target.exists():
            try:
                data, kind = download_document(row["url"], opener)
            except SkipDocument as exc:
                print(f"  [{i}/{len(rows)}] {row['title'][:60]}: skipped ({exc})")
                continue
            target = target.with_suffix(f".{kind}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            time.sleep(pause)
        print(f"  [{i}/{len(rows)}] {row['title'][:60]}")
        entries.append({**row, "file": target.relative_to(s.docs_dir).as_posix()})

    s.manifest_file.parent.mkdir(parents=True, exist_ok=True)
    append_to_manifest(s.manifest_file, entries)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(pack / "indicators.csv", tracker)
    print(f"\n{len(entries)} of {len(rows)} report(s) downloaded; indicator tracker copied.")
    return len(entries)


def _passages_per_report(s: Settings) -> dict[str, int]:
    try:
        stored = DocumentIndex(s).collection.get(include=["metadatas"])
    except RuntimeError:  # no index yet
        return {}
    counts: dict[str, int] = {}
    for meta in stored["metadatas"]:
        counts[meta["source"]] = counts.get(meta["source"], 0) + 1
    return counts


def _spread_across_projects(rows: list[dict], limit: int) -> list[dict]:
    """Newest reports first, taking one per project in turn so the pack covers many projects."""
    by_project: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda r: r.get("reporting_year", 0), reverse=True):
        by_project.setdefault(row.get("project", ""), []).append(row)
    picked = []
    while len(picked) < limit and any(by_project.values()):
        for project_rows in by_project.values():
            if project_rows and len(picked) < limit:
                picked.append(project_rows.pop(0))
    return picked


def build(
    s: Settings = settings,
    pack: Path = PACK,
    country: str = "Kenya",
    since: int = 2020,
    limit: int = 25,
) -> list[dict]:
    """Write the pack from the maintainer's manifest and tracker: reports with a source link,
    from `since` on, for `country`, spread across projects."""
    wanted = canonical_country(country)
    candidates = [
        {**meta, "file": source}
        for source, meta in load_manifest(s.manifest_file).items()
        if meta.get("url")
        and meta.get("reporting_year", 0) >= since
        and wanted in canonical_countries(meta.get("country"))
        and (s.docs_dir / source).exists()
    ]
    picked = _spread_across_projects(candidates, limit)

    pack.mkdir(parents=True, exist_ok=True)
    with open(pack / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(picked)

    code = country_code(country)
    tracker = read_csv(s.data_dir / "indicators.csv")
    kept = [r for r in tracker if code in r.get("country", "").upper().replace(" ", "").split(";")]
    with open(pack / "indicators.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(tracker[0]) if tracker else [])
        writer.writeheader()
        writer.writerows(kept)

    passages = _passages_per_report(s)
    total = sum(passages.get(r["file"], 0) for r in picked)
    print(
        f"Wrote {len(picked)} report(s) from {len({r.get('project') for r in picked})} "
        f"project(s) and {len(kept)} indicator row(s) to {pack}."
    )
    if total:
        print(f"Testers will index about {total} passages.")
    return picked


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Load or build the Kenya starter pack.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("load", help="Download the reports and build the search index")
    make = commands.add_parser("build", help="Make the pack from your manifest and tracker")
    make.add_argument("--country", default="Kenya")
    make.add_argument("--since", type=int, default=2020, help="First reporting year (default 2020)")
    make.add_argument("--limit", type=int, default=25, help="Most reports (default 25)")
    args = parser.parse_args(argv)

    if args.command == "build":
        build(country=args.country, since=args.since, limit=args.limit)
        return
    print("Downloading the starter pack's reports from their publishers")
    if load():
        ingest.main([])
