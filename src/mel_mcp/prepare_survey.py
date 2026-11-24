"""Convert a cleaned survey file (.dta, .csv or .parquet) for the server.

Writes data/<name>.parquet and a starter data dictionary, and adds the dataset to
data/datasets.csv. The roles in the dictionary are guesses: review them before use, and set
anything that could identify a person (names, phone numbers, GPS, free text) to 'exclude'.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

from mel_mcp.config import settings
from mel_mcp.metadata import STUDY_PHASES

PII_PATTERN = re.compile(
    r"(^|_)(name|names|phone|mobile|tel|gps|lat|latitude|lon|lng|longitude|"
    r"address|email|dob|birth|nid|national_id|photo|signature)(_|$)",
    re.IGNORECASE,
)
ID_PATTERN = re.compile(r"(^|_)id$|^id_|hhid|household_id|respondent_id|uuid", re.IGNORECASE)
MAX_GROUP_CATEGORIES = 30
CATALOG_FIELDS = (
    "name",
    "title",
    "project",
    "study_phase",
    "country",
    "reporting_year",
    "source_url",
)


def read_any(path: Path) -> tuple[pd.DataFrame, dict[str, str]]:
    """Return the data and its variable labels (Stata only; others get none)."""
    suffix = path.suffix.lower()
    if suffix == ".dta":
        with pd.io.stata.StataReader(path) as reader:
            df = reader.read(convert_categoricals=True)
            labels = reader.variable_labels()
        return df, labels
    if suffix == ".csv":
        return pd.read_csv(path), {}
    if suffix == ".parquet":
        return duckdb.sql(
            f"SELECT * FROM read_parquet('{str(path).replace(chr(39), chr(39) * 2)}')"
        ).df(), {}
    raise SystemExit(f"Unsupported file type '{suffix}'. Use .dta, .csv or .parquet.")


def guess_role(column: str, series: pd.Series, id_columns: set[str]) -> str:
    if column in id_columns or ID_PATTERN.search(column):
        return "id"
    if PII_PATTERN.search(column):
        return "exclude"
    if (
        isinstance(series.dtype, pd.CategoricalDtype)
        or series.dtype == object
        or pd.api.types.is_string_dtype(series)
    ):
        n_unique = series.nunique(dropna=True)
        return (
            "group" if n_unique <= MAX_GROUP_CATEGORIES else "exclude"
        )  # many values: likely free text
    if pd.api.types.is_bool_dtype(series) or pd.api.types.is_numeric_dtype(series):
        return "measure"
    return "exclude"  # dates and anything unusual: decide by hand


def write_dictionary(
    path: Path, df: pd.DataFrame, labels: dict[str, str], id_columns: set[str]
) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["variable", "label", "role", "unit"])
        for col in df.columns:
            writer.writerow([col, labels.get(col, ""), guess_role(col, df[col], id_columns), ""])


def update_catalog(data_dir: Path, entry: dict) -> None:
    path = data_dir / "datasets.csv"
    rows = []
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(f) if r.get("name") != entry["name"]]
    rows.append(entry)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CATALOG_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Prepare a survey dataset for the MCP server.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--name", required=True, help="Short name: lowercase letters, digits and _")
    parser.add_argument("--title", required=True)
    parser.add_argument("--phase", required=True, choices=STUDY_PHASES)
    parser.add_argument("--project", default="")
    parser.add_argument("--country", default="")
    parser.add_argument("--year", default="")
    parser.add_argument("--url", default="", help="Where the public data can be downloaded (DOI)")
    parser.add_argument("--id", action="append", default=[], help="ID column (repeatable)")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing data dictionary"
    )
    args = parser.parse_args(argv)

    if not re.match(r"^[a-z0-9_]+$", args.name):
        raise SystemExit("--name may only use lowercase letters, digits and _.")

    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    df, labels = read_any(args.input)
    # Stata value labels become text categories, which DuckDB stores as text.
    for col in df.columns:
        if isinstance(df[col].dtype, pd.CategoricalDtype):
            df[col] = df[col].astype("string")
    print(f"Read {len(df):,} rows and {len(df.columns)} columns from {args.input}")

    parquet = data_dir / f"{args.name}.parquet"
    con = duckdb.connect()
    con.register("df", df)
    escaped = str(parquet).replace("'", "''")
    con.execute(f"COPY (SELECT * FROM df) TO '{escaped}' (FORMAT parquet)")
    con.close()
    print(f"Wrote {parquet}")

    dictionary = data_dir / f"{args.name}.dictionary.csv"
    if dictionary.exists() and not args.force:
        print(f"Kept your existing {dictionary.name} (use --force to regenerate it).")
    else:
        write_dictionary(dictionary, df, labels, set(args.id))
        print(f"Wrote {dictionary}")
        with open(dictionary, newline="", encoding="utf-8") as f:
            roles = [r["role"] for r in csv.DictReader(f)]
        print(
            "Guessed roles: "
            + ", ".join(f"{r}={roles.count(r)}" for r in ("group", "measure", "id", "exclude"))
        )
        print("REVIEW THE ROLES before starting the server: they decide what the model can see.")

    update_catalog(
        data_dir,
        {
            "name": args.name,
            "title": args.title,
            "project": args.project,
            "study_phase": args.phase,
            "country": args.country,
            "reporting_year": args.year,
            "source_url": args.url,
        },
    )
    print(f"Added '{args.name}' to {data_dir / 'datasets.csv'}")


if __name__ == "__main__":
    main(sys.argv[1:])
