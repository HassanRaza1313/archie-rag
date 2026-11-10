"""Backup and restore: one file with the database (chatbots and their settings history, people, the change history,
passage edits, enrichment results, traces) and the uploaded documents.

Vectors are derived data and stay out: a restore keeps every index that still exists and rebuilds the others. The
enrichment results come back with the database, so a rebuild repeats no language-model calls for them.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from ragkit import __version__
from ragkit.settings import Settings

FORMAT = 1


def backup(settings: Settings, target: Path) -> Path:
    """Write a consistent backup while Archie keeps running (SQLite's online backup)."""
    if not settings.db_path.exists():
        raise FileNotFoundError(f"no Archie database at {settings.db_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "ragkit.db"
        source, copy = sqlite3.connect(settings.db_path), sqlite3.connect(snapshot)
        try:
            source.backup(copy)
        finally:
            source.close()
            copy.close()
        manifest = Path(tmp) / "manifest.json"
        manifest.write_text(json.dumps({"format": FORMAT, "version": __version__, "created_at": time.time()}))
        with tarfile.open(target, "w:gz") as archive:
            archive.add(manifest, "manifest.json")
            archive.add(snapshot, "ragkit.db")
            if settings.uploads_dir.exists():
                archive.add(settings.uploads_dir, "uploads")
    return target


def restore(settings: Settings, source: Path) -> list[str]:
    """Replace this install's state with a backup (Archie must be stopped). Returns the chatbots queued to rebuild."""
    with tempfile.TemporaryDirectory() as tmp:
        unpacked = Path(tmp)
        with tarfile.open(source, "r:gz") as archive:
            archive.extractall(unpacked, filter="data")  # refuses absolute paths, '..' and links out of the folder
        manifest = unpacked / "manifest.json"
        if not manifest.exists() or not (unpacked / "ragkit.db").exists():
            raise ValueError(f"{source} is not an Archie backup")
        if json.loads(manifest.read_text()).get("format") != FORMAT:
            raise ValueError("this backup was made by a different Archie version; restore it with that version")
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):  # the old database's journal must not be replayed onto the restored one
            Path(f"{settings.db_path}{suffix}").unlink(missing_ok=True)
        shutil.copyfile(unpacked / "ragkit.db", settings.db_path)
        shutil.rmtree(settings.uploads_dir, ignore_errors=True)
        if (unpacked / "uploads").exists():
            shutil.copytree(unpacked / "uploads", settings.uploads_dir)
    return _rebuild_missing_indexes(settings)


def _rebuild_missing_indexes(settings: Settings) -> list[str]:
    from ragkit.index import VectorIndex
    from ragkit.jobs import BUILD_STAGES
    from ragkit.storage import Database, JobConflictError

    db, index = Database(settings.db_path), VectorIndex(settings)
    queued = []
    try:
        for kb in db.list_kbs():
            if not kb["active_collection"] or index.exists(kb["active_collection"]):
                continue  # never built, or its index is still there
            db.clear_index(kb["id"])
            with contextlib.suppress(JobConflictError):  # a build from the backup is still queued: it runs anyway
                db.create_job(kb["id"], kb["active_version"], BUILD_STAGES)
            queued.append(kb["name"])
    finally:
        index.close()
        db.close()
    return queued
