"""The drift agent: a LangGraph workflow that checks survey rounds for silent changes.

    plan -> detect -> record_all_clear                    (nothing changed)
                   -> investigate -> summarise -> alert   (drift found)

The summary is written by Claude if ANTHROPIC_API_KEY is set, otherwise from a template.
Exit code 2 means high-severity drift was found, so a scheduler can react.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from mel_mcp import drift
from mel_mcp.config import Settings, settings
from mel_mcp.surveys import SurveyData

# Phases in the order a study moves through them; others aren't auto-paired.
PHASE_ORDER = {
    p: i for i, p in enumerate(("formative", "baseline", "midline", "endline", "ex-post"))
}


class DriftState(TypedDict, total=False):
    pairs: list[list[str]]  # [[earlier, later], ...]
    findings: list[dict]
    summary: str
    status: str  # "clear" or "drift"
    report_path: str


Summariser = Callable[[list[dict]], str]


def plan_pairs(engine: SurveyData) -> list[list[str]]:
    """Consecutive rounds of each project, ordered by study phase. Datasets with no project
    are left out, since two unrelated surveys would otherwise be compared; pass --pair to
    compare them."""
    by_project: dict[str, list[dict]] = {}
    for ds in engine.list_datasets():
        if ds.get("project") and ds.get("study_phase") in PHASE_ORDER:
            by_project.setdefault(ds["project"], []).append(ds)
    pairs = []
    for rounds in by_project.values():
        rounds.sort(key=lambda d: (PHASE_ORDER[d["study_phase"]], d.get("reporting_year", "")))
        pairs += [[x["name"], y["name"]] for x, y in zip(rounds, rounds[1:], strict=False)]
    return pairs


def template_summary(findings: list[dict]) -> str:
    high = [f for f in findings if f["severity"] == "high"]
    lines = [f"{len(findings)} change(s) found between survey rounds, {len(high)} high severity."]
    for f in high[:10]:
        readers = f.get("affected_readers")
        lines.append(
            f"- {f['earlier']} → {f['later']}: {f['detail']}"
            + (f" Affects: {', '.join(readers)}." if readers else "")
        )
    return "\n".join(lines)


def claude_summary(model: str) -> Summariser:
    """Summarise with Claude. Only findings are sent, and they only ever cover group and
    measure variables (names, labels, common categories, averages), never survey rows."""
    import anthropic

    client = anthropic.Anthropic()

    def summarise(findings: list[dict]) -> str:
        response = client.messages.create(
            model=model,
            max_tokens=700,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You are helping a monitoring and evaluation team. These are changes "
                        "detected between survey rounds. Write a short alert (under 150 words) "
                        "for the data team: what changed, which changes break analysis or "
                        "indicators, and what to check first. Don't speculate beyond the "
                        "evidence.\n\n" + json.dumps(findings, indent=1)
                    ),
                }
            ],
        )
        return response.content[0].text.strip()

    return summarise


def build_graph(engine: SurveyData, s: Settings = settings, summariser: Summariser | None = None):
    summariser = summariser or template_summary
    usage = drift.load_usage(engine.data_dir)

    def plan(state: DriftState) -> DriftState:
        return {} if state.get("pairs") else {"pairs": plan_pairs(engine)}

    def detect(state: DriftState) -> DriftState:
        findings = []
        for earlier, later in state.get("pairs", []):
            result = drift.compare(engine, earlier, later)
            findings += [{**f, "earlier": earlier, "later": later} for f in result["findings"]]
        return {"findings": findings}

    def route(state: DriftState) -> str:
        # Only "added" variables is not worth an alert on its own.
        return (
            "investigate"
            if any(f["type"] != "added" for f in state["findings"])
            else "record_all_clear"
        )

    def investigate(state: DriftState) -> DriftState:
        findings = []
        for f in state["findings"]:
            readers = drift.affected_readers(f, (f["earlier"], f["later"]), usage)
            if readers:
                f = {**f, "affected_readers": readers, "severity": "high"}
            findings.append(f)
        findings.sort(
            key=lambda f: (drift.SEVERITY_ORDER[f["severity"]], f["later"], f["variable"])
        )
        return {"findings": findings}

    def summarise(state: DriftState) -> DriftState:
        try:
            return {"summary": summariser(state["findings"])}
        except Exception as exc:  # an LLM outage must not stop the alert
            return {
                "summary": template_summary(state["findings"])
                + f"\n\n(Claude summary unavailable: {exc})"
            }

    def alert(state: DriftState) -> DriftState:
        path = write_report(state, s.reports_dir)
        return {"status": "drift", "report_path": str(path)}

    def record_all_clear(state: DriftState) -> DriftState:
        write_last_run(s.reports_dir, "clear", state.get("pairs", []), None)
        return {"status": "clear"}

    graph = StateGraph(DriftState)
    for name, node in [
        ("plan", plan),
        ("detect", detect),
        ("investigate", investigate),
        ("summarise", summarise),
        ("alert", alert),
        ("record_all_clear", record_all_clear),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "detect")
    graph.add_conditional_edges("detect", route, ["investigate", "record_all_clear"])
    graph.add_edge("investigate", "summarise")
    graph.add_edge("summarise", "alert")
    graph.add_edge("alert", END)
    graph.add_edge("record_all_clear", END)
    return graph.compile()


def write_last_run(reports_dir: Path, status: str, pairs: list, report: str | None) -> None:
    """reports/last_run.json: when the agent last checked, so freshness is visible."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "last_run.json").write_text(
        json.dumps(
            {
                "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "status": status,
                "pairs_compared": pairs,
                "report": report,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def write_report(state: DriftState, reports_dir: Path) -> Path:
    now = datetime.now(UTC)
    reports_dir.mkdir(parents=True, exist_ok=True)
    # Microseconds keep two runs in the same second apart, and names still sort by time.
    md_path = reports_dir / f"drift_{now:%Y%m%d-%H%M%S-%f}.md"

    lines = [
        f"# Survey drift report ({now:%Y-%m-%d %H:%M:%S} UTC)",
        "",
        drift.PUBLIC_ONLY,
        "",
        "## Summary",
        "",
        state["summary"],
        "",
        "## Findings",
        "",
        "| Severity | Rounds | Variable | What changed | Affects |",
        "|---|---|---|---|---|",
    ]
    for f in state["findings"]:
        lines.append(
            f"| {f['severity']} | {f['earlier']} → {f['later']} | `{f['variable']}` | "
            f"{f['detail']} | {', '.join(f.get('affected_readers', [])) or ''} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    md_path.with_suffix(".json").write_text(
        json.dumps(state["findings"], indent=2, default=str), encoding="utf-8"
    )
    write_last_run(reports_dir, "drift", state.get("pairs", []), md_path.name)
    return md_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Check survey rounds for silent changes.")
    parser.add_argument(
        "--pair",
        nargs=2,
        action="append",
        metavar=("EARLIER", "LATER"),
        help="Compare two datasets (repeatable). Default: consecutive phases of each project.",
    )
    parser.add_argument(
        "--no-llm", action="store_true", help="Use the template summary even if an API key is set"
    )
    args = parser.parse_args(argv)

    engine = SurveyData(settings.data_dir, settings.min_cell_size, settings.query_timeout_seconds)
    use_llm = not args.no_llm and os.getenv("ANTHROPIC_API_KEY")
    summariser = claude_summary(settings.llm_model) if use_llm else template_summary
    agent = build_graph(engine, settings, summariser)

    result = agent.invoke({"pairs": args.pair} if args.pair else {})
    if not result.get("pairs"):
        print("No survey rounds to compare. Add datasets with a project and study phase.")
        return
    print(f"Compared: {', '.join(' → '.join(p) for p in result['pairs'])}")
    if result["status"] == "clear":
        print("No drift found.")
        return
    print(result["summary"])
    print(f"\nReport: {result['report_path']}")
    if any(f["severity"] == "high" for f in result["findings"]):
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
