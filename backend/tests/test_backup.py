"""Backup and restore: the database and uploads travel in one file; missing indexes are rebuilt."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

from ragkit import cli
from ragkit.config import IndexConfig, KnowledgeBaseConfig
from ragkit.index import VectorIndex
from ragkit.settings import Settings
from ragkit.storage import Database


def test_restore_onto_a_new_machine_rebuilds_what_is_missing(settings: Settings, tmp_path: Path) -> None:
    db = Database(settings.db_path)
    kept, lost, draft = (db.create_kb(name, KnowledgeBaseConfig()) for name in ("kept", "lost", "draft"))
    db.activate_index(kept, 1, "hash", "kb_kept_index")
    db.activate_index(lost, 1, "hash", "kb_lost_index")
    db.add_turn(f"{kept}:s", "q", "a", keep=5)
    upload = settings.uploads_dir / lost / "policy.txt"
    upload.parent.mkdir(parents=True)
    upload.write_text("Staff get 25 days of leave.")
    archive = tmp_path / "archie.tar.gz"
    assert cli._backup(settings, archive) == 0  # while the database is open, as on a running install

    fresh = settings.model_copy(update={"state_dir": tmp_path / "new-machine"})
    index = VectorIndex(fresh)
    index.create("kb_kept_index", 8, IndexConfig(), [])  # this vector store still has one of the indexes
    index.close()
    assert cli._restore(fresh, archive) == 0

    restored = Database(fresh.db_path)
    kbs = {kb["name"]: kb for kb in restored.list_kbs()}
    assert kbs["kept"]["active_collection"] == "kb_kept_index" and restored.active_job(kept) is None
    assert kbs["lost"]["active_collection"] is None and restored.active_job(lost) is not None
    assert restored.active_job(draft) is None  # never built: nothing to rebuild
    assert restored.turns(f"{kept}:s", 5, since=0) == [("q", "a")]
    assert (fresh.uploads_dir / lost / "policy.txt").read_text() == "Staff get 25 days of leave."


def test_restore_refuses_files_that_are_not_safe_backups(settings: Settings, tmp_path: Path) -> None:
    hostile = tmp_path / "hostile.tar.gz"
    with tarfile.open(hostile, "w:gz") as archive:
        payload = b"owned"
        member = tarfile.TarInfo("../outside.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    assert cli._restore(settings, hostile) == 1  # a path that climbs out of the folder

    empty = tmp_path / "empty.tar.gz"
    with tarfile.open(empty, "w:gz"):
        pass
    assert cli._restore(settings, empty) == 1
    assert cli._backup(settings.model_copy(update={"state_dir": tmp_path / "nothing"}), tmp_path / "x.tar.gz") == 1
