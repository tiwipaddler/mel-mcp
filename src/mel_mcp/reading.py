"""Reading reports: PDF and OpenDocument pages, their manifest details, and chunking."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from mel_mcp.countries import canonical_countries

SEPARATORS = ("\n\n", "\n", ". ", " ")  # paragraph, line, sentence, word


@dataclass
class Chunk:
    text: str
    metadata: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Readable, stable ID, e.g. 'report.pdf::p4::c2'."""
        m = self.metadata
        return f"{m['source']}::p{m['page_number']}::c{m.get('chunk_index', 0)}"


DOCUMENT_TYPES = (".pdf", ".odt")


def list_documents(docs_dir: Path) -> list[Path]:
    """Every PDF and OpenDocument text file under docs_dir (none is a valid answer)."""
    if not docs_dir.exists():
        raise FileNotFoundError(f"{docs_dir} does not exist. Create it and add your documents.")
    return sorted(
        p for p in docs_dir.rglob("*") if p.suffix.lower() in DOCUMENT_TYPES and p.is_file()
    )


def _md5(text: str, length: int) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:length]


def fingerprint(path: Path, meta: dict) -> str:
    """'<file part>:<details part>'. The file part changes when the file does (size and
    modification time, so unchanged files are never re-read); the details part when its
    manifest row does. Only a file change needs re-embedding."""
    stat = path.stat()
    file_part = _md5(f"{stat.st_size}:{int(stat.st_mtime)}", 8)
    return f"{file_part}:{_md5(json.dumps(meta, sort_keys=True, default=str), 8)}"


def doc_metadata(path: Path, docs_dir: Path, manifest: dict[str, dict]) -> dict:
    """The manifest row for a PDF (or just its name), plus source and fingerprint."""
    rel = path.relative_to(docs_dir).as_posix()
    meta = {k: v for k, v in manifest.get(rel, {}).items() if k != "file"} or {"title": path.stem}
    out = {**meta, "source": rel, "fingerprint": fingerprint(path, meta)}
    countries = canonical_countries(meta.get("country"))
    if countries:
        out["countries"] = countries  # a list, so "contains Kenya" filters work
    return out


def load_pdf(path: Path, doc_meta: dict) -> list[Chunk]:
    """One Chunk per page of one PDF, carrying the document's metadata."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    labels = reader.page_labels
    return [
        Chunk(
            text=clean_text(page.extract_text() or ""),
            metadata={
                **doc_meta,
                "page_number": i + 1,
                "page_label": labels[i] if i < len(labels) else str(i + 1),
            },
        )
        for i, page in enumerate(reader.pages)
    ]


_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
MAX_ODT_XML = 50 * 1024 * 1024
# <text:s text:c="n"/> stands for n spaces. A crafted file could ask for billions, so the
# spaces expanded across one document are capped.
MAX_ODT_SPACES = 1_000_000
PAGE_BREAK = "\f"  # marks a soft page break inside a paragraph; XML text can't contain it
PAGE_UNKNOWN = "-"  # page_label for documents without page information


class _SpaceBudget:
    def __init__(self, limit: int = MAX_ODT_SPACES):
        self.left = limit

    def take(self, count: str | None) -> str:
        n = int(count) if count and count.isdigit() else 1
        self.left -= n
        if self.left < 0:
            raise ValueError("the file encodes too many spaces to read safely")
        return " " * n


def _inline_text(element, budget: _SpaceBudget) -> str:
    """The text of a paragraph, honouring ODT's encoded spaces, tabs and line breaks."""
    parts = [element.text or ""]
    for child in element:
        if child.tag == _TEXT + "s":
            parts.append(budget.take(child.get(_TEXT + "c")))
        elif child.tag == _TEXT + "tab":
            parts.append("\t")
        elif child.tag == _TEXT + "line-break":
            parts.append("\n")
        elif child.tag == _TEXT + "soft-page-break":
            parts.append(PAGE_BREAK)
        elif child.tag != _TEXT + "note":
            parts.append(_inline_text(child, budget))
        parts.append(child.tail or "")
    return "".join(parts)


