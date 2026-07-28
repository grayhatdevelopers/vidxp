INDEX_STATUS_SCHEMA = 1


class IndexNotReadyError(RuntimeError):
    """Raised when a search is attempted without a completed index."""


class IndexingInProgressError(RuntimeError):
    """Raised when another indexing run is already active."""
