"""OpenDocument (.odt) support: reading, downloading and indexing, e.g. UK FCDO reports."""

import io
import zipfile

from conftest import FakeEmbedder
from test_ingest import CountingEmbedder, make_pdf

from mel_mcp import download_docs, ingest, prepare_iati
from mel_mcp.documents import DocumentIndex, citation
from mel_mcp.reading import load_odt

NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
)


def odt_bytes(body: str) -> bytes:
    """A minimal valid .odt file: a zip with an uncompressed mimetype entry first."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        z.writestr(
            "content.xml",
            f'<?xml version="1.0"?><office:document-content {NS}>'
            f"<office:body><office:text>{body}</office:text></office:body>"
            "</office:document-content>",
            compress_type=zipfile.ZIP_DEFLATED,
        )
    return buffer.getvalue()


REVIEW = (
    "<text:h>Annual Review</text:h>"
    "<text:p>Outcome<text:s text:c='2'/>score: A.<text:tab/>Targets largely met.</text:p>"
    "<table:table><table:table-row><table:table-cell><text:p>Indicator</text:p></table:table-cell>"
    "<table:table-cell><text:p>Target</text:p></table:table-cell><table:table-cell><text:p>Actual</text:p>"
    "</table:table-cell></table:table-row><table:table-row><table:table-cell><text:p>Farmers trained</text:p>"
    "</table:table-cell><table:table-cell><text:p>5000</text:p></table:table-cell><table:table-cell>"
    "<text:p>5400</text:p></table:table-cell></table:table-row></table:table>"
    "<text:list><text:list-item><text:p>Lesson: start procurement early</text:p></text:list-item></text:list>"
)


def write_odt(path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(odt_bytes(body))


def test_odt_text_keeps_tables_as_rows(tmp_path):
    write_odt(tmp_path / "review.odt", REVIEW)
    pages = load_odt(tmp_path / "review.odt", {"source": "review.odt", "title": "Annual Review"})
    assert len(pages) == 1
    text = pages[0].text
    assert "Outcome  score: A.\tTargets largely met." in text  # encoded spaces and tabs kept
    assert (
        "Indicator | Target | Actual\nFarmers trained | 5000 | 5400" in text
    )  # one line per table row
    assert "Lesson: start procurement early" in text  # list items too


def test_odt_pages_follow_saved_page_breaks_otherwise_no_page_in_citations(tmp_path):
    write_odt(
        tmp_path / "paged.odt",
        "<text:p>Page one</text:p><text:soft-page-break/><text:p>Page two</text:p>",
    )
    pages = load_odt(tmp_path / "paged.odt", {"source": "paged.odt", "title": "Paged"})
    assert [p.text for p in pages] == ["Page one", "Page two"]
    assert citation(pages[1].metadata) == "Paged, p. 2"

    write_odt(tmp_path / "flat.odt", "<text:p>All on one page</text:p>")
    flat = load_odt(
        tmp_path / "flat.odt", {"source": "flat.odt", "title": "Flat", "study_phase": "endline"}
    )
    assert citation(flat[0].metadata) == "Flat (endline)"  # no made-up page number


def test_odt_links_get_odt_names_and_annual_reviews_are_monitoring():
    assert (
        prepare_iati.file_extension("application/vnd.oasis.opendocument.text", "http://x.org/D1")
        == "odt"
    )
    assert (
        prepare_iati.file_extension("", "http://iati.fcdo.gov.uk/iati_documents/D0000907.odt")
        == "odt"
    )
    assert prepare_iati.file_extension("application/pdf", "http://x.org/a.pdf") == "pdf"
    assert prepare_iati.guess_phase("Annual review (53175005) 202698") == "monitoring"
    assert prepare_iati.guess_phase("Project completion review (D0000907)") == "endline"


def fake_web(pages):
    def opener(request, timeout):
        return io.BytesIO(pages[request.full_url])

    return opener


def test_downloads_are_saved_under_their_real_type(mel_settings, monkeypatch, tmp_path):
    monkeypatch.setattr(download_docs, "settings", mel_settings)
    mel_settings.data_dir.mkdir(parents=True, exist_ok=True)
    (mel_settings.data_dir / "iati_documents.csv").write_text(
        "activity_id,project,donor,implementing_partner,country,category,category_name,source_type,study_phase,"
        "title,document_date,reporting_year,year_source,format,language,url,file\n"
        "GB-1,P,FCDO,,Kenya,A07,Review,evaluation_report,monitoring,Annual review,,2021,url-or-title,"
        "application/vnd.oasis.opendocument.text,en,http://fcdo.example/review.odt,gb_1_annual_review_aaaaaa.odt\n"
        "GB-1,P,FCDO,,Kenya,A07,Review,evaluation_report,endline,Completion review,,2022,url-or-title,"
        "application/vnd.oasis.opendocument.text,en,http://fcdo.example/mislabelled.odt,gb_1_completion_bbbbbb.odt\n"
        "GB-1,P,FCDO,,Kenya,A07,Review,evaluation_report,other,Web page,,2022,url-or-title,"
        "application/vnd.oasis.opendocument.text,en,http://fcdo.example/page.odt,gb_1_page_cccccc.odt\n"
        "GB-1,P,FCDO,,Kenya,A07,Review,evaluation_report,other,Spreadsheet,,2022,url-or-title,"
        "application/vnd.ms-excel,en,http://fcdo.example/data.xls,gb_1_data_dddddd.pdf\n"
    )
    pdf_path = tmp_path / "x.pdf"
    make_pdf(pdf_path, ["A completion review"])
    opener = fake_web(
        {
            "http://fcdo.example/review.odt": odt_bytes(REVIEW),
            "http://fcdo.example/mislabelled.odt": pdf_path.read_bytes(),  # labelled .odt, really a PDF
            "http://fcdo.example/page.odt": b"<html>Not found</html>",
        }
    )
    download_docs.main([], opener=opener)
    saved = sorted(p.name for p in (mel_settings.docs_dir / "iati").iterdir())
    assert saved == [
        "gb_1_annual_review_aaaaaa.odt",
        "gb_1_completion_bbbbbb.pdf",
    ]  # Excel never tried

    from mel_mcp.metadata import load_manifest

    assert set(load_manifest(mel_settings.manifest_file)) == {f"iati/{name}" for name in saved}


def test_odt_files_are_indexed_and_broken_files_are_skipped(mel_settings, monkeypatch, capsys):
    from dataclasses import replace

    s = replace(mel_settings, embedding_model=FakeEmbedder.name)
    monkeypatch.setattr(ingest, "settings", s)
    monkeypatch.setattr(ingest, "SentenceTransformerEmbedder", CountingEmbedder)
    write_odt(s.docs_dir / "iati" / "review.odt", REVIEW)
    make_pdf(s.docs_dir / "report.pdf", ["Yields rose"])
    (s.docs_dir / "broken.odt").write_bytes(b"not really a zip")
    s.manifest_file.write_text(
        "file,title,study_phase,source_type\n"
        "iati/review.odt,FCDO Annual Review,monitoring,evaluation_report\n"
    )

    ingest.main([])
    out = capsys.readouterr().out
    assert "broken.odt: could not be read" in out and "will be retried" in out
    hits = DocumentIndex(s, FakeEmbedder()).search("farmers trained target actual", top_k=5)
    review = next(h for h in hits if h.metadata["source"] == "iati/review.odt")
    assert "Farmers trained | 5000 | 5400" in review.text
    assert citation(review.metadata) == "FCDO Annual Review (monitoring)"

    ingest.main([])  # the broken file is tried again; the others are not re-embedded
    assert "broken.odt: could not be read" in capsys.readouterr().out
