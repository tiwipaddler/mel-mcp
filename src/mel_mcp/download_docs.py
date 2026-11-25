"""Download the reports listed by mel-prepare-iati and add them to the manifest.

Only real PDF and OpenDocument files are kept. Each file's content is checked, so a
mislabelled link is saved under its real type. The study phase in each new manifest row is
guessed from the title, so check it. See `mel-download-docs --help` for the options.
"""

import argparse
import csv
import ipaddress
import socket
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from mel_mcp.config import settings
from mel_mcp.metadata import ManifestError, append_to_manifest, load_manifest, update_manifest

MAX_BYTES = 50 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; mel-evidence-mcp; research use)"
# IATI format attribute → whether to try the link. Blank means unknown, so try.
SUPPORTED_FORMATS = ("application/pdf", "application/vnd.oasis.opendocument.text", "")
SUPPORTED_EXTENSIONS = (".pdf", ".odt")
ODT_MIMETYPE = b"application/vnd.oasis.opendocument.text"


class SkipDocument(Exception):
    pass


def check_url(url: str, resolve=socket.getaddrinfo) -> None:
    """Allow only web links to public internet addresses.

    Links come from IATI data, which anyone can publish. Without this, a link could make
    the downloader request something on the user's own network, such as a router page.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SkipDocument("not a web link")
    try:
        addresses = resolve(parts.hostname, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise SkipDocument(f"could not connect ({exc})") from exc
    for *_, sockaddr in addresses:
        if not ipaddress.ip_address(sockaddr[0].split("%")[0]).is_global:
            raise SkipDocument("the link points to a private or local network address")


class _CheckedRedirects(HTTPRedirectHandler):
    """Check every redirect too, so a public link can't forward to a private address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_public(request: Request, timeout: float):
    check_url(request.full_url)
    return build_opener(_CheckedRedirects).open(request, timeout=timeout)


def load_documents(data_dir: Path) -> list[dict]:
    path = Path(data_dir) / "iati_documents.csv"
    if not path.exists():
        raise SystemExit(
            "No data/iati_documents.csv yet. Run mel-fetch-iati or mel-prepare-iati first."
        )
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def recent_enough(doc: dict, since_year: int, include_undated: bool = False) -> bool:
    year = doc.get("reporting_year", "")
    if not year.isdigit():
        return include_undated
    return int(year) >= since_year


def looks_supported(doc: dict) -> bool:
    """A link that may be a PDF or OpenDocument text file, judged by its label or URL."""
    url = doc.get("url", "").lower().split("?")[0]
    return doc.get("format", "").lower() in SUPPORTED_FORMATS or url.endswith(SUPPORTED_EXTENSIONS)


def file_kind(data: bytes) -> str | None:
    """'pdf' or 'odt' from the file's content, whatever the link claimed."""
    if b"%PDF" in data[:1024]:
        return "pdf"
    # An .odt file is a zip whose first entry, "mimetype", holds its type uncompressed.
    if data[:4] == b"PK\x03\x04" and ODT_MIMETYPE in data[:200]:
        return "odt"
    return None


