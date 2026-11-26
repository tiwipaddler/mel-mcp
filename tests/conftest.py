"""Shared test fixtures. Everything here is synthetic and made up for testing."""

import hashlib
import math
from dataclasses import replace

import pandas as pd
import pytest

from mel_mcp import prepare_iati, prepare_survey
from mel_mcp.config import settings

IATI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<iati-activities version="2.03">
  <iati-activity>
    <iati-identifier>XM-TEST-001</iati-identifier>
    <reporting-org ref="XM-DONOR" type="10"><narrative>Test Donor Agency</narrative></reporting-org>
    <title><narrative>Test smallholder programme</narrative></title>
    <participating-org role="1"><narrative>Test Donor Agency</narrative></participating-org>
    <participating-org role="4"><narrative>Test Farmers Cooperative</narrative></participating-org>
    <recipient-country code="KE"/>
    <document-link format="application/pdf" url="https://example.org/docs/midterm-review.pdf">
      <title><narrative>Mid-term Evaluation Report</narrative></title>
      <category code="A07"/>
      <language code="en"/>
      <document-date iso-date="2022-06-30"/>
    </document-link>
    <document-link format="text/html" url="https://example.org/project-page">
      <title><narrative>Project web page</narrative></title>
      <category code="A12"/>
    </document-link>
    <document-link format="" url="https://example.org/docs/annual-results-2023">
      <title><narrative>Annual results report 2023</narrative></title>
      <category code="A08"/>
      <document-date iso-date="2024-03-01"/>
    </document-link>
    <result type="2">
      <title><narrative>Higher yields</narrative></title>
      <indicator measure="1" ascending="1">
        <title><narrative>Average maize yield (kg per acre)</narrative></title>
        <baseline year="2021" value="500"/>
        <period>
          <period-start iso-date="2022-01-01"/><period-end iso-date="2022-12-31"/>
          <target value="600"/><actual value="540"/>
        </period>
        <period>
          <period-start iso-date="2023-01-01"/><period-end iso-date="2023-12-31"/>
          <target value="700"/><actual value="720"/>
        </period>
      </indicator>
      <indicator measure="2" ascending="0">
        <title><narrative>Post-harvest loss (%)</narrative></title>
        <baseline year="2021" value="30"/>
        <period>
          <period-start iso-date="2023-01-01"/><period-end iso-date="2023-12-31"/>
          <target value="20"/><actual value="18"/>
        </period>
      </indicator>
    </result>
    <result type="1">
      <title><narrative/></title>
      <description><narrative>&lt;p&gt;Prepare the final report&lt;/p&gt;</narrative></description>
      <indicator measure="1">
        <title><narrative/></title>
      </indicator>
    </result>
  </iati-activity>
  <iati-activity>
    <iati-identifier>XM-OTHER-002</iati-identifier>
    <title><narrative>Other programme</narrative></title>
  </iati-activity>
</iati-activities>
"""

MANIFEST = """file,title,project,donor,implementing_partner,study_phase,reporting_year,country,source_type,sample_size,url
baseline.pdf,Test Baseline Report,Test programme,Test donor,,baseline,2021,Kenya,evaluation_report,"1,200",
endline.pdf,Test Endline Report,Test programme,Test donor,,Endline,2023,Kenya,evaluation_report,1150,
"""


class FakeEmbedder:
    """Deterministic stand-in for the embedding model: hashes words into a vector."""

    name = "fake-embedder"

    def embed(self, texts):
        vectors = []
        for text in texts:
            v = [0.0] * 32
            for word in text.lower().split():
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 32] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v])
        return vectors


def synthetic_survey(round_: str = "baseline") -> pd.DataFrame:
    """40 households in Alpha, 30 in Beta, 5 in Gamma (too few to report).

    The endline round has deliberate drift: yield renamed, gender recoded,
    land recorded in square metres instead of acres, fertiliser use mostly
    missing, and a new irrigation question.
    """
    endline = round_ == "endline"
    rows = []
    for county, n in (("Alpha", 40), ("Beta", 30), ("Gamma", 5)):
        for i in range(n):
            k = len(rows)
            female = bool(i % 2)
            row = {
                "hh_id": k + 1,
                "respondent_name": f"Person {k + 1}",
                "phone_number": f"07000{k:05d}",
                "county": county,
                "gender": ("F" if female else "M") if endline else ("Female" if female else "Male"),
                ("maize_yield_kg" if endline else "yield_kg"): 400.0
                + (100 if county == "Alpha" else 0)
                + i,
                "land_acres": (2.0 + i % 3) * (4047 if endline else 1),
                "used_fert": (None if endline and i % 5 else (1 if i % 4 else 0)),
                "main_crop": "maize" if i < 25 else ("beans" if i < 28 else "sorghum"),
            }
            if endline:
                row["irrigation"] = i % 2
            rows.append(row)
    df = pd.DataFrame(rows)
    df["county"] = pd.Categorical(df["county"])  # becomes a Stata value label
    return df


LABELS = {
    "yield_kg": "Maize yield (kg per acre)",
    "maize_yield_kg": "Maize yield (kg per acre)",
    "used_fert": "Used fertiliser (1=yes)",
    "land_acres": "Land cultivated",
}


@pytest.fixture
def mel_settings(tmp_path, monkeypatch):
    s = replace(
        settings,
        docs_dir=tmp_path / "docs",
        manifest_file=tmp_path / "docs" / "manifest.csv",
        data_dir=tmp_path / "data",
        db_dir=tmp_path / "db",
        reports_dir=tmp_path / "reports",
        audit_log=tmp_path / "logs" / "audit.jsonl",
        collection="test-reports",
        score_threshold=-1.0,  # fake embeddings: keep every hit
        min_cell_size=10,
        user="tester",
    )
    monkeypatch.setattr(prepare_survey, "settings", s)
    monkeypatch.setattr(prepare_iati, "settings", s)
    return s


def _prepare(tmp_path, round_: str, year: str) -> None:
    dta = tmp_path / f"survey_{round_}.dta"
    df = synthetic_survey(round_)
    df.to_stata(
        dta, write_index=False, variable_labels={k: v for k, v in LABELS.items() if k in df.columns}
    )
    prepare_survey.main(
        [
            str(dta),
            "--name",
            f"test_{round_}",
            "--title",
            f"Synthetic {round_} survey",
            "--phase",
            round_,
            "--project",
            "Test programme",
            "--country",
            "Kenya",
            "--year",
            year,
        ]
    )


@pytest.fixture
def prepared_data(mel_settings, tmp_path):
    """Run the preparation scripts the way a user would: two rounds + indicators."""
    _prepare(tmp_path, "baseline", "2021")
    _prepare(tmp_path, "endline", "2023")
    xml = tmp_path / "activities.xml"
    xml.write_text(IATI_XML, encoding="utf-8")
    prepare_iati.main([str(xml), "--activity", "XM-TEST"])
    return mel_settings


@pytest.fixture
def manifest_file(mel_settings):
    mel_settings.docs_dir.mkdir(parents=True, exist_ok=True)
    mel_settings.manifest_file.write_text(MANIFEST, encoding="utf-8")
    return mel_settings.manifest_file


@pytest.fixture(autouse=True)
def close_databases():
    """Close each test's Chroma databases when it ends.

    Every test builds its own small index. Without this, open files pile up
    until the operating system's limit is hit; macOS allows only 256 by default.
    """
    yield
    import gc

    from chromadb.api.client import SharedSystemClient

    from mel_mcp.documents import _client

    _client.cache_clear()
    SharedSystemClient.clear_system_cache()
    gc.collect()
