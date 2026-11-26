"""The indicator tracker and its data checks.

The test data copies patterns found in real Kenyan IATI data: World Bank periods dated 1900
with 0 targets, an actual of 2,478 against a target of 400, and AfDB figures with no baseline.
"""

import csv

import pytest

from mel_mcp.indicators import IndicatorTracker, in_years
from mel_mcp.tables import DataError


@pytest.fixture
def iati_tracker(prepared_data):
    return IndicatorTracker(prepared_data.data_dir)


def test_indicator_progress_direction_and_freshness(iati_tracker):
    code = iati_tracker.find("maize yield")["indicators"][0]["indicator_code"]
    out = iati_tracker.get(code)
    assert out["baseline"] == {"year": "2021", "value": 500.0}
    assert [p["pct_of_target"] for p in out["periods"]] == [90.0, 102.9]
    assert out["direction"] == "higher is better" and out["tracker_updated"]

    loss = iati_tracker.get(iati_tracker.find("post-harvest")["indicators"][0]["indicator_code"])
    assert loss["direction"] == "lower is better" and loss["note"]
    assert len(iati_tracker.get(code, period="2023")["periods"]) == 1


def test_empty_indicators_are_skipped_unless_asked_for(iati_tracker):
    reported = iati_tracker.find("")["indicators"]
    assert {i["indicator_name"] for i in reported} == {
        "Average maize yield (kg per acre)",
        "Post-harvest loss (%)",
    }
    maize = next(i for i in reported if i["indicator_name"].startswith("Average maize"))
    assert maize["periods"] == 2 and maize["periods_with_actuals"] == 2

    everything = iati_tracker.find("", include_unusable=True)["indicators"]
    shell = next(i for i in everything if i["periods_with_actuals"] == 0)
    # Blank titles fall back to the result's description, with HTML removed.
    assert shell["indicator_name"] == "Prepare the final report"


TRACKER = """indicator_code,indicator_name,unit,ascending,project,country,baseline_year,baseline_value,period_start,period_end,target,actual,source
WB/R4/I1,Area provided with irrigation services,unit,1,Water Security,KE,,,1900-01-01,1900-01-01,0,0,IATI: WB
WB/R9/I1,Farmers trained in improved practices,unit,1,Water Security,KE,,,2021-01-01,2021-12-31,400,2478,IATI: WB
AF/R1/I1,Yield (Ton/Ha),unit,1,Irrigation,KE;UG,2019,1.2,2022-01-01,2022-12-31,2.5,2.1,IATI: AF
"""


@pytest.fixture
def tracker(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "indicators.csv").write_text(TRACKER)
    return IndicatorTracker(data)


def test_placeholder_and_doubtful_figures_are_flagged(tracker):
    irrigation = tracker.get("WB/R4/I1")
    warnings = " ".join(irrigation["data_quality"])
    assert "1900 looks like a placeholder" in warnings
    assert "probably nothing was reported" in warnings

    trained = tracker.get("WB/R9/I1")
    assert trained["periods"][0]["pct_of_target"] == 619.5
    assert any("619.5" in w for w in trained["data_quality"])

    assert tracker.get("AF/R1/I1")["data_quality"] == ["No problems detected."]


def test_only_usable_indicators_are_listed(tracker):
    out = tracker.find("")
    assert {i["indicator_code"] for i in out["indicators"]} == {"AF/R1/I1"}
    assert "2 matching indicator(s) have no usable period" in out["note"]
    everything = tracker.find("", include_unusable=True)["indicators"]
    assert [i["usable"] for i in everything] == [True, False, False]  # usable first
    empty = next(i for i in everything if i["indicator_code"] == "WB/R4/I1")
    assert empty["periods_with_actuals"] == 0 and empty["usable_periods"] == 0
    trained = next(i for i in everything if i["indicator_code"] == "WB/R9/I1")
    assert any("619.5" in issue for issue in trained["issues"])


def test_projects_summary_shows_which_projects_have_usable_data(tracker):
    out = tracker.find("")
    assert out["projects"] == [
        {
            "project": "Irrigation",
            "project_code": "AF",
            "countries": ["Kenya", "Uganda"],
            "usable_indicators": 1,
            "unusable_indicators": 0,
        },
        {
            "project": "Water Security",
            "project_code": "WB",
            "countries": ["Kenya"],
            "usable_indicators": 0,
            "unusable_indicators": 2,
        },
    ]
    assert out["projects_with_usable_data"] == 1 and "target above 0" in out["usable_rule"]


def test_get_indicator_says_whether_figures_can_be_used(tracker):
    trained = tracker.get("WB/R9/I1")
    assert trained["usable"] is False and trained["periods"][0]["usable"] is False
    assert "don't use these figures as evidence" in trained["evidence_rule"]
    good = tracker.get("AF/R1/I1")
    assert good["usable"] is True and good["evidence_rule"] == "All periods are usable as evidence."


