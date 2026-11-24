"""fetch_iati with the IATI API replaced by a fake (no network, no key needed)."""

import csv
import io
import json

import pytest
from conftest import IATI_XML

from mel_mcp import fetch_iati


def test_query_building():
    q = fetch_iati.build_query(["ke", "MW"], ["311"], "2018-01-01")
    assert q == (
        "recipient_country_code:(KE MW) AND (sector_code:311*) AND "
        "activity_date_iso_date:[2018-01-01T00:00:00Z TO *]"
    )
    assert fetch_iati.build_query([], [], None) == "*:*"


def fake_api(pages):
    """An opener that returns Solr-style JSON pages and records each request."""
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return io.BytesIO(
            json.dumps(pages[len(calls) - 1]).encode()
        )  # BytesIO works as a context manager

    return opener, calls


def activity_xml(identifier: str) -> str:
    start = IATI_XML.index("<iati-activity>")
    end = IATI_XML.index("</iati-activity>") + len("</iati-activity>")
    return '<?xml version="1.0"?>' + IATI_XML[start:end].replace("XM-TEST-001", identifier)


def test_fetch_pages_through_results_and_sends_the_key():
    pages = [
        {
            "response": {
                "numFound": 3,
                "docs": [{"iati_xml": activity_xml("A-1")}, {"iati_xml": [activity_xml("A-2")]}],
            }
        },
        {"response": {"numFound": 3, "docs": [{"iati_xml": activity_xml("A-3")}]}},
    ]
    opener, calls = fake_api(pages)
    activities, found = fetch_iati.fetch_activities(
        "q", "secret", rows=2, max_pages=5, opener=opener, pause=0
    )
    assert found == 3 and len(activities) == 3 and len(calls) == 2
    assert calls[0].get_header("Ocp-apim-subscription-key") == "secret"
    assert "start=2" in calls[1].full_url
    assert not activities[0].startswith("<?xml")  # declarations stripped before combining


def test_fetched_xml_feeds_the_indicator_tracker(mel_settings, tmp_path, monkeypatch):
    pages = [{"response": {"numFound": 1, "docs": [{"iati_xml": activity_xml("XM-TEST-001")}]}}]
    opener, _ = fake_api(pages)
    monkeypatch.setattr(fetch_iati, "urlopen", opener)
    monkeypatch.setattr(fetch_iati, "CALL_PAUSE_SECONDS", 0)
    monkeypatch.setenv("IATI_API_KEY", "secret")

    out = tmp_path / "raw.xml"
    fetch_iati.main(["--country", "KE", "--sector", "311", "--out", str(out)])
    assert out.read_text().startswith('<?xml version="1.0" encoding="UTF-8"?>\n<iati-activities')
    rows = list(csv.DictReader((mel_settings.data_dir / "indicators.csv").read_text().splitlines()))
    assert len(rows) == 4  # includes one empty indicator


def test_missing_key_explains_what_to_do(monkeypatch):
    monkeypatch.delenv("IATI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="developer.iatistandard.org"):
        fetch_iati.main(["--country", "KE"])


def test_requests_carry_a_user_agent():
    pages = [{"response": {"numFound": 0, "docs": []}}]
    opener, calls = fake_api(pages)
    fetch_iati.fetch_activities("q", "secret", opener=opener, pause=0)
    assert "mel-evidence-mcp" in calls[0].get_header("User-agent")


def test_api_errors_show_iatis_reason():
    from urllib.error import HTTPError

    def refusing_opener(request, timeout):
        raise HTTPError(
            request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"message": "Blocked by policy"}')
        )

    with pytest.raises(SystemExit, match="Blocked by policy"):
        fetch_iati.fetch_activities("q", "secret", opener=refusing_opener, pause=0)


def test_with_docs_limits_to_projects_linking_reports():
    q = fetch_iati.build_query(["KE"], ["311"], None, ["A07", "A08"])
    assert q == (
        "recipient_country_code:(KE) AND (sector_code:311*) AND "
        "(document_link_category_code:A07 OR document_link_category_code:A08)"
    )
