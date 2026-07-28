import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INDEX_DIRECTORY = Path("chroma_data")
INDEX_STATUS_FILE = "index_status.json"
INDEX_STATUS_SCHEMA = 1


class IndexNotReadyError(RuntimeError):
    """Raised when a search is attempted without a completed index."""


class IndexingInProgressError(RuntimeError):
    """Raised when another indexing run is already active."""


def fingerprint_file(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    digest = hashlib.sha256()

    with file_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)

    stat = file_path.stat()
    return {
        "path": str(file_path.resolve()),
        "size": stat.st_size,
        "sha256": digest.hexdigest(),
    }


def read_index_status(index_directory: str | Path = INDEX_DIRECTORY) -> dict[str, Any] | None:
    status_path = Path(index_directory) / INDEX_STATUS_FILE
    if not status_path.is_file():
        return None

    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": INDEX_STATUS_SCHEMA,
            "state": "failed",
            "stage": "status",
            "message": "The saved index status is unreadable. Re-index the video.",
        }


def write_index_status(
    *,
    state: str,
    stage: str,
    message: str,
    video: dict[str, Any] | None = None,
    current: int | None = None,
    total: int | None = None,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
    index_directory: str | Path = INDEX_DIRECTORY,
) -> dict[str, Any]:
    index_path = Path(index_directory)
    index_path.mkdir(parents=True, exist_ok=True)
    status_path = index_path / INDEX_STATUS_FILE
    temporary_path = status_path.with_suffix(".tmp")

    payload: dict[str, Any] = {
        "schema_version": INDEX_STATUS_SCHEMA,
        "state": state,
        "stage": stage,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if video is not None:
        payload["video"] = video
    if current is not None:
        payload["current"] = current
    if total is not None:
        payload["total"] = total
    if summary is not None:
        payload["summary"] = summary
    if error is not None:
        payload["error"] = error

    temporary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_path.replace(status_path)
    return payload
