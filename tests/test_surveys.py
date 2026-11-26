"""Survey data: preparation, aggregates and privacy rules."""

import csv

import pytest

from mel_mcp.surveys import SurveyData
from mel_mcp.tables import DataError


@pytest.fixture
def surveys(prepared_data):
    return SurveyData(prepared_data.data_dir, prepared_data.min_cell_size)


def test_prepare_survey_guesses_safe_roles(prepared_data):
    path = prepared_data.data_dir / "test_baseline.dictionary.csv"
    roles = {r["variable"]: r for r in csv.DictReader(path.read_text().splitlines())}
    assert roles["hh_id"]["role"] == "id"
    assert roles["respondent_name"]["role"] == "exclude"
    assert roles["phone_number"]["role"] == "exclude"
    assert roles["county"]["role"] == "group"
    assert roles["yield_kg"]["role"] == "measure"
    assert roles["yield_kg"]["label"] == "Maize yield (kg per acre)"  # Stata label kept


def test_prepare_survey_keeps_an_edited_dictionary(prepared_data, tmp_path):
    from mel_mcp import prepare_survey

    path = prepared_data.data_dir / "test_baseline.dictionary.csv"
    path.write_text(
        path.read_text().replace(
            "yield_kg,Maize yield (kg per acre),measure", "yield_kg,Edited,exclude"
        )
    )
    prepare_survey.main(
        [
            str(tmp_path / "survey_baseline.dta"),
            "--name",
            "test_baseline",
            "--title",
            "T",
            "--phase",
            "baseline",
        ]
    )
    assert "yield_kg,Edited,exclude" in path.read_text()


def test_mean_by_group_suppresses_small_cells(surveys):
    out = surveys.aggregate("test_baseline", "yield_kg", "mean", group_by="county")
    groups = {r["group"]: r for r in out["results"]}
    assert set(groups) == {"Alpha", "Beta"}
    assert groups["Alpha"]["n"] == 40
    assert groups["Alpha"]["value"] == pytest.approx(519.5)
    assert out["suppressed_groups"] == ["Gamma"]  # its size is not revealed
    assert out["source"]["data_updated"]  # freshness


def test_share_of_binary_variable(surveys):
    out = surveys.aggregate("test_baseline", "used_fert", "share", filters={"county": "Alpha"})
    assert out["results"][0]["value"] == pytest.approx(75.0)
    assert out["unit"] == "% of respondents"


def test_share_of_categories_suppresses_small_categories(surveys):
    shares = surveys.aggregate("test_baseline", "main_crop", "share", filters={"county": "Alpha"})[
        "results"
    ][0]["value"]
    assert shares["maize"] == pytest.approx(62.5)  # 25 of 40
    assert str(shares["beans"]).startswith("suppressed")  # only 3 households


def test_identifying_variables_are_never_available(surveys):
    for variable in ("hh_id", "respondent_name", "phone_number"):
        with pytest.raises(DataError):
            surveys.aggregate("test_baseline", variable, "count")
    with pytest.raises(DataError):
        surveys.aggregate("test_baseline", "yield_kg", "mean", group_by="hh_id")
    with pytest.raises(DataError):
        surveys.aggregate(
            "test_baseline", "yield_kg", "mean", filters={"respondent_name": "Person 1"}
        )
    public = {v["variable"] for v in surveys.public_dictionary("test_baseline")}
    assert public.isdisjoint({"hh_id", "respondent_name", "phone_number"})


def test_filtering_down_to_a_small_group_is_suppressed(surveys):
    out = surveys.aggregate("test_baseline", "yield_kg", "mean", filters={"county": "Gamma"})
    assert out["results"] == [] and out["suppressed_groups"] == ["all respondents"]


def test_helpful_errors(surveys):
    with pytest.raises(DataError, match="Unknown dataset"):
        surveys.aggregate("nope", "yield_kg", "mean")
    with pytest.raises(DataError, match="not a 0/1"):
        surveys.aggregate("test_baseline", "yield_kg", "share")


def test_slow_queries_are_stopped(prepared_data):
    surveys = SurveyData(prepared_data.data_dir, 10, query_timeout_seconds=0.2)
    ds = surveys._dataset("test_baseline")
    with pytest.raises(DataError, match="stopped after"):
        with surveys._query(ds) as con:
            con.execute("SELECT count(*) FROM range(100000000000) a").fetchall()


def test_profile_covers_only_group_and_measure_variables(surveys):
    profile = surveys.profile("test_baseline")
    assert not {"respondent_name", "phone_number", "hh_id"} & set(profile)
    # Gamma has fewer than 10 respondents, so it is counted but not named.
    assert profile["county"]["categories"] == ["Alpha", "Beta"]
    assert profile["county"]["rare_categories"] == 1
    assert profile["yield_kg"]["mean"] > 0


def test_a_small_category_cannot_be_worked_out_by_subtraction(surveys):
    # Gamma has 5 respondents. If Alpha and Beta were both shown, 100% minus them is Gamma.
    [result] = surveys.aggregate("test_baseline", "county", "share")["results"]
    shares = result["value"]
    assert shares["Gamma"] == "suppressed (n<10)"
    assert shares["Beta"] == "suppressed (protects a small category)"  # the next smallest
    assert isinstance(shares["Alpha"], float)