def download_document(url: str, opener=None) -> tuple[bytes, str]:
    """Fetch a URL; return (bytes, 'pdf' or 'odt') if it really is one of those, under MAX_BYTES."""
    opener = opener or _open_public
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with opener(request, timeout=60) as response:
            data = response.read(MAX_BYTES + 1)
            headers = getattr(response, "headers", None)
            declared = headers.get("Content-Length") if headers else None
    except ValueError as exc:  # Request() rejects relative or malformed links
        raise SkipDocument("not a valid link") from exc
    except HTTPError as exc:
        raise SkipDocument(f"HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise SkipDocument(f"could not connect ({getattr(exc, 'reason', exc)})") from exc
    if len(data) > MAX_BYTES:
        raise SkipDocument("larger than 50 MB")
    if declared and declared.isdigit() and len(data) < int(declared):
        raise SkipDocument("the download was cut off")
    kind = file_kind(data)
    if kind is None:
        raise SkipDocument("not a PDF or OpenDocument file (probably a web page)")
    return data, kind


def existing_file(target_dir: Path, doc: dict) -> Path | None:
    """The already-downloaded file for this document, under either extension."""
    stem = Path(doc["file"]).stem
    for ext in ("pdf", "odt"):
        if (target_dir / f"{stem}.{ext}").exists():
            return target_dir / f"{stem}.{ext}"
    return None


def manifest_entry(doc: dict, file_name: str) -> dict:
    return {
        "file": f"iati/{file_name}",
        "title": doc.get("title")
        or f"{doc.get('project', 'Untitled project')} ({doc.get('category_name', '')})",
        "project": doc.get("project", ""),
        "donor": doc.get("donor", ""),
        "implementing_partner": doc.get("implementing_partner", ""),
        "study_phase": doc.get("study_phase") or "other",
        "reporting_year": doc.get("reporting_year", ""),
        "country": doc.get("country", ""),
        "source_type": doc.get("source_type") or "other",
        "sample_size": "",
        "url": doc.get("url", ""),
    }


def update_countries() -> int:
    """Set each IATI-sourced manifest row's country to every country its activity covers.
    Matched by URL; nothing else in the manifest changes."""
    countries_by_url = {
        d["url"]: d.get("country", "") for d in load_documents(settings.data_dir) if d.get("url")
    }
    changes = {
        file: {"country": countries_by_url[meta["url"]]}
        for file, meta in load_manifest(settings.manifest_file).items()
        if meta.get("url") in countries_by_url
        and countries_by_url[meta["url"]]
        and countries_by_url[meta["url"]] != meta.get("country")
    }
    changed = update_manifest(settings.manifest_file, changes) if changes else 0
    print(f"Updated the country of {changed} manifest row(s). Run mel-ingest to re-index them.")
    return changed


def main(argv: list[str] | None = None, opener=None) -> None:
    parser = argparse.ArgumentParser(
        description="Download IATI-linked reports and add them to the manifest."
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Most documents to download this run (default 10)"
    )
    parser.add_argument(
        "--category", choices=("A07", "A08"), help="Only evaluations (A07) or results reports (A08)"
    )
    parser.add_argument("--country", help="Only documents for this country, e.g. Kenya")
    parser.add_argument(
        "--since-year",
        type=int,
        help="Only documents from this year on, e.g. 2020 (undated documents are skipped)",
    )
    parser.add_argument(
        "--include-undated",
        action="store_true",
        help="With --since-year, also keep documents whose year couldn't be worked out",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List the documents without downloading"
    )
    parser.add_argument(
        "--update-countries",
        action="store_true",
        help="Only update manifest countries from IATI (multi-country documents), then stop",
    )
    args = parser.parse_args(argv)

    if args.update_countries:
        update_countries()
        return

    docs = load_documents(settings.data_dir)
    if args.category:
        docs = [d for d in docs if d["category"] == args.category]
    if args.country:
        docs = [d for d in docs if d.get("country", "").lower() == args.country.lower()]
    if args.since_year:
        docs = [d for d in docs if recent_enough(d, args.since_year, args.include_undated)]
    candidates = [d for d in docs if looks_supported(d)]
    print(
        f"{len(docs)} document link(s) match; "
        f"{len(candidates)} look like PDF or OpenDocument files."
    )

    if args.dry_run:
        for d in candidates[: args.limit]:
            print(
                f"- [{d['category']}, {d['study_phase']}, {d['reporting_year'] or 'no date'}] "
                f"{d['title'] or '(untitled)'}  ({d['project'][:50]})\n    {d['url']}"
            )
        return

    target_dir = settings.docs_dir / "iati"
    target_dir.mkdir(parents=True, exist_ok=True)
    added, skipped, entries = 0, [], []
    for doc in candidates:
        if added >= args.limit:
            break
        path = existing_file(target_dir, doc)
        if path is None:
            try:
                data, kind = download_document(doc["url"], opener)
            except SkipDocument as exc:
                skipped.append((doc, str(exc)))
                continue
            path = target_dir / f"{Path(doc['file']).stem}.{kind}"  # named by its real type
            path.write_bytes(data)
            added += 1
            print(f"Downloaded {path.name}")
        entries.append(manifest_entry(doc, path.name))

    try:
        new_rows = append_to_manifest(settings.manifest_file, entries)
    except ManifestError as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"\nDownloaded {added} file(s) to {target_dir}; "
        f"added {new_rows} row(s) to {settings.manifest_file}."
    )
    if skipped:
        print(f"Skipped {len(skipped)}:")
        for doc, reason in skipped:
            print(f"  - {reason}: {doc['url']}")
    if new_rows:
        print(
            "Check the new manifest rows (study phase is guessed; add sample sizes), "
            "then run mel-ingest."
        )


if __name__ == "__main__":
    main(sys.argv[1:])
