from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich import print as rich_print
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from vidxp.benchmarks.didemo import run_didemo
from vidxp.benchmarks.hirest import (
    HIREST_DEFAULT_WINDOW_FRACTION,
    run_hirest,
)
from vidxp.benchmarks.prepare import (
    PreparationPlan,
    execute_preparation,
    plan_didemo,
    plan_hirest,
)
from vidxp.app_paths import available_storage_bytes
from vidxp.capabilities.registry import create_capability_registry
from vidxp.cli_support import (
    OutputFormat,
    effective_output_format,
    emit_json,
    emit_progress,
    state_from_context,
)
from vidxp.dependencies import (
    active_requirements,
    inspect_requirement,
    packaged_requirements,
)


app = typer.Typer(help="Prepare and run official benchmark adapters.")
prepare_app = typer.Typer(
    help="Download, verify, and arrange pinned benchmark inputs."
)
app.add_typer(prepare_app, name="prepare")


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("bytes", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            precision = 0 if unit == "bytes" else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def _show_preparation_plan(plan: PreparationPlan) -> None:
    typer.secho("Benchmark preparation plan", bold=True)
    typer.echo(f"  Benchmark: {plan.benchmark} {plan.split}")
    typer.echo(
        f"  Selection: {plan.selected_count} item(s), "
        f"{len(plan.selected_video_names)} video(s)"
    )
    typer.echo(f"  Destination: {plan.root}")
    typer.echo(
        f"  New files: {plan.download_count}; "
        "maximum additional storage: "
        f"{_format_bytes(plan.additional_bytes)}"
    )
    free_bytes = available_storage_bytes(plan.root)
    if free_bytes is not None:
        typer.echo(f"  Free space at destination: {_format_bytes(free_bytes)}")
        if free_bytes < plan.additional_bytes:
            typer.secho(
                "  Warning: the destination may not have enough free space. "
                "Choose another --output-directory or free space first.",
                fg=typer.colors.YELLOW,
            )
    replacements = [
        resource
        for resource in plan.resources
        if resource.replacement_for is not None
    ]
    for resource in replacements:
        typer.secho(
            "  Documented replacement: "
            f"{resource.replacement_for} -> {resource.url}",
            fg=typer.colors.YELLOW,
        )


def _execute_preparation_plan(
    plan: PreparationPlan,
    *,
    state,
    yes: bool,
    json_output: bool,
) -> None:
    output_format = effective_output_format(state, json_output)
    if output_format == OutputFormat.rich:
        _show_preparation_plan(plan)
    if plan.download_count and not yes:
        if output_format == OutputFormat.json:
            raise typer.BadParameter(
                "Benchmark downloads require --yes with JSON output.",
                param_hint="--yes",
            )
        typer.confirm(
            "Download and prepare these benchmark inputs?",
            abort=True,
        )
    network_bytes = plan.network_bytes
    if (
        state.quiet
        or output_format == OutputFormat.json
        or network_bytes == 0
    ):
        result = execute_preparation(
            plan,
            ffprobe=state.settings.ffprobe_executable,
            ffmpeg=state.settings.ffmpeg_executable,
        )
    else:
        console = Console(stderr=True)
        with Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                "Downloading benchmark inputs",
                total=network_bytes or None,
            )

            def advance(name: str, amount: int) -> None:
                progress.update(
                    task,
                    description=f"Downloading {name}",
                    advance=amount,
                )

            result = execute_preparation(
                plan,
                ffprobe=state.settings.ffprobe_executable,
                ffmpeg=state.settings.ffmpeg_executable,
                progress=advance,
            )
    if output_format == OutputFormat.json:
        emit_json(result)
        return
    typer.secho("Benchmark inputs are ready.", fg=typer.colors.GREEN)
    typer.echo(f"Manifest: {plan.manifest_path}")
    typer.echo("Run:")
    typer.secho(plan.command, bold=True)


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


