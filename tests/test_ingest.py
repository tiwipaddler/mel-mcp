"""mel-ingest: progress output and incremental updates, on real (tiny) PDFs."""

import pytest
from conftest import FakeEmbedder

from mel_mcp import ingest
from mel_mcp.documents import DocumentIndex


def make_pdf(path, pages: list[str]) -> None:
    """Write a small, valid PDF with one line of text per page (no parentheses in text)."""
    n = len(pages)
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{4 + 2 * i} 0 R' for i in range(n))}] /Count {n} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


class CountingEmbedder(FakeEmbedder):
    created = 0
    embedded = 0

    def __init__(self, model_name=None):
        CountingEmbedder.created += 1

    def embed(self, texts):
        CountingEmbedder.embedded += len(texts)
        return super().embed(texts)


@pytest.fixture
def setup(mel_settings, monkeypatch):
    from dataclasses import replace

    s = replace(mel_settings, embedding_model=FakeEmbedder.name)
    monkeypatch.setattr(ingest, "settings", s)
    monkeypatch.setattr(ingest, "SentenceTransformerEmbedder", CountingEmbedder)
    CountingEmbedder.created = CountingEmbedder.embedded = 0
    make_pdf(s.docs_dir / "a.pdf", ["Farmers adopted improved seed", "Yields rose in Machakos"])
    make_pdf(s.docs_dir / "iati" / "b.pdf", ["Water storage was completed late"])
    s.manifest_file.write_text(
        "file,title,study_phase,source_type,sample_size\n"
        "a.pdf,Report A,endline,evaluation_report,500\n"
        "iati/b.pdf,Report B,monitoring,donor_report,\n"
    )
    return s


def run(capsys, *args) -> str:
    CountingEmbedder.embedded = 0
    ingest.main(list(args))
    return capsys.readouterr().out


def test_first_run_shows_every_step_and_progress(setup, capsys):
    out = run(capsys)
    assert "Full rebuild (no index yet)" in out
    for n in range(1, 6):
        assert f"[{n}/5]" in out
    assert "a.pdf: 2 page(s)" in out and "chunks (" in out and "left)" in out
    assert DocumentIndex(setup, FakeEmbedder()).info()["chunks_indexed"] == 3


def test_second_run_does_nothing_and_skips_the_model(setup, capsys):
    run(capsys)
    created = CountingEmbedder.created
    out = run(capsys)
    assert "up to date" in out
    assert CountingEmbedder.created == created and CountingEmbedder.embedded == 0


def test_only_new_reports_are_embedded(setup, capsys):
    run(capsys)
    make_pdf(setup.docs_dir / "c.pdf", ["Training reached 300 women"])
    out = run(capsys)
    assert "2 unchanged, 1 new" in out
    assert CountingEmbedder.embedded == 1  # only the new report's single chunk
    assert DocumentIndex(setup, FakeEmbedder()).info()["chunks_indexed"] == 4


def test_editing_the_manifest_updates_details_without_re_embedding(setup, capsys):
    run(capsys)
    created = CountingEmbedder.created
    setup.manifest_file.write_text(
        setup.manifest_file.read_text().replace("evaluation_report,500", "evaluation_report,750")
    )
    out = run(capsys)
    assert "1 unchanged, 0 new, 0 changed, 1 with new details only" in out
    assert "Updated details on 2 passage(s) from 1 document(s)" in out
    assert (
        CountingEmbedder.embedded == 0 and CountingEmbedder.created == created
    )  # model never loaded
    hits = DocumentIndex(setup, FakeEmbedder()).search("seed", top_k=5)
    a = [h.metadata for h in hits if h.metadata["source"] == "a.pdf"]
    assert {m["sample_size"] for m in a} == {750}
    assert sorted(m["page_number"] for m in a) == [1, 2]  # page details kept
    assert DocumentIndex(setup, FakeEmbedder()).info()["chunks_indexed"] == 3
    assert "up to date" in run(capsys)  # the new details were recorded


