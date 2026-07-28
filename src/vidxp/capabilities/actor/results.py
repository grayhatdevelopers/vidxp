from __future__ import annotations

import base64
import json

from vidxp.capabilities.actor.schemas import (
    ActorClusterSummary,
    ActorClustersOutput,
    ActorDetection,
    ActorDetectionsOutput,
)
from vidxp.core.contracts import IndexConfig
from vidxp.capabilities.contracts import CapabilityRequestError
from vidxp.ports import IndexReader


class ActorClusterNotFoundError(LookupError):
    """Raised when an actor cluster has no retained detections."""


def _decode_cursor(cursor: str | None, scope: str) -> int:
    if cursor is None:
        return 0
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(cursor.encode()).decode()
        )
        if not isinstance(payload, dict):
            raise TypeError
        offset = int(payload["offset"])
    except (
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise CapabilityRequestError("The actor cursor is invalid.") from exc
    if payload.get("version") != 1 or payload.get("scope") != scope or offset < 0:
        raise CapabilityRequestError("The actor cursor is invalid.")
    return offset


def _encode_cursor(
    offset: int,
    *,
    scope: str,
    has_more: bool,
) -> str | None:
    if not has_more:
        return None
    payload = json.dumps(
        {"version": 1, "scope": scope, "offset": offset},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode()


def _snapshot_scope(config: IndexConfig) -> str:
    return str(config.snapshot_id or config.fingerprint())


def _decode_detection_cursor(
    cursor: str | None,
    scope: str,
) -> tuple[int, str] | None:
    if cursor is None:
        return None
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(cursor.encode()).decode()
        )
        after = payload["after"]
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("scope") != scope
            or not isinstance(after, list)
            or len(after) != 2
        ):
            raise ValueError
        key = (int(after[0]), str(after[1]))
    except (
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise CapabilityRequestError("The actor cursor is invalid.") from exc
    if key[0] < 0 or not key[1]:
        raise CapabilityRequestError("The actor cursor is invalid.")
    return key


def _encode_detection_cursor(
    key: tuple[int, str],
    scope: str,
) -> str:
    payload = json.dumps(
        {"version": 1, "scope": scope, "after": list(key)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode()


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


def actor_clusters(
    config: IndexConfig,
    *,
    storage: IndexReader,
    page_size: int,
    cursor: str | None,
) -> ActorClustersOutput:
    scope = f"actor:clusters:{_snapshot_scope(config)}"
    grouped: dict[str, dict] = {}
    storage_offset = 0
    while True:
        records = storage.records(
            "actor",
            limit=1000,
            offset=storage_offset,
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

    offset = _decode_cursor(cursor, scope)
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
    scope = (
        f"actor:detections:{_snapshot_scope(config)}:{cluster_id}"
    )
    after = _decode_detection_cursor(cursor, scope)
    filters = {"cluster_id": cluster_id}
    candidates: list[ActorDetection] = []
    storage_offset = 0
    found_any = False
    while True:
        records = storage.records(
            "actor",
            filters=filters,
            limit=1000,
            offset=storage_offset,
        )
        found_any = found_any or bool(records)
        for record in records:
            detection = _actor_detection(record)
            key = (detection.frame_index, detection.detection_id)
            if after is None or key > after:
                candidates.append(detection)
        candidates = sorted(
            candidates,
            key=lambda item: (item.frame_index, item.detection_id),
        )[: page_size + 1]
        if len(records) < 1000:
            break
        storage_offset += len(records)
    if not found_any:
        raise ActorClusterNotFoundError(
            f"Actor cluster {cluster_id} was not found in the completed index."
        )
    if after is not None and not candidates:
        raise CapabilityRequestError(
            "The actor cursor is outside the result set."
        )
    has_more = len(candidates) > page_size
    detections = tuple(candidates[:page_size])
    next_cursor = None
    if has_more:
        last = detections[-1]
        next_cursor = _encode_detection_cursor(
            (last.frame_index, last.detection_id),
            scope,
        )
    return ActorDetectionsOutput(
        cluster_id=cluster_id,
        detections=detections,
        total=None,
        next_cursor=next_cursor,
    )
