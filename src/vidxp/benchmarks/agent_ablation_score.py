from __future__ import annotations

import json
import re
from collections.abc import Mapping
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


def score_temporal_grounding(
    output: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Score the single predicted interval using LongVALE grounding metrics."""

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

    iou = interval_iou(start, end, expected_start, expected_end)
    scores = {
        "valid_interval": 1.0,
        "temporal_iou": iou,
        "r1_tiou_0_3": float(iou >= 0.3),
        "r1_tiou_0_5": float(iou >= 0.5),
        "r1_tiou_0_7": float(iou >= 0.7),
    }
    return {
        "pass": iou >= 0.3,
        "score": iou,
        "reason": f"Temporal IoU is {iou:.4f}.",
        "namedScores": scores,
    }


def score_ablation_boundary(
    _output: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove MCP-on used VidXP and MCP-off did not bypass the condition."""

    variables = context.get("vars", {})
    expected_mcp = variables.get("expected_mcp") is True
    trace = context.get("trace")
    spans = trace.get("spans", []) if isinstance(trace, Mapping) else []
    if not spans:
        return _failed("No trace spans were captured; isolation is unproven.")

    used_tools: set[str] = set()
    invoked_vidxp_command = False
    for span in spans:
        if not isinstance(span, Mapping):
            continue
        attributes = span.get("attributes")
        if not isinstance(attributes, Mapping):
            attributes = {}
        candidates = [
            span.get("name"),
            attributes.get("tool.name"),
            attributes.get("gen_ai.tool.name"),
            attributes.get("ai.toolCall.name"),
            attributes.get("mcp.tool.name"),
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and _is_vidxp_tool(candidate):
                used_tools.add(candidate)
        for key, value in attributes.items():
            if "command" not in str(key).casefold():
                continue
            text = value if isinstance(value, str) else json.dumps(value)
            if _VIDXP_COMMAND.search(text):
                invoked_vidxp_command = True

    if invoked_vidxp_command:
        return _failed(
            "The agent invoked VidXP through the shell and bypassed the condition."
        )
    used_mcp = bool(used_tools)
    passed = used_mcp is expected_mcp
    expected = "at least one VidXP MCP call" if expected_mcp else "no VidXP MCP call"
    observed = ", ".join(sorted(used_tools)) if used_tools else "none"
    return {
        "pass": passed,
        "score": float(passed),
        "reason": f"Expected {expected}; observed {observed}.",
        "namedScores": {"ablation_boundary": float(passed)},
    }


def _is_vidxp_tool(value: str) -> bool:
    normalized = value.casefold().replace("-", "_")
    return "mcp__vidxp__" in normalized or any(
        normalized == name or normalized.endswith(f".{name}")
        for name in VIDXP_TOOL_NAMES
    )


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _failed(reason: str) -> dict[str, Any]:
    return {"pass": False, "score": 0.0, "reason": reason}
