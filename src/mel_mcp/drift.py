"""What changed between two rounds of the same survey?

Renamed variables, recoded categories ("Female" to "F") and unit changes break comparisons
without raising errors. data/variable_usage.csv lists which indicators or scripts read each
variable, so a finding can say what it breaks.
"""

import csv
import difflib
from pathlib import Path

from mel_mcp.surveys import SurveyData

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
UNIT_CHANGE_RATIO = 5.0  # a mean 5x larger or smaller suggests a unit change
MISSINGNESS_JUMP = 0.2  # 20 percentage points more (or fewer) missing answers
PRIVATE_ROLES = ("id", "exclude")
# Written into every report. Reports without it predate the privacy rules and aren't served.
PUBLIC_ONLY = "Covers group and measure variables only."


def _is_numeric(col_type: str | None) -> bool:
    return any(t in (col_type or "") for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL"))


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _rename_score(old: dict, new: dict, old_name: str, new_name: str) -> int:
    """Evidence that `new_name` is `old_name` under a different name."""
    score = 0
    if _norm(old.get("label")) and _norm(old.get("label")) == _norm(new.get("label")):
        score += 2
    if old.get("type") == new.get("type"):
        score += 1
    old_cats, new_cats = set(old.get("categories", [])), set(new.get("categories", []))
    if old_cats and new_cats and len(old_cats & new_cats) / len(old_cats | new_cats) >= 0.8:
        score += 2
    if (
        old_name in new_name
        or new_name in old_name
        or difflib.SequenceMatcher(None, old_name, new_name).ratio() >= 0.6
    ):
        score += 1
    return score


def _finding(kind: str, severity: str, variable: str, detail: str, **evidence) -> dict:
    return {
        "type": kind,
        "severity": severity,
        "variable": variable,
        "detail": detail,
        **({"evidence": evidence} if evidence else {}),
    }


def compare(engine: SurveyData, earlier: str, later: str) -> dict:
    """Compare two datasets' group and measure variables (see SurveyData.profile).

    A variable marked id or exclude in either round is left out of both, so reclassifying
    a variable as private can't make it show up as removed or added.
    """
    private = {
        v["variable"]
        for name in (earlier, later)
        for v in engine.dictionary(name)
        if v["role"] in PRIVATE_ROLES
    }
    a = {k: v for k, v in engine.profile(earlier).items() if k not in private}
    b = {k: v for k, v in engine.profile(later).items() if k not in private}

    def present(profiles):  # variables that actually exist in the data
        return {k for k, v in profiles.items() if not v.get("absent")}

    removed = sorted(present(a) - present(b))
    added = sorted(present(b) - present(a))
    findings = []

    # Likely renames: a removed and an added variable that look like the same thing.
    for old in list(removed):
        candidates = [(_rename_score(a[old], b[new], old, new), new) for new in added]
        if candidates:
            score, new = max(candidates)
            if score >= 3:
                findings.append(
                    _finding(
                        "possible_rename",
                        "high",
                        old,
                        f"'{old}' is missing in {later}, and '{new}' looks like the same variable.",
                        renamed_to=new,
                        label=b[new].get("label"),
                        match_score=score,
                    )
                )
                removed.remove(old)
                added.remove(new)

    for var in removed:
        findings.append(
            _finding(
                "removed",
                "high",
                var,
                f"'{var}' is in {earlier} but not in {later}.",
                label=a[var].get("label"),
            )
        )
    for var in added:
        findings.append(
            _finding("added", "low", var, f"'{var}' is new in {later}.", label=b[var].get("label"))
        )

    for var in sorted(present(a) & present(b)):
        old, new = a[var], b[var]
        if old["type"] != new["type"]:
            # INTEGER to DOUBLE usually just means missing values appeared;
            # number to text breaks analysis.
            both_numeric = _is_numeric(old["type"]) and _is_numeric(new["type"])
            findings.append(
                _finding(
                    "type_changed",
                    "low" if both_numeric else "high",
                    var,
                    f"'{var}' changed type from {old['type']} to {new['type']}"
                    + (" (both numeric; usually harmless)." if both_numeric else "."),
                )
            )
        if old["role"] != new["role"]:
            findings.append(
                _finding(
                    "role_changed",
                    "medium",
                    var,
                    f"'{var}' changed role from {old['role']} to {new['role']} "
                    "in the data dictionary.",
                )
            )
        if (
            _norm(old.get("label")) != _norm(new.get("label"))
            and old.get("label")
            and new.get("label")
        ):
            findings.append(
                _finding(
                    "label_changed",
                    "low",
                    var,
                    f"'{var}' has a new label.",
                    before=old["label"],
                    after=new["label"],
                )
            )
        if "categories" in old and "categories" in new and old["categories"] != new["categories"]:
            gone = sorted(set(old["categories"]) - set(new["categories"]))
            new_cats = sorted(set(new["categories"]) - set(old["categories"]))
            overlap = set(old["categories"]) & set(new["categories"])
            findings.append(
                _finding(
                    "recoded",
                    "high" if not overlap else "medium",
                    var,
                    f"'{var}' categories changed"
                    + (" completely (codes may have been recoded)." if not overlap else "."),
                    no_longer_used=gone,
                    new_categories=new_cats,
                )
            )
        if old.get("mean") and new.get("mean"):
            ratio = new["mean"] / old["mean"]
            if ratio >= UNIT_CHANGE_RATIO or ratio <= 1 / UNIT_CHANGE_RATIO:
                findings.append(
                    _finding(
                        "possible_unit_change",
                        "high",
                        var,
                        f"The average of '{var}' changed {ratio:.1f}x: "
                        "check whether its unit or scale changed.",
                        mean_before=round(old["mean"], 3),
                        mean_after=round(new["mean"], 3),
                    )
                )
        jump = new["missing_share"] - old["missing_share"]
        # Rounded so that 10% -> 30% counts as 20 points (0.3 - 0.1 is 0.1999... in floats).
        if round(abs(jump), 9) >= MISSINGNESS_JUMP:
            if min(old["n"], new["n"]) < engine.min_cell_size:
                # With so few answers, the percentages would reveal a small count.
                change = "changed sharply"
            else:
                change = f"went from {old['missing_share']:.0%} to {new['missing_share']:.0%}"
            findings.append(
                _finding(
                    "missingness_jump",
                    "medium",
                    var,
                    f"Missing answers for '{var}' {change} (check skip logic or the form).",
                )
            )

    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["variable"]))
    counts = {s: sum(f["severity"] == s for f in findings) for s in SEVERITY_ORDER}
    return {"earlier": earlier, "later": later, "findings": findings, "counts": counts}


def latest_report(reports_dir: Path) -> Path | None:
    """The drift agent's newest Markdown report, if any."""
    reports = sorted(Path(reports_dir).glob("drift_*.md"))
    return reports[-1] if reports else None


def load_usage(data_dir: Path) -> list[dict]:
    """Rows of data/variable_usage.csv: reader, dataset (optional), variable."""
    path = Path(data_dir) / "variable_usage.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [
            {k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f)
        ]


def affected_readers(finding: dict, datasets: tuple[str, ...], usage: list[dict]) -> list[str]:
    """Readers that depend on a variable this finding says is gone or changed.

    A usage row applies if it names no dataset, or names one of `datasets`
    (the two rounds being compared).
    """
    if finding["type"] not in (
        "removed",
        "possible_rename",
        "type_changed",
        "recoded",
        "possible_unit_change",
        "missingness_jump",
    ):
        return []
    return sorted(
        {
            u["reader"]
            for u in usage
            if u.get("variable") == finding["variable"] and u.get("dataset", "") in ("", *datasets)
        }
    )
