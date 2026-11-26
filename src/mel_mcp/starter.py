"""The Kenya starter pack: real IATI reports and indicators to try the server with.

starter/kenya-agriculture/ lists the reports, with their source links and details, and holds
the indicator tracker. The reports themselves are downloaded from their sources, because most
publishers don't allow them to be redistributed.

Run:  mel-starter load    # download the reports, then build the search index
      mel-starter build   # maintainers: make the pack from reports.txt and your manifest
"""

import argparse
import csv
import shutil
import tempfile
import time
from pathlib import Path

from mel_mcp import ingest
from mel_mcp.config import ROOT, Settings, settings
from mel_mcp.countries import country_code
from mel_mcp.documents import DocumentIndex
from mel_mcp.download_docs import SkipDocument, download_document
from mel_mcp.metadata import MANIFEST_COLUMNS, ManifestError, append_to_manifest, load_manifest
from mel_mcp.reading import load_document
from mel_mcp.tables import read_csv

PACK = ROOT / "starter" / "kenya-agriculture"
PAUSE_SECONDS = 1  # between downloads, to be polite to the publishers' servers
PACK_FILES = ("manifest.csv", "indicators.csv")
REPORT_LIST = "reports.txt"  # the links chosen for the pack, one per line
MIN_TEXT = 2000  # characters; less usually means a scanned report with no text layer


def _stem(file: str) -> str:
    return file.rsplit(".", 1)[0]  # a report saved as .odt is the same report


def _is_starter_data(s: Settings, pack: Path, tracker: Path) -> bool:
    """Whether the manifest and tracker that exist came from this pack, unchanged."""
    if tracker.exists() and tracker.read_bytes() != (pack / "indicators.csv").read_bytes():
        return False
    if s.manifest_file.exists():
        try:
            listed = {_stem(f) for f in load_manifest(s.manifest_file)}
        except ManifestError:
            return False
        return listed <= {_stem(r["file"]) for r in read_csv(pack / "manifest.csv")}
    return True


def _downloaded(target: Path) -> Path | None:
    return next(
        (p for p in (target.with_suffix(".pdf"), target.with_suffix(".odt")) if p.exists()), None
    )


