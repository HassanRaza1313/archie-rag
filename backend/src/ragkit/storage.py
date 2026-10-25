"""SQLite persistence for app state: knowledge bases, config versions, jobs, indexed documents, traces, cache.

One connection per ``Database`` guarded by a lock; WAL mode lets the API and the worker share the file.
shortcut: single-node SQLite; move to Postgres behind this same interface for multi-node deployments.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ragkit.config import KnowledgeBaseConfig

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    active_version INTEGER NOT NULL,
    built_version INTEGER,
    built_index_hash TEXT,
    active_collection TEXT
);
CREATE TABLE IF NOT EXISTS config_versions (
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    config TEXT NOT NULL,
    index_hash TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (kb_id, version)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    config_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    heartbeat_at REAL,
    error TEXT,
    summary TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
CREATE TABLE IF NOT EXISTS job_stages (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL,
    items_in INTEGER NOT NULL DEFAULT 0,
    items_out INTEGER NOT NULL DEFAULT 0,
    items_skipped INTEGER NOT NULL DEFAULT 0,
    items_failed INTEGER NOT NULL DEFAULT 0,
    started_at REAL,
    finished_at REAL,
    detail TEXT,
    PRIMARY KEY (job_id, stage)
);
CREATE TABLE IF NOT EXISTS job_failures (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    document TEXT NOT NULL,
    stage TEXT NOT NULL,
    error TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS indexed_documents (
    collection TEXT NOT NULL,
    uri TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    indexed_at REAL NOT NULL,
    PRIMARY KEY (collection, uri)
);
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    config_version INTEGER NOT NULL,
    session_id TEXT,
    created_at REAL NOT NULL,
    outcome TEXT NOT NULL,
    total_ms REAL NOT NULL,
    ttft_ms REAL,
    cache_hit INTEGER NOT NULL,
    confidence REAL,
    retries INTEGER NOT NULL,
    verify TEXT,
    query TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS traces_kb_time ON traces(kb_id, created_at DESC);
CREATE TABLE IF NOT EXISTS answer_cache (
    scope TEXT NOT NULL,
    question_key TEXT NOT NULL,
    question TEXT NOT NULL,
    embedding TEXT,
    answer TEXT NOT NULL,
    citations TEXT NOT NULL,
    asks INTEGER NOT NULL,
    last_asked_at REAL NOT NULL,
    PRIMARY KEY (scope, question_key)
);
"""

