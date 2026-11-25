"""The document manifest (one row per report) and its fixed vocabularies.

Manifest fields are stored with every chunk, so searches can filter on them and citations
can give the study phase and sample size.
"""

import csv
from pathlib import Path
from typing import Literal

from mel_mcp.countries import canonical_countries

STUDY_PHASES = (
    "formative",
    "baseline",
    "midline",
    "endline",
    "ex-post",
    "monitoring",
    "other",
)
SOURCE_TYPES = (
    "evaluation_report",
    "learning_brief",
    "donor_report",
    "survey_report",
    "fgd_transcript",
    "kii_transcript",
    "logframe",
    "policy_brief",
    "other",
)

StudyPhase = Literal[STUDY_PHASES]
SourceType = Literal[SOURCE_TYPES]

MANIFEST_COLUMNS = (
    "file",  # path relative to docs/, e.g. kenya/endline_report.pdf
    "title",
    "project",
    "donor",
    "implementing_partner",
    "study_phase",
    "reporting_year",
    "country",
    "source_type",
    "sample_size",  # n for quantitative studies; leave blank if not applicable
    "url",
)
REQUIRED_COLUMNS = ("file", "title", "study_phase", "source_type")
INT_COLUMNS = ("reporting_year", "sample_size")


class ManifestError(ValueError):
    pass


def _clean_row(row: dict[str, str], line: int) -> dict:
    meta: dict = {}
    for col in MANIFEST_COLUMNS:
        value = (row.get(col) or "").strip()
        if not value:
            continue  # Chroma metadata can't hold None, so leave the key out
        if col in INT_COLUMNS:
            try:
                meta[col] = int(value.replace(",", ""))
            except ValueError:
                raise ManifestError(
                    f"Line {line}: {col} must be a whole number, got '{value}'."
                ) from None
        else:
            meta[col] = value

    for col in REQUIRED_COLUMNS:
        if col not in meta:
            raise ManifestError(f"Line {line}: '{col}' is required.")

    meta["study_phase"] = meta["study_phase"].lower()
    meta["source_type"] = meta["source_type"].lower()
    if meta["study_phase"] not in STUDY_PHASES:
        raise ManifestError(
            f"Line {line}: study_phase '{meta['study_phase']}' is not one of {STUDY_PHASES}."
        )
    if meta["source_type"] not in SOURCE_TYPES:
        raise ManifestError(
            f"Line {line}: source_type '{meta['source_type']}' is not one of {SOURCE_TYPES}."
        )
    meta["file"] = meta["file"].replace("\\", "/")
    return meta


def load_manifest(path: Path) -> dict[str, dict]:
    """Read the manifest into {file: metadata}. Returns {} if there is no manifest."""
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ManifestError(f"{path} is missing column(s): {missing}")
        manifest: dict[str, dict] = {}
        for line, row in enumerate(reader, start=2):  # line 1 is the header
            if not any((v or "").strip() for v in row.values()):
                continue
            meta = _clean_row(row, line)
            if meta["file"] in manifest:
                raise ManifestError(f"Line {line}: '{meta['file']}' is listed twice.")
            manifest[meta["file"]] = meta
    return manifest


def _cell(value) -> str:
    """A CSV cell: only a missing value is blank, so 0 stays 0."""
    return "" if value is None else str(value)


def append_to_manifest(path: Path, entries: list[dict]) -> int:
    """Add entries for files the manifest doesn't list yet. Returns how many were added.

    Existing rows and column order are kept: the file is rewritten with the
    new rows at the end. Every new row is validated first, so a bad entry
    never makes the manifest unreadable.
    """
    existing_rows: list[dict] = []
    columns = list(MANIFEST_COLUMNS)
    if path.exists() and path.stat().st_size:
        load_manifest(path)  # fail early if the current manifest is already invalid
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            columns = list(reader.fieldnames or []) + [
                c for c in MANIFEST_COLUMNS if c not in (reader.fieldnames or [])
            ]
            existing_rows = list(reader)

    listed = {(r.get("file") or "").strip().replace("\\", "/") for r in existing_rows}
    new_rows = []
    for entry in entries:
        row = {c: _cell(entry.get(c)) for c in MANIFEST_COLUMNS}
        try:
            meta = _clean_row(row, len(existing_rows) + len(new_rows) + 2)
        except ManifestError as exc:
            raise ManifestError(f"Not added to {path.name}: {exc}") from exc
        if meta["file"] in listed:
            continue
        listed.add(meta["file"])
        new_rows.append(row)

    if new_rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(existing_rows + new_rows)
    return len(new_rows)


def update_manifest(path: Path, changes: dict[str, dict]) -> int:
    """Change fields of rows already in the manifest: {file: {column: value}}.
    Other rows and columns are kept as they are. Returns how many rows changed."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    changed = 0
    for line, row in enumerate(rows, start=2):
        update = changes.get((row.get("file") or "").strip().replace("\\", "/"))
        if not update:
            continue
        new = {**row, **{k: _cell(v) for k, v in update.items()}}
        _clean_row(new, line)  # never write a row that wouldn't load
        if new != row:
            row.update(new)
            changed += 1
    for column in {k for u in changes.values() for k in u}:
        if column not in columns:
            columns.append(column)
    if changed:
        seen: dict[str, int] = {}
        for line, row in enumerate(rows, start=2):
            file = (row.get("file") or "").strip().replace("\\", "/")
            if file in seen:
                raise ManifestError(
                    f"Line {line}: '{file}' is listed twice (also line {seen[file]}). "
                    f"{path.name} was not changed."
                )
            seen[file] = line
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return changed


def vocabulary(manifest: dict[str, dict]) -> dict[str, list]:
    """The filter values that actually occur, so the model uses exact spellings."""

    def distinct(field: str) -> list:
        return sorted({m[field] for m in manifest.values() if field in m})

    countries = sorted(
        {c for m in manifest.values() for c in canonical_countries(m.get("country"))}
    )
    return {
        "study_phase": distinct("study_phase"),
        "source_type": distinct("source_type"),
        "country": countries,
        "project": distinct("project"),
        "reporting_year": distinct("reporting_year"),
    }
