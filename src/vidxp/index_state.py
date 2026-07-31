from __future__ import annotations

from typing import Any

from vidxp.core.contracts import INDEX_SCHEMA_VERSION
from vidxp.core.snapshots import IndexSnapshot


INDEX_STATUS_SCHEMA = 1
INDEX_STATUS_MEDIA_ID_LIMIT = 100


def bounded_media_ids(media_ids: list[str]) -> dict[str, object]:
    selected = media_ids[:INDEX_STATUS_MEDIA_ID_LIMIT]
    return {
        "media_ids": selected,
        "media_ids_truncated": len(media_ids) > len(selected),
    }


def snapshot_status(snapshot: IndexSnapshot) -> dict[str, Any]:
    media_ids = sorted(snapshot.generations)
    ready = bool(media_ids)
    return {
        "schema_version": INDEX_STATUS_SCHEMA,
        "state": "ready" if ready else "empty",
        "stage": "status",
        "message": (
            f"The active snapshot contains {len(media_ids)} media item(s)."
            if ready
            else "The active index snapshot is empty."
        ),
        "updated_at": snapshot.created_at.isoformat(),
        "summary": {
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "snapshot_id": snapshot.snapshot_id,
            "media_count": len(media_ids),
            **bounded_media_ids(media_ids),
            "modalities": tuple(
                snapshot.configuration.get("enabled_modalities", ())
            ),
        },
    }


class IndexNotReadyError(RuntimeError):
    """Raised when a search is attempted without a completed index."""


class IndexingInProgressError(RuntimeError):
    """Raised when another indexing run is already active."""
