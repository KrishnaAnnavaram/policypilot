"""Document loaders: text/Markdown files, PDF files or uploaded PDF bytes."""
from __future__ import annotations

import io
from importlib import resources
from pathlib import Path

from .index import HybridIndex

TEXT_SUFFIXES = {".txt", ".md"}


def pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise RuntimeError("PDF support needs the 'pdf' extra: pip install 'policypilot[pdf]'") from exc
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def index_path(index: HybridIndex, path: str | Path) -> int:
    path = Path(path)
    added = 0
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    for file in files:
        suffix = file.suffix.lower()
        if suffix in TEXT_SUFFIXES:
            added += index.add_document(file.read_text(encoding="utf-8"), file.name)
        elif suffix == ".pdf":
            added += index.add_document(pdf_text(file.read_bytes()), file.name)
    return added


def index_demo_documents(index: HybridIndex) -> int:
    """Index the small synthetic policy documents shipped with the package."""
    added = 0
    root = resources.files("policypilot") / "demo_docs"
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            added += index.add_document(entry.read_text(encoding="utf-8"), entry.name)
    return added
