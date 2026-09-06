from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from vidxp.benchmarks.agent_ablation_score import (
    DEFAULT_MAX_CHUNK_SECONDS,
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MIN_CHUNK_SECONDS,
    DEFAULT_MIN_EVENT_COVERAGE,
    DEFAULT_TARGET_CHUNK_SECONDS,
)


_SCORER = "file://../../src/vidxp/benchmarks/agent_ablation_score.py"
_MODALITIES = frozenset({"scene", "action", "sound", "speech"})
_RUN_MODES = frozenset({"all", "smoke", "pilot"})
_DEFAULT_CONDITIONS = frozenset({"vidxp-on", "vidxp-off", "clean-user"})
_CONDITIONS = _DEFAULT_CONDITIONS | {"local-slm"}
_DEFAULT_PILOT_REPETITIONS = 3


def generate_tests(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Expand one task manifest into selected agent-condition cases."""

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
        ),
        (
            "vidxp-off",
            providers.get("vidxp_off", "codex-baseline"),
            False,
            True,
        ),
        (
            "clean-user",
            providers.get("clean_user", "codex-clean-user"),
            False,
            True,
        ),
        (
            "local-slm",
            providers.get("local_slm", "local-slm"),
            True,
            False,
        ),
    )
    requested_conditions = _requested_conditions()
    conditions = tuple(
        condition for condition in conditions if condition[0] in requested_conditions
    )

    mode = os.environ.get("VIDXP_EVAL_MODE", "all")
    if mode not in _RUN_MODES:
        raise ValueError(f"Unknown agent-ablation run mode: {mode}")
    selected_tasks = (
        tasks[:1] if mode == "smoke" else tasks[1:] if mode == "pilot" else tasks
    )
    repetitions = _repetitions(mode)
    machine_id = str(
        options.get("machine_id") or os.environ.get("VIDXP_EVAL_MACHINE_ID", "")
    )
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", machine_id) is None:
        raise ValueError("A stable VIDXP_EVAL_MACHINE_ID is required.")

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
            ) in ordered_conditions:
                variables = dict(task)
                # Promptfoo expands array-valued vars into separate test cases.
                # Keep modalities reportable without multiplying each task.
                variables["modalities"] = json.dumps(
                    task["modalities"], separators=(",", ":")
                )
                variables["condition"] = condition
                variables["expected_vidxp"] = expected_vidxp
                variables["allow_media_shell"] = allow_media_shell
                variables["evaluation_mode"] = mode
                variables["repetition"] = repetition + 1
                variables["target_chunk_seconds"] = DEFAULT_TARGET_CHUNK_SECONDS
                variables["min_chunk_seconds"] = DEFAULT_MIN_CHUNK_SECONDS
                variables["max_chunk_seconds"] = DEFAULT_MAX_CHUNK_SECONDS
                variables["min_event_coverage"] = DEFAULT_MIN_EVENT_COVERAGE
                variables["max_candidates"] = DEFAULT_MAX_CANDIDATES
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
                            "machine_id": machine_id,
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


def _repetitions(mode: str) -> int:
    if mode != "pilot":
        return 1
    raw = os.environ.get("VIDXP_EVAL_REPETITIONS")
    if raw is None:
        return _DEFAULT_PILOT_REPETITIONS
    try:
        repetitions = int(raw)
    except ValueError as error:
        raise ValueError("VIDXP_EVAL_REPETITIONS must be a positive integer.") from error
    if repetitions < 1:
        raise ValueError("VIDXP_EVAL_REPETITIONS must be a positive integer.")
    return repetitions


def _requested_conditions() -> frozenset[str]:
    raw = os.environ.get("VIDXP_EVAL_CONDITIONS")
    if raw is None:
        return _DEFAULT_CONDITIONS
    requested = frozenset(
        condition.strip() for condition in raw.split(",") if condition.strip()
    )
    unknown = requested.difference(_CONDITIONS)
    if not requested or unknown:
        detail = ", ".join(sorted(unknown)) if unknown else "none supplied"
        raise ValueError(f"Invalid VIDXP_EVAL_CONDITIONS: {detail}")
    return requested


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
