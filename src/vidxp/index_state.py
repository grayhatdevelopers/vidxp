INDEX_STATUS_SCHEMA = 1
INDEX_STATUS_MEDIA_ID_LIMIT = 100


def bounded_media_ids(media_ids: list[str]) -> dict[str, object]:
    selected = media_ids[:INDEX_STATUS_MEDIA_ID_LIMIT]
    return {
        "media_ids": selected,
        "media_ids_truncated": len(media_ids) > len(selected),
    }


class IndexNotReadyError(RuntimeError):
    """Raised when a search is attempted without a completed index."""


class IndexingInProgressError(RuntimeError):
    """Raised when another indexing run is already active."""
