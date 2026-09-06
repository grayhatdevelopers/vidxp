from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VIDXP_TOOL_NAMES = frozenset(
    {
        "get_workspace",
        "list_capabilities",
        "get_capability",
        "get_runtime_readiness",
        "list_media",
        "get_media",
        "get_index_status",
        "search_moments",
        "query_video",
        "get_job",
        "wait_job",
        "get_job_evidence",
        "create_clip",
        "create_keyframe",
    }
)
_VIDXP_COMMAND = re.compile(
    r"(?:^|[;&|]\s*|['\"]\s*|(?:command|exec)\s+)"
    r"(?:[^\s'\";&|]*[/\\])?vidxp(?:-mcp)?(?:\.exe)?(?:\s|$)",
    re.IGNORECASE,
)
_MEDIA_INSPECTION_COMMAND = re.compile(
    r"(?:^|[\s'\"/\\])ff(?:mpeg|probe)(?:\.exe)?(?:\s|$)",
    re.IGNORECASE,
)
_MEDIA_PATH = re.compile(
    r"/[^\s'\";&|]+\.(?:aac|flac|jpe?g|m4a|mkv|mov|mp3|mp4|ogg|png|wav|webm|webp)",
    re.IGNORECASE,
)
_SKILL_NAME = "vidxp-find-video-evidence"
_SKILL_PATH = ".agents/skills/vidxp-find-video-evidence/SKILL.md"

DEFAULT_TARGET_CHUNK_SECONDS = 10.0
DEFAULT_MIN_CHUNK_SECONDS = 8.0
DEFAULT_MAX_CHUNK_SECONDS = 12.0
DEFAULT_MIN_EVENT_COVERAGE = 0.5
DEFAULT_MAX_CANDIDATES = 3


def interval_iou(
    predicted_start: float,
    predicted_end: float,
    expected_start: float,
    expected_end: float,
) -> float:
    """Return temporal intersection over union for two valid intervals."""

    intersection = max(
        0.0,
        min(predicted_end, expected_end) - max(predicted_start, expected_start),
    )
    union = max(predicted_end, expected_end) - min(
        predicted_start, expected_start
    )
    return 0.0 if union <= 0 else intersection / union


def event_coverage(
    predicted_start: float,
    predicted_end: float,
    expected_start: float,
    expected_end: float,
    *,
    target_chunk_seconds: float,
) -> float:
    """Return the useful-event coverage available to one target-size chunk."""

    intersection = max(
        0.0,
        min(predicted_end, expected_end) - max(predicted_start, expected_start),
    )
    expected_duration = expected_end - expected_start
    useful_duration = min(expected_duration, target_chunk_seconds)
    return (
        0.0
        if useful_duration <= 0
        else min(1.0, intersection / useful_duration)
    )


