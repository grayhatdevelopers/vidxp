from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from vidxp.benchmarks.agent_ablation_score import (
    DEFAULT_MAX_CHUNK_SECONDS,
    DEFAULT_MIN_CHUNK_SECONDS,
    DEFAULT_MIN_EVENT_COVERAGE,
    DEFAULT_TARGET_CHUNK_SECONDS,
)


_SCORER = "file://../../src/vidxp/benchmarks/agent_ablation_score.py"
_MODALITIES = frozenset({"scene", "action", "sound", "speech"})
_RUN_MODES = frozenset({"all", "smoke", "pilot"})


def generate_tests(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Expand one task manifest into matched VidXP-on and VidXP-off cases."""

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
        (
            "vidxp-on",
            providers.get("vidxp_on", "codex-vidxp"),
            True,
            False,
            True,
            "Use VidXP evidence; do not inspect the media with FFmpeg or ffprobe.",
        ),
        (
            "vidxp-off",
            providers.get("vidxp_off", "codex-baseline"),
            False,
            True,
            True,
            "VidXP is unavailable; use the local media and any available local tools.",
        ),
        (
            "model-only",
            providers.get("model_only", "codex-model-only"),
            False,
            False,
            False,
            "VidXP and local tools are unavailable; use only the model's native "
            "capabilities.",
        ),
    )

    mode = os.environ.get("VIDXP_EVAL_MODE", "all")
    if mode not in _RUN_MODES:
        raise ValueError(f"Unknown agent-ablation run mode: {mode}")
    selected_tasks = (
        tasks[:1] if mode == "smoke" else tasks[1:] if mode == "pilot" else tasks
    )
    repetitions = 3 if mode == "pilot" else 1
    run_id = os.environ.get("VIDXP_EVAL_RUN_ID", "validation")

    generated: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for task in selected_tasks:
        _validate_task(task)
        if task["id"] in task_ids:
            raise ValueError(f"Duplicate agent-ablation task ID: {task['id']}")
        task_ids.add(task["id"])
        for repetition in range(repetitions):
            # Rotate serial execution order so three pilot repetitions do not
            # always time the same condition first or last.
            ordered_conditions = conditions[repetition:] + conditions[:repetition]
            for (
                condition,
                provider,
                expected_vidxp,
                allow_media_shell,
                allow_agent_tools,
                evidence_access,
            ) in ordered_conditions:
                nonce_source = f"{run_id}\0{task['id']}\0{repetition}\0{condition}"
                retrieval_nonce = hashlib.sha256(
                    nonce_source.encode("utf-8")
                ).hexdigest()[:32]
                variables = dict(task)
                # Promptfoo expands array-valued vars into separate test cases.
                # Keep modalities reportable without multiplying each task.
                variables["modalities"] = json.dumps(
                    task["modalities"], separators=(",", ":")
                )
                variables["condition"] = condition
                variables["expected_vidxp"] = expected_vidxp
                variables["allow_media_shell"] = allow_media_shell
                variables["allow_agent_tools"] = allow_agent_tools
                variables["evidence_access"] = evidence_access
                variables["evaluation_mode"] = mode
                variables["repetition"] = repetition + 1
                variables["retrieval_nonce"] = retrieval_nonce
                variables["target_chunk_seconds"] = DEFAULT_TARGET_CHUNK_SECONDS
                variables["min_chunk_seconds"] = DEFAULT_MIN_CHUNK_SECONDS
                variables["max_chunk_seconds"] = DEFAULT_MAX_CHUNK_SECONDS
                variables["min_event_coverage"] = DEFAULT_MIN_EVENT_COVERAGE
                generated.append(
                    {
                        "description": (
                            f"{task['id']} [{condition}]"
                            + (
                                f" repetition {repetition + 1}"
                                if repetitions > 1
                                else ""
                            )
                        ),
                        "providers": [provider],
                        "vars": variables,
                        "metadata": {
                            "dataset": task["dataset"],
                            "task_id": task["id"],
                            "condition": condition,
                            "modalities": task["modalities"],
                            "evaluation_mode": mode,
                            "repetition": repetition + 1,
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
