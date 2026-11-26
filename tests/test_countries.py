"""Country names: one spelling everywhere, and multi-country documents."""

import csv
from dataclasses import replace

import pytest
from conftest import FakeEmbedder

from mel_mcp import download_docs, prepare_iati
from mel_mcp.documents import DocumentIndex, build_filter, load_state, matches, update_index
from mel_mcp.metadata import load_manifest, vocabulary
from mel_mcp.reading import Chunk, chunk_pages

MULTI_COUNTRY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<iati-activities version="2.03">
  <iati-activity>
    <iati-identifier>GB-GOV-1-300123</iati-identifier>
    <reporting-org><narrative>FCDO</narrative></reporting-org>
    <title><narrative>Commercial Agriculture for Smallholders and Agribusiness</narrative></title>
    <recipient-country code="UG"/>
    <recipient-country code="KE"/>
    <recipient-country code="KE"/>
    <document-link format="application/pdf" url="https://fcdo.example/annual-review-2023.pdf">
      <title><narrative>Annual review 2023</narrative></title>
      <category code="A07"/>
    </document-link>
  </iati-activity>
</iati-activities>
"""


def test_documents_list_every_country_once(mel_settings, tmp_path):
    xml = tmp_path / "multi.xml"
    xml.write_text(MULTI_COUNTRY_XML)
    prepare_iati.main([str(xml)])
    rows = list(
        csv.DictReader((mel_settings.data_dir / "iati_documents.csv").read_text().splitlines())
    )
    assert rows[0]["country"] == "Uganda; Kenya"  # all countries, no duplicates, Kenya not first


@pytest.fixture
def index(mel_settings):
    s = replace(mel_settings, embedding_model=FakeEmbedder.name)
    manifest = {
        "casa.pdf": {
            "title": "CASA annual review",
            "study_phase": "monitoring",
            "source_type": "evaluation_report",
            "country": "Uganda; Kenya",
        },
        "malawi.pdf": {
            "title": "Malawi report",
            "study_phase": "endline",
            "source_type": "evaluation_report",
            "country": "Malawi",
        },
    }
    from mel_mcp.countries import split_countries

    pages = [
        Chunk(
            f"Producer groups and market access. {f}",
            {**m, "source": f, "page_number": 1, "countries": split_countries(m["country"])},
        )
        for f, m in manifest.items()
    ]
    update_index(chunk_pages(pages, 800, 0), FakeEmbedder(), s, rebuild=True)
    return s, manifest, DocumentIndex(s, FakeEmbedder())


def test_country_filter_finds_multi_country_documents(index):
    s, manifest, idx = index
    for country, expected in (
        ("Kenya", ["casa.pdf"]),
        ("Uganda", ["casa.pdf"]),
        ("Malawi", ["malawi.pdf"]),
    ):
        hits = idx.search("market access", where=build_filter({"country": country}))
        assert [h.metadata["source"] for h in hits] == expected
    assert vocabulary(manifest)["country"] == ["Kenya", "Malawi", "Uganda"]
    assert matches(manifest["casa.pdf"], {"country": "Kenya"}, None, None)
    assert not matches(manifest["malawi.pdf"], {"country": "Kenya"}, None, None)


def test_indexes_from_before_this_change_are_rebuilt(index):
    s, _, idx = index
    meta = dict(idx.collection.metadata)
    meta.pop("index_schema")
    idx.collection.modify(metadata=meta)
    state, reason = load_state(s)
    assert state is None and "index_schema" in reason


def test_update_countries_fixes_only_the_country(mel_settings, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(download_docs, "settings", mel_settings)
    xml = tmp_path / "multi.xml"
    xml.write_text(MULTI_COUNTRY_XML)
    prepare_iati.main([str(xml)])
    mel_settings.manifest_file.parent.mkdir(parents=True, exist_ok=True)
    mel_settings.manifest_file.write_text(
        "file,title,study_phase,source_type,country,sample_size,url\n"
        "iati/casa.pdf,My edited title,monitoring,evaluation_report,Uganda,420,https://fcdo.example/annual-review-2023.pdf\n"
        "own.pdf,Own report,endline,evaluation_report,Kenya,,\n"
    )
    download_docs.main(["--update-countries"])
    assert "Updated the country of 1 manifest row(s)" in capsys.readouterr().out
    manifest = load_manifest(mel_settings.manifest_file)
    casa = manifest["iati/casa.pdf"]
    assert casa["country"] == "Uganda; Kenya"
    assert casa["title"] == "My edited title" and casa["sample_size"] == 420  # your edits are kept
    assert manifest["own.pdf"]["country"] == "Kenya"


def test_country_names_have_one_spelling():
    from mel_mcp.countries import canonical_countries, canonical_country, country_code

    for variant in ("Kenya", "KENYA", "kenya ", "Kenia", "KE", "ke"):
        assert canonical_country(variant) == "Kenya" and country_code(variant) == "KE"
    assert canonical_country("Tansania") == "Tanzania"
    assert canonical_country("UK") == "United Kingdom"
    assert canonical_countries("KENYA; Kenia; UG") == ["Kenya", "Uganda"]
    assert canonical_country("Atlantis") == "Atlantis"  # unknown names are kept as written