def _media_override_file(path: Path | None) -> dict[str, Path] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(
            "The DiDeMo media override file must contain valid readable JSON.",
            param_hint="--media-overrides",
        ) from exc
    if not isinstance(payload, dict) or not payload:
        raise typer.BadParameter(
            "The DiDeMo media override file must be a non-empty JSON object.",
            param_hint="--media-overrides",
        )
    overrides: dict[str, Path] = {}
    for video_name, replacement in payload.items():
        if (
            not isinstance(video_name, str)
            or not video_name.strip()
            or not isinstance(replacement, str)
            or not replacement.strip()
        ):
            raise typer.BadParameter(
                "Each DiDeMo media override must map a video name to a path.",
                param_hint="--media-overrides",
            )
        candidate = Path(replacement)
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise typer.BadParameter(
                f"DiDeMo replacement media was not found: {candidate}",
                param_hint="--media-overrides",
            )
        overrides[video_name] = candidate
    return overrides


@prepare_app.command("didemo")
def prepare_didemo_command(
    ctx: typer.Context,
    split: Annotated[
        Literal["validation", "test"],
        typer.Option(help="Official split to prepare."),
    ] = "test",
    annotation_indices: Annotated[
        str | None,
        typer.Option(
            help=(
                "Comma-separated zero-based annotation indices. "
                "Omit to prepare the full selected split."
            )
        ),
    ] = None,
    output_directory: Annotated[
        Path | None,
        typer.Option(
            file_okay=False,
            help=(
                "Preparation destination. Defaults to the VidXP application "
                "data directory."
            ),
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Confirm the displayed download and replacement plan.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Prepare verified DiDeMo artifacts and selected official videos."""

    state = state_from_context(ctx)
    root = (
        output_directory
        if output_directory is not None
        else state.settings.data_dir / "benchmarks" / "didemo"
    )
    if (
        not state.quiet
        and effective_output_format(state, json_output) == OutputFormat.rich
    ):
        emit_progress(
            "Inspecting pinned DiDeMo metadata and download sizes..."
        )
    plan = plan_didemo(
        root=root,
        split=split,
        annotation_indices=_annotation_indices(annotation_indices),
        ffprobe=state.settings.ffprobe_executable,
        ffmpeg=state.settings.ffmpeg_executable,
    )
    _execute_preparation_plan(
        plan,
        state=state,
        yes=yes,
        json_output=json_output,
    )


@prepare_app.command("hirest")
def prepare_hirest_command(
    ctx: typer.Context,
    split: Annotated[
        Literal["validation", "test"],
        typer.Option(help="Official split to prepare."),
    ] = "validation",
    pairs: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            help=(
                "Optional JSON list of {prompt, video} pairs for a "
                "declared smoke subset."
            ),
        ),
    ] = None,
    output_directory: Annotated[
        Path | None,
        typer.Option(
            file_okay=False,
            help=(
                "Preparation destination. Defaults to the VidXP application "
                "data directory."
            ),
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Confirm the displayed download plan.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Prepare verified HiREST artifacts and released transcripts."""

    state = state_from_context(ctx)
    root = (
        output_directory
        if output_directory is not None
        else state.settings.data_dir / "benchmarks" / "hirest"
    )
    if (
        not state.quiet
        and effective_output_format(state, json_output) == OutputFormat.rich
    ):
        emit_progress(
            "Inspecting pinned HiREST metadata and download sizes..."
        )
    plan = plan_hirest(
        root=root,
        split=split,
        pairs=_pair_file(pairs),
    )
    _execute_preparation_plan(
        plan,
        state=state,
        yes=yes,
        json_output=json_output,
    )


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
    media_overrides: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            help=(
                "JSON object mapping an official video name to documented "
                "replacement media. Relative paths resolve beside the file."
            ),
        ),
    ] = None,
    chunk_pooling: Annotated[
        Literal["max", "mean"],
        typer.Option(
            help="Pool sampled frame scores within each five-second chunk."
        ),
    ] = "max",
    scene_sample_fps: Annotated[
        float,
        typer.Option(
            "--scene-sample-fps",
            min=0.01,
            help="Target time-based scene samples per second.",
        ),
    ] = 1.0,
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
        media_overrides=_media_override_file(media_overrides),
        scene_sample_fps=scene_sample_fps,
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
        "speech",
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
