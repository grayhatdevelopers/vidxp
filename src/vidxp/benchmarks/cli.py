from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich import print as rich_print

from vidxp.benchmarks.didemo import run_didemo
from vidxp.benchmarks.hirest import (
    HIREST_DEFAULT_WINDOW_FRACTION,
    run_hirest,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.cli_support import (
    OutputFormat,
    effective_output_format,
    emit_json,
    state_from_context,
)
from vidxp.dependencies import (
    active_requirements,
    inspect_requirement,
    packaged_requirements,
)


app = typer.Typer(help="Run official benchmark adapters.")


def _require_benchmark_dependencies(
    capability: str,
    *,
    include_benchmark_extra: bool = False,
) -> None:
    requirements = list(
        create_capability_registry().requirements_for((capability,))
    )
    extras = [capability]
    if include_benchmark_extra:
        requirements.extend(
            active_requirements(packaged_requirements("vidxp.benchmarks"))
        )
        extras.append("benchmarks")
    failures = [
        check
        for requirement in dict.fromkeys(requirements)
        if not (check := inspect_requirement(requirement))["ok"]
    ]
    if not failures:
        return
    unavailable = ", ".join(
        f"{failure['requirement']} ({failure['error']})"
        for failure in failures
    )
    extra = ",".join(extras)
    raise typer.BadParameter(
        f"Benchmark dependencies are unavailable: {unavailable}. "
        f'Install them with: pip install "vidxp[{extra}]"'
    )


def _annotation_indices(value: str | None) -> list[int] | None:
    if value is None:
        return None
    try:
        indices = [
            int(item.strip())
            for item in value.split(",")
            if item.strip()
        ]
    except ValueError as exc:
        raise typer.BadParameter(
            "Annotation indices must be comma-separated integers."
        ) from exc
    if not indices:
        raise typer.BadParameter(
            "At least one annotation index is required."
        )
    return indices


def _pair_file(path: Path | None) -> list[tuple[str, str]] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(
            "The HiREST pair file must contain valid readable JSON.",
            param_hint="--pairs",
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise typer.BadParameter(
            "The HiREST pair file must be a non-empty JSON list."
        )
    pairs = []
    for index, item in enumerate(payload):
        if (
            not isinstance(item, dict)
            or set(item) != {"prompt", "video"}
            or not str(item["prompt"]).strip()
            or not str(item["video"]).strip()
        ):
            raise typer.BadParameter(
                f"HiREST pair {index} must contain prompt and video."
            )
        pairs.append((str(item["prompt"]), str(item["video"])))
    return pairs


@app.command("didemo")
def didemo_command(
    ctx: typer.Context,
    annotations: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    evaluator: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    media_directory: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False),
    ],
    run_id: Annotated[str, typer.Option()],
    output_root: Annotated[Path, typer.Option()] = Path("benchmark_runs"),
    split: Annotated[
        Literal["validation", "test"],
        typer.Option(help="Official split identified by --annotations."),
    ] = "test",
    annotation_indices: Annotated[
        str | None,
        typer.Option(
            help=(
                "Comma-separated zero-based annotation indices. "
                "Omit for the full selected split."
            )
        ),
    ] = None,
    chunk_pooling: Annotated[
        Literal["max", "mean"],
        typer.Option(
            help="Pool sampled frame scores within each five-second chunk."
        ),
    ] = "max",
    frame_stride: Annotated[int, typer.Option(min=1)] = 1,
    reset: Annotated[bool, typer.Option()] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Run DiDeMo scene retrieval and its official evaluator."""

    _require_benchmark_dependencies("scene")
    state = state_from_context(ctx)
    metrics = run_didemo(
        annotations_path=annotations,
        evaluator_path=evaluator,
        media_directory=media_directory,
        run_id=run_id,
        output_root=output_root,
        annotation_indices=_annotation_indices(annotation_indices),
        frame_stride=frame_stride,
        split=split,
        chunk_pooling=chunk_pooling,
        reset=reset,
        device=state.settings.runtime_backend,
    )
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(metrics)
    else:
        rich_print(metrics)


@app.command("hirest")
def hirest_command(
    ctx: typer.Context,
    ground_truth: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    categories: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    evaluator: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    asr_archive: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    asr_directory: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False),
    ],
    run_id: Annotated[str, typer.Option()],
    output_root: Annotated[Path, typer.Option()] = Path("benchmark_runs"),
    split: Annotated[
        Literal["validation", "test"],
        typer.Option(help="Official split identified by --ground-truth."),
    ] = "test",
    pairs: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            help=(
                "JSON list of {prompt, video} pairs. Omit for every "
                "moment pair in the selected split."
            ),
        ),
    ] = None,
    temporal_window_fraction: Annotated[
        float,
        typer.Option(
            min=0.0,
            max=1.0,
            help="Video-duration fraction used for the localization window.",
        ),
    ] = HIREST_DEFAULT_WINDOW_FRACTION,
    reset: Annotated[bool, typer.Option()] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Run HiREST released-ASR retrieval; score validation predictions."""

    if not 0 < temporal_window_fraction < 1:
        raise typer.BadParameter(
            "The temporal window fraction must be greater than zero and "
            "less than one.",
            param_hint="--temporal-window-fraction",
        )
    _require_benchmark_dependencies(
        "dialogue",
        include_benchmark_extra=True,
    )
    state = state_from_context(ctx)
    metrics = run_hirest(
        ground_truth_path=ground_truth,
        categories_path=categories,
        evaluator_path=evaluator,
        asr_archive_path=asr_archive,
        asr_directory=asr_directory,
        run_id=run_id,
        output_root=output_root,
        pairs=_pair_file(pairs),
        split=split,
        temporal_window_fraction=temporal_window_fraction,
        reset=reset,
        device=state.settings.runtime_backend,
    )
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(metrics)
    else:
        rich_print(metrics)
