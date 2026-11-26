"""Search the document index from the command line, without an MCP client.

Run:  mel-search "barriers to fertiliser adoption" --phase endline --country Kenya
"""

import argparse

from mel_mcp.config import settings
from mel_mcp.documents import DocumentIndex, build_filter, citation
from mel_mcp.metadata import SOURCE_TYPES, STUDY_PHASES


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Search the MEL document index.")
    parser.add_argument("query")
    parser.add_argument("--phase", choices=STUDY_PHASES)
    parser.add_argument("--source-type", choices=SOURCE_TYPES)
    parser.add_argument("--country")
    parser.add_argument("--project")
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)
    parser.add_argument("--top-k", type=int, default=settings.top_k)
    args = parser.parse_args(argv)

    where = build_filter(
        {
            "study_phase": args.phase,
            "source_type": args.source_type,
            "country": args.country,
            "project": args.project,
        },
        args.year_from,
        args.year_to,
    )
    hits = DocumentIndex().search(args.query, where=where, top_k=args.top_k)
    if not hits:
        print("No passages above the relevance threshold.")
    for i, hit in enumerate(hits, 1):
        print(f"\n[{i}] {citation(hit.metadata)}  (relevance {hit.relevance})")
        print(hit.text[:600] + ("..." if len(hit.text) > 600 else ""))


if __name__ == "__main__":
    main()