def _odt_blocks(element, out: list, budget: _SpaceBudget) -> None:
    """Paragraphs, headings and tables in reading order; None marks a page break."""
    for child in element:
        if child.tag == _TEXT + "soft-page-break":
            out.append(None)
        elif child.tag in (_TEXT + "p", _TEXT + "h"):
            # A page break inside a paragraph splits it: the text before it is on one page.
            for i, piece in enumerate(_inline_text(child, budget).split(PAGE_BREAK)):
                if i:
                    out.append(None)
                if piece.strip():
                    out.append(piece.strip())
        elif child.tag == _TABLE + "table":
            # Keep the table's shape: one line per row, cells separated by " | ".
            rows = []
            for row in child.iter(_TABLE + "table-row"):
                cells = [
                    " ".join(
                        _inline_text(p, budget).replace(PAGE_BREAK, " ").strip()
                        for p in cell.iter(_TEXT + "p")
                    ).strip()
                    for cell in row.findall(_TABLE + "table-cell")
                ]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                out.append("\n".join(rows))
        else:  # lists, sections, frames: look inside
            _odt_blocks(child, out, budget)


def load_odt(path: Path, doc_meta: dict) -> list[Chunk]:
    """One Chunk per page of an OpenDocument text file (.odt).

    Pages come from the page breaks saved in the file. If it has none, the
    whole document is one block and citations give no page number.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(path) as archive:
        if archive.getinfo("content.xml").file_size > MAX_ODT_XML:
            raise ValueError(f"{path.name}: content too large to read safely")
        root = ET.fromstring(archive.read("content.xml"))
    body = root.find(f"{_OFFICE}body/{_OFFICE}text")
    blocks: list = []
    if body is not None:
        _odt_blocks(body, blocks, _SpaceBudget())

    pages: list[list[str]] = [[]]
    for block in blocks:
        if block is None:
            # A break before any text doesn't start a new page. After that every break
            # does, so a blank page between two breaks keeps the page numbers right.
            if len(pages) > 1 or pages[0]:
                pages.append([])
        else:
            pages[-1].append(block)
    known = len(pages) > 1
    return [
        Chunk(
            text="\n\n".join(page),
            metadata={
                **doc_meta,
                "page_number": i + 1,
                "page_label": str(i + 1) if known else PAGE_UNKNOWN,
            },
        )
        for i, page in enumerate(pages)
    ]


def load_document(path: Path, doc_meta: dict) -> list[Chunk]:
    """Pages of a PDF or .odt file."""
    return load_odt(path, doc_meta) if path.suffix.lower() == ".odt" else load_pdf(path, doc_meta)


def load_documents(docs_dir: Path, manifest: dict[str, dict] | None = None) -> list[Chunk]:
    """Every page of every document under docs_dir."""
    manifest = manifest or {}
    pages = []
    for path in list_documents(docs_dir):
        pages += load_document(path, doc_metadata(path, docs_dir, manifest))
    return pages


def chunk_pages(pages: list[Chunk], size: int, overlap: int) -> list[Chunk]:
    chunks = []
    for page in pages:
        for i, text in enumerate(split_text(page.text, size, overlap)):
            chunks.append(Chunk(text=text, metadata={**page.metadata, "chunk_index": i}))
    return chunks


def split_text(text: str, size: int, overlap: int = 0) -> list[str]:
    """Split text into chunks of at most `size` characters.

    Tries to break at paragraphs, then lines, then sentences, then words, so
    chunks end at natural boundaries. Consecutive chunks share up to `overlap`
    characters, so a sentence cut at a boundary still appears whole somewhere.
    """
    text = text.strip()
    if not text:
        return []
    return _merge(_pieces(text, size, SEPARATORS), size, overlap)


def _pieces(text: str, size: int, separators: tuple[str, ...]) -> list[str]:
    """Break text into pieces no longer than `size`, keeping separators attached."""
    if len(text) <= size:
        return [text]
    for i, sep in enumerate(separators):
        if sep in text:
            parts = text.split(sep)
            parts = [p + sep for p in parts[:-1]] + [parts[-1]]
            out = []
            for part in parts:
                out.extend(_pieces(part, size, separators[i + 1 :]) if len(part) > size else [part])
            return [p for p in out if p]
    return [text[i : i + size] for i in range(0, len(text), size)]  # no separators left


def _merge(pieces: list[str], size: int, overlap: int) -> list[str]:
    chunks, current, length = [], [], 0
    for piece in pieces:
        if current and length + len(piece) > size:
            chunks.append("".join(current).strip())
            # Carry the last few pieces forward as overlap.
            carried, carried_len = [], 0
            for prev in reversed(current):
                if carried_len + len(prev) > overlap:
                    break
                carried.insert(0, prev)
                carried_len += len(prev)
            if carried_len + len(piece) > size:
                carried, carried_len = [], 0
            current, length = carried, carried_len
        current.append(piece)
        length += len(piece)
    if current:
        chunks.append("".join(current).strip())
    return [c for c in chunks if c]


def clean_text(text: str) -> str:
    """Collapse the extra whitespace PDF extraction leaves behind."""
    return re.sub(r"[ \t]+", " ", text).strip()
