"""Settings, read from environment variables or .env (see .env.example)."""

import getpass
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# The project folder: MEL_HOME if set, otherwise the repo this file lives in
# (src/mel_mcp/config.py → two levels up).
ROOT = Path(os.getenv("MEL_HOME") or Path(__file__).resolve().parents[2])
load_dotenv(ROOT / ".env")


def _path(name: str, default: str) -> Path:
    path = Path(os.getenv(name, default))
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Settings:
    # Where things live
    docs_dir: Path
    manifest_file: Path
    data_dir: Path
    db_dir: Path
    reports_dir: Path
    audit_log: Path
    collection: str

    # Documents: embeddings, chunking and search
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    score_threshold: float

    # Survey data protection and limits
    min_cell_size: int
    query_timeout_seconds: float

    # Drift agent
    llm_model: str
    user: str


def _user() -> str:
    try:
        return os.getenv("MEL_USER") or getpass.getuser()
    except Exception:  # no login name available (some containers)
        return "unknown"


def load_settings() -> Settings:
    docs_dir = _path("DOCS_DIR", "docs")
    return Settings(
        docs_dir=docs_dir,
        manifest_file=_path("MANIFEST_FILE", str(docs_dir / "manifest.csv")),
        data_dir=_path("DATA_DIR", "data"),
        db_dir=_path("CHROMA_DIR", "db"),
        reports_dir=_path("REPORTS_DIR", "reports"),
        audit_log=_path("AUDIT_LOG", "logs/audit.jsonl"),
        collection=os.getenv("CHROMA_COLLECTION", "reports"),
        embedding_model=os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/multi-qa-mpnet-base-cos-v1"
        ),
        chunk_size=int(os.getenv("CHUNK_SIZE", "800")),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "100")),
        top_k=int(os.getenv("TOP_K", "5")),
        score_threshold=float(os.getenv("SCORE_THRESHOLD", "0.3")),
        min_cell_size=int(os.getenv("MIN_CELL_SIZE", "10")),
        query_timeout_seconds=float(os.getenv("QUERY_TIMEOUT_SECONDS", "10")),
        llm_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        user=_user(),
    )


settings = load_settings()
