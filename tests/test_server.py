"""The MCP server, tested through a real client session (in memory)."""

import json

import pytest
from conftest import FakeEmbedder
from mcp.shared.memory import create_connected_server_and_client_session

from mel_mcp.documents import DocumentIndex, build_index
from mel_mcp.metadata import load_manifest
from mel_mcp.reading import Chunk, chunk_pages
from mel_mcp.server import create_server

EMBEDDER = FakeEmbedder()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def server(prepared_data, manifest_file):
    manifest = load_manifest(manifest_file)
    pages = [
        Chunk(
            f"Findings on input access. {f}",
            {
                **{k: v for k, v in manifest[f].items() if k != "file"},
                "source": f,
                "page_number": 1,
            },
        )
        for f in manifest
    ]
    build_index(chunk_pages(pages, 800, 0), EMBEDDER, prepared_data)
    return create_server(prepared_data, index=DocumentIndex(prepared_data, EMBEDDER))


def payload(result):
    assert not result.isError, result.content[0].text
    if result.structuredContent is not None:
        return result.structuredContent.get("result", result.structuredContent)
    return json.loads(result.content[0].text)


@pytest.mark.anyio
async def test_tools_are_listed_and_read_only(server):
    async with create_connected_server_and_client_session(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {
            "search_reports",
            "list_datasets",
            "describe_dataset",
            "aggregate",
            "compare_rounds",
            "find_indicators",
            "get_indicator",
        }
        assert all(t.annotations.readOnlyHint for t in tools.values())
        assert "midline" in json.dumps(
            tools["search_reports"].inputSchema
        )  # phase vocabulary in the schema


@pytest.mark.anyio
async def test_search_filters_cites_and_shows_freshness(server):
    async with create_connected_server_and_client_session(server) as client:
        out = payload(
            await client.call_tool(
                "search_reports", {"query": "input access", "study_phase": "endline"}
            )
        )
        assert [r["source"] for r in out["results"]] == ["endline.pdf"]
        assert out["results"][0]["citation"] == "Test Endline Report, p. 1 (endline, 2023, n=1,150)"
        assert out["index"]["built_at"]

        out = payload(await client.call_tool("search_reports", {"query": "x", "country": "kenya"}))
        assert out["filters"]["country"] == "Kenya"  # any capitalisation works

        bad = await client.call_tool("search_reports", {"query": "x", "country": "Narnia"})
        assert bad.isError and "Available" in bad.content[0].text
        bad = await client.call_tool("search_reports", {"query": "x", "study_phase": "midterm"})
        assert bad.isError  # rejected by the schema


@pytest.mark.anyio
async def test_search_reports_gaps_in_the_evidence(server):
    async with create_connected_server_and_client_session(server) as client:
        out = payload(
            await client.call_tool("search_reports", {"query": "x", "study_phase": "midline"})
        )
        assert out["results"] == [] and "gap in the evidence" in out["note"]


@pytest.mark.anyio
async def test_data_tools_end_to_end(server):
    async with create_connected_server_and_client_session(server) as client:
        names = {d["name"] for d in payload(await client.call_tool("list_datasets", {}))}
        assert names == {"test_baseline", "test_endline"}

        described = payload(
            await client.call_tool("describe_dataset", {"dataset": "test_baseline"})
        )
        visible = {
            v["variable"] for v in described["group_variables"] + described["measure_variables"]
        }
        assert "respondent_name" not in visible and "hh_id" not in visible

        out = payload(
            await client.call_tool(
                "aggregate",
                {
                    "dataset": "test_baseline",
                    "variable": "yield_kg",
                    "stat": "mean",
                    "group_by": "county",
                },
            )
        )
        assert out["suppressed_groups"] == ["Gamma"]

        refused = await client.call_tool(
            "aggregate", {"dataset": "test_baseline", "variable": "phone_number", "stat": "count"}
        )
        assert refused.isError

        drift_out = payload(
            await client.call_tool(
                "compare_rounds", {"earlier": "test_baseline", "later": "test_endline"}
            )
        )
        assert any(f["type"] == "possible_rename" for f in drift_out["findings"])

        code = payload(await client.call_tool("find_indicators", {"search": "maize"}))[
            "indicators"
        ][0]["indicator_code"]
        assert (
            payload(await client.call_tool("get_indicator", {"code": code}))["periods"][-1][
                "pct_of_target"
            ]
            == 102.9
        )


@pytest.mark.anyio
async def test_every_call_is_audited(server, prepared_data):
    async with create_connected_server_and_client_session(server) as client:
        await client.call_tool("list_datasets", {})
        await client.call_tool(
            "aggregate", {"dataset": "test_baseline", "variable": "phone_number", "stat": "count"}
        )

    entries = [json.loads(line) for line in prepared_data.audit_log.read_text().splitlines()]
    assert [e["tool"] for e in entries] == ["list_datasets", "aggregate"]
    assert entries[0]["ok"] and entries[0]["user"] == "tester" and entries[0]["results"] == 2
    assert not entries[1]["ok"] and "phone_number" in entries[1]["error"]
    assert (
        "results" in entries[0] and "Alpha" not in prepared_data.audit_log.read_text()
    )  # no data in the log


@pytest.mark.anyio
async def test_resources_and_prompts(server, prepared_data):
    from mel_mcp.agents.drift_agent import build_graph
    from mel_mcp.surveys import SurveyData

    build_graph(SurveyData(prepared_data.data_dir), prepared_data).invoke({})
    async with create_connected_server_and_client_session(server) as client:
        vocab = json.loads((await client.read_resource("mel://vocabulary")).contents[0].text)
        assert vocab["documents"]["study_phase"] == ["baseline", "endline"]

        dictionary = json.loads(
            (await client.read_resource("mel://datasets/test_baseline/dictionary")).contents[0].text
        )
        assert "respondent_name" not in {v["variable"] for v in dictionary}

        latest = (await client.read_resource("mel://drift/latest")).contents[0].text
        assert latest.startswith("Last checked:") and "Survey drift report" in latest

        assert {p.name for p in (await client.list_prompts()).prompts} == {
            "compare_phases",
            "indicator_report",
            "evidence_summary",
        }
        prompt = await client.get_prompt("compare_phases", {"topic": "fertiliser use"})
        assert "compare_rounds" in prompt.messages[0].content.text


@pytest.mark.anyio
async def test_indicator_tools_report_country_totals_and_quality(server):
    async with create_connected_server_and_client_session(server) as client:
        out = payload(await client.call_tool("find_indicators", {"country": "Kenya"}))
        assert out["total_matches"] >= 1 and out["indicators"][0]["countries"] == ["Kenya"]
        code = out["indicators"][0]["indicator_code"]
        indicator = payload(await client.call_tool("get_indicator", {"code": code}))
        assert indicator["data_quality"]


@pytest.mark.anyio
async def test_evidence_summary_prompt_checks_every_cited_project(server):
    async with create_connected_server_and_client_session(server) as client:
        prompt = await client.get_prompt(
            "evidence_summary", {"question": "Why were targets missed?", "country": "Kenya"}
        )
        text = prompt.messages[0].content.text
        assert "country='Kenya'" in text and "matrix" in text
        assert "EVERY cited project that has usable indicators" in text
        assert "Never use periods that aren't usable" in text and "ONE table per project" in text
        assert (
            "the completion report first" in text and "missing from the tracker or is in it" in text
        )


@pytest.mark.anyio
async def test_find_indicators_lists_usable_data_and_projects(server):
    async with create_connected_server_and_client_session(server) as client:
        out = payload(await client.call_tool("find_indicators", {}))
        assert all(i["usable"] for i in out["indicators"]) and out["projects_with_usable_data"] >= 1
        both = payload(await client.call_tool("find_indicators", {"include_unusable": True}))
        assert both["total_matches"] > out["total_matches"]


@pytest.mark.anyio
async def test_evidence_summary_copes_with_split_arguments(server):
    async with create_connected_server_and_client_session(server) as client:
        # What Claude Code sends for: evidence_summary Kenya What are the reasons...
        text = (
            (await client.get_prompt("evidence_summary", {"country": "Kenya", "question": "What"}))
            .messages[0]
            .content.text
        )
        assert "ask the user to type their full question" in text and "country='Kenya'" in text
        # The old order: question first, so the country slot got the second word.
        text = (
            (await client.get_prompt("evidence_summary", {"country": "are"}))
            .messages[0]
            .content.text
        )
        assert "'are' isn't a country name" in text and "country='" not in text
        text = (
            (
                await client.get_prompt(
                    "evidence_summary",
                    {"country": "kenia", "question": "Why did Kenyan projects miss targets?"},
                )
            )
            .messages[0]
            .content.text
        )
        assert "FIRST" not in text and "country='Kenya'" in text


@pytest.mark.anyio
async def test_evidence_summary_takes_a_start_year(server):
    async with create_connected_server_and_client_session(server) as client:
        text = (
            (
                await client.get_prompt(
                    "evidence_summary",
                    {
                        "country": "Kenya",
                        "year_from": "2020",
                        "question": "Why did projects miss targets?",
                    },
                )
            )
            .messages[0]
            .content.text
        )
        assert "country='Kenya' and year_from=2020" in text and "FIRST" not in text
        text = (
            (await client.get_prompt("evidence_summary", {"country": "Kenya", "year_from": "What"}))
            .messages[0]
            .content.text
        )
        assert "'What' isn't a year" in text and "year_from=" not in text
