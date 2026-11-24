"""The starter pack: built from a maintainer's data, loaded on a fresh copy."""

import csv
import io
from dataclasses import replace
from urllib.error import HTTPError

import pytest
from test_ingest import make_pdf

from mel_mcp import starter
from mel_mcp.metadata import load_manifest

HEADER = (
    "indicator_code,indicator_name,unit,ascending,project,country,baseline_year,"
    "baseline_value,period_start,period_end,target,actual,source"
)


def write_manifest(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def report(file, project, year, country="Kenya", url=True):
    return {
        "file": file,
        "title": f"Report {file}",
        "project": project,
        "study_phase": "monitoring",
        "source_type": "donor_report",
        "reporting_year": str(year),
        "country": country,
        "url": f"https://example.org/{file}" if url else "",
    }


@pytest.fixture
def maintainer(mel_settings, tmp_path):
    """A maintainer's data: reports for three projects, one too old, one Ugandan, one unlinked."""
    rows = [
        report("a1.pdf", "Irrigation", 2024),
        report("a2.pdf", "Irrigation", 2023),
        report("a3.pdf", "Irrigation", 2022),
        report("b1.pdf", "Seeds", 2021),
        report("c1.pdf", "Youth", 2025),
        report("old.pdf", "Youth", 2018),
        report("ug.pdf", "Uganda roads", 2024, country="Uganda"),
        report("nolink.pdf", "Seeds", 2024, url=False),
    ]
    for row in rows:
        make_pdf(mel_settings.docs_dir / row["file"], ["Delays in procurement"])
    write_manifest(mel_settings.manifest_file, rows)
    mel_settings.data_dir.mkdir(parents=True)
    (mel_settings.data_dir / "indicators.csv").write_text(
        HEADER + "\n"
        "K/R1/I1,Farmers trained (Number),unit,1,Irrigation,KE,,,2022-01-01,2024-12-31,100,80,IATI\n"
        "U/R1/I1,Roads built (km),unit,1,Uganda roads,UG,,,2022-01-01,2024-12-31,50,40,IATI\n"
        "M/R1/I1,Seed sold (t),unit,1,Regional seeds,KE;UG,,,2022-01-01,2024-12-31,9,9,IATI\n"
    )
    return mel_settings, tmp_path / "pack"


def test_build_picks_recent_linked_reports_across_projects(maintainer):
    s, pack = maintainer
    picked = starter.build(s, pack, limit=4)
    assert [r["file"] for r in picked] == ["c1.pdf", "a1.pdf", "b1.pdf", "a2.pdf"]
    assert set(load_manifest(pack / "manifest.csv")) == {"c1.pdf", "a1.pdf", "b1.pdf", "a2.pdf"}
    codes = [r["indicator_code"] for r in csv.DictReader(open(pack / "indicators.csv"))]
    assert codes == ["K/R1/I1", "M/R1/I1"]  # Kenyan and multi-country, not Ugandan


def fake_web(pages):
    def opener(request, timeout):
        if request.full_url not in pages:
            raise HTTPError(request.full_url, 404, "Not Found", {}, None)
        return io.BytesIO(pages[request.full_url])

    return opener


@pytest.fixture
def fresh_copy(maintainer, tmp_path):
    s, pack = maintainer
    starter.build(s, pack, limit=3)
    fresh = replace(
        s,
        docs_dir=tmp_path / "fresh" / "docs",
        manifest_file=tmp_path / "fresh" / "docs" / "manifest.csv",
        data_dir=tmp_path / "fresh" / "data",
    )
    pdf = (s.docs_dir / "a1.pdf").read_bytes()
    pages = {"https://example.org/c1.pdf": pdf, "https://example.org/a1.pdf": pdf}
    return fresh, pack, fake_web(pages)  # b1.pdf's link is broken


def test_load_downloads_reports_and_copies_the_tracker(fresh_copy, capsys):
    s, pack, opener = fresh_copy
    assert starter.load(s, pack, opener, pause=0) == 2
    assert set(load_manifest(s.manifest_file)) == {"c1.pdf", "a1.pdf"}
    assert (s.docs_dir / "c1.pdf").read_bytes().startswith(b"%PDF")
    assert (s.data_dir / "indicators.csv").read_text() == (pack / "indicators.csv").read_text()
    assert "skipped (HTTP 404)" in capsys.readouterr().out


def test_load_never_replaces_existing_data(fresh_copy):
    s, pack, opener = fresh_copy
    starter.load(s, pack, opener, pause=0)
    with pytest.raises(SystemExit, match="already exist"):
        starter.load(s, pack, opener, pause=0)
