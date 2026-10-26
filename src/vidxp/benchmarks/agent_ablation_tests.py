from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_SCORER = "file://../../src/vidxp/benchmarks/agent_ablation_score.py"
_MODALITIES = frozenset({"scene", "action", "sound", "speech"})


def generate_tests(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Expand one task manifest into matched MCP-on and MCP-off cases."""

    options = config or {}
    manifest = Path(options.get("manifest", ""))
    if not manifest.is_file():
        raise ValueError(f"Agent-ablation task manifest was not found: {manifest}")
    tasks = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise ValueError("The agent-ablation manifest must be a JSON array.")
    if not tasks:
        raise ValueError("The agent-ablation manifest must not be empty.")
    providers = options.get("providers", {})
    conditions = (
        ("mcp-on", providers.get("mcp_on", "codex-vidxp-mcp"), True),
        ("mcp-off", providers.get("mcp_off", "codex-no-mcp"), False),
    )

    generated: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for task in tasks:
        _validate_task(task)
        if task["id"] in task_ids:
            raise ValueError(f"Duplicate agent-ablation task ID: {task['id']}")
        task_ids.add(task["id"])
        for condition, provider, expected_mcp in conditions:
            variables = dict(task)
            variables["condition"] = condition
            variables["expected_mcp"] = expected_mcp
            generated.append(
                {
                    "description": f"{task['id']} [{condition}]",
                    "providers": [provider],
                    "vars": variables,
                    "metadata": {
                        "dataset": task["dataset"],
                        "task_id": task["id"],
                        "condition": condition,
                        "modalities": task["modalities"],
                    },
                    "assert": [
                        {"type": "is-json"},
                        {
                            "type": "python",
                            "value": f"{_SCORER}:score_temporal_grounding",
                            "metric": "temporal_grounding",
                        },
                        {
                            "type": "python",
                            "value": f"{_SCORER}:score_ablation_boundary",
                            "metric": "ablation_boundary",
                        },
                    ],
                }
            )
    return generated


def _validate_task(task: Any) -> None:
    required = {
        "id",
        "dataset",
        "video_id",
        "media_relpath",
        "duration_seconds",
        "event_index",
        "query",
        "expected_start",
        "expected_end",
        "modalities",
    }
    if not isinstance(task, dict):
        raise ValueError("Every agent-ablation task must be an object.")
    missing = sorted(required.difference(task))
    if missing:
        raise ValueError(
            f"Agent-ablation task {task.get('id', '<unknown>')} is missing: "
            f"{', '.join(missing)}"
        )
    for key in ("id", "dataset", "video_id", "media_relpath", "query"):
        if not isinstance(task[key], str) or not task[key].strip():
            raise ValueError(f"Agent-ablation task field {key} must be text.")

    duration = task["duration_seconds"]
    start = task["expected_start"]
    end = task["expected_end"]
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in (duration, start, end)
    ):
        raise ValueError("Agent-ablation durations and bounds must be numeric.")
    if duration <= 0 or start < 0 or end <= start or end > duration + 0.001:
        raise ValueError(f"Agent-ablation task {task['id']} has invalid bounds.")
    if (
        isinstance(task["event_index"], bool)
        or not isinstance(task["event_index"], int)
        or task["event_index"] < 0
    ):
        raise ValueError(
            "Agent-ablation event_index must be a non-negative integer."
        )

    modalities = task["modalities"]
    if (
        not isinstance(modalities, list)
        or not modalities
        or any(
            not isinstance(modality, str) or modality not in _MODALITIES
            for modality in modalities
        )
        or len(modalities) != len(set(modalities))
    ):
        raise ValueError(f"Agent-ablation task {task['id']} has invalid modalities.")