def test_mixed_periods_and_baseline_repeats(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    header = TRACKER.splitlines()[0]
    (data / "indicators.csv").write_text(
        "\n".join(
            [
                header,
                "S/R7/I1,Certified seed (t),t,1,Seed,KE,2020,20081,2021-01-01,2021-12-31,35000,20081,IATI: AF",
                "S/R7/I1,Certified seed (t),t,1,Seed,KE,2020,20081,2022-01-01,2022-12-31,35000,24000,IATI: AF",
            ]
        )
        + "\n"
    )
    seed = IndicatorTracker(data).get("S/R7/I1")
    assert [p["usable"] for p in seed["periods"]] == [False, True]
    assert "baseline repeated" in seed["periods"][0]["issues"][0]
    assert (
        seed["usable_periods"] == 1
        and "Only the 1 period(s) marked usable" in seed["evidence_rule"]
    )


def test_country_filter_accepts_names_and_codes(tracker):
    assert tracker.find("", country="Uganda")["total_matches"] == 1  # the KE;UG project
    assert tracker.find("", country="KE", include_unusable=True)["total_matches"] == 3
    assert tracker.find("", country="kenya")["indicators"][0]["countries"][0] == "Kenya"
    assert tracker.find("", country="Malawi")["total_matches"] == 0


def test_totals_say_when_results_are_cut_off(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    rows = [TRACKER.splitlines()[0]] + [
        f"P/R1/I{i},Indicator {i},unit,1,Big project,KE,,,2022-01-01,2022-12-31,10,{i + 1},IATI: P"
        for i in range(30)
    ]
    (data / "indicators.csv").write_text("\n".join(rows) + "\n")
    out = IndicatorTracker(data).find("")
    assert out["total_matches"] == 30 and out["shown"] == 25 and len(out["indicators"]) == 25
    assert "first 25 of 30" in out["note"]


def test_projects_match_by_code_or_part_of_the_name(tracker):
    assert tracker.find("", project="water", include_unusable=True)["total_matches"] == 2
    assert tracker.find("", project="wb", include_unusable=True)["total_matches"] == 2
    assert tracker.find("", project="Irrigation")["projects"][0]["project_code"] == "AF"
    assert tracker.get("AF/R1/I1")["project_code"] == "AF"
    assert tracker.find("", project="Malawi dairy")["total_matches"] == 0


def test_unit_only_indicator_titles_get_a_real_name():
    import xml.etree.ElementTree as ET

    from mel_mcp.prepare_iati import indicator_name, is_unit_only

    assert all(is_unit_only(t) for t in ("Ton", "Number", "Ton/Ha", "(%)", "Yes/No", "Nb"))
    assert not any(
        is_unit_only(t) for t in ("Farmers trained", "Yield per ha", "Tons of fertiliser")
    )
    result = ET.fromstring(
        "<result><title><narrative>Fertiliser distributed to farmers</narrative></title>"
        "<indicator><title><narrative>Ton</narrative></title></indicator></result>"
    )
    assert (
        indicator_name(result.find("indicator"), result)
        == "Fertiliser distributed to farmers (Ton)"
    )
    alone = ET.fromstring(
        "<result><indicator><title><narrative>Number</narrative></title></indicator></result>"
    )
    assert indicator_name(alone.find("indicator"), alone) == "Number"  # nothing better to use


CAUTIONS = (
    TRACKER.splitlines()[0]
    + """
F/R1/I1,Fertilizer distributed (Ton),unit,1,Food facility,KE,,,2022-01-01,2025-12-31,600000,293442,IATI: AF
F/R2/I1,Farmers receiving extension (Number),unit,1,Food facility,KE,,,2022-01-01,2025-12-31,600000,0,IATI: AF
F/R3/I1,Farmers using improved technology (Number),unit,1,Food facility,KE,2022,0,2022-01-01,2025-12-31,600000,389080,IATI: AF
F/R4/I1,Food-secure population (Number),unit,1,Food facility,KE,2022,0,2022-01-01,2025-12-31,600000,389080,IATI: AF
F/R5/I1,Food-secure population - of which women (Number),percentage,1,Food facility,KE,2022,0,2022-01-01,2025-12-31,294000,190649.2,IATI: AF
F/R6/I1,Agricultural inputs provided (Tons),unit,1,Food facility,KE,2022,0,2022-01-01,2025-12-31,524000,342901.7,IATI: AF
Y/R2/I1,Business plans developed (Number),unit,1,Enable Youth,KE,2018,0,2018-01-01,2026-12-31,1955,195.5,IATI: AF
S/R1/I1,New irrigation area (ha),unit,1,Irrigation,KE,2015,0,2015-01-01,2026-12-31,1644,1326,IATI: AF
S/R2/I1,Old scheme area (ha),unit,1,Irrigation,KE,2015,0,2015-01-01,2018-12-31,500,450,IATI: AF
"""
)


@pytest.fixture
def cautious(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "indicators.csv").write_text(CAUTIONS)
    return IndicatorTracker(data)


def _cautions(t, code, **kwargs):
    return " ".join(t.get(code, **kwargs)["periods"][0]["cautions"])


def test_fractional_counts_are_not_usable(cautious):
    plans = cautious.get("Y/R2/I1")
    assert plans["usable"] is False and "isn't a whole number" in " ".join(plans["data_quality"])
    women = cautious.get("F/R5/I1")
    assert women["usable"] is False
    assert cautious.get("F/R6/I1")["usable"] is True  # 342,901.7 tons is fine


def test_usable_figures_carry_cautions_to_check_in_the_reports(cautious):
    fertiliser = cautious.get("F/R1/I1")
    assert fertiliser["usable"] and fertiliser["check_against_reports"]
    assert "No baseline is published" in _cautions(cautious, "F/R1/I1")
    assert "check those figures against the reports" in fertiliser["evidence_rule"]
    assert "The actual is 0" in _cautions(cautious, "F/R2/I1")
    assert "Food-secure population (Number) (F/R4/I1)" in _cautions(cautious, "F/R3/I1")
    assert "F/R3/I1" in _cautions(cautious, "F/R4/I1")
    assert _cautions(cautious, "F/R6/I1") == ""  # baseline published, nothing odd
    listed = {i["indicator_code"]: i for i in cautious.find("", project="food")["indicators"]}
    assert (
        listed["F/R1/I1"]["check_against_reports"]
        and not listed["F/R6/I1"]["check_against_reports"]
    )
    assert "prefer the report's figure" in cautious.find("")["caution_rule"]


def test_percentage_units_above_100_are_questioned(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "indicators.csv").write_text(
        TRACKER.splitlines()[0] + "\n"
        "P/R1/I1,Women reached,percentage,1,Food facility,KE,2022,0,2022-01-01,2025-12-31,294000,190649,IATI: AF\n"
    )
    assert "probably counts" in _cautions(IndicatorTracker(data), "P/R1/I1")


def test_year_from_drops_old_periods_and_flags_early_starts(cautious):
    shown = {i["indicator_code"] for i in cautious.find("", project="irrigation")["indicators"]}
    assert shown == {"S/R1/I1", "S/R2/I1"}
    out = cautious.find("", project="irrigation", year_from=2020)
    assert {i["indicator_code"] for i in out["indicators"]} == {"S/R1/I1"} and out[
        "year_from"
    ] == 2020
    assert "starts in 2015, before 2020" in _cautions(cautious, "S/R1/I1", year_from=2020)
    with pytest.raises(DataError, match="no period ending in or after 2020"):
        cautious.get("S/R2/I1", year_from=2020)


def test_percentages_and_averages_may_be_fractional(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "indicators.csv").write_text(
        TRACKER.splitlines()[0] + "\n"
        "P/R1/I1,Percentage of women farmers,percentage,1,P,KE,2022,0,2022-01-01,2025-12-31,50,45.5,IATI: X\n"
        "P/R2/I1,Average yield per farmer (kg),unit,1,P,KE,2022,0,2022-01-01,2025-12-31,900,812.4,IATI: X\n"
        "P/R3/I1,Women trained (Number),percentage,1,P,KE,2022,0,2022-01-01,2025-12-31,50,45.5,IATI: X\n"
    )
    t = IndicatorTracker(data)
    assert all(t.get(c)["usable"] for c in ("P/R1/I1", "P/R2/I1", "P/R3/I1"))


def test_year_filter_keeps_placeholder_dated_projects_visible(tracker):
    out = tracker.find("", year_from=2020)
    water = next(p for p in out["projects"] if p["project"] == "Water Security")
    assert water["usable_indicators"] == 0 and water["unusable_indicators"] == 2
    assert tracker.get("WB/R4/I1", year_from=2020)["usable"] is False


def test_an_actual_equal_to_the_target_is_questioned(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "indicators.csv").write_text(
        TRACKER.splitlines()[0] + "\n"
        "S/R5/I1,Beneficiary households (Number),unit,1,Irrigation,KE,2015,0,2020-01-01,2026-12-31,330942,330942,IATI: AF\n"
    )
    assert "exactly equals the target" in _cautions(IndicatorTracker(data), "S/R5/I1")


def test_prepare_iati_builds_tracker(prepared_data):
    rows = list(
        csv.DictReader((prepared_data.data_dir / "indicators.csv").read_text().splitlines())
    )
    assert (
        len(rows) == 4
    )  # 2 + 1 periods, plus 1 empty indicator; the other activity is filtered out
    assert {r["project"] for r in rows} == {"Test smallholder programme"}


def test_indicator_names_fall_back_and_lose_html():
    import xml.etree.ElementTree as ET

    from mel_mcp.prepare_iati import indicator_name

    result = ET.fromstring(
        "<result><title><narrative>Result title</narrative></title>"
        "<indicator><title><narrative/></title><description>"
        "<narrative>&amp;lt;b&amp;gt;Farmers trained&amp;lt;/b&amp;gt;</narrative>"
        "</description></indicator></result>"
    )
    assert (
        indicator_name(result.find("indicator"), result) == "Farmers trained"
    )  # double-escaped HTML handled


def _one_period(tmp_path, start, end, target, actual, baseline=""):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "indicators.csv").write_text(
        TRACKER.splitlines()[0] + "\n"
        f"P/R1/I1,Farmers trained,unit,1,P,KE,,{baseline},{start},{end},{target},{actual},IATI: X\n"
    )
    return IndicatorTracker(data).get("P/R1/I1")


@pytest.mark.parametrize(
    "target,actual,expected",
    [
        ("nan", "120", "target 'nan' isn't a number"),
        ("400", "inf", "actual 'inf' isn't a number"),
        ("400", "about 300", "actual 'about 300' isn't a number"),
        ("400", "", "No actual is reported"),
        ("", "", "Neither a target nor an actual"),
        ("-400", "300", "target is negative"),
        ("", "120", "An actual is reported without a target"),
    ],
)
def test_unusable_periods_always_say_why(tmp_path, target, actual, expected):
    out = _one_period(tmp_path, "2022-01-01", "2022-12-31", target, actual)
    assert out["usable"] is False
    assert any(expected in issue for issue in out["data_quality"])
    # Every reason ends with what to do about it.
    assert all(" in the reports" in i or "tracker" in i for i in out["data_quality"])


def test_a_baseline_that_isnt_a_number_counts_as_missing(tmp_path):
    out = _one_period(tmp_path, "2022-01-01", "2022-12-31", "400", "310", baseline="nan")
    assert out["baseline"]["value"] is None
    assert "No baseline is published" in " ".join(out["periods"][0]["cautions"])


def test_impossible_dates_are_flagged(tmp_path):
    out = _one_period(tmp_path, "2022-02-30", "2022-12-31", "400", "300")
    assert out["usable"] is False
    assert "'2022-02-30' isn't a real date" in out["data_quality"][0]


def test_copied_figures_compare_whole_periods(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "indicators.csv").write_text(
        TRACKER.splitlines()[0] + "\n"
        "P/R1/I1,Farmers trained,unit,1,P,KE,,,2022-01-01,2022-12-31,400,310,IATI: X\n"
        "P/R1/I1,Farmers trained,unit,1,P,KE,,,2022-01-01,2023-12-31,800,650,IATI: X\n"
        "P/R2/I1,Women trained,unit,1,P,KE,,,2022-01-01,2022-12-31,400,310,IATI: X\n"
    )
    periods = IndicatorTracker(data).get("P/R1/I1")["periods"]
    copied = ["copied" in " ".join(p["cautions"]) for p in periods]
    assert copied == [True, False]


def test_projects_with_the_same_name_are_counted_apart(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "indicators.csv").write_text(
        TRACKER.splitlines()[0] + "\n"
        "XM-A-1/R1/I1,Farmers trained,unit,1,Dairy support,KE,,,2022-01-01,2022-12-31,400,310,IATI: A\n"
        "XM-B-2/R1/I1,Farmers trained,unit,1,Dairy support,KE,,,2022-01-01,2022-12-31,500,420,IATI: B\n"
    )
    out = IndicatorTracker(data).find("")
    assert sorted(p["project_code"] for p in out["projects"]) == ["XM-A-1", "XM-B-2"]
    assert out["projects_with_usable_data"] == 2


def test_a_placeholder_end_date_keeps_the_period_whatever_its_start():
    # All this drama was because a WB project's period started in 2013 and ended in 1900 (issue #6).
    assert in_years({"period_start": "2013-01-01", "period_end": "1900-12-31"}, 2020)
    assert in_years({"period_start": "2013-01-01", "period_end": ""}, 2020)
    assert not in_years({"period_start": "2013-01-01", "period_end": "2018-12-31"}, 2020)


def test_a_period_with_no_end_date_is_not_usable(tmp_path):
    out = _one_period(tmp_path, "2013-01-01", "", "400", "300")
    assert out["usable"] is False
    assert "no end date" in out["data_quality"][0]