def score_temporal_grounding(
    output: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Score practical chunk retrieval and retain LongVALE boundary metrics."""

    variables = context.get("vars", {})
    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError) as exc:
        return _failed(f"Output is not valid JSON: {exc}")
    if not isinstance(result, dict):
        return _failed("Output must be a JSON object.")
    if result.get("video_id") != variables.get("video_id"):
        return _failed("The returned video_id does not match the task.")

    duration = _finite_number(variables.get("duration_seconds"))
    expected_start = _finite_number(variables.get("expected_start"))
    expected_end = _finite_number(variables.get("expected_end"))
    if None in (duration, expected_start, expected_end):
        return _failed("The task has a missing/non-numeric interval.")
    assert duration is not None
    assert expected_start is not None
    assert expected_end is not None

    target_chunk = _positive_number(
        variables.get("target_chunk_seconds", DEFAULT_TARGET_CHUNK_SECONDS)
    )
    min_chunk = _positive_number(
        variables.get("min_chunk_seconds", DEFAULT_MIN_CHUNK_SECONDS)
    )
    max_chunk = _positive_number(
        variables.get("max_chunk_seconds", DEFAULT_MAX_CHUNK_SECONDS)
    )
    min_coverage = _finite_number(
        variables.get("min_event_coverage", DEFAULT_MIN_EVENT_COVERAGE)
    )
    if None in (target_chunk, min_chunk, max_chunk, min_coverage):
        return _failed("The task has invalid bounded-chunk settings.")
    assert target_chunk is not None
    assert min_chunk is not None
    assert max_chunk is not None
    assert min_coverage is not None
    if min_chunk > target_chunk or target_chunk > max_chunk:
        return _failed("The task's chunk duration bounds are inconsistent.")
    if not 0 < min_coverage <= 1:
        return _failed("The task's event coverage threshold must be in (0, 1].")
    max_candidates = variables.get("max_candidates", DEFAULT_MAX_CANDIDATES)
    if (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or max_candidates < 1
    ):
        return _failed("The task has an invalid candidate limit.")

    candidates = _candidate_items(result)
    if not candidates:
        return _failed("The output contains no candidate clips.")
    if len(candidates) > max_candidates:
        return _failed(f"The output exceeds the {max_candidates}-candidate limit.")

    scored_candidates: list[dict[str, float | bool]] = []
    seen_intervals: set[tuple[float, float]] = set()
    effective_min_chunk = min(min_chunk, duration)
    for rank, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, Mapping):
            return _failed(f"Candidate {rank} is not an object.")
        start = _finite_number(candidate.get("start_seconds"))
        end = _finite_number(candidate.get("end_seconds"))
        if start is None or end is None:
            return _failed(f"Candidate {rank} has a missing/non-numeric interval.")
        if start < 0 or end <= start or end > duration + 0.001:
            return _failed(f"Candidate {rank} is outside the video bounds.")
        interval = (start, end)
        if interval in seen_intervals:
            return _failed("The output contains a duplicate candidate interval.")
        seen_intervals.add(interval)
        predicted_duration = end - start
        duration_in_range = (
            predicted_duration + 0.001 >= effective_min_chunk
            and predicted_duration <= max_chunk + 0.001
        )
        coverage = event_coverage(
            start,
            end,
            expected_start,
            expected_end,
            target_chunk_seconds=target_chunk,
        )
        iou = interval_iou(start, end, expected_start, expected_end)
        scored_candidates.append(
            {
                "rank": float(rank),
                "start": start,
                "end": end,
                "duration": predicted_duration,
                "duration_in_range": duration_in_range,
                "coverage": coverage,
                "hit": duration_in_range and coverage >= min_coverage,
                "iou": iou,
            }
        )

    top = scored_candidates[0]
    hits = [candidate for candidate in scored_candidates if candidate["hit"]]
    first_hit_rank = int(hits[0]["rank"]) if hits else None
    bounded_chunk_hit_at_1 = bool(top["hit"])
    bounded_chunk_hit_at_3 = bool(hits)
    best_coverage = max(float(candidate["coverage"]) for candidate in scored_candidates)
    best_iou = max(float(candidate["iou"]) for candidate in scored_candidates)
    duration_valid_rate = sum(
        bool(candidate["duration_in_range"]) for candidate in scored_candidates
    ) / len(scored_candidates)
    scores = {
        "valid_interval": 1.0,
        "bounded_chunk_hit": float(bounded_chunk_hit_at_3),
        "bounded_chunk_hit_at_1": float(bounded_chunk_hit_at_1),
        "bounded_chunk_hit_at_3": float(bounded_chunk_hit_at_3),
        "bounded_chunk_mrr": 0.0 if first_hit_rank is None else 1 / first_hit_rank,
        "candidate_count": float(len(scored_candidates)),
        "event_coverage": best_coverage,
        "top1_event_coverage": float(top["coverage"]),
        "chunk_duration_in_range": float(bool(top["duration_in_range"])),
        "candidate_duration_in_range_rate": duration_valid_rate,
        "temporal_iou": float(top["iou"]),
        "best_temporal_iou": best_iou,
        "r1_tiou_0_3": float(top["iou"] >= 0.3),
        "r1_tiou_0_5": float(top["iou"] >= 0.5),
        "r1_tiou_0_7": float(top["iou"] >= 0.7),
        "r3_tiou_0_3": float(best_iou >= 0.3),
        "r3_tiou_0_5": float(best_iou >= 0.5),
        "r3_tiou_0_7": float(best_iou >= 0.7),
    }
    return {
        "pass": bounded_chunk_hit_at_3,
        "score": max(
            (
                float(candidate["coverage"])
                for candidate in scored_candidates
                if candidate["duration_in_range"]
            ),
            default=0.0,
        ),
        "reason": (
            f"Bounded chunk {'hit' if bounded_chunk_hit_at_3 else 'miss'} in "
            f"{len(scored_candidates)} candidate(s); top-1 "
            f"{'hit' if bounded_chunk_hit_at_1 else 'miss'}, first hit rank "
            f"{first_hit_rank if first_hit_rank is not None else 'none'}, "
            f"best coverage {best_coverage:.4f}, best temporal IoU {best_iou:.4f}."
        ),
        "namedScores": scores,
    }


def score_ablation_boundary(
    output: str,
    context: Mapping[str, Any],
    *,
    job_loader: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify condition isolation and attest VidXP output to a durable job."""

    variables = context.get("vars", {})
    expected_vidxp = variables.get("expected_vidxp") is True
    allow_media_shell = variables.get("allow_media_shell") is True
    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError) as exc:
        return _failed(f"Output is not valid JSON: {exc}")
    if not isinstance(result, Mapping):
        return _failed("Output must be a JSON object.")

    trace = context.get("trace")
    spans = list(trace.get("spans", [])) if isinstance(trace, Mapping) else []
    metadata = context.get("metadata")
    provider_trace = metadata.get("trace") if isinstance(metadata, Mapping) else None
    if isinstance(provider_trace, Mapping):
        provider_spans = provider_trace.get("spans")
        if isinstance(provider_spans, list):
            spans.extend(provider_spans)
    if not spans:
        return _failed("No trace spans were captured; isolation is unproven.")

    tool_calls: list[tuple[int, str, Mapping[str, Any]]] = []
    invoked_vidxp_command = False
    media_shell_commands: list[str] = []
    skill_used = False
    media_filename = Path(str(variables.get("media_relpath", ""))).name
    for index, span in enumerate(spans):
        if not isinstance(span, Mapping):
            continue
        attributes = span.get("attributes")
        if not isinstance(attributes, Mapping):
            attributes = {}
        skill_used = skill_used or (
            attributes.get("promptfoo.skill.name") == _SKILL_NAME
            and _is_expected_skill_path(attributes.get("promptfoo.skill.path"))
        )
        tool = _vidxp_tool_name(span, attributes)
        if tool is not None:
            tool_calls.append(
                (index, tool, _json_mapping(attributes.get("codex.mcp.input")))
            )
        for key, value in attributes.items():
            if "command" not in str(key).casefold():
                continue
            text = value if isinstance(value, str) else json.dumps(value)
            invoked_vidxp_command = invoked_vidxp_command or bool(
                _VIDXP_COMMAND.search(text)
            )
            if _MEDIA_INSPECTION_COMMAND.search(text) or (
                media_filename and media_filename in text
            ):
                media_shell_commands.append(text)

    if not expected_vidxp:
        if invoked_vidxp_command:
            return _failed(
                "The agent invoked VidXP through the shell and bypassed the condition."
            )
        if tool_calls:
            return _failed("VidXP-off used a VidXP MCP tool.")
        if skill_used:
            return _failed("VidXP-off loaded the VidXP evidence skill.")
        return _passed(
            "The condition remained isolated from VidXP and respected its tool policy."
        )

    if invoked_vidxp_command:
        return _failed(
            "The agent invoked VidXP through the shell and bypassed the condition."
        )
    retrieval_calls = [
        call
        for call in tool_calls
        if call[1] in {"search_moments", "query_video"}
    ]
    if not retrieval_calls:
        return _failed("VidXP-on did not submit a retrieval job.")

    source_job_id = result.get("source_job_id")
    if not isinstance(source_job_id, str) or not source_job_id:
        return _failed("VidXP-on did not return its source_job_id.")
    if not any(
        tool == "get_job_evidence" and arguments.get("job_id") == source_job_id
        for _, tool, arguments in tool_calls
    ):
        return _failed("VidXP-on did not inspect evidence from its source job.")

    try:
        job = (job_loader or _load_durable_job)(source_job_id)
    except Exception as exc:  # pragma: no cover - exact backend errors vary
        return _failed(f"Could not attest the durable VidXP job: {exc}")
    if not allow_media_shell and any(
        not _inspects_delivered_artifact(command, job)
        for command in media_shell_commands
    ):
        return _failed(
            "VidXP-on inspected the source media through the shell instead of "
            "using MCP evidence."
        )
    expected_tool = {
        "search": "search_moments",
        "query": "query_video",
    }.get(job.get("kind"))
    matching_calls: list[tuple[str, str, str]] = []
    for _, tool, arguments in retrieval_calls:
        command = arguments.get("command")
        if not isinstance(command, Mapping):
            continue
        query_key = "query" if tool == "search_moments" else "question"
        media_id = command.get("media_id")
        submitted_query = command.get(query_key)
        if (
            tool == expected_tool
            and isinstance(submitted_query, str)
            and submitted_query.strip()
            and isinstance(media_id, str)
            and media_id
        ):
            matching_calls.append((tool, media_id, submitted_query))
    if not matching_calls:
        return _failed(
            "No retrieval call matches the source job kind and supplies a query and media."
        )
    trace_started_at = _trace_started_at(context, spans)
    if trace_started_at is None:
        return _failed("The trace has no usable start time for job freshness.")

    attestation_errors: list[str] = []
    for search_tool, media_id, submitted_query in reversed(matching_calls):
        attestation_error = _attest_job(
            job=job,
            result=result,
            source_job_id=source_job_id,
            search_tool=search_tool,
            media_id=media_id,
            submitted_query=submitted_query,
            trace_started_at=trace_started_at,
        )
        if attestation_error is None:
            break
        attestation_errors.append(attestation_error)
    else:
        return _failed(attestation_errors[0])
    return _passed(
        "VidXP-on returned evidence from a fresh, successful, matching MCP job."
    )