def test_fields_removed_from_the_manifest_are_removed_from_the_index(setup, capsys):
    run(capsys)
    setup.manifest_file.write_text(
        setup.manifest_file.read_text().replace("evaluation_report,500", "evaluation_report,")
    )
    run(capsys)
    hits = DocumentIndex(setup, FakeEmbedder()).search("seed", top_k=5)
    assert all("sample_size" not in h.metadata for h in hits if h.metadata["source"] == "a.pdf")


def test_a_changed_file_is_re_embedded(setup, capsys):
    import os

    run(capsys)
    path = setup.docs_dir / "iati" / "b.pdf"
    make_pdf(path, ["Water storage was completed late in 2023"])
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
    out = run(capsys)
    assert "1 changed, 0 with new details only" in out and CountingEmbedder.embedded == 1


def test_country_spellings_are_standardised(setup, capsys):
    from mel_mcp.documents import build_filter

    setup.manifest_file.write_text(
        "file,title,study_phase,source_type,country\n"
        "a.pdf,Report A,endline,evaluation_report,KENYA\n"
        "iati/b.pdf,Report B,monitoring,donor_report,Kenia; UG\n"
    )
    run(capsys)
    index = DocumentIndex(setup, FakeEmbedder())
    hits = index.search("seed", where=build_filter({"country": "kenya"}), top_k=5)
    assert {h.metadata["source"] for h in hits} == {"a.pdf", "iati/b.pdf"}
    hits = index.search("seed", where=build_filter({"country": "Uganda"}), top_k=5)
    assert {h.metadata["source"] for h in hits} == {"iati/b.pdf"}


def test_deleted_reports_leave_the_index(setup, capsys):
    run(capsys)
    (setup.docs_dir / "iati" / "b.pdf").unlink()
    out = run(capsys)
    assert "1 removed" in out
    assert DocumentIndex(setup, FakeEmbedder()).info()["chunks_indexed"] == 2


def test_changed_chunk_settings_force_a_rebuild(setup, capsys, monkeypatch):
    from dataclasses import replace

    run(capsys)
    monkeypatch.setattr(ingest, "settings", replace(setup, chunk_size=500))
    out = run(capsys)
    assert "Full rebuild (chunk_size changed" in out and CountingEmbedder.embedded == 3


def test_rebuild_flag_and_interrupted_runs(setup, capsys):
    run(capsys)
    assert "Full rebuild (--rebuild was given)" in run(capsys, "--rebuild")
    # A run that dies before saving its state is simply redone next time.
    state = setup.db_dir / f"{setup.collection}.state.json"
    state.unlink()
    out = run(capsys)
    assert "no record of which files it holds" in out
    assert DocumentIndex(setup, FakeEmbedder()).info()["chunks_indexed"] == 3


def test_a_report_that_fails_to_read_can_still_be_removed_later(setup, capsys):
    run(capsys)
    broken = setup.docs_dir / "iati" / "b.pdf"
    broken.write_bytes(b"not a pdf")
    assert "could not be read" in run(capsys)
    broken.unlink()
    assert "1 removed" in run(capsys)
    assert DocumentIndex(setup, FakeEmbedder()).info()["chunks_indexed"] == 2


def test_an_interrupted_rebuild_is_started_again(setup, capsys, monkeypatch):
    run(capsys)
    embed = CountingEmbedder.embed

    def fail(self, texts):
        raise KeyboardInterrupt

    monkeypatch.setattr(CountingEmbedder, "embed", fail)
    with pytest.raises(KeyboardInterrupt):
        run(capsys, "--rebuild")
    monkeypatch.setattr(CountingEmbedder, "embed", embed)
    assert "no record of which files it holds" in run(capsys)
    assert DocumentIndex(setup, FakeEmbedder()).info()["chunks_indexed"] == 3


def test_emptying_the_folder_empties_the_index(setup, capsys):
    run(capsys)
    for pdf in setup.docs_dir.rglob("*.pdf"):
        pdf.unlink()
    assert "no PDF or .odt files" in run(capsys)
    with pytest.raises(RuntimeError, match="index is empty"):
        DocumentIndex(setup, FakeEmbedder())
