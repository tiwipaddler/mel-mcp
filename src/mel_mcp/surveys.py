"""Aggregate-only access to survey datasets, backed by DuckDB.

The data folder holds datasets.csv (the catalog), and for each dataset <name>.parquet and
<name>.dictionary.csv. The dictionary is the access policy: variables with role 'group' can
break results down or filter them, 'measure' variables can be summarised, and 'id' and
'exclude' variables are never read out. No individual row is ever returned, and groups
smaller than min_cell_size are suppressed.
"""

import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import duckdb

from mel_mcp.tables import DataError, modified, read_csv

Stat = Literal["mean", "median", "share", "count"]
ROLES = ("group", "measure", "id", "exclude")
PUBLIC_ROLES = ("group", "measure")
NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
NUMERIC_TYPES = ("INT", "DOUBLE", "FLOAT", "DECIMAL")
MAX_GROUPS = 50


def _quote(identifier: str) -> str:
    """Quote a column name for SQL. Only ever called on names from the data dictionary."""
    return '"' + identifier.replace('"', '""') + '"'


def _is_numeric(column_type: str) -> bool:
    return any(t in column_type for t in NUMERIC_TYPES)


class SurveyData:
    def __init__(self, data_dir: Path, min_cell_size: int = 10, query_timeout_seconds: float = 10):
        self.data_dir = Path(data_dir)
        self.min_cell_size = min_cell_size
        self.query_timeout_seconds = query_timeout_seconds

    @contextmanager
    def _query(self, ds: dict):
        """A connection with the dataset as view `d`, interrupted if a query runs too long."""
        con = duckdb.connect()
        timer = threading.Timer(self.query_timeout_seconds, con.interrupt)
        timer.start()
        try:
            path_sql = "'" + str(ds["parquet"]).replace("'", "''") + "'"
            con.execute(f"CREATE VIEW d AS SELECT * FROM read_parquet({path_sql})")
            yield con
        except duckdb.InterruptException as exc:
            raise DataError(
                f"The query was stopped after {self.query_timeout_seconds:g} seconds."
            ) from exc
        finally:
            timer.cancel()
            con.close()

    def _catalog(self) -> dict[str, dict]:
        path = self.data_dir / "datasets.csv"
        if not path.exists():
            return {}
        return {row["name"]: row for row in read_csv(path) if row.get("name")}

    def _dataset(self, name: str) -> dict:
        catalog = self._catalog()
        if name not in catalog:
            raise DataError(
                f"Unknown dataset '{name}'. Available: {sorted(catalog) or 'none'}. "
                "Call list_datasets to see them."
            )
        if not NAME_PATTERN.match(name):
            raise DataError(f"Dataset names may only use a-z, 0-9 and _: '{name}'.")
        parquet = self.data_dir / f"{name}.parquet"
        if not parquet.exists():
            raise DataError(f"Dataset '{name}' is in the catalog but {parquet.name} is missing.")
        return {**catalog[name], "parquet": parquet}

    def dictionary(self, name: str) -> list[dict]:
        """Every variable with its role, including id and exclude. For internal use only."""
        self._dataset(name)
        path = self.data_dir / f"{name}.dictionary.csv"
        if not path.exists():
            raise DataError(f"Dataset '{name}' has no data dictionary ({path.name}).")
        variables = read_csv(path)
        for v in variables:
            if v.get("role") not in ROLES:
                raise DataError(
                    f"{path.name}: variable '{v.get('variable')}' has role "
                    f"'{v.get('role')}'; use one of {ROLES}."
                )
        return variables

    def public_dictionary(self, name: str) -> list[dict]:
        """The variables the model may use. id and exclude variables are left out entirely."""
        return [
            {k: v[k] for k in ("variable", "label", "role", "unit") if v.get(k)}
            for v in self.dictionary(name)
            if v["role"] in ("group", "measure")
        ]

    def catalog_entry(self, name: str) -> dict:
        return {k: v for k, v in self._dataset(name).items() if k != "parquet"}

    def list_datasets(self) -> list[dict]:
        fields = (
            "name",
            "title",
            "project",
            "study_phase",
            "country",
            "reporting_year",
            "source_url",
        )
        return [{k: row[k] for k in fields if row.get(k)} for row in self._catalog().values()]

    def describe_dataset(self, name: str) -> dict:
        ds = self._dataset(name)
        variables = self.public_dictionary(name)
        return {
            "dataset": name,
            "title": ds.get("title", name),
            "study_phase": ds.get("study_phase"),
            "source_url": ds.get("source_url"),
            "group_variables": [v for v in variables if v["role"] == "group"],
            "measure_variables": [v for v in variables if v["role"] == "measure"],
            "rules": (
                f"Only aggregates are returned. Groups with fewer than {self.min_cell_size} "
                "respondents are suppressed."
            ),
        }

    def profile(self, name: str) -> dict[str, dict]:
        """A summary of each group and measure variable, used to compare survey rounds.

        Only variables the dictionary lists as group or measure are read. id, exclude and
        undocumented columns are never queried, so nothing about them can reach a drift
        finding. Categories held by fewer than min_cell_size respondents are counted in
        rare_categories but not named.
        """
        ds = self._dataset(name)
        public = {v["variable"]: v for v in self.dictionary(name) if v["role"] in PUBLIC_ROLES}
        profiles: dict[str, dict] = {}
        with self._query(ds) as con:
            types = dict(
                con.execute("SELECT column_name, column_type FROM (DESCRIBE d)").fetchall()
            )
            total = con.execute("SELECT count(*) FROM d").fetchone()[0]
            for column, meta in public.items():
                base = {"role": meta["role"], "label": meta.get("label", "")}
                if column not in types:
                    profiles[column] = {
                        **base,
                        "type": None,
                        "n": 0,
                        "missing_share": 1.0,
                        "absent": True,
                    }
                    continue
                q = _quote(column)
                n = con.execute(f"SELECT count({q}) FROM d").fetchone()[0]
                p = {
                    **base,
                    "type": types[column],
                    "n": n,
                    "missing_share": 1 - n / total if total else 0.0,
                }
                numeric = _is_numeric(types[column])
                if meta["role"] == "measure" and numeric and n >= self.min_cell_size:
                    p["mean"] = con.execute(f"SELECT avg({q}) FROM d").fetchone()[0]
                if not numeric:
                    counts = con.execute(
                        f"SELECT CAST({q} AS VARCHAR), count(*) FROM d WHERE {q} IS NOT NULL "
                        f"GROUP BY 1 LIMIT {MAX_GROUPS + 1}"
                    ).fetchall()
                    if len(counts) <= MAX_GROUPS:
                        p["categories"] = sorted(c for c, k in counts if k >= self.min_cell_size)
                        p["rare_categories"] = sum(k < self.min_cell_size for _, k in counts)
                profiles[column] = p
        return profiles

    def _variable(self, variables: list[dict], name: str, roles: tuple[str, ...]) -> dict:
        allowed = {v["variable"]: v for v in variables if v["role"] in roles}
        if name not in allowed:
            kind = "broken down or filtered by" if roles == ("group",) else "summarised this way"
            raise DataError(
                f"'{name}' can't be {kind}. Allowed: {sorted(allowed)}. "
                "Call describe_dataset to see each variable's label."
            )
        return allowed[name]

    def aggregate(
        self,
        dataset: str,
        variable: str,
        stat: Stat,
        group_by: str | None = None,
        filters: dict[str, str | int | float] | None = None,
    ) -> dict:
        if stat not in ("mean", "median", "share", "count"):
            raise DataError("stat must be one of: mean, median, share, count.")
        ds = self._dataset(dataset)
        variables = self.dictionary(dataset)
        # Group variables such as county can also be counted or shown as shares.
        roles = ("measure", "group") if stat in ("share", "count") else ("measure",)
        measure = self._variable(variables, variable, roles)
        if group_by:
            self._variable(variables, group_by, ("group",))
        filters = filters or {}
        for name in filters:
            self._variable(variables, name, ("group",))

        # Filters compare as text, so "Machakos" and 2021 both work.
        where = " AND ".join(f"CAST({_quote(k)} AS VARCHAR) = ?" for k in filters)
        where_sql = f"WHERE {where}" if where else ""
        params = [str(value) for value in filters.values()]
        query = (where_sql, params)

        with self._query(ds) as con:
            types = dict(
                con.execute("SELECT column_name, column_type FROM (DESCRIBE d)").fetchall()
            )
            if not _is_numeric(types.get(variable, "")):
                if stat == "share":
                    results, suppressed = self._category_shares(con, variable, group_by, query)
                    return self._result(
                        dataset, ds, measure, stat, group_by, filters, results, suppressed
                    )
                if stat != "count":
                    raise DataError(f"'{variable}' is not numeric. Use 'share' or 'count' instead.")
            rows = self._summarise(con, variable, stat, group_by, query)

        if len(rows) > MAX_GROUPS:
            raise DataError(
                f"'{group_by}' has {len(rows)} groups; break down by a coarser variable."
            )
        results, suppressed = [], []
        for group, n, value in rows:
            label = self._label(group, group_by)
            if n < self.min_cell_size:
                suppressed.append(label)
                continue
            value = None if value is None else round(float(value), 3)
            results.append({"group": label, "n": n, "value": value})
        return self._result(dataset, ds, measure, stat, group_by, filters, results, suppressed)

    def _summarise(self, con, variable, stat, group_by, query) -> list[tuple]:
        where_sql, params = query
        v = _quote(variable)
        if stat == "share":
            not_binary = con.execute(
                f"SELECT count(*) FROM d WHERE {v} IS NOT NULL AND {v} NOT IN (0, 1)"
            ).fetchone()[0]
            if not_binary:
                raise DataError(
                    f"'{variable}' is not a 0/1 variable, so 'share' doesn't apply. "
                    "Use mean or median."
                )
        value_sql = {
            "mean": f"avg({v})",
            "median": f"median({v})",
            "share": f"avg({v}) * 100",
            "count": f"count({v})",
        }[stat]
        group_sql = _quote(group_by) if group_by else "'all'"
        return con.execute(
            f"SELECT {group_sql} AS grp, count({v}) AS n, {value_sql} AS value "
            f"FROM d {where_sql} GROUP BY grp ORDER BY grp",
            params,
        ).fetchall()

    def _category_shares(self, con, variable, group_by, query) -> tuple[list, list]:
        """Percentage of respondents in each category of a text variable."""
        where_sql, params = query
        v = _quote(variable)
        group_sql = _quote(group_by) if group_by else "'all'"
        rows = con.execute(
            f"SELECT {group_sql} AS grp, {v} AS category, count(*) AS n FROM d {where_sql} "
            f"{'AND' if where_sql else 'WHERE'} {v} IS NOT NULL "
            "GROUP BY grp, category ORDER BY grp, category",
            params,
        ).fetchall()

        totals: dict = {}
        for group, _, n in rows:
            totals[group] = totals.get(group, 0) + n
        if len(totals) > MAX_GROUPS:
            raise DataError(
                f"'{group_by}' has {len(totals)} groups; break down by a coarser variable."
            )

        results, suppressed = [], []
        for group, total in totals.items():
            label = self._label(group, group_by)
            if total < self.min_cell_size:
                suppressed.append(label)
                continue
            counts = {str(category): n for g, category, n in rows if g == group}
            hidden = self._complementary(counts)
            shares = {
                category: (
                    f"suppressed (n<{self.min_cell_size})"
                    if n < self.min_cell_size
                    else "suppressed (protects a small category)"
                    if category in hidden
                    else round(100 * n / total, 1)
                )
                for category, n in counts.items()
            }
            results.append({"group": label, "n": total, "value": shares})
        return results, suppressed

    def _complementary(self, counts: dict[str, int]) -> set[str]:
        """Categories to hide in one group: the small ones, plus the next smallest when only
        one is small. Otherwise it could be worked out by subtracting the others from 100%."""
        hidden = {c for c, n in counts.items() if n < self.min_cell_size}
        if len(hidden) == 1 and len(counts) > 1:
            visible = sorted((n, c) for c, n in counts.items() if c not in hidden)
            hidden.add(visible[0][1])
        return hidden

    @staticmethod
    def _label(group, group_by: str | None) -> str:
        if not group_by:
            return "all respondents"
        return "missing" if group is None else str(group)

    def _result(self, dataset, ds, measure, stat, group_by, filters, results, suppressed) -> dict:
        source = {
            "title": ds.get("title", dataset),
            "study_phase": ds.get("study_phase"),
            "country": ds.get("country"),
            "reporting_year": ds.get("reporting_year"),
            "url": ds.get("source_url"),
            "data_updated": modified(ds["parquet"]),
        }
        out = {
            "dataset": dataset,
            "source": {k: v for k, v in source.items() if v},
            "variable": measure["variable"],
            "label": measure.get("label", ""),
            "stat": stat,
            "unit": "% of respondents" if stat == "share" else measure.get("unit", ""),
            "group_by": group_by,
            "filters": filters,
            "results": results,
        }
        if suppressed:
            out["suppressed_groups"] = suppressed
            out["note"] = (
                f"Groups with fewer than {self.min_cell_size} respondents are not reported "
                "to protect respondents' privacy."
            )
        return out
