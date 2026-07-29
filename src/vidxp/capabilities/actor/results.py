from __future__ import annotations

from vidxp.capabilities.actor.schemas import (
    ActorClusterSummary,
    ActorClustersOutput,
    ActorDetection,
    ActorDetectionsOutput,
)
from vidxp.capabilities.contracts import CapabilityRequestError
from vidxp.core.contracts import IndexConfig
from vidxp.core.cursors import (
    CursorError,
    decode_cursor,
    decode_offset_cursor,
    encode_offset_cursor,
)
from vidxp.ports import IndexReader


class ActorClusterNotFoundError(LookupError):
    """Raised when an actor cluster has no retained detections."""


def _decode_cursor(cursor: str | None, scope: str) -> int:
    if cursor is None:
        return 0
    try:
        payload = decode_cursor(cursor, scope)
        if set(payload) != {"version", "scope", "offset"}:
            raise CursorError("The cursor payload is invalid.")
        return decode_offset_cursor(cursor, scope=scope)
    except CursorError as exc:
        raise CapabilityRequestError("The actor cursor is invalid.") from exc


def _encode_cursor(
    offset: int,
    *,
    scope: str,
    has_more: bool,
) -> str | None:
    return encode_offset_cursor(
        offset,
        scope=scope,
        has_more=has_more,
    )


def _snapshot_scope(config: IndexConfig) -> str:
    return str(config.snapshot_id or config.fingerprint())


def _actor_detection(record: dict) -> ActorDetection:
    return ActorDetection(
        **{
            key: value
            for key, value in record.items()
            if not key.startswith("bbox_") and key != "video_id"
        },
        media_id=str(record["video_id"]),
        bbox=(
            int(record["bbox_top"]),
            int(record["bbox_right"]),
            int(record["bbox_bottom"]),
            int(record["bbox_left"]),
        ),
    )


def _cluster_summary(
    cluster_id: str,
    records: list[dict],
) -> ActorClusterSummary:
    if not records:
        raise ActorClusterNotFoundError(
            f"Actor cluster {cluster_id} was not found in the completed index."
        )
    media_ids = {str(record["video_id"]) for record in records}
    generation_ids = {
        str(record["generation_id"])
        for record in records
        if record.get("generation_id") is not None
    }
    if len(media_ids) != 1 or len(generation_ids) != 1:
        raise RuntimeError(
            f"Actor cluster {cluster_id!r} spans multiple index identities."
        )
    timestamps = [float(record["timestamp"]) for record in records]
    return ActorClusterSummary(
        cluster_id=cluster_id,
        media_id=next(iter(media_ids)),
        generation_id=next(iter(generation_ids)),
        detection_count=len(records),
        first_timestamp=min(timestamps),
        last_timestamp=max(timestamps),
    )


def _stored_cluster_summary(record: dict) -> ActorClusterSummary:
    if record.get("record_kind") != "cluster_summary":
        raise RuntimeError("The actor cluster summary record is invalid.")
    return ActorClusterSummary(
        cluster_id=str(record["summary_cluster_id"]),
        media_id=str(record["video_id"]),
        generation_id=str(record["generation_id"]),
        detection_count=int(record["detection_count"]),
        first_timestamp=float(record["first_timestamp"]),
        last_timestamp=float(record["last_timestamp"]),
    )


def actor_cluster(
    config: IndexConfig,
    cluster_id: str,
    *,
    storage: IndexReader,
) -> ActorClusterSummary:
    del config
    summaries = storage.records(
        "actor",
        filters={
            "record_kind": "cluster_summary",
            "summary_cluster_id": cluster_id,
        },
        limit=2,
    )
    if len(summaries) > 1:
        raise RuntimeError(
            f"Actor cluster {cluster_id!r} spans multiple index identities."
        )
    if summaries:
        return _stored_cluster_summary(summaries[0])
    return _cluster_summary(
        cluster_id,
        storage.records(
            "actor",
            filters={"cluster_id": cluster_id},
        ),
    )


