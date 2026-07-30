from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TypeVar
from urllib.parse import urlsplit

from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
    batched,
)

_T = TypeVar("_T")

BUNDLED_CHROMA_SERVER_URL = "http://chroma:8000"


class IndexStorageUnavailableError(RuntimeError):
    """The configured remote vector store could not serve an operation."""


def _is_remote_unavailable(exc: Exception) -> bool:
    """Return whether an exception represents a retryable remote failure."""

    import httpx
    from chromadb.errors import (
        InternalError,
        RateLimitError,
    )

    return isinstance(
        exc,
        (
            httpx.TransportError,
            InternalError,
            RateLimitError,
        ),
    )


def metadata_filter(
    config: IndexConfig,
    *,
    video_id: str | None = None,
    generation_ids: Iterable[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "dataset": config.dataset,
        "split": config.split,
        "run_id": config.run_id,
    }
    if video_id is not None:
        values["video_id"] = video_id
    selected_generations = _generation_ids(generation_ids)
    if selected_generations is not None:
        if not selected_generations:
            raise ValueError("generation_ids must not be empty.")
        values["generation_id"] = {"$in": list(selected_generations)}
    if extra:
        reserved = set(values) & set(extra)
        if reserved:
            raise ValueError(
                "Search filters cannot override run identity fields: "
                + ", ".join(sorted(reserved))
            )
        values.update(extra)
    clauses = [{key: value} for key, value in values.items()]
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _generation_ids(
    generation_ids: Iterable[str] | None,
) -> tuple[str, ...] | None:
    if generation_ids is None:
        return None
    if isinstance(generation_ids, str):
        raise TypeError("generation_ids must be an iterable of identifiers.")
    values = tuple(generation_ids)
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("generation_ids must contain non-empty strings.")
    return tuple(dict.fromkeys(values))


class ChromaClientFactory:
    def __init__(self, server_url: str | None = None) -> None:
        self.server_url = server_url

    @property
    def remote(self) -> bool:
        return self.server_url is not None

    def create(self, path: Path):
        import chromadb

        if self.server_url is None:
            return chromadb.PersistentClient(path=str(path.resolve()))
        parsed = urlsplit(self.server_url)
        return chromadb.HttpClient(
            host=parsed.hostname or "",
            port=parsed.port or (443 if parsed.scheme == "https" else 80),
            ssl=parsed.scheme == "https",
        )

    def heartbeat(self) -> int:
        client = self.create(Path("."))
        try:
            return int(client.heartbeat())
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                close()


class IndexStorage:
    def __init__(
        self,
        config: IndexConfig,
        *,
        create: bool = True,
        client_factory: ChromaClientFactory | None = None,
    ):
        self.config = config
        self.path = config.index_directory
        self._create = create
        self._client_factory = client_factory or ChromaClientFactory()
        self._names = dict(config.collection_names)
        self._collections: dict[str, Any] = {}
        if create and not self._client_factory.remote:
            self.path.mkdir(parents=True, exist_ok=True)
        elif not self._client_factory.remote and not self.path.is_dir():
            raise FileNotFoundError(
                f"The committed Chroma store is missing at {self.path}."
            )
        try:
            self.client = self._client_factory.create(self.path)
        except Exception as exc:
            if (
                self._client_factory.remote
                and _is_remote_unavailable(exc)
            ):
                raise IndexStorageUnavailableError(
                    "The remote Chroma service is unavailable."
                ) from exc
            raise
        if not create:
            try:
                existing = self._call(self.client.list_collections)
            except Exception:
                self.close()
                raise
            if not existing:
                self.close()
                raise FileNotFoundError(
                    f"The committed Chroma store is missing at {self.path}."
                )

    def close(self) -> None:
        self._collections.clear()
        close = getattr(self.client, "close", None)
        if close is not None:
            close()

    def __enter__(self) -> "IndexStorage":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def collection(self, modality: str):
        if modality in self._collections:
            return self._collections[modality]
        try:
            name = self._names[modality]
        except KeyError as exc:
            raise ValueError(f"Unsupported collection modality: {modality}") from exc
        if self._create:
            collection = self._call(
                self.client.get_or_create_collection,
                name=name,
                metadata={"hnsw:space": self.config.vector_distance},
            )
        else:
            from chromadb.errors import NotFoundError

            try:
                collection = self._call(
                    self.client.get_collection,
                    name=name,
                )
            except NotFoundError as exc:
                raise FileNotFoundError(
                    f"Committed Chroma collection {name!r} is missing."
                ) from exc
        configuration = getattr(collection, "configuration", {}) or {}
        actual_distance = (configuration.get("hnsw") or {}).get("space")
        if (
            actual_distance is not None
            and actual_distance != self.config.vector_distance
        ):
            raise ValueError(
                f"Collection {name!r} uses {actual_distance!r} distance, "
                f"not configured {self.config.vector_distance!r} distance."
            )
        self._collections[modality] = collection
        return collection

    def _call(self, operation: Callable[..., _T], /, *args, **kwargs) -> _T:
        try:
            return operation(*args, **kwargs)
        except Exception as exc:
            if (
                self._client_factory.remote
                and _is_remote_unavailable(exc)
            ):
                raise IndexStorageUnavailableError(
                    "The remote Chroma service is unavailable."
                ) from exc
            raise

    def clear(self, modalities: Iterable[str] | None = None) -> None:
        selected = tuple(modalities or self._names)
        existing = {
            getattr(collection, "name", collection)
            for collection in self._call(self.client.list_collections)
        }
        for modality in selected:
            name = self._names[modality]
            if name in existing:
                self._call(self.client.delete_collection, name)
            self._collections.pop(modality, None)

    def delete_video(self, modality: str, video_id: str) -> None:
        self._call(
            self.collection(modality).delete,
            where=metadata_filter(self.config, video_id=video_id),
        )

    def delete_records(
        self,
        modality: str,
        *,
        video_id: str,
        filters: Mapping[str, Any] | None = None,
    ) -> None:
        self._call(
            self.collection(modality).delete,
            where=metadata_filter(
                self.config,
                video_id=video_id,
                extra=filters,
            ),
        )

    def delete_generation(
        self,
        generation_id: str,
        modalities: Iterable[str] | None = None,
    ) -> None:
        selected = (
            tuple(self._names)
            if modalities is None
            else tuple(modalities)
        )
        where = metadata_filter(
            self.config,
            generation_ids=(generation_id,),
        )
        for modality in selected:
            self._call(self.collection(modality).delete, where=where)

    def upsert(
        self,
        modality: str,
        records: list[StorageRecord],
        *,
        batch_size: int,
        cancellation: CancellationToken,
    ) -> int:
        if not records:
            return 0
        collection = self.collection(modality)
        stored = 0
        for group in batched(records, batch_size):
            cancellation.raise_if_cancelled()
            if any(record.embedding is None for record in group):
                raise ValueError("Every stored record requires an explicit embedding.")
            options: dict[str, Any] = {
                "ids": [record.source_id for record in group],
                "embeddings": [list(record.embedding or ()) for record in group],
                "metadatas": [dict(record.metadata) for record in group],
            }
            if any(record.document is not None for record in group):
                options["documents"] = [record.document or "" for record in group]
            self._call(collection.upsert, **options)
            stored += len(group)
        return stored

    def query(
        self,
        modality: str,
        embedding: list[float],
        *,
        top_k: int,
        video_id: str | None = None,
        generation_ids: Iterable[str] | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero.")
        selected_generations = _generation_ids(generation_ids)
        if selected_generations == ():
            return []

        options: dict[str, Any] = {
            "query_embeddings": [embedding],
            "include": ["metadatas", "distances"],
            "n_results": top_k,
            "where": metadata_filter(
                self.config,
                video_id=video_id,
                generation_ids=selected_generations,
                extra=filters,
            ),
        }
        result = self._call(self.collection(modality).query, **options)
        ids = (result.get("ids") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        if not (len(ids) == len(metadatas) == len(distances)):
            raise RuntimeError(
                "The vector store returned misaligned IDs, metadata, and distances."
            )
        return [
            {
                "source_id": source_id,
                "metadata": dict(metadata or {}),
                "raw_distance": float(distance),
            }
            for source_id, metadata, distance in zip(ids, metadatas, distances)
        ]

    def records(
        self,
        modality: str,
        *,
        video_id: str | None = None,
        generation_ids: Iterable[str] | None = None,
        filters: Mapping[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        selected_generations = _generation_ids(generation_ids)
        if selected_generations == ():
            return []
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be nonnegative")
        result = self._call(
            self.collection(modality).get,
            where=metadata_filter(
                self.config,
                video_id=video_id,
                generation_ids=selected_generations,
                extra=filters,
            ),
            include=["metadatas"],
            limit=limit,
            offset=offset,
        )
        return [
            dict(metadata)
            for metadata in (result.get("metadatas") or [])
            if metadata
        ]

    def count_records(
        self,
        modality: str,
        *,
        video_id: str | None = None,
        generation_ids: Iterable[str] | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> int:
        selected_generations = _generation_ids(generation_ids)
        if selected_generations == ():
            return 0
        result = self._call(
            self.collection(modality).get,
            where=metadata_filter(
                self.config,
                video_id=video_id,
                generation_ids=selected_generations,
                extra=filters,
            ),
            include=[],
        )
        return len(result.get("ids") or ())

    def size_bytes(self) -> int | None:
        if self._client_factory.remote:
            return None
        return directory_size(self.path)


class SnapshotScopedIndexStore:
    """Read-only view of records referenced by one immutable snapshot."""

    def __init__(
        self,
        store: IndexStorage,
        generation_ids: Iterable[str],
    ) -> None:
        self._store = store
        self._generation_ids = _generation_ids(generation_ids) or ()

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "SnapshotScopedIndexStore":
        self._store.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._store.__exit__(*args)

    def size_bytes(self) -> int | None:
        return self._store.size_bytes()

    def query(
        self,
        modality: str,
        embedding: list[float],
        *,
        top_k: int,
        video_id: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._store.query(
            modality,
            embedding,
            top_k=top_k,
            video_id=video_id,
            generation_ids=self._generation_ids,
            filters=filters,
        )

    def records(
        self,
        modality: str,
        *,
        video_id: str | None = None,
        filters: Mapping[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        options: dict[str, Any] = {
            "video_id": video_id,
            "generation_ids": self._generation_ids,
            "filters": filters,
        }
        if limit is not None:
            options["limit"] = limit
        if offset:
            options["offset"] = offset
        return self._store.records(modality, **options)

    def count_records(
        self,
        modality: str,
        *,
        video_id: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> int:
        return self._store.count_records(
            modality,
            video_id=video_id,
            generation_ids=self._generation_ids,
            filters=filters,
        )

def directory_size(path: str | Path) -> int:
    root = Path(path)
    if not root.exists():
        return 0
    return sum(
        item.stat().st_size
        for item in root.rglob("*")
        if item.is_file()
    )
