"""Qdrant vector index: dense + BM25 sparse vectors per chunk, payload indexes, batched hybrid queries.

Server mode (``RAG_QDRANT_MODE=server``) gives real HNSW and payload indexes. Embedded mode stores data
locally through the same client API but searches by brute force, so it suits small corpora; it also allows
a single process per storage path, which is why ``ragkit serve --with-worker`` runs the worker in-process.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from ragkit.config import IndexConfig
from ragkit.settings import Settings

DENSE = "dense"
SPARSE = "bm25"
POINT_NAMESPACE = uuid.UUID("5f0e7d1c-8a4b-4c39-9d5e-2b7a1e6c3f90")
BUILTIN_FILTER_FIELDS = ("document_uri", "file_name", "file_type", "content_type")


@dataclass
class Hit:
    id: str
    score: float
    payload: dict[str, Any]


def point_id(kb_id: str, content_hash: str, ordinal: int) -> str:
    """Deterministic UUID, so re-indexing the same document overwrites instead of duplicating."""
    return str(uuid.uuid5(POINT_NAMESPACE, f"{kb_id}:{content_hash}:{ordinal}"))


def collection_name(kb_id: str, index_hash: str) -> str:
    return f"{kb_id}__{index_hash[:10]}"


class VectorIndex:
    def __init__(self, settings: Settings) -> None:
        self.mode = settings.qdrant_mode
        if self.mode == "server":
            self.client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60)
        else:
            settings.embedded_qdrant_path.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(settings.embedded_qdrant_path))
        # Embedded storage is not safe for concurrent access from threads; the server handles its own.
        self._lock = threading.Lock() if self.mode == "embedded" else None

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self._lock is None:
            return getattr(self.client, method)(*args, **kwargs)
        with self._lock:
            return getattr(self.client, method)(*args, **kwargs)

    def close(self) -> None:
        self._call("close")

    def capabilities(self) -> dict[str, Any]:
        server = self.mode == "server"
        return {"mode": self.mode, "hnsw": server, "payload_indexes": server, "hybrid": True, "fusion": ["rrf", "dbsf"]}

    def ping(self) -> bool:
        try:
            self._call("get_collections")
        except Exception:
            return False
        return True

    def exists(self, name: str) -> bool:
        return bool(self._call("collection_exists", name))

    def create(self, name: str, dimension: int, config: IndexConfig, filter_fields: Sequence[str]) -> None:
        quantization = (
            models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(type=models.ScalarType.INT8, always_ram=True)
            )
            if config.quantization
            else None
        )
        self._call(
            "create_collection",
            collection_name=name,
            vectors_config={
                DENSE: models.VectorParams(
                    size=dimension,
                    distance=models.Distance.COSINE,
                    hnsw_config=models.HnswConfigDiff(m=config.hnsw_m, ef_construct=config.hnsw_ef_construct),
                    quantization_config=quantization,
                )
            },
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        if self.mode == "server":
            for field_name in (*BUILTIN_FILTER_FIELDS, *filter_fields):
                self._call(
                    "create_payload_index", name, field_name=field_name, field_schema=models.PayloadSchemaType.KEYWORD
                )
            self._call(
                "create_payload_index", name, field_name="page_start", field_schema=models.PayloadSchemaType.INTEGER
            )

    def drop(self, name: str) -> None:
        if self.exists(name):
            self._call("delete_collection", name)

    def drop_prefix(self, prefix: str) -> list[str]:
        names = [c.name for c in self._call("get_collections").collections if c.name.startswith(prefix)]
        for name in names:
            self._call("delete_collection", name)
        return names

    def count(self, name: str) -> int:
        return int(self._call("count", name, exact=True).count) if self.exists(name) else 0

    def upsert(self, name: str, points: list[tuple[str, list[float], dict[int, float], dict[str, Any]]]) -> None:
        self._call(
            "upsert",
            collection_name=name,
            points=[
                models.PointStruct(
                    id=pid,
                    vector={
                        DENSE: dense,
                        SPARSE: models.SparseVector(indices=list(sparse), values=list(sparse.values())),
                    },
                    payload=payload,
                )
                for pid, dense, sparse, payload in points
            ],
            wait=True,
        )

    def delete_documents(self, name: str, uris: Sequence[str]) -> None:
        if uris:
            self._call(
                "delete",
                collection_name=name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[models.FieldCondition(key="document_uri", match=models.MatchAny(any=list(uris)))]
                    )
                ),
                wait=True,
            )

    def search(
        self,
        name: str,
        dense: list[float] | None,
        sparse: dict[int, float] | None,
        limit: int,
        filters: dict[str, Any] | None = None,
        search_ef: int = 64,
    ) -> tuple[list[Hit], list[Hit]]:
        """Run the dense and keyword queries in one round trip; returns (dense hits, keyword hits)."""
        query_filter = _build_filter(filters)
        requests, kinds = [], []
        if dense is not None:
            params = models.SearchParams(hnsw_ef=search_ef) if self.mode == "server" else None  # embedded: exact
            requests.append(
                models.QueryRequest(
                    query=dense, using=DENSE, limit=limit, filter=query_filter, with_payload=True, params=params
                )
            )
            kinds.append(DENSE)
        if sparse:
            requests.append(
                models.QueryRequest(
                    query=models.SparseVector(indices=list(sparse), values=list(sparse.values())),
                    using=SPARSE,
                    limit=limit,
                    filter=query_filter,
                    with_payload=True,
                )
            )
            kinds.append(SPARSE)
        responses = self._call("query_batch_points", name, requests) if requests else []
        results: dict[str, list[Hit]] = {DENSE: [], SPARSE: []}
        for kind, response in zip(kinds, responses, strict=True):
            results[kind] = [Hit(str(p.id), float(p.score), dict(p.payload or {})) for p in response.points]
        return results[DENSE], results[SPARSE]

    def sample_payloads(self, name: str, limit: int) -> list[dict[str, Any]]:
        points, _ = self._call("scroll", name, limit=limit, with_payload=True, with_vectors=False)
        return [dict(point.payload or {}) for point in points]


def _build_filter(filters: dict[str, Any] | None) -> models.Filter | None:
    if not filters:
        return None
    conditions: list[models.Condition] = []
    for key, value in filters.items():
        match = models.MatchAny(any=list(value)) if isinstance(value, list) else models.MatchValue(value=value)
        conditions.append(models.FieldCondition(key=key, match=match))
    return models.Filter(must=conditions)
