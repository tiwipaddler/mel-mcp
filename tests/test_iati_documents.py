"""IATI document links → downloaded PDFs → manifest rows. All downloads are faked."""

import csv
import io
from urllib.error import HTTPError

import pytest
from conftest import IATI_XML

from mel_mcp import download_docs, prepare_iati
from mel_mcp.metadata import ManifestError, append_to_manifest, load_manifest

PDF = b"%PDF-1.4\n% fake report\n"


@pytest.fixture
def documents(mel_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(download_docs, "settings", mel_settings)
    xml = tmp_path / "activities.xml"
    xml.write_text(IATI_XML, encoding="utf-8")
    prepare_iati.main([str(xml)])
    path = mel_settings.data_dir / "iati_documents.csv"
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))


def fake_web(pages: dict):
    """An opener serving fixed bytes per URL; unknown URLs give HTTP 404."""
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if request.full_url not in pages:
            raise HTTPError(request.full_url, 404, "Not Found", {}, None)
        return io.BytesIO(pages[request.full_url])

    return opener, calls


def test_documents_are_extracted_with_context(documents):
    by_category = {d["category"]: d for d in documents}
    assert set(by_category) == {"A07", "A08"}  # the A12 web page is ignored

    review = by_category["A07"]
    assert review["project"] == "Test smallholder programme"
    assert review["donor"] == "Test Donor Agency"
    assert review["implementing_partner"] == "Test Farmers Cooperative"
    assert review["country"] == "Kenya"  # from the code KE
    assert review["study_phase"] == "midline"  # guessed from "Mid-term Evaluation Report"
    assert review["source_type"] == "evaluation_report"
    assert review["reporting_year"] == "2022"
    assert review["file"].startswith("xm_test_001_mid_term_evaluation_report_") and review[
        "file"
    ].endswith(".pdf")

    assert (
        by_category["A08"]["study_phase"] == "other"
        and by_category["A08"]["source_type"] == "donor_report"
    )


@pytest.mark.parametrize(
    "title, phase",
    [
        ("Baseline survey report", "baseline"),
        ("Midline assessment", "midline"),
        ("Final Evaluation of the programme", "endline"),
        ("End-term review", "endline"),
        ("Ex-post impact study", "ex-post"),
        ("Quarterly update", "other"),
    ],
)
def test_phase_guessing(title, phase):
    assert prepare_iati.guess_phase(title) == phase


def test_dry_run_downloads_nothing(documents, mel_settings, capsys):
    opener, calls = fake_web({})
    download_docs.main(["--dry-run"], opener=opener)
    assert calls == [] and "Mid-term Evaluation Report" in capsys.readouterr().out
    assert not mel_settings.manifest_file.exists()


def test_download_keeps_pdfs_skips_web_pages_and_fills_manifest(documents, mel_settings, capsys):
    opener, calls = fake_web(
        {
            "https://example.org/docs/midterm-review.pdf": PDF,
            "https://example.org/docs/annual-results-2023": b"<html>Sign in to view</html>",
        }
    )
    download_docs.main([], opener=opener)

    saved = list((mel_settings.docs_dir / "iati").glob("*.pdf"))
    assert len(saved) == 1 and saved[0].read_bytes() == PDF
    assert "not a PDF" in capsys.readouterr().out

    manifest = load_manifest(mel_settings.manifest_file)  # still valid for mel-ingest
    entry = manifest[f"iati/{saved[0].name}"]
    assert entry["study_phase"] == "midline" and entry["country"] == "Kenya"
    assert entry["url"] == "https://example.org/docs/midterm-review.pdf"


def test_rerunning_does_not_download_or_list_twice(documents, mel_settings):
    opener, calls = fake_web({"https://example.org/docs/midterm-review.pdf": PDF})
    download_docs.main(["--category", "A07"], opener=opener)
    download_docs.main(["--category", "A07"], opener=opener)
    assert calls == ["https://example.org/docs/midterm-review.pdf"]  # fetched once
    assert len(load_manifest(mel_settings.manifest_file)) == 1


def test_failed_downloads_are_reported_not_fatal(documents, mel_settings, capsys):
    opener, _ = fake_web({})  # every URL gives 404
    download_docs.main([], opener=opener)
    out = capsys.readouterr().out
    assert "HTTP 404" in out and "Downloaded 0 file(s)" in out


def test_oversized_files_are_rejected(monkeypatch):
    monkeypatch.setattr(download_docs, "MAX_BYTES", 10)
    opener, _ = fake_web({"https://x.org/big.pdf": PDF})
    with pytest.raises(download_docs.SkipDocument, match="larger than"):
        download_docs.download_document("https://x.org/big.pdf", opener)