def load(
    s: Settings = settings, pack: Path = PACK, opener=None, pause: float = PAUSE_SECONDS
) -> int:
    """Download the pack's reports into the docs folder and copy its tracker. Returns the
    number of reports now available. Running it again fetches only reports that are missing.
    Refuses to touch a manifest or tracker that isn't the pack's own."""
    if missing := [name for name in PACK_FILES if not (pack / name).exists()]:
        raise SystemExit(f"The starter pack in this copy ({pack}) is missing {', '.join(missing)}.")
    tracker = s.data_dir / "indicators.csv"
    existing = [str(p) for p in (s.manifest_file, tracker) if p.exists()]
    if existing and not _is_starter_data(s, pack, tracker):
        raise SystemExit(
            f"Not loading the starter pack: {' and '.join(existing)} already exist. "
            "The starter pack is for a fresh copy of the project; your own data is left as is."
        )
    if existing:
        print("  The starter pack is already loaded; fetching any reports that are missing.")

    rows = read_csv(pack / "manifest.csv")
    entries = []
    for i, row in enumerate(rows, 1):
        # Each link is shown so you can open the report at its source and check it yourself.
        target = _downloaded(s.docs_dir / row["file"])
        if target is None:
            try:
                data, kind = download_document(row["url"], opener)
            except SkipDocument as exc:
                print(f"  [{i}/{len(rows)}] {row['title'][:60]}: skipped ({exc})")
                print(f"         {row['url']}")
                continue
            target = (s.docs_dir / row["file"]).with_suffix(f".{kind}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            time.sleep(pause)
        print(f"  [{i}/{len(rows)}] {row['title'][:60]}")
        print(f"         {row['url']}")
        entries.append({**row, "file": target.relative_to(s.docs_dir).as_posix()})

    s.manifest_file.parent.mkdir(parents=True, exist_ok=True)
    append_to_manifest(s.manifest_file, entries)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(pack / "indicators.csv", tracker)
    print(f"\n{len(entries)} of {len(rows)} report(s) downloaded; indicator tracker copied.")
    print("Open any link above to see a report at its source.")
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


def read_report_list(path: Path) -> list[str]:
    """The links in reports.txt, in order. Blank lines and lines starting with # are notes."""
    if not path.exists():
        raise SystemExit(f"No report list at {path}. List one report link per line.")
    links = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    links = [link for link in links if link and not link.startswith("#")]
    if repeated := sorted({link for link in links if links.count(link) > 1}):
        raise SystemExit(f"Listed more than once in {path.name}: {', '.join(repeated)}")
    return links


def check_report(url: str, opener=None) -> str | None:
    """Why a tester couldn't use this link, or None if it downloads a readable report."""
    try:
        data, kind = download_document(url, opener)
    except SkipDocument as exc:
        return str(exc)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"check.{kind}"
        path.write_bytes(data)
        try:
            pages = load_document(path, {})
        except Exception as exc:  # whatever the PDF or .odt reader raises for a broken file
            return f"can't be read ({type(exc).__name__})"
    # Judged on all the text, not on pages: .odt files without saved page breaks are one page.
    if sum(len(p.text.strip()) for p in pages) < MIN_TEXT:
        return "little or no text (scanned?)"
    return None


def build(
    s: Settings = settings,
    pack: Path = PACK,
    country: str = "Kenya",
    opener=None,
    pause: float = PAUSE_SECONDS,
) -> list[dict]:
    """Write the pack from the links in reports.txt, with each report's details from the
    maintainer's manifest, and the tracker rows for `country`. Every link is downloaded and
    read first, the way a tester's computer will; if any fails, nothing is written."""
    links = read_report_list(pack / REPORT_LIST)
    by_url = {
        meta["url"]: {**meta, "file": source}
        for source, meta in load_manifest(s.manifest_file).items()
        if meta.get("url")
    }
    if unknown := [link for link in links if link not in by_url]:
        raise SystemExit(
            f"Not in {s.manifest_file.name}, so there are no details to use for: "
            + ", ".join(unknown)
        )
    picked = [by_url[link] for link in links]

    # Read everything before writing anything, so a missing tracker can't leave half a pack.
    tracker_file = s.data_dir / "indicators.csv"
    if not tracker_file.exists():
        raise SystemExit(f"No indicator tracker at {tracker_file}. Run mel-prepare-iati first.")
    code = country_code(country)
    tracker = read_csv(tracker_file)
    kept = [r for r in tracker if code in r.get("country", "").upper().replace(" ", "").split(";")]

    print(f"Checking {len(picked)} report link(s) the way a tester's computer will download them")
    failed = []
    for i, row in enumerate(picked, 1):
        problem = check_report(row["url"], opener)
        print(f"  [{i}/{len(picked)}] {'ok' if problem is None else 'FAILED'}: {row['title'][:60]}")
        print(f"         {row['url']}" + (f"\n         {problem}" if problem else ""))
        if problem:
            failed.append(row["url"])
        time.sleep(pause)
    if failed:
        raise SystemExit(
            f"{len(failed)} link(s) failed, so nothing was written. Replace them in "
            f"{REPORT_LIST}, or try again later if the site was only down."
        )

    pack.mkdir(parents=True, exist_ok=True)
    with open(pack / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(picked)
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
    make = commands.add_parser(
        "build", help=f"Make the pack from {REPORT_LIST}, your manifest and your tracker"
    )
    make.add_argument("--country", default="Kenya", help="Whose indicators to include")
    args = parser.parse_args(argv)

    if args.command == "build":
        build(country=args.country)
        return
    print("Downloading the starter pack's reports from their publishers")
    if load():
        ingest.main([])
