"""Reading the CSV files in the data folder, and the error the data tools raise."""

import csv
import math
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
    """A finite number, or None. 'nan', 'inf' and text all give None."""
    try:
        parsed = float(value.replace(",", ""))
    except (AttributeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def modified(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
