"""Reading the CSV files in the data folder, and the error the data tools raise."""

import csv
from datetime import UTC, datetime
from pathlib import Path


class DataError(ValueError):
    """A problem the caller can fix. The server shows it to the model as a tool error."""


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [
            {k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f)
        ]


def number(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except (AttributeError, ValueError):
        return None


def modified(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
