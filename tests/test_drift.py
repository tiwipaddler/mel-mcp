"""Drift checks and the LangGraph drift agent."""

import json

import pytest

from mel_mcp import drift
from mel_mcp.agents.drift_agent import build_graph, plan_pairs, template_summary
from mel_mcp.surveys import SurveyData


@pytest.fixture
def engine(prepared_data):
    return SurveyData(prepared_data.data_dir, prepared_data.min_cell_size)


def by_type(result) -> dict:
    out = {}
    for f in result["findings"]:
        out.setdefault(f["type"], []).append(f)
    return out


def test_compare_finds_the_planted_drift(engine):
    found = by_type(drift.compare(engine, "test_baseline", "test_endline"))

    rename = found["possible_rename"][0]
    assert rename["variable"] == "yield_kg" and rename["evidence"]["renamed_to"] == "maize_yield_kg"
    assert rename["severity"] == "high"

    recode = found["recoded"][0]
    assert (
        recode["variable"] == "gender" and recode["severity"] == "high"
    )  # no categories in common
    assert recode["evidence"]["new_categories"] == ["F", "M"]

    assert found["possible_unit_change"][0]["variable"] == "land_acres"  # acres → m²
    assert found["missingness_jump"][0]["variable"] == "used_fert"
    assert found["added"][0]["variable"] == "irrigation"
    assert "removed" not in found  # the rename was recognised, not reported twice


def test_comparisons_never_mention_private_variables(engine):
    import json

    result = json.dumps(drift.compare(engine, "test_baseline", "test_endline"))
    for private in ("respondent_name", "phone_number", "hh_id"):
        assert private not in result


def test_same_round_has_no_drift(engine):
    assert drift.compare(engine, "test_baseline", "test_baseline")["findings"] == []


def test_readers_affected_by_a_rename(engine, prepared_data):
    (prepared_data.data_dir / "variable_usage.csv").write_text(
        "reader,dataset,variable\nIndicator 1.1 maize yield,,yield_kg\nRegression script,test_other,yield_kg\n"
    )
    usage = drift.load_usage(prepared_data.data_dir)
    rename = by_type(drift.compare(engine, "test_baseline", "test_endline"))["possible_rename"][0]
    # The second reader is for a different dataset, so it isn't affected.
    assert drift.affected_readers(rename, ("test_baseline", "test_endline"), usage) == [
        "Indicator 1.1 maize yield"
    ]


def test_agent_plans_consecutive_rounds(engine):
    assert plan_pairs(engine) == [["test_baseline", "test_endline"]]


def test_datasets_without_a_project_are_not_paired(engine, monkeypatch):
    datasets = [
        {"name": "a", "study_phase": "baseline"},
        {"name": "b", "study_phase": "endline"},
        {"name": "c", "project": "Dairy", "study_phase": "baseline"},
        {"name": "d", "project": "Dairy", "study_phase": "endline"},
    ]
    monkeypatch.setattr(engine, "list_datasets", lambda: datasets)
    assert plan_pairs(engine) == [["c", "d"]]


def test_two_runs_in_the_same_second_keep_both_reports(engine, prepared_data):
    from mel_mcp.agents.drift_agent import write_report

    state = {"summary": "s", "findings": [], "pairs": []}
    first = write_report(state, prepared_data.reports_dir)
    second = write_report(state, prepared_data.reports_dir)
    assert first != second and first.exists() and second.exists()
    assert drift.latest_report(prepared_data.reports_dir) == second


def test_agent_raises_an_alert_with_affected_readers(engine, prepared_data):
    (prepared_data.data_dir / "variable_usage.csv").write_text(
        "reader,dataset,variable\nIndicator 2.3 fertiliser use,,used_fert\n"
    )
    agent = build_graph(engine, prepared_data, template_summary)
    result = agent.invoke({})

    assert result["status"] == "drift"
    fert = next(
        f
        for f in result["findings"]
        if f["variable"] == "used_fert" and f["type"] == "missingness_jump"
    )
    # A missingness jump is medium on its own; something depends on it, so it's high.
    assert fert["severity"] == "high" and fert["affected_readers"] == [
        "Indicator 2.3 fertiliser use"
    ]

    report = prepared_data.reports_dir / result["report_path"].split("/")[-1]
    text = report.read_text()
    assert "yield_kg" in text and "Indicator 2.3" in text
    last_run = json.loads((prepared_data.reports_dir / "last_run.json").read_text())
    assert last_run["status"] == "drift" and last_run["report"] == report.name
    assert drift.latest_report(prepared_data.reports_dir) == report


