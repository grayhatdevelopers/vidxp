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
    r"(?:^|[\s'\"/\\])vidxp(?:-mcp)?(?:\.exe)?(?:\s|$)",
    re.IGNORECASE,
)
_MEDIA_INSPECTION_COMMAND = re.compile(
    r"(?:^|[\s'\"/\\])ff(?:mpeg|probe)(?:\.exe)?(?:\s|$)",
    re.IGNORECASE,
)
_SKILL_NAME = "vidxp-find-video-evidence"
_SKILL_PATH = ".agents/skills/vidxp-find-video-evidence/SKILL.md"

DEFAULT_TARGET_CHUNK_SECONDS = 10.0
DEFAULT_MIN_CHUNK_SECONDS = 8.0
DEFAULT_MAX_CHUNK_SECONDS = 12.0
DEFAULT_MIN_EVENT_COVERAGE = 0.5


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

    start = _finite_number(result.get("start_seconds"))
    end = _finite_number(result.get("end_seconds"))
    duration = _finite_number(variables.get("duration_seconds"))
    expected_start = _finite_number(variables.get("expected_start"))
    expected_end = _finite_number(variables.get("expected_end"))
    if None in (start, end, duration, expected_start, expected_end):
        return _failed("The result or task has a missing/non-numeric interval.")
    assert start is not None
    assert end is not None
    assert duration is not None
    assert expected_start is not None
    assert expected_end is not None
    if start < 0 or end <= start or end > duration + 0.001:
        return _failed("The predicted interval is outside the video bounds.")

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

    predicted_duration = end - start
    effective_min_chunk = min(min_chunk, duration)
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
    bounded_chunk_hit = duration_in_range and coverage >= min_coverage
    iou = interval_iou(start, end, expected_start, expected_end)
    scores = {
        "valid_interval": 1.0,
        "bounded_chunk_hit": float(bounded_chunk_hit),
        "event_coverage": coverage,
        "chunk_duration_in_range": float(duration_in_range),
        "temporal_iou": iou,
        "r1_tiou_0_3": float(iou >= 0.3),
        "r1_tiou_0_5": float(iou >= 0.5),
        "r1_tiou_0_7": float(iou >= 0.7),
    }
    return {
        "pass": bounded_chunk_hit,
        "score": coverage if duration_in_range else 0.0,
        "reason": (
            f"Bounded chunk {'hit' if bounded_chunk_hit else 'miss'}: "
            f"{predicted_duration:.3f}s duration, {coverage:.4f} event coverage; "
            f"temporal IoU {iou:.4f}."
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
    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError) as exc:
        return _failed(f"Output is not valid JSON: {exc}")
    if not isinstance(result, Mapping):
        return _failed("Output must be a JSON object.")

    trace = context.get("trace")
    spans = trace.get("spans", []) if isinstance(trace, Mapping) else []
    if not spans:
        return _failed("No trace spans were captured; isolation is unproven.")

    tool_calls: list[tuple[int, str, Mapping[str, Any]]] = []
    invoked_vidxp_command = False
    inspected_media_from_shell = False
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
            inspected_media_from_shell = inspected_media_from_shell or bool(
                _MEDIA_INSPECTION_COMMAND.search(text)
                or (media_filename and media_filename in text)
            )

    if invoked_vidxp_command:
        return _failed(
            "The agent invoked VidXP through the shell and bypassed the condition."
        )
    if not expected_vidxp:
        if tool_calls:
            return _failed("VidXP-off used a VidXP MCP tool.")
        if skill_used:
            return _failed("VidXP-off loaded the VidXP evidence skill.")
        if result.get("source_job_id") is not None:
            return _failed("VidXP-off claimed a VidXP source job.")
        if any(
            isinstance(item, Mapping) and item.get("evidence_id") is not None
            for item in _evidence_items(result)
        ):
            return _failed("VidXP-off claimed VidXP evidence IDs.")
        return _passed("VidXP-off remained isolated from the skill, MCP, and CLI.")

    if inspected_media_from_shell:
        return _failed(
            "VidXP-on inspected the media through the shell instead of using MCP evidence."
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
    expected_tool = {
        "search": "search_moments",
        "query": "query_video",
    }.get(job.get("kind"))
    matching_calls: list[tuple[str, str]] = []
    for _, tool, arguments in retrieval_calls:
        command = arguments.get("command")
        if not isinstance(command, Mapping):
            continue
        query_key = "query" if tool == "search_moments" else "question"
        media_id = command.get("media_id")
        if (
            tool == expected_tool
            and command.get(query_key) == variables.get("query")
            and isinstance(media_id, str)
            and media_id
        ):
            matching_calls.append((tool, media_id))
    if not matching_calls:
        return _failed(
            "No retrieval call matches the source job kind, task query, and media."
        )
    search_tool, media_id = matching_calls[-1]
    trace_started_at = _trace_started_at(context, spans)
    if trace_started_at is None:
        return _failed("The trace has no usable start time for job freshness.")

    attestation_error = _attest_job(
        job=job,
        result=result,
        variables=variables,
        source_job_id=source_job_id,
        search_tool=search_tool,
        media_id=media_id,
        trace_started_at=trace_started_at,
    )
    if attestation_error is not None:
        return _failed(attestation_error)
    return _passed(
        "VidXP-on returned evidence from a fresh, successful, matching MCP job."
    )


def _attest_job(
    *,
    job: Mapping[str, Any],
    result: Mapping[str, Any],
    variables: Mapping[str, Any],
    source_job_id: str,
    search_tool: str,
    media_id: str,
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
    if payload.get(query_key) != variables.get("query"):
        return "The durable VidXP result does not match the benchmark query."

    delivery = payload.get("evidence_delivery")
    delivered = delivery.get("items") if isinstance(delivery, Mapping) else None
    if not isinstance(delivered, list) or not delivered:
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
    output_evidence = _evidence_items(result)
    if not output_evidence:
        return "VidXP-on returned no evidence entries to attest."

    verified_ranges: list[tuple[float, float]] = []
    for item in output_evidence:
        if not isinstance(item, Mapping):
            return "A returned evidence entry is not an object."
        evidence_id = item.get("evidence_id")
        delivered_item = ready.get(evidence_id)
        if delivered_item is None:
            return "A returned evidence_id is not ready evidence from the source job."
        if item.get("modality") not in delivered_item.get("modalities", []):
            return "A returned evidence modality is not supported by its evidence_id."
        source_range = delivered_item.get("range")
        if not isinstance(source_range, Mapping):
            return "A returned evidence_id has no source interval."
        source_start = _finite_number(source_range.get("source_start_seconds"))
        source_end = _finite_number(source_range.get("source_end_seconds"))
        item_start = _finite_number(item.get("start_seconds"))
        item_end = _finite_number(item.get("end_seconds"))
        if None in (source_start, source_end, item_start, item_end):
            return "A returned evidence interval cannot be attested."
        assert source_start is not None
        assert source_end is not None
        assert item_start is not None
        assert item_end is not None
        if interval_iou(item_start, item_end, source_start, source_end) <= 0:
            return "A returned evidence interval does not overlap its source evidence."
        verified_ranges.append((source_start, source_end))

    predicted_start = _finite_number(result.get("start_seconds"))
    predicted_end = _finite_number(result.get("end_seconds"))
    if predicted_start is None or predicted_end is None or not any(
        interval_iou(predicted_start, predicted_end, start, end) > 0
        for start, end in verified_ranges
    ):
        return "The predicted interval does not overlap its attested VidXP evidence."

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
