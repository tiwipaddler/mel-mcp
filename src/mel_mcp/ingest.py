"""Build or update the document index from the docs folder and its manifest.

Run:  mel-ingest            # index new or changed reports; remove deleted ones
      mel-ingest --rebuild  # re-index everything from scratch

A report is re-embedded only when its file changes. When only its manifest row changes,
its stored details are updated in place, which takes seconds. Changing the embedding model
or chunk settings rebuilds everything.
"""

import argparse
import time
from dataclasses import dataclass, field

from mel_mcp.config import Settings, settings
from mel_mcp.documents import (
    SentenceTransformerEmbedder,
    load_state,
    save_state,
    update_details,
    update_index,
)
from mel_mcp.metadata import load_manifest
from mel_mcp.reading import chunk_pages, doc_metadata, list_documents, load_document


@dataclass
class Plan:
    to_index: list[str] = field(default_factory=list)
    details_only: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"


def _step(n: int, text: str) -> None:
    print(f"\n[{n}/5] {text}", flush=True)


def find_documents(s: Settings) -> dict[str, tuple]:
    """{source: (path, metadata)} for every report, with warnings about manifest mismatches."""
    manifest = load_manifest(s.manifest_file)
    docs = {}
    for path in list_documents(s.docs_dir):
        meta = doc_metadata(path, s.docs_dir, manifest)
        docs[meta["source"]] = (path, meta)
    print(f"  {len(docs)} document(s) in {s.docs_dir}; {len(manifest)} described in the manifest")
    if unlisted := sorted(set(docs) - set(manifest)):
        print(f"  Warning: not in the manifest, so they can't be filtered: {unlisted}")
    if missing := sorted(set(manifest) - set(docs)):
        print(f"  Warning: listed in the manifest but not found: {missing}")
    return docs


def plan_changes(docs: dict[str, tuple], state: dict[str, str]) -> Plan:
    """Compare fingerprints ('<file>:<details>') with what the index holds."""
    plan = Plan(removed=sorted(src for src in state if src not in docs))
    for src, (_, meta) in sorted(docs.items()):
        stored, current = state.get(src), meta["fingerprint"]
        if stored == current:
            continue
        if stored and stored.split(":")[0] == current.split(":")[0]:
            plan.details_only.append(src)
        else:
            plan.to_index.append(src)
    return plan


def read_pages(sources: list[str], docs: dict[str, tuple]) -> tuple[list, list[str]]:
    """The pages of each document. Unreadable files are skipped and retried next run."""
    pages, failed = [], []
    for i, src in enumerate(sources, 1):
        started = time.perf_counter()
        try:
            doc_pages = load_document(*docs[src])
        except Exception as exc:
            failed.append(src)
            print(f"  [{i}/{len(sources)}] {src}: could not be read ({exc}); skipped", flush=True)
            continue
        pages += doc_pages
        took = _duration(time.perf_counter() - started)
        print(f"  [{i}/{len(sources)}] {src}: {len(doc_pages)} page(s) ({took})", flush=True)
    if empty := sum(1 for p in pages if not p.text.strip()):
        print(
            f"  Warning: {empty} page(s) had no extractable text (scanned or image-only) "
            "and were skipped."
        )
    return pages, failed


def _progress(done: int, total: int, seconds: float) -> None:
    rate = done / seconds if seconds else 0
    left = (total - done) / rate if rate else 0
    print(
        f"  {done}/{total} chunks ({rate:.1f} per second, about {_duration(left)} left)",
        flush=True,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build or update the document index.")
    parser.add_argument(
        "--rebuild", action="store_true", help="Re-index every document from scratch"
    )
    args = parser.parse_args(argv)
    s = settings
    started = time.perf_counter()

    _step(1, "Reading the manifest and comparing with the index")
    docs = find_documents(s)
    state, reason = (None, "--rebuild was given") if args.rebuild else load_state(s)
    rebuild = state is None
    if rebuild:
        state, plan = {}, Plan(to_index=sorted(docs))
        print(f"  Full rebuild ({reason}): {len(plan.to_index)} document(s) to index")
    else:
        plan = plan_changes(docs, state)
        new = sum(src not in state for src in plan.to_index)
        unchanged = len(docs) - len(plan.to_index) - len(plan.details_only)
        print(
            f"  {unchanged} unchanged, {new} new, {len(plan.to_index) - new} changed, "
            f"{len(plan.details_only)} with new details only, {len(plan.removed)} removed"
        )
        if plan.details_only:
            n = sum(update_details(src, docs[src][1], s) for src in plan.details_only)
            print(
                f"  Updated details on {n} passage(s) from {len(plan.details_only)} document(s) "
                "in place (no re-embedding)"
            )
            state.update({src: docs[src][1]["fingerprint"] for src in plan.details_only})
        if not plan.to_index and not plan.removed:
            save_state({k: v for k, v in state.items() if k in docs}, s)
            print(
                "\nDone. Nothing needed re-embedding."
                if plan.details_only
                else "\nThe index is up to date."
            )
            return

    _step(2, f"Extracting text from {len(plan.to_index)} document(s)")
    pages, failed = read_pages(plan.to_index, docs)
    to_index = [src for src in plan.to_index if src not in failed]

    _step(3, "Splitting into chunks")
    chunks = chunk_pages(pages, s.chunk_size, s.chunk_overlap)
    print(f"  {len(chunks)} chunk(s) (size {s.chunk_size}, overlap {s.chunk_overlap})")

    embedder = None
    if chunks:
        _step(4, f"Loading {s.embedding_model} (the first run downloads about 400 MB)")
        loading = time.perf_counter()
        embedder = SentenceTransformerEmbedder(s.embedding_model)
        print(f"  Loaded in {_duration(time.perf_counter() - loading)}")
    else:
        _step(4, "No new text to embed; skipping the model")

    _step(5, f"Embedding and storing {len(chunks)} chunk(s)")
    result = update_index(
        chunks,
        embedder,
        s,
        remove_sources=to_index + plan.removed,
        rebuild=rebuild,
        progress=_progress,
    )

    kept = {k: v for k, v in state.items() if k not in plan.removed and k not in failed}
    new_state = {**kept, **{src: docs[src][1]["fingerprint"] for src in to_index}}
    save_state(new_state, s)

    summary = (
        f"\nDone in {_duration(time.perf_counter() - started)}. The index now holds "
        f"{result['chunks_total']} chunk(s) from {len(new_state)} document(s) in {s.db_dir}."
    )
    if failed:
        summary += f" {len(failed)} could not be read and will be retried next time."
    print(summary)


if __name__ == "__main__":
    main()
