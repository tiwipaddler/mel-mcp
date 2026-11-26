"""The document engine: chunking, indexing, filtered search, citations."""

import pytest
from conftest import FakeEmbedder

from mel_mcp.documents import DocumentIndex, build_filter, build_index, citation, matches
from mel_mcp.metadata import ManifestError, load_manifest
from mel_mcp.reading import Chunk, chunk_pages, split_text

EMBEDDER = FakeEmbedder()


def test_chunks_respect_size_and_break_at_sentences():
    text = " ".join(f"Sentence number {i} is about farmers." for i in range(60))
    chunks = split_text(text, size=200, overlap=0)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    assert all(c.endswith(".") for c in chunks)  # no sentence cut in half


def test_chunks_overlap():
    text = "\n\n".join(f"Paragraph {i}. " + "Words here. " * 5 for i in range(10))
    chunks = split_text(text, size=150, overlap=60)
    shared = [a for a, b in zip(chunks, chunks[1:], strict=False) if a[-30:] in b]
    assert shared, "consecutive chunks should share text"


def test_long_word_is_hard_split_and_empty_text_gives_nothing():
    assert split_text("x" * 250, size=100) == ["x" * 100, "x" * 100, "x" * 50]
    assert split_text("   \n  ", size=100) == []


def test_chunk_ids_are_unique_and_readable():
    pages = [
        Chunk("One. " * 100, {"source": "a.pdf", "page_number": 1}),
        Chunk("Two. " * 100, {"source": "a.pdf", "page_number": 2}),
    ]
    ids = [c.id for c in chunk_pages(pages, 200, 0)]
    assert len(ids) == len(set(ids))
    assert ids[0] == "a.pdf::p1::c0"


def test_manifest_parses_and_normalises(manifest_file):
    m = load_manifest(manifest_file)
    assert m["baseline.pdf"]["sample_size"] == 1200  # "1,200" becomes a number
    assert m["endline.pdf"]["study_phase"] == "endline"  # "Endline" is lower-cased
    assert "implementing_partner" not in m["baseline.pdf"]  # blanks are left out


def test_manifest_rejects_unknown_phase(tmp_path):
    path = tmp_path / "manifest.csv"
    path.write_text("file,title,study_phase,source_type\na.pdf,A,midterm,evaluation_report\n")
    with pytest.raises(ManifestError, match="midterm"):
        load_manifest(path)


@pytest.fixture
def index(mel_settings, manifest_file):
    manifest = load_manifest(manifest_file)
    pages = [
        Chunk(
            f"Farmers reported barriers to adoption such as credit. {f}",
            {
                **{k: v for k, v in manifest[f].items() if k != "file"},
                "source": f,
                "page_number": 3,
            },
        )
        for f in manifest
    ]
    build_index(chunk_pages(pages, 800, 0), EMBEDDER, mel_settings)
    return DocumentIndex(mel_settings, EMBEDDER)


def test_rebuilding_does_not_duplicate(index, mel_settings, manifest_file):
    pages = [Chunk("text", {"source": "baseline.pdf", "page_number": 1})]
    build_index(chunk_pages(pages, 800, 0), EMBEDDER, mel_settings)
    build_index(chunk_pages(pages, 800, 0), EMBEDDER, mel_settings)
    assert DocumentIndex(mel_settings, EMBEDDER).info()["chunks_indexed"] == 1


def test_phase_and_year_filters_keep_phases_apart(index):
    hits = index.search("barriers", where=build_filter({"study_phase": "baseline"}))
    assert [h.metadata["source"] for h in hits] == ["baseline.pdf"]
    hits = index.search("barriers", where=build_filter(year_from=2022))
    assert [h.metadata["source"] for h in hits] == ["endline.pdf"]


def test_citation_includes_phase_year_and_n(index):
    hit = index.search("barriers", where=build_filter({"study_phase": "baseline"}))[0]
    assert citation(hit.metadata) == "Test Baseline Report, p. 3 (baseline, 2021, n=1,200)"


def test_index_info_shows_freshness(index):
    info = index.info()
    assert (
        info["chunks_indexed"] == 2
        and info["built_at"]
        and info["embedding_model"] == "fake-embedder"
    )


def test_missing_index_and_model_mismatch_give_clear_errors(mel_settings, index):
    class OtherEmbedder(FakeEmbedder):
        name = "another-model"

    with pytest.raises(RuntimeError, match="built with"):
        DocumentIndex(mel_settings, OtherEmbedder())
    from dataclasses import replace

    with pytest.raises(RuntimeError, match="mel-ingest"):
        DocumentIndex(replace(mel_settings, collection="does-not-exist"), EMBEDDER)


def test_filters_reject_unknown_fields_and_match_manifest(manifest_file):
    with pytest.raises(ValueError, match="Can't filter"):
        build_filter({"respondent": "x"})
    meta = load_manifest(manifest_file)["endline.pdf"]
    assert matches(meta, {"country": "Kenya"}, 2022, None)
    assert not matches(meta, {"study_phase": "midline"}, None, None)


def test_pdf_page_labels_are_kept(tmp_path):
    # Reports often number their front pages i, ii, iii before page 1.
    from pypdf import PdfReader, PdfWriter
    from test_ingest import make_pdf

    from mel_mcp.reading import load_pdf

    plain = tmp_path / "plain.pdf"
    make_pdf(plain, ["Contents", "Introduction", "Findings"])
    writer = PdfWriter(clone_from=PdfReader(plain))
    writer.set_page_label(0, 0, style="/r")
    writer.set_page_label(1, 2, style="/D", start=1)
    labelled = tmp_path / "labelled.pdf"
    writer.write(labelled)

    pages = load_pdf(labelled, {"title": "Report"})
    assert [p.metadata["page_label"] for p in pages] == ["i", "1", "2"]
    assert [p.metadata["page_number"] for p in pages] == [1, 2, 3]