def test_append_keeps_existing_rows_and_column_order(manifest_file):
    before = manifest_file.read_text().splitlines()[0]
    added = append_to_manifest(
        manifest_file,
        [
            {
                "file": "iati/new.pdf",
                "title": "New",
                "study_phase": "endline",
                "source_type": "evaluation_report",
            }
        ],
    )
    assert added == 1
    assert manifest_file.read_text().splitlines()[0] == before  # same header
    manifest = load_manifest(manifest_file)
    assert {"baseline.pdf", "endline.pdf", "iati/new.pdf"} <= set(manifest)
    assert manifest["baseline.pdf"]["sample_size"] == 1200  # untouched


def test_invalid_entries_never_reach_the_manifest(manifest_file):
    before = manifest_file.read_text()
    with pytest.raises(ManifestError, match="midterm"):
        append_to_manifest(
            manifest_file,
            [
                {
                    "file": "x.pdf",
                    "title": "X",
                    "study_phase": "midterm",
                    "source_type": "evaluation_report",
                }
            ],
        )
    assert manifest_file.read_text() == before


def test_downloaded_reports_get_their_metadata_at_ingestion(documents, mel_settings):
    from pypdf import PdfWriter

    from mel_mcp.reading import load_documents

    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buffer)
    opener, _ = fake_web({"https://example.org/docs/midterm-review.pdf": buffer.getvalue()})
    download_docs.main(["--category", "A07"], opener=opener)

    pages = load_documents(mel_settings.docs_dir, load_manifest(mel_settings.manifest_file))
    meta = pages[0].metadata
    assert meta["source"].startswith("iati/")
    assert meta["project"] == "Test smallholder programme" and meta["study_phase"] == "midline"


@pytest.mark.parametrize(
    "date, title, url, expected",
    [
        ("2022-06-30", "Report", "https://x.org/a.pdf", ("2022", "document-date")),
        (
            "",
            "Implementation Completion and Results Report",
            "https://documents.worldbank.org/curated/en/099122425102515384/pdf/BOSIB-24e6.pdf",
            ("2025", "world-bank-id"),
        ),
        (
            "",
            "Implementation Status and Results Report",
            "http://documents.worldbank.org/curated/en/259841468048304432/pdf/ISR-Disclosable-P117635-10-14-2014-1413318446187.pdf",
            ("2014", "url-or-title"),
        ),
        ("", "Annual results report 2023", "https://x.org/annual", ("2023", "url-or-title")),
        (
            "",
            "Implementation Completion and Results Report",
            "http://documents.worldbank.org/curated/en/865211468272737775/pdf/ICR16480P1160610313111110BOX358324B.pdf",
            ("", ""),
        ),
    ],
)
def test_document_years_are_inferred(date, title, url, expected):
    assert prepare_iati.infer_year(date, title, url, latest=2027) == expected


def test_status_reports_are_monitoring_and_completion_reports_endline():
    assert prepare_iati.guess_phase("Implementation Status and Results Report") == "monitoring"
    assert prepare_iati.guess_phase("Implementation Completion and Results Report") == "endline"
    assert prepare_iati.guess_phase("Implementation Completion Report Review") == "endline"


def test_since_year_skips_old_and_undated_documents(documents, capsys):
    opener, calls = fake_web({})
    download_docs.main(["--dry-run", "--since-year", "2023"], opener=opener)
    out = capsys.readouterr().out
    assert (
        "Annual results report 2023" in out and "Mid-term Evaluation Report" not in out
    )  # 2022 is too old

    assert download_docs.recent_enough({"reporting_year": ""}, 2020) is False
    assert download_docs.recent_enough({"reporting_year": ""}, 2020, include_undated=True) is True


def fake_dns(address):
    return lambda host, port, type: [(2, 1, 6, "", (address, 0))]


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"]
)
def test_links_to_private_addresses_are_refused(address):
    with pytest.raises(download_docs.SkipDocument, match="private or local"):
        download_docs.check_url("https://reports.example.org/a.pdf", resolve=fake_dns(address))


def test_public_web_links_are_allowed():
    download_docs.check_url("https://reports.example.org/a.pdf", resolve=fake_dns("93.184.215.14"))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/a.pdf", "https://"])
def test_non_web_links_are_refused(url):
    with pytest.raises(download_docs.SkipDocument, match="not a web link"):
        download_docs.check_url(url, resolve=fake_dns("93.184.215.14"))


def test_redirects_to_private_addresses_are_refused():
    handler = download_docs._CheckedRedirects()
    with pytest.raises(download_docs.SkipDocument, match="private or local"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/admin")


def test_malformed_links_are_skipped_not_fatal():
    with pytest.raises(download_docs.SkipDocument, match="not a valid link"):
        download_docs.download_document("reports/a.pdf", opener=lambda request, timeout: None)


def test_cut_off_downloads_are_skipped():
    class Response(io.BytesIO):
        headers = {"Content-Length": "5000"}

    def opener(request, timeout):
        return Response(b"%PDF-1.4 only the start")

    with pytest.raises(download_docs.SkipDocument, match="cut off"):
        download_docs.download_document("https://example.org/a.pdf", opener=opener)