def actor_clusters(
    config: IndexConfig,
    *,
    storage: IndexReader,
    page_size: int,
    cursor: str | None,
    media_id: str | None = None,
) -> ActorClustersOutput:
    scope = (
        f"actor:clusters:{_snapshot_scope(config)}:"
        f"{media_id or '*'}"
    )
    offset = _decode_cursor(cursor, scope)
    media_scope = {"video_id": media_id} if media_id is not None else {}
    records = storage.records(
        "actor",
        filters={"record_kind": "cluster_summary"},
        limit=page_size + 1,
        offset=offset,
        **media_scope,
    )
    if records:
        has_more = len(records) > page_size
        summaries = tuple(
            _stored_cluster_summary(record)
            for record in records[:page_size]
        )
        return ActorClustersOutput(
            clusters=summaries,
            total=None,
            next_cursor=_encode_cursor(
                offset + len(summaries),
                scope=scope,
                has_more=has_more,
            ),
        )
    if offset:
        raise CapabilityRequestError(
            "The actor cursor is outside the result set."
        )

    # Indexes created before cluster summaries were materialized remain
    # readable, but must use the legacy full-scan path once.
    grouped: dict[str, dict] = {}
    storage_offset = 0
    while True:
        records = storage.records(
            "actor",
            limit=1000,
            offset=storage_offset,
            **media_scope,
        )
        for record in records:
            cluster_id = str(record["cluster_id"])
            timestamp = float(record["timestamp"])
            summary = grouped.setdefault(
                cluster_id,
                {
                    "media_ids": set(),
                    "generation_ids": set(),
                    "detection_count": 0,
                    "first_timestamp": timestamp,
                    "last_timestamp": timestamp,
                },
            )
            summary["media_ids"].add(str(record["video_id"]))
            if record.get("generation_id") is not None:
                summary["generation_ids"].add(str(record["generation_id"]))
            summary["detection_count"] += 1
            summary["first_timestamp"] = min(
                summary["first_timestamp"],
                timestamp,
            )
            summary["last_timestamp"] = max(
                summary["last_timestamp"],
                timestamp,
            )
        if len(records) < 1000:
            break
        storage_offset += len(records)

    summaries = []
    for cluster_id, summary in sorted(grouped.items()):
        media_ids = summary["media_ids"]
        generation_ids = summary["generation_ids"]
        if len(media_ids) != 1 or len(generation_ids) != 1:
            raise RuntimeError(
                f"Actor cluster {cluster_id!r} spans multiple index identities."
            )
        summaries.append(
            ActorClusterSummary(
                cluster_id=cluster_id,
                media_id=next(iter(media_ids)),
                generation_id=next(iter(generation_ids)),
                detection_count=summary["detection_count"],
                first_timestamp=summary["first_timestamp"],
                last_timestamp=summary["last_timestamp"],
            )
        )

    total = len(summaries)
    if offset > total:
        raise CapabilityRequestError(
            "The actor cursor is outside the result set."
        )
    selected = tuple(summaries[offset : offset + page_size])
    return ActorClustersOutput(
        clusters=selected,
        total=total,
        next_cursor=_encode_cursor(
            offset + len(selected),
            scope=scope,
            has_more=offset + len(selected) < total,
        ),
    )


def actor_detections(
    config: IndexConfig,
    cluster_id: str,
    *,
    storage: IndexReader,
    page_size: int,
    cursor: str | None,
) -> ActorDetectionsOutput:
    scope = f"actor:detections:{_snapshot_scope(config)}:{cluster_id}"
    storage_offset = _decode_cursor(cursor, scope)
    filters = {"cluster_id": cluster_id}
    records = storage.records(
        "actor",
        filters=filters,
        limit=page_size + 1,
        offset=storage_offset,
    )
    if not records and storage_offset == 0:
        raise ActorClusterNotFoundError(
            f"Actor cluster {cluster_id} was not found in the completed index."
        )
    if not records:
        raise CapabilityRequestError(
            "The actor cursor is outside the result set."
        )
    has_more = len(records) > page_size
    detections = tuple(
        _actor_detection(record) for record in records[:page_size]
    )
    return ActorDetectionsOutput(
        cluster_id=cluster_id,
        detections=detections,
        total=None,
        next_cursor=_encode_cursor(
            storage_offset + len(detections),
            scope=scope,
            has_more=has_more,
        ),
    )