def _attest_job(
    *,
    job: Mapping[str, Any],
    result: Mapping[str, Any],
    source_job_id: str,
    search_tool: str,
    media_id: str,
    submitted_query: str,
    trace_started_at: float,
) -> str | None:
    expected_kind = "search" if search_tool == "search_moments" else "query"
    if job.get("job_id") != source_job_id:
        return "The durable job ID does not match source_job_id."
    job_created_at = _timestamp_seconds(job.get("created_at"))
    if job_created_at is None:
        return "The durable job has no usable creation time."
    if job_created_at < trace_started_at:
        return "The durable job predates the current evaluation trace."
    if job.get("state") != "succeeded" or job.get("kind") != expected_kind:
        return "The source VidXP retrieval job did not succeed with the expected kind."
    wrapper = job.get("result")
    payload = wrapper.get("result") if isinstance(wrapper, Mapping) else None
    if not isinstance(wrapper, Mapping) or not isinstance(payload, Mapping):
        return "The successful VidXP job has no typed retrieval result."
    if wrapper.get("kind") != expected_kind:
        return "The durable job result kind does not match the retrieval tool."
    query_key = "query" if expected_kind == "search" else "question"
    if payload.get(query_key) != submitted_query:
        return "The durable VidXP result does not match the submitted MCP query."

    delivered = _delivery_evidence_items(payload)
    if not delivered:
        return "The durable VidXP result contains no delivered evidence."
    ready = {
        item.get("evidence_id"): item
        for item in delivered
        if isinstance(item, Mapping)
        and item.get("state") == "ready"
        and item.get("media_id") == media_id
        and isinstance(item.get("evidence_id"), str)
    }
    if not ready:
        return "The durable VidXP result has no ready evidence for the task media."
    output_candidates = _candidate_items(result)
    if not output_candidates:
        return "VidXP-on returned no candidate clips to attest."
    for candidate in output_candidates:
        if not isinstance(candidate, Mapping):
            return "A returned candidate is not an object."
        verified_ranges: list[tuple[float, float]] = []
        evidence_ids = candidate.get("evidence_ids")
        if evidence_ids is not None:
            if (
                not isinstance(evidence_ids, list)
                or not evidence_ids
                or any(not isinstance(item, str) or not item for item in evidence_ids)
            ):
                return "A VidXP candidate has no usable evidence IDs to attest."
            candidate_modalities = candidate.get("modalities")
            if not isinstance(candidate_modalities, list):
                return "A VidXP candidate has no modality list to attest."
            for evidence_id in evidence_ids:
                delivered_item = ready.get(evidence_id)
                if delivered_item is None:
                    return "A returned evidence_id is not ready evidence from the source job."
                if not set(candidate_modalities).intersection(
                    delivered_item.get("modalities", [])
                ):
                    return "A candidate modality is not supported by its evidence_id."
                source_interval = _delivered_source_interval(delivered_item)
                if source_interval is None:
                    return "A returned evidence interval cannot be attested."
                verified_ranges.append(source_interval)
        else:
            output_evidence = _evidence_items(candidate)
            if not output_evidence:
                return "A VidXP candidate has no evidence entries to attest."
            for item in output_evidence:
                if not isinstance(item, Mapping):
                    return "A returned evidence entry is not an object."
                evidence_id = item.get("evidence_id")
                delivered_item = ready.get(evidence_id)
                if delivered_item is None:
                    return "A returned evidence_id is not ready evidence from the source job."
                if item.get("modality") not in delivered_item.get("modalities", []):
                    return "A returned evidence modality is not supported by its evidence_id."
                source_interval = _delivered_source_interval(delivered_item)
                item_start = _finite_number(item.get("start_seconds"))
                item_end = _finite_number(item.get("end_seconds"))
                if source_interval is None or item_start is None or item_end is None:
                    return "A returned evidence interval cannot be attested."
                source_start, source_end = source_interval
                if interval_iou(item_start, item_end, source_start, source_end) <= 0:
                    return "A returned evidence interval does not overlap its source evidence."
                verified_ranges.append(source_interval)

        predicted_start = _finite_number(candidate.get("start_seconds"))
        predicted_end = _finite_number(candidate.get("end_seconds"))
        if predicted_start is None or predicted_end is None or not any(
            interval_iou(predicted_start, predicted_end, start, end) > 0
            for start, end in verified_ranges
        ):
            return "A predicted interval does not overlap its attested VidXP evidence."

    moments = payload.get("moments")
    if not isinstance(moments, list) or not moments:
        return "The durable VidXP result contains no retrieved moments."
    hits = [
        hit
        for moment in moments
        if isinstance(moment, Mapping)
        for hit in moment.get("hits", [])
        if isinstance(hit, Mapping)
    ]
    if not hits or any(hit.get("media_id") != media_id for hit in hits):
        return "The durable VidXP moments do not belong to the task video."
    return None


