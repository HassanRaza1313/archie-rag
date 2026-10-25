"""Data sources: discover documents to index.

A source yields ``DocumentRef``s. Add a source type by writing a ``discover`` function and registering it
in ``SOURCES``; its options live on ``SourceConfig``.
"""

from __future__ import annotations

import fnmatch
import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ragkit.config import SourceConfig
from ragkit.settings import Settings


class SourceError(ValueError):
    """The source is misconfigured or not allowed; the message is safe to show to users."""


@dataclass(frozen=True)
class DocumentRef:
    uri: str  # stable id: path relative to the source root, POSIX separators
    path: Path
    size: int

    def content_hash(self) -> str:
        digest = hashlib.sha256()
        with self.path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()


def resolve_root(config: SourceConfig, settings: Settings, kb_id: str) -> Path:
    """The folder to read, guaranteed to be inside an allowed data root (or the KB's upload folder)."""
    if config.type == "uploads":
        root = (settings.uploads_dir / kb_id).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root
    root = Path(config.path).expanduser().resolve()
    allowed = [Path(item).expanduser().resolve() for item in settings.data_roots]
    if not any(root == base or base in root.parents for base in allowed):
        roots = ", ".join(str(base) for base in allowed)
        raise SourceError(f"'{config.path}' is outside the allowed data roots ({roots}); set RAG_DATA_ROOTS")
    if not root.is_dir():
        raise SourceError(f"folder '{config.path}' does not exist")
    return root


def discover_folder(root: Path, config: SourceConfig) -> Iterator[DocumentRef]:
    suffixes = {suffix.lower() for suffix in config.file_types}
    candidates = root.rglob("*") if config.recursive else root.glob("*")
    for path in sorted(candidates):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        uri = path.relative_to(root).as_posix()
        if config.include and not any(fnmatch.fnmatch(uri, pattern) for pattern in config.include):
            continue
        if any(fnmatch.fnmatch(uri, pattern) for pattern in config.exclude):
            continue
        yield DocumentRef(uri=uri, path=path, size=path.stat().st_size)


SOURCES: dict[str, Callable[[Path, SourceConfig], Iterator[DocumentRef]]] = {
    "local_folder": discover_folder,
    "uploads": discover_folder,
}


def discover(config: SourceConfig, settings: Settings, kb_id: str) -> list[DocumentRef]:
    return list(SOURCES[config.type](resolve_root(config, settings, kb_id), config))
