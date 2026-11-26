"""The MCP server: fixed, read-only tools over a programme's MEL evidence.

Every tool call goes to the audit log. Never print() here: stdout carries the MCP protocol.
"""

import json
from collections.abc import Callable
from typing import Annotated

import duckdb
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from mel_mcp import drift
from mel_mcp.audit import AuditLog, result_size
from mel_mcp.config import Settings, settings
from mel_mcp.countries import canonical_country
from mel_mcp.documents import DocumentIndex, build_filter, citation, matches
from mel_mcp.indicators import IndicatorTracker
from mel_mcp.metadata import SourceType, StudyPhase, load_manifest, vocabulary
from mel_mcp.prompts import register_prompts
from mel_mcp.surveys import Stat, SurveyData
from mel_mcp.tables import DataError

INSTRUCTIONS = """\
Evidence from development programmes' monitoring, evaluation and learning (MEL):
evaluation reports, briefs and transcripts (search_reports), survey datasets
(list_datasets, describe_dataset, aggregate, compare_rounds) and an indicator
tracker (find_indicators, get_indicator).

- Use search_reports for what reports say and aggregate for numbers from survey
  data. Never estimate a statistic from report text when a dataset can answer it.
- Cite every claim: title, page, study phase and sample size (n) for documents;
  dataset title, phase and n for numbers.
- Say which study phase each finding comes from. Before comparing rounds, call
  compare_rounds: renamed or recoded variables make comparisons misleading.
- Treat qualitative findings (FGDs, KIIs) as illustrative, not as prevalence.
- Mention how fresh the evidence is when it matters (index built_at, data_updated).
- Check indicator figures that carry cautions against the reports, and prefer the
  report's figure when they disagree.
- Use only usable indicator figures as evidence: find_indicators lists only
  usable indicators, and get_indicator marks each period usable or not and gives
  an evidence_rule to follow. Never work out a shortfall from flagged figures;
  name the project and say why its figures aren't usable instead.
- If the tools return nothing relevant, say the evidence doesn't cover it."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
YearFrom = Annotated[
    int | None,
    Field(description="Only reporting periods that end in or after this year, e.g. 2020"),
]


class Backend:
    """What the tools share: settings, data sources, the document index and the audit log."""

    def __init__(self, s: Settings, index, surveys, tracker):
        self.s = s
        self.surveys = surveys or SurveyData(s.data_dir, s.min_cell_size, s.query_timeout_seconds)
        self.tracker = tracker or IndicatorTracker(s.data_dir)
        self.audit = AuditLog(s.audit_log, s.user)
        self._index = index

    def index(self) -> DocumentIndex:
        # Opened on first use, so the data tools work before any reports are indexed and the
        # embedding model only loads when someone searches.
        if self._index is None:
            try:
                self._index = DocumentIndex(self.s)
            except (RuntimeError, ImportError) as exc:
                raise ToolError(f"Document search is unavailable: {exc}") from exc
        return self._index

    def call(self, tool: str, arguments: dict, fn: Callable, *args):
        """Run a tool with audit logging, turning data errors into errors the model can read."""
        with self.audit.record(tool, arguments) as outcome:
            try:
                result = fn(*args)
            except (DataError, ValueError) as exc:
                raise ToolError(str(exc)) from exc
            except duckdb.Error as exc:
                raise ToolError(f"The data query failed: {exc}") from exc
            outcome["results"] = result_size(result)
            return result


def match_value(value: str | None, allowed: list, field: str) -> str | None:
    """Accept any capitalisation, but only values that occur in the documents."""
    if not value:
        return None
    for option in allowed:
        if str(option).lower() == value.strip().lower():
            return option
    raise ValueError(f"No documents have {field} '{value}'. Available: {allowed or 'none'}.")


def search_documents(
    backend: Backend, query: str, filters: dict, year_from, year_to, top_k
) -> dict:
    manifest = load_manifest(backend.s.manifest_file)
    vocab = vocabulary(manifest)
    country = filters.get("country")
    filters = {
        **filters,
        "country": match_value(
            canonical_country(country) if country else None, vocab["country"], "country"
        ),
        "project": match_value(filters.get("project"), vocab["project"], "project"),
    }
    filters = {k: v for k, v in filters.items() if v}
    index = backend.index()
    hits = index.search(query, where=build_filter(filters, year_from, year_to), top_k=top_k)
    fields = (
        "title",
        "source",
        "page_number",
        "study_phase",
        "reporting_year",
        "country",
        "source_type",
        "sample_size",
        "url",
    )
    out = {
        "query": query,
        "filters": filters,
        "results": [
            {
                "ref": i,
                "citation": citation(h.metadata),
                "relevance": h.relevance,
                "text": h.text,
                **{k: h.metadata[k] for k in fields if k in h.metadata},
            }
            for i, h in enumerate(hits, 1)
        ],
        "index": index.info(),
    }
    if not hits:
        # Tell a gap in the evidence apart from documents that just aren't relevant.
        in_scope = [m for m in manifest.values() if matches(m, filters, year_from, year_to)]
        if in_scope:
            out["note"] = (
                f"{len(in_scope)} document(s) match these filters but no passage was relevant "
                "enough. Say the documents don't cover this."
            )
        else:
            out["note"] = (
                "No documents match these filters at all: this is a gap in the evidence base, "
                "not a negative finding."
            )
    return out


def register_document_tools(mcp: FastMCP, backend: Backend) -> None:
    @mcp.tool(annotations=READ_ONLY)
    def search_reports(
        query: Annotated[str, Field(description="What to look for, in plain language")],
        study_phase: StudyPhase | None = None,
        source_type: SourceType | None = None,
        country: Annotated[
            str | None, Field(description="Country name; see mel://vocabulary")
        ] = None,
        project: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        top_k: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> dict:
        """Search evaluation reports, learning briefs and transcripts for passages
        relevant to a question. Filter by study phase, source type, country, project
        or year to keep findings from different phases apart. Each result has a
        citation (title, page, phase, n and the report's link) to use in the answer."""
        filters = {
            "study_phase": study_phase,
            "source_type": source_type,
            "country": country,
            "project": project,
        }
        args = {
            "query": query,
            **filters,
            "year_from": year_from,
            "year_to": year_to,
            "top_k": top_k,
        }
        return backend.call(
            "search_reports",
            args,
            search_documents,
            backend,
            query,
            filters,
            year_from,
            year_to,
            top_k,
        )


def register_survey_tools(mcp: FastMCP, backend: Backend) -> None:
    surveys = backend.surveys

    @mcp.tool(annotations=READ_ONLY)
    def list_datasets() -> list[dict]:
        """List the survey datasets available for aggregate queries, with study phase,
        country and year. Call describe_dataset next to see their variables."""
        return backend.call("list_datasets", {}, surveys.list_datasets)

    @mcp.tool(annotations=READ_ONLY)
    def describe_dataset(dataset: str) -> dict:
        """Show which variables in a dataset can be summarised (measure variables)
        and which can break results down or filter them (group variables), with
        labels and units. Call this before aggregate."""
        return backend.call(
            "describe_dataset", {"dataset": dataset}, surveys.describe_dataset, dataset
        )

    @mcp.tool(annotations=READ_ONLY)
    def aggregate(
        dataset: str,
        variable: Annotated[str, Field(description="A measure variable from describe_dataset")],
        stat: Annotated[
            Stat,
            Field(
                description=(
                    "mean or median for numbers; share for 0/1 variables (% with value 1) or "
                    "text categories (% in each); count for number of responses"
                )
            ),
        ],
        group_by: Annotated[
            str | None, Field(description="A group variable, e.g. county or gender")
        ] = None,
        filters: Annotated[
            dict[str, str] | None,
            Field(
                description=(
                    "Keep only rows where group variables equal these values, "
                    'e.g. {"gender": "Female"}'
                )
            ),
        ] = None,
    ) -> dict:
        """Compute a summary statistic from survey data, optionally broken down by a
        group variable and filtered. Returns only aggregates with their sample size
        (n), never individual responses; groups too small to report are suppressed."""
        args = {
            "dataset": dataset,
            "variable": variable,
            "stat": stat,
            "group_by": group_by,
            "filters": filters,
        }
        return backend.call(
            "aggregate", args, surveys.aggregate, dataset, variable, stat, group_by, filters
        )

    @mcp.tool(annotations=READ_ONLY)
    def compare_rounds(
        earlier: Annotated[
            str, Field(description="Dataset name of the earlier round, e.g. the baseline")
        ],
        later: Annotated[str, Field(description="Dataset name of the later round")],
    ) -> dict:
        """Check what changed between two rounds of a survey before comparing them:
        renamed, removed or recoded variables, type or unit changes, and jumps in
        missing answers. Any of these can make a comparison across rounds misleading."""
        args = {"earlier": earlier, "later": later}
        return backend.call("compare_rounds", args, drift.compare, surveys, earlier, later)


def register_indicator_tools(mcp: FastMCP, backend: Backend) -> None:
    tracker = backend.tracker

    @mcp.tool(annotations=READ_ONLY)
    def find_indicators(
        search: Annotated[str, Field(description="Words from the indicator name or code")] = "",
        project: Annotated[
            str | None,
            Field(
                description=(
                    "Project name or part of it, or its IATI identifier or part of it, e.g. P117635"
                )
            ),
        ] = None,
        country: Annotated[
            str | None, Field(description="Country name or code, e.g. Kenya or KE")
        ] = None,
        include_unusable: Annotated[
            bool,
            Field(
                description=(
                    "Also list indicators with no usable figures. Only to explain why a "
                    "project has none; never use their figures as evidence."
                )
            ),
        ] = False,
        year_from: YearFrom = None,
    ) -> dict:
        """Find indicators in the indicator tracker by name or code, optionally for one
        project or country. Lists only indicators with usable figures (usable_rule
        says what that means), with codes to pass to get_indicator. 'projects'
        summarises every matching project, with how many of its indicators are
        usable: use it to choose projects, and to name the ones with no usable data.
        Indicators marked check_against_reports carry cautions (caution_rule): check
        their figures in the reports before citing them. At most 25 indicators are
        shown (see total_matches), so narrow the search if there are more."""
        args = {
            "search": search,
            "project": project,
            "country": country,
            "include_unusable": include_unusable,
            "year_from": year_from,
        }
        return backend.call(
            "find_indicators",
            args,
            tracker.find,
            search,
            project,
            country,
            include_unusable,
            year_from,
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_indicator(
        code: str,
        period: Annotated[str | None, Field(description="Optional year or date, e.g. 2024")] = None,
        year_from: YearFrom = None,
    ) -> dict:
        """Get an indicator's baseline, and its target, actual and % of target achieved
        for each reporting period. Check 'direction': for 'lower is better'
        indicators, being under target is good. Each period says whether it is
        usable; follow 'evidence_rule'. Never present or calculate from periods that
        aren't usable: say why instead, using 'data_quality'. Periods with 'cautions'
        must be checked against the reports before their figures are cited."""
        args = {"code": code, "period": period, "year_from": year_from}
        return backend.call("get_indicator", args, tracker.get, code, period, year_from)


def register_resources(mcp: FastMCP, backend: Backend) -> None:
    s = backend.s

    @mcp.resource("mel://documents", mime_type="application/json")
    def documents() -> str:
        """Every indexed document with its project, phase, country, year and n."""
        return json.dumps(list(load_manifest(s.manifest_file).values()), indent=2)

    @mcp.resource("mel://vocabulary", mime_type="application/json")
    def filter_values() -> str:
        """The exact filter values that occur in the documents and datasets."""
        return json.dumps(
            {
                "documents": vocabulary(load_manifest(s.manifest_file)),
                "datasets": [d["name"] for d in backend.surveys.list_datasets()],
            },
            indent=2,
        )

    @mcp.resource("mel://datasets/{name}/dictionary", mime_type="application/json")
    def data_dictionary(name: str) -> str:
        """A dataset's usable variables, labels and units."""
        try:
            return json.dumps(backend.surveys.public_dictionary(name), indent=2)
        except DataError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.resource("mel://drift/latest", mime_type="text/markdown")
    def drift_latest() -> str:
        """The drift agent's most recent report, and when it last checked."""
        last_run = s.reports_dir / "last_run.json"
        header = ""
        if last_run.exists():
            info = json.loads(last_run.read_text(encoding="utf-8"))
            header = f"Last checked: {info['checked_at']} (status: {info['status']})\n\n"
        report = drift.latest_report(s.reports_dir)
        if not report:
            return header + "No drift reports yet."
        text = report.read_text(encoding="utf-8")
        if drift.PUBLIC_ONLY not in text:
            return header + (
                "The latest drift report was made by an older version that could name private "
                "variables, so it isn't shown. Run mel-drift to make a new one."
            )
        return header + text


def create_server(
    s: Settings = settings,
    index: DocumentIndex | None = None,
    surveys: SurveyData | None = None,
    tracker: IndicatorTracker | None = None,
) -> FastMCP:
    """Build the server. Tests pass in an index that uses fake embeddings."""
    mcp = FastMCP("mel-evidence", instructions=INSTRUCTIONS)
    backend = Backend(s, index, surveys, tracker)
    register_document_tools(mcp, backend)
    register_survey_tools(mcp, backend)
    register_indicator_tools(mcp, backend)
    register_resources(mcp, backend)
    register_prompts(mcp)
    return mcp


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