def test_agent_all_clear_writes_no_report(engine, prepared_data):
    result = build_graph(engine, prepared_data).invoke(
        {"pairs": [["test_baseline", "test_baseline"]]}
    )
    assert result["status"] == "clear"
    assert drift.latest_report(prepared_data.reports_dir) is None
    assert (
        json.loads((prepared_data.reports_dir / "last_run.json").read_text())["status"] == "clear"
    )


def test_agent_falls_back_when_the_llm_fails(engine, prepared_data):
    def broken_summariser(findings):
        raise ConnectionError("API unreachable")

    result = build_graph(engine, prepared_data, broken_summariser).invoke({})
    assert result["status"] == "drift"
    assert "Claude summary unavailable" in result["summary"]


def test_agent_passes_only_findings_to_the_summariser(engine, prepared_data):
    seen = {}

    def spy(findings):
        seen["text"] = json.dumps(findings)
        return "summary"

    build_graph(engine, prepared_data, spy).invoke({})
    assert (
        "Person " not in seen["text"] and "07000" not in seen["text"]
    )  # no names or phone numbers


def test_numeric_widening_is_low_severity(engine):
    found = by_type(drift.compare(engine, "test_baseline", "test_endline"))
    widen = [f for f in found.get("type_changed", []) if f["variable"] == "used_fert"]
    assert widen and widen[0]["severity"] == "low"  # INTEGER → DOUBLE because of missing values


def test_missingness_on_few_answers_gives_no_percentages(engine, monkeypatch):
    profiles = {
        "a": {
            "x": {"role": "measure", "label": "", "type": "DOUBLE", "n": 400, "missing_share": 0.0}
        },
        "b": {
            "x": {"role": "measure", "label": "", "type": "DOUBLE", "n": 4, "missing_share": 0.99}
        },
    }
    monkeypatch.setattr(engine, "profile", lambda name: profiles[name])
    monkeypatch.setattr(engine, "dictionary", lambda name: [{"variable": "x", "role": "measure"}])
    [finding] = drift.compare(engine, "a", "b")["findings"]
    assert finding["type"] == "missingness_jump" and "%" not in finding["detail"]


@pytest.mark.parametrize("roles", [("group", "exclude"), ("exclude", "group"), ("measure", "id")])
def test_a_variable_private_in_either_round_is_left_out(engine, monkeypatch, roles):
    def dictionary(name):
        role = roles[0] if name == "a" else roles[1]
        return [{"variable": "village", "role": role}]

    def profile(name):
        role = roles[0] if name == "a" else roles[1]
        if role not in ("group", "measure"):
            return {}  # profile() never includes private variables
        return {
            "village": {
                "role": role,
                "label": "Village name",
                "type": "VARCHAR",
                "n": 50,
                "missing_share": 0.0,
            }
        }

    monkeypatch.setattr(engine, "dictionary", dictionary)
    monkeypatch.setattr(engine, "profile", profile)
    result = json.dumps(drift.compare(engine, "a", "b"))
    assert "village" not in result and "Village name" not in result


def test_a_jump_of_exactly_twenty_points_is_reported(engine, monkeypatch):
    def profile(name):
        share = 0.1 if name == "a" else 0.3
        return {
            "x": {
                "role": "measure",
                "label": "",
                "type": "DOUBLE",
                "n": 400,
                "missing_share": share,
            }
        }

    monkeypatch.setattr(engine, "profile", profile)
    monkeypatch.setattr(engine, "dictionary", lambda name: [{"variable": "x", "role": "measure"}])
    [finding] = drift.compare(engine, "a", "b")["findings"]
    assert finding["type"] == "missingness_jump" and "10% to 30%" in finding["detail"]