TERMINAL_JOB_STATES = ("completed", "completed_with_errors", "failed", "cancelled")


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    def _run(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    # Knowledge bases and config versions

    def create_kb(self, name: str, config: KnowledgeBaseConfig) -> str:
        kb_id = "kb_" + uuid.uuid4().hex[:12]
        now = time.time()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_bases (id, name, created_at, active_version) VALUES (?, ?, ?, 1)",
                (kb_id, name, now),
            )
            self._insert_version(conn, kb_id, 1, config, now)
        return kb_id

    def list_kbs(self) -> list[dict[str, Any]]:
        return self._all(
            """SELECT kb.*, cv.index_hash AS active_index_hash,
                      (SELECT COUNT(*) FROM indexed_documents d WHERE d.collection = kb.active_collection)
                          AS document_count,
                      (SELECT COALESCE(SUM(chunk_count), 0) FROM indexed_documents d
                          WHERE d.collection = kb.active_collection) AS chunk_count
               FROM knowledge_bases kb
               JOIN config_versions cv ON cv.kb_id = kb.id AND cv.version = kb.active_version
               ORDER BY kb.created_at"""
        )

    def get_kb(self, kb_id: str) -> dict[str, Any] | None:
        return next((kb for kb in self.list_kbs() if kb["id"] == kb_id), None)

    def get_kb_by_name(self, name: str) -> dict[str, Any] | None:
        return next((kb for kb in self.list_kbs() if kb["name"] == name), None)

    def delete_kb(self, kb_id: str) -> None:
        self._run("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))

    def add_config_version(self, kb_id: str, config: KnowledgeBaseConfig) -> int:
        with self.transaction() as conn:
            row = conn.execute("SELECT MAX(version) FROM config_versions WHERE kb_id = ?", (kb_id,)).fetchone()
            version = int(row[0] or 0) + 1
            self._insert_version(conn, kb_id, version, config, time.time())
            conn.execute("UPDATE knowledge_bases SET active_version = ? WHERE id = ?", (version, kb_id))
        return version

    @staticmethod
    def _insert_version(
        conn: sqlite3.Connection, kb_id: str, version: int, config: KnowledgeBaseConfig, now: float
    ) -> None:
        conn.execute(
            "INSERT INTO config_versions VALUES (?, ?, ?, ?, ?, ?)",
            (kb_id, version, config.model_dump_json(), config.index_fingerprint(), config.query_fingerprint(), now),
        )

    def get_config(self, kb_id: str, version: int | None = None) -> tuple[int, KnowledgeBaseConfig]:
        if version is None:
            kb = self._one("SELECT active_version FROM knowledge_bases WHERE id = ?", (kb_id,))
            if kb is None:
                raise KeyError(kb_id)
            version = int(kb["active_version"])
        row = self._one("SELECT config FROM config_versions WHERE kb_id = ? AND version = ?", (kb_id, version))
        if row is None:
            raise KeyError(f"{kb_id} v{version}")
        return version, KnowledgeBaseConfig.model_validate_json(row["config"])

    def list_versions(self, kb_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT version, index_hash, query_hash, created_at FROM config_versions WHERE kb_id = ? "
            "ORDER BY version DESC",
            (kb_id,),
        )

    def activate_index(self, kb_id: str, version: int, index_hash: str, collection: str) -> str | None:
        """Point the KB at a freshly built collection; returns the previous collection, if any."""
        with self.transaction() as conn:
            row = conn.execute("SELECT active_collection FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
            conn.execute(
                "UPDATE knowledge_bases SET built_version = ?, built_index_hash = ?, active_collection = ? "
                "WHERE id = ?",
                (version, index_hash, collection, kb_id),
            )
        previous = row["active_collection"] if row else None
        return previous if previous != collection else None

    # Jobs

    def create_job(self, kb_id: str, config_version: int, stages: list[str]) -> str:
        job_id = "job_" + uuid.uuid4().hex[:12]
        with self.transaction() as conn:
            busy = conn.execute(
                "SELECT id FROM jobs WHERE kb_id = ? AND status IN ('queued', 'running')", (kb_id,)
            ).fetchone()
            if busy:
                raise JobConflictError(busy["id"])
            conn.execute(
                "INSERT INTO jobs (id, kb_id, config_version, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
                (job_id, kb_id, config_version, time.time()),
            )
            conn.executemany(
                "INSERT INTO job_stages (job_id, stage, position, status) VALUES (?, ?, ?, 'pending')",
                [(job_id, name, position) for position, name in enumerate(stages)],
            )
        return job_id

    def claim_job(self, stale_after_s: float = 60.0) -> dict[str, Any] | None:
        """Atomically take the oldest queued job, or resume one whose worker stopped heartbeating."""
        now = time.time()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' OR (status = 'running' AND heartbeat_at < ?) "
                "ORDER BY created_at LIMIT 1",
                (now - stale_after_s,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = COALESCE(started_at, ?), heartbeat_at = ? "
                "WHERE id = ?",
                (now, now, row["id"]),
            )
        return dict(row) | {"status": "running"}

    def heartbeat(self, job_id: str) -> bool:
        """Refresh the job heartbeat; returns True when cancellation was requested."""
        self._run("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (time.time(), job_id))
        row = self._one("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,))
        return bool(row and row["cancel_requested"])

    def request_cancel(self, job_id: str) -> bool:
        changed = self._run(
            "UPDATE jobs SET cancel_requested = 1 WHERE id = ? AND status IN ('queued', 'running')", (job_id,)
        )
        self._run(
            "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ? AND status = 'queued'",
            (time.time(), job_id),
        )
        return changed > 0

    def finish_job(self, job_id: str, status: str, error: str | None, summary: dict[str, Any]) -> None:
        self._run(
            "UPDATE jobs SET status = ?, error = ?, summary = ?, finished_at = ? WHERE id = ?",
            (status, error, json.dumps(summary), time.time(), job_id),
        )

    def update_stage(self, job_id: str, stage_name: str, **fields: Any) -> None:
        allowed = {
            "status",
            "items_in",
            "items_out",
            "items_skipped",
            "items_failed",
            "started_at",
            "finished_at",
            "detail",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown stage fields {unknown}")
        if "detail" in fields and not isinstance(fields["detail"], str):
            fields["detail"] = json.dumps(fields["detail"])
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self._run(
            f"UPDATE job_stages SET {assignments} WHERE job_id = ? AND stage = ?",  # noqa: S608 - keys allowlisted
            (*fields.values(), job_id, stage_name),
        )

    def add_failure(self, job_id: str, document: str, stage_name: str, error: str) -> None:
        self._run("INSERT INTO job_failures VALUES (?, ?, ?, ?)", (job_id, document, stage_name, error[:2000]))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self._one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if job is None:
            return None
        job["summary"] = json.loads(job["summary"]) if job["summary"] else None
        job["stages"] = self._all("SELECT * FROM job_stages WHERE job_id = ? ORDER BY position", (job_id,))
        for row in job["stages"]:
            row["detail"] = json.loads(row["detail"]) if row["detail"] else None
        job["failures"] = self._all("SELECT document, stage, error FROM job_failures WHERE job_id = ?", (job_id,))
        return job

    def list_jobs(self, kb_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._all(
            "SELECT id, config_version, status, created_at, started_at, finished_at, error FROM jobs "
            "WHERE kb_id = ? ORDER BY created_at DESC LIMIT ?",
            (kb_id, limit),
        )

    def active_job(self, kb_id: str) -> dict[str, Any] | None:
        return self._one("SELECT id, status FROM jobs WHERE kb_id = ? AND status IN ('queued', 'running')", (kb_id,))

    # Indexed documents (incremental builds)

    def indexed_documents(self, collection: str) -> dict[str, str]:
        rows = self._all("SELECT uri, content_hash FROM indexed_documents WHERE collection = ?", (collection,))
        return {row["uri"]: row["content_hash"] for row in rows}

    def record_document(self, collection: str, uri: str, content_hash: str, chunk_count: int) -> None:
        self._run(
            "INSERT OR REPLACE INTO indexed_documents VALUES (?, ?, ?, ?, ?)",
            (collection, uri, content_hash, chunk_count, time.time()),
        )

    def forget_documents(self, collection: str, uris: list[str] | None = None) -> None:
        if uris is None:
            self._run("DELETE FROM indexed_documents WHERE collection = ?", (collection,))
            return
        with self.transaction() as conn:
            conn.executemany(
                "DELETE FROM indexed_documents WHERE collection = ? AND uri = ?", [(collection, u) for u in uris]
            )

    def collection_stats(self, collection: str) -> dict[str, int]:
        row = self._one(
            "SELECT COUNT(*) AS documents, COALESCE(SUM(chunk_count), 0) AS chunks FROM indexed_documents "
            "WHERE collection = ?",
            (collection,),
        )
        return {"documents": int(row["documents"]), "chunks": int(row["chunks"])} if row else {}

    # Traces

    def insert_trace(self, record: dict[str, Any]) -> None:
        columns = (
            "id",
            "kb_id",
            "config_version",
            "session_id",
            "created_at",
            "outcome",
            "total_ms",
            "ttft_ms",
            "cache_hit",
            "confidence",
            "retries",
            "verify",
            "query",
            "data",
        )
        values = [record[c] if c != "data" else json.dumps(record["data"]) for c in columns]
        placeholders = ", ".join("?" for _ in columns)
        self._run(f"INSERT INTO traces ({', '.join(columns)}) VALUES ({placeholders})", tuple(values))  # noqa: S608

    def list_traces(self, kb_id: str | None, outcome: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
        clauses, params = [], []
        if kb_id:
            clauses.append("kb_id = ?")
            params.append(kb_id)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # WHERE clauses are static strings; every value is a bound parameter.
        return self._all(
            "SELECT id, kb_id, config_version, session_id, created_at, outcome, total_ms, ttft_ms, cache_hit, "  # noqa: S608
            f"confidence, retries, verify, query FROM traces {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM traces WHERE id = ?", (trace_id,))
        if row:
            row["data"] = json.loads(row["data"])
        return row

    def trace_metrics(self, kb_id: str, window: int = 500) -> list[dict[str, Any]]:
        return self._all(
            "SELECT outcome, total_ms, ttft_ms, cache_hit, verify FROM traces WHERE kb_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (kb_id, window),
        )

    def slo_rows(self, kb_id: str | None, since: float) -> list[dict[str, Any]]:
        where, params = (
            ("WHERE created_at >= ? AND kb_id = ?", (since, kb_id)) if kb_id else ("WHERE created_at >= ?", (since,))
        )
        return self._all(f"SELECT created_at, outcome, total_ms, ttft_ms FROM traces {where}", params)  # noqa: S608

    def job_status_counts(self) -> dict[str, int]:
        return {
            row["status"]: int(row["n"]) for row in self._all("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        }

    def last_failed_build_time(self) -> float | None:
        row = self._one("SELECT MAX(finished_at) AS t FROM jobs WHERE status = 'failed'")
        return float(row["t"]) if row and row["t"] else None

    def purge_traces(self, kb_id: str, older_than: float) -> int:
        return self._run("DELETE FROM traces WHERE kb_id = ? AND created_at < ?", (kb_id, older_than))

    # Answer cache

    def cache_rows(self, scope: str, limit: int) -> list[dict[str, Any]]:
        """The active cache: the ``limit`` most-asked questions in scope (ties: most recent)."""
        return self._all(
            "SELECT * FROM answer_cache WHERE scope = ? ORDER BY asks DESC, last_asked_at DESC, rowid DESC LIMIT ?",
            (scope, limit),
        )

    def cache_touch(self, scope: str, question_key: str) -> None:
        self._run(
            "UPDATE answer_cache SET asks = asks + 1, last_asked_at = ? WHERE scope = ? AND question_key = ?",
            (time.time(), scope, question_key),
        )

    def cache_record(
        self,
        scope: str,
        question_key: str,
        question: str,
        embedding: list[float] | None,
        answer: str,
        citations: list[dict[str, Any]],
        history: int,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO answer_cache VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT (scope, question_key) DO UPDATE SET asks = asks + 1, answer = excluded.answer,
                       citations = excluded.citations, embedding = excluded.embedding,
                       last_asked_at = excluded.last_asked_at""",
                (
                    scope,
                    question_key,
                    question,
                    json.dumps(embedding) if embedding else None,
                    answer,
                    json.dumps(citations),
                    time.time(),
                ),
            )
            conn.execute(
                """DELETE FROM answer_cache WHERE scope = ? AND question_key NOT IN (
                       SELECT question_key FROM answer_cache WHERE scope = ?
                       ORDER BY asks DESC, last_asked_at DESC, rowid DESC LIMIT ?)""",
                (scope, scope, history),
            )

    def cache_stats(self, scope_prefix: str, capacity: int) -> dict[str, Any]:
        rows = self._all(
            "SELECT question, asks FROM answer_cache WHERE scope LIKE ? "
            "ORDER BY asks DESC, last_asked_at DESC, rowid DESC",
            (scope_prefix + "%",),
        )
        return {"history_size": len(rows), "cache_size": min(capacity, len(rows)), "top": rows[:capacity]}


class JobConflictError(Exception):
    """A build is already queued or running for this knowledge base."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"job {job_id} is already active")
        self.job_id = job_id
