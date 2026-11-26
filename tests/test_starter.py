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
        make_pdf(
            mel_settings.docs_dir / row["file"], ["Delays in procurement held up works. " * 15] * 5
        )
    write_manifest(mel_settings.manifest_file, rows)
    mel_settings.data_dir.mkdir(parents=True)
    (mel_settings.data_dir / "indicators.csv").write_text(
        HEADER + "\n"
        "K/R1/I1,Farmers trained (Number),unit,1,Irrigation,KE,,,2022-01-01,2024-12-31,100,80,IATI\n"
        "U/R1/I1,Roads built (km),unit,1,Uganda roads,UG,,,2022-01-01,2024-12-31,50,40,IATI\n"
        "M/R1/I1,Seed sold (t),unit,1,Seeds,KE;UG,,,2022-01-01,2024-12-31,9,9,IATI\n"
        "Z/R1/I1,Wells dug,unit,1,Water,KE,,,2022-01-01,2024-12-31,20,15,IATI\n"
    )
    return mel_settings, tmp_path / "pack"


def fake_web(pages):
    def opener(request, timeout):
        if request.full_url not in pages:
            raise HTTPError(request.full_url, 404, "Not Found", {}, None)
        return io.BytesIO(pages[request.full_url])

    return opener


def choose(pack, *files, note="# Chosen for the test"):
    pack.mkdir(parents=True, exist_ok=True)
    links = "\n".join(f"https://example.org/{f}" for f in files)
    (pack / "reports.txt").write_text(f"{note}\n\n{links}\n", encoding="utf-8")


def the_web(s, *files):
    return fake_web({f"https://example.org/{f}": (s.docs_dir / f).read_bytes() for f in files})


def test_build_writes_the_chosen_reports_in_order(maintainer, capsys):
    s, pack = maintainer
    choose(pack, "c1.pdf", "a3.pdf", "b1.pdf")
    picked = starter.build(s, pack, opener=the_web(s, "c1.pdf", "a3.pdf", "b1.pdf"), pause=0)
    assert [r["file"] for r in picked] == ["c1.pdf", "a3.pdf", "b1.pdf"]
    assert list(load_manifest(pack / "manifest.csv")) == ["c1.pdf", "a3.pdf", "b1.pdf"]
    codes = [r["indicator_code"] for r in csv.DictReader(open(pack / "indicators.csv"))]
    # The pack's projects only, Kenyan or multi-country: not Ugandan, not a project with no report.
    assert codes == ["K/R1/I1", "M/R1/I1"]
    out = capsys.readouterr().out
    assert "https://example.org/a3.pdf" in out
    assert "No indicators in the tracker for: Youth" in out


def test_build_writes_nothing_when_a_link_fails(maintainer, capsys):
    s, pack = maintainer
    choose(pack, "c1.pdf", "a1.pdf")
    with pytest.raises(SystemExit, match="1 link"):
        starter.build(s, pack, opener=the_web(s, "c1.pdf"), pause=0)
    assert "FAILED: Report a1.pdf" in capsys.readouterr().out
    assert not (pack / "manifest.csv").exists()


def test_build_refuses_reports_with_too_little_text(maintainer):
    s, pack = maintainer
    make_pdf(s.docs_dir / "a1.pdf", ["Scanned"])
    choose(pack, "a1.pdf")
    with pytest.raises(SystemExit, match="1 link"):
        starter.build(s, pack, opener=the_web(s, "a1.pdf"), pause=0)


def test_build_needs_every_chosen_link_in_the_manifest(maintainer):
    s, pack = maintainer
    choose(pack, "c1.pdf", "unknown.pdf")
    with pytest.raises(SystemExit, match="unknown.pdf"):
        starter.build(s, pack, opener=the_web(s, "c1.pdf"), pause=0)


def test_a_link_chosen_twice_is_refused(maintainer):
    s, pack = maintainer
    choose(pack, "c1.pdf", "c1.pdf")
    with pytest.raises(SystemExit, match="more than once"):
        starter.build(s, pack, opener=the_web(s, "c1.pdf"), pause=0)


@pytest.fixture
def fresh_copy(maintainer, tmp_path):
    s, pack = maintainer
    choose(pack, "c1.pdf", "a1.pdf", "b1.pdf")
    starter.build(s, pack, opener=the_web(s, "c1.pdf", "a1.pdf", "b1.pdf"), pause=0)
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
    out = capsys.readouterr().out.split("Checking")[-1]
    lines = out[out.index("Report c1.pdf") :].splitlines()
    # Every report is followed by its link, downloaded or not.
    assert lines[1].strip() == "https://example.org/c1.pdf"
    assert "skipped (HTTP 404)" in out and "         https://example.org/b1.pdf" in out


def test_load_never_replaces_your_own_data(fresh_copy):
    s, pack, opener = fresh_copy
    starter.load(s, pack, opener, pause=0)
    with open(s.manifest_file, "a", encoding="utf-8") as f:
        f.write("mine.pdf,My report,,,,endline,2024,Kenya,evaluation_report,,\n")
    before = s.manifest_file.read_text()
    with pytest.raises(SystemExit, match="already exist"):
        starter.load(s, pack, opener, pause=0)
    assert s.manifest_file.read_text() == before


def test_loading_again_fetches_only_what_is_missing(fresh_copy, capsys):
    s, pack, opener = fresh_copy
    starter.load(s, pack, opener, pause=0)
    pdf = (s.docs_dir / "a1.pdf").read_bytes()
    fixed = fake_web({"https://example.org/b1.pdf": pdf})  # only b1 can be downloaded now
    assert starter.load(s, pack, fixed, pause=0) == 3
    assert "already loaded" in capsys.readouterr().out
    assert set(load_manifest(s.manifest_file)) == {"c1.pdf", "a1.pdf", "b1.pdf"}


def test_an_incomplete_pack_changes_nothing(fresh_copy):
    s, pack, opener = fresh_copy
    (pack / "indicators.csv").unlink()
    with pytest.raises(SystemExit, match="missing indicators.csv"):
        starter.load(s, pack, opener, pause=0)
    assert not s.docs_dir.exists() and not s.data_dir.exists()


def test_build_without_a_tracker_writes_nothing(maintainer):
    s, pack = maintainer
    choose(pack, "c1.pdf")
    (s.data_dir / "indicators.csv").unlink()
    with pytest.raises(SystemExit, match="No indicator tracker"):
        starter.build(s, pack, opener=the_web(s, "c1.pdf"), pause=0)
    assert not (pack / "manifest.csv").exists()


def test_downloads_from_one_site_are_spaced_out():
    waits, now = [], [0.0]
    pacer = starter.Pacer(
        15,
        fake_web(
            {"https://a.org/1": b"%PDF", "https://a.org/2": b"%PDF", "https://b.org/1": b"%PDF"}
        ),
        sleep=waits.append,
        clock=lambda: now[0],
    )
    pacer.download("https://a.org/1")
    now[0] = 4.0
    pacer.download("https://b.org/1")  # another site: no wait
    pacer.download("https://a.org/2")
    assert waits == [11.0]


def test_a_refused_download_is_tried_once_more(fresh_copy):
    s, pack, opener = fresh_copy
    refusals = []

    def busy(request, timeout):
        if request.full_url.endswith("b1.pdf") and not refusals:
            refusals.append(request.full_url)
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
        return io.BytesIO((s.docs_dir.parent.parent / "docs" / "a1.pdf").read_bytes())

    assert starter.load(s, pack, busy, pause=0) == 3
    assert refusals == ["https://example.org/b1.pdf"]