def _delivered_source_interval(
    item: Mapping[str, Any],
) -> tuple[float, float] | None:
    source_range = item.get("range")
    if isinstance(source_range, Mapping):
        start = _finite_number(source_range.get("source_start_seconds"))
        end = _finite_number(source_range.get("source_end_seconds"))
    else:
        start = _finite_number(item.get("start"))
        end = _finite_number(item.get("end"))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _delivery_evidence_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the evidence records exposed by get_job_evidence."""

    delivery = payload.get("evidence_delivery")
    if not isinstance(delivery, Mapping):
        return []
    items = delivery.get("items")
    delivered = (
        [item for item in items if isinstance(item, Mapping)]
        if isinstance(items, list)
        else []
    )
    board = delivery.get("board")
    tiles = board.get("tiles") if isinstance(board, Mapping) else None
    if isinstance(tiles, list):
        delivered.extend(tile for tile in tiles if isinstance(tile, Mapping))
    return delivered


def _inspects_delivered_artifact(
    command: str,
    job: Mapping[str, Any],
) -> bool:
    """Return whether a media command reads an artifact delivered by the job."""

    normalized = command.replace("\\", "/")
    if "/artifacts/objects/" not in normalized:
        return False
    wrapper = job.get("result")
    payload = wrapper.get("result") if isinstance(wrapper, Mapping) else None
    if not isinstance(payload, Mapping):
        return False
    artifact_ids: set[str] = set()
    for item in _delivery_evidence_items(payload):
        keyframe = item.get("keyframe")
        evidence_artifacts = (
            item.get("clip"),
            keyframe.get("artifact") if isinstance(keyframe, Mapping) else None,
        )
        for evidence_artifact in evidence_artifacts:
            artifact = (
                evidence_artifact.get("artifact")
                if isinstance(evidence_artifact, Mapping)
                else None
            )
            artifact_id = (
                artifact.get("artifact_id") if isinstance(artifact, Mapping) else None
            )
            if isinstance(artifact_id, str) and artifact_id:
                artifact_ids.add(artifact_id)
    media_paths = _MEDIA_PATH.findall(normalized)
    return bool(media_paths) and all(
        "/artifacts/objects/" in media_path
        and any(artifact_id in media_path for artifact_id in artifact_ids)
        for media_path in media_paths
    )


def _load_durable_job(job_id: str) -> Mapping[str, Any]:
    from vidxp.composition import create_local_application
    from vidxp.infrastructure.dbos_jobs import DBOSJobBackend
    from vidxp.workflow_runtime import (
        workflow_application_version,
        workflow_database_url,
    )

    data_directory = os.environ.get("VIDXP_EVAL_DATA_DIR")
    index_directory = os.environ.get("VIDXP_EVAL_INDEX_DIR")
    if not data_directory or not index_directory:
        raise RuntimeError("benchmark data/index environment is missing")
    context = create_local_application(
        repository_name=os.environ.get("VIDXP_EVAL_REPOSITORY", "default"),
        index_directory=index_directory,
        data_directory=data_directory,
        device=os.environ.get("VIDXP_EVAL_DEVICE", "cpu"),
    )
    backend = DBOSJobBackend(
        system_database_url=workflow_database_url(context.settings),
        application_version=workflow_application_version(),
    )
    try:
        job = backend.get(job_id)
        if job is None:
            raise RuntimeError("source job was not found")
        return job.model_dump(mode="json")
    finally:
        backend.close()
        context.close()


def _vidxp_tool_name(
    span: Mapping[str, Any], attributes: Mapping[str, Any]
) -> str | None:
    candidates = (
        attributes.get("codex.mcp.tool"),
        attributes.get("gen_ai.tool.name"),
        attributes.get("tool.name"),
        span.get("name"),
    )
    server = attributes.get("codex.mcp.server")
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        normalized = candidate.casefold().replace("-", "_")
        for tool in VIDXP_TOOL_NAMES:
            if normalized == tool or normalized.endswith(f"/{tool}"):
                return tool if server in {None, "vidxp"} else None
            if f"mcp__vidxp__{tool}" in normalized:
                return tool
    return None


def _json_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _is_expected_skill_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/")
    return normalized == _SKILL_PATH or normalized.endswith(f"/{_SKILL_PATH}")


def _trace_started_at(
    context: Mapping[str, Any],
    spans: list[Any],
) -> float | None:
    timestamps = [
        timestamp
        for span in spans
        if isinstance(span, Mapping)
        for timestamp in (
            _timestamp_seconds(
                span.get("start_time", span.get("startTime"))
            ),
        )
        if timestamp is not None
    ]
    if timestamps:
        return min(timestamps)

    metadata = context.get("metadata")
    evaluation_id = (
        metadata.get("evaluationId") if isinstance(metadata, Mapping) else None
    )
    if isinstance(evaluation_id, str):
        match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)$", evaluation_id)
        if match:
            return _timestamp_seconds(match.group(1))
    return None


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp >= 1e17:
            timestamp /= 1e9
        elif timestamp >= 1e14:
            timestamp /= 1e6
        elif timestamp >= 1e11:
            timestamp /= 1e3
        return timestamp if timestamp > 0 else None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _candidate_items(result: Mapping[str, Any]) -> list[Any]:
    candidates = result.get("candidates")
    if candidates is not None:
        return candidates if isinstance(candidates, list) else []
    if "start_seconds" in result or "end_seconds" in result:
        return [result]
    return []


def _evidence_items(result: Mapping[str, Any]) -> list[Any]:
    evidence = result.get("evidence")
    return evidence if isinstance(evidence, list) else []


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _positive_number(value: Any) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


def _passed(reason: str) -> dict[str, Any]:
    return {
        "pass": True,
        "score": 1.0,
        "reason": reason,
        "namedScores": {"ablation_boundary": 1.0},
    }


def _failed(reason: str) -> dict[str, Any]:
    return {"pass": False, "score": 0.0, "reason": reason}
