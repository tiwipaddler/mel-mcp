"""The document index: embeddings in Chroma, incremental updates, search and citations.

Uses chromadb and sentence-transformers directly, so every step is visible here.
"""

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Protocol

import chromadb

from mel_mcp.config import Settings, settings
from mel_mcp.countries import canonical_countries, canonical_country
from mel_mcp.reading import PAGE_UNKNOWN, Chunk

BATCH_SIZE = 64  # small batches give frequent progress updates
EXACT_FILTERS = ("study_phase", "source_type", "country", "project", "donor")
PAGE_FIELDS = ("page_number", "page_label", "chunk_index")
# Raise when the metadata stored with each chunk changes shape, so old indexes rebuild.
INDEX_SCHEMA = 3


@dataclass
class Hit:
    text: str
    metadata: dict
    relevance: float  # cosine similarity: 1.0 means the same meaning


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Runs the embedding model locally; it downloads once (~400 MB), then works offline."""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # slow import: only when needed

        self.name = model_name
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False
        )
        return vectors.tolist()


@cache
def _client(path: str) -> chromadb.ClientAPI:
    # Chroma allows one client configuration per folder per process, so share it.
    return chromadb.PersistentClient(
        path=path, settings=chromadb.Settings(anonymized_telemetry=False)
    )


def _index_settings(model_name: str, s: Settings) -> dict:
    """What the index was built with; if any of it changes, everything is re-embedded."""
    return {
        "embedding_model": model_name,
        "chunk_size": s.chunk_size,
        "chunk_overlap": s.chunk_overlap,
        "index_schema": INDEX_SCHEMA,
    }


def _state_path(s: Settings) -> Path:
    return Path(s.db_dir) / f"{s.collection}.state.json"


def load_state(s: Settings = settings) -> tuple[dict[str, str] | None, str]:
    """({source: fingerprint} of what's fully indexed, reason) — None means rebuild everything."""
    client = _client(str(s.db_dir))
    if s.collection not in {c.name for c in client.list_collections()}:
        return None, "no index yet"
    meta = client.get_collection(s.collection).metadata or {}
    for key, value in _index_settings(s.embedding_model, s).items():
        if meta.get(key) != value:
            return None, f"{key} changed since the index was built"
    path = _state_path(s)
    if not path.exists():
        return None, "the index has no record of which files it holds"
    return json.loads(path.read_text(encoding="utf-8")), ""


def update_details(source: str, doc_meta: dict, s: Settings = settings) -> int:
    """Replace the details stored with a document's passages, keeping their text and
    embeddings. Used when only its manifest row changed. Returns the passages updated."""
    collection = _client(str(s.db_dir)).get_collection(s.collection)
    got = collection.get(where={"source": source}, include=["metadatas"])
    metadatas = []
    for old in got["metadatas"]:
        new = {**doc_meta, **{k: old[k] for k in PAGE_FIELDS if k in old}}
        # Fields that are no longer in the manifest are removed (None deletes them).
        new.update({k: None for k in old if k not in new})
        metadatas.append(new)
    for start in range(0, len(got["ids"]), 500):
        collection.update(
            ids=got["ids"][start : start + 500], metadatas=metadatas[start : start + 500]
        )
    return len(got["ids"])


def save_state(state: dict[str, str], s: Settings = settings) -> None:
    """Written only after a successful run, so an interrupted run is redone next time."""
    _state_path(s).write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def update_index(
    chunks: list[Chunk],
    embedder: Embedder | None,
    s: Settings = settings,
    remove_sources: list[str] = (),
    rebuild: bool = False,
    progress=None,
) -> dict:
    """Add chunks to the index, after deleting any chunks from `remove_sources`.

    rebuild=True starts from an empty index. `progress(done, total, seconds)`
    is called after each batch.
    """
    client = _client(str(s.db_dir))
    exists = s.collection in {c.name for c in client.list_collections()}
    if rebuild and exists:
        # Drop the record first: if the rebuild is interrupted, the next run sees no
        # record and starts over instead of trusting a list of files that are gone.
        _state_path(s).unlink(missing_ok=True)
        client.delete_collection(s.collection)
        exists = False
    now = datetime.now(UTC).isoformat(timespec="seconds")
    index_meta = {
        **_index_settings(embedder.name if embedder else s.embedding_model, s),
        "built_at": now,
    }
    collection = (
        client.get_collection(s.collection)
        if exists
        else client.create_collection(s.collection, metadata={"hnsw:space": "cosine", **index_meta})
    )

    for source in remove_sources:
        collection.delete(where={"source": source})

    started = time.perf_counter()
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        collection.add(
            ids=[c.id for c in batch],
            documents=[c.text for c in batch],
            metadatas=[c.metadata for c in batch],
            embeddings=embedder.embed([c.text for c in batch]),
        )
        if progress:
            progress(start + len(batch), len(chunks), time.perf_counter() - started)

    collection.modify(metadata=index_meta)  # "hnsw:space" can't be sent again
    return {"chunks_added": len(chunks), "chunks_total": collection.count(), "built_at": now}


def build_index(
    chunks: list[Chunk], embedder: Embedder, s: Settings = settings, progress=None
) -> dict:
    """Replace the whole index with these chunks. Re-running never duplicates."""
    result = update_index(chunks, embedder, s, rebuild=True, progress=progress)
    return {
        "chunks": result["chunks_added"],
        "built_at": result["built_at"],
        "collection": s.collection,
    }


class DocumentIndex:
    """An existing index, ready to search."""

    def __init__(self, s: Settings = settings, embedder: Embedder | None = None):
        self.s = s
        client = _client(str(s.db_dir))
        if s.collection not in {c.name for c in client.list_collections()}:
            raise RuntimeError(f"No document index found in {s.db_dir}. Run `mel-ingest` first.")
        self.collection = client.get_collection(s.collection)
        if self.collection.count() == 0:
            raise RuntimeError("The document index is empty. Run `mel-ingest` first.")

        meta = self.collection.metadata or {}
        expected = embedder.name if embedder else s.embedding_model
        if meta.get("embedding_model") not in (None, expected):
            raise RuntimeError(
                f"The index was built with '{meta['embedding_model']}' but the current model is "
                f"'{expected}'. Re-run `mel-ingest`."
            )
        self._embedder = embedder

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:  # load the model on the first search, not at startup
            self._embedder = SentenceTransformerEmbedder(self.s.embedding_model)
        return self._embedder

    def info(self) -> dict:
        meta = self.collection.metadata or {}
        return {
            "chunks_indexed": self.collection.count(),
            "built_at": meta.get("built_at"),
            "embedding_model": meta.get("embedding_model"),
        }

    def search(self, query: str, where: dict | None = None, top_k: int | None = None) -> list[Hit]:
        result = self.collection.query(
            query_embeddings=self.embedder.embed([query]),
            n_results=top_k or self.s.top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        for text, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        ):
            relevance = round(1 - float(distance), 3)  # cosine distance → similarity
            if relevance >= self.s.score_threshold:
                hits.append(Hit(text=text, metadata=meta, relevance=relevance))
        return hits


def build_filter(
    filters: dict | None = None, year_from: int | None = None, year_to: int | None = None
) -> dict | None:
    """Turn simple filters into a Chroma `where` clause."""
    conditions = []
    for key, value in (filters or {}).items():
        if key not in EXACT_FILTERS:
            raise ValueError(f"Can't filter on '{key}'. Use one of {EXACT_FILTERS}.")
        if value in (None, ""):
            continue
        if key == "country":  # documents covering several countries match any of them
            conditions.append({"countries": {"$contains": canonical_country(value)}})
        else:
            conditions.append({key: value})
    if year_from is not None:
        conditions.append({"reporting_year": {"$gte": year_from}})
    if year_to is not None:
        conditions.append({"reporting_year": {"$lte": year_to}})
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def matches(meta: dict, filters: dict, year_from: int | None, year_to: int | None) -> bool:
    """Whether a manifest entry passes the filters.

    Used to tell 'no documents match' apart from 'nothing relevant in them'.
    """
    for key, value in filters.items():
        if value in (None, ""):
            continue
        if key == "country":
            if canonical_country(value) not in canonical_countries(meta.get("country")):
                return False
        elif meta.get(key) != value:
            return False
    year = meta.get("reporting_year")
    if year_from is not None and (year is None or year < year_from):
        return False
    if year_to is not None and (year is None or year > year_to):
        return False
    return True


def citation(meta: dict) -> str:
    """E.g. 'Endline Report, p. 4 (endline, 2023, n=1,150)'."""
    label = meta.get("title") or meta.get("source", "unknown")
    if meta.get("page_label") != PAGE_UNKNOWN:
        label += f", p. {meta.get('page_number', '?')}"
    details = [str(meta[k]) for k in ("study_phase", "reporting_year") if k in meta]
    if isinstance(meta.get("sample_size"), int):
        details.append(f"n={meta['sample_size']:,}")
    return f"{label} ({', '.join(details)})" if details else label
