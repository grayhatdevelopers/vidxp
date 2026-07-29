from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from vidxp.app_paths import available_storage_bytes
from vidxp.application_models import (
    ApplicationError,
    CapabilityDependencyCheck,
    DependencyCheckCommand,
    DependencyKind,
    ErrorCategory,
    PrepareModelsCommand,
)
from vidxp.cli_support import (
    OutputFormat,
    effective_output_format,
    emit_job_progress,
    emit_json,
    emit_progress,
    parse_capability_options,
    parse_modalities,
    require_media_runtime,
    state_from_context,
)
from vidxp.media_runtime import (
    MediaRuntimeStatus,
    inspect_media_runtime,
    install_media_runtime,
    media_runtime_config_path,
    save_media_runtime_configuration,
)


def _format_bytes(size: int) -> str:
    gib = 1024**3
    mib = 1024**2
    if size >= gib:
        return f"{size / gib:.2f} GiB"
    return f"{size / mib:.1f} MiB"


def _media_runtime_payload(status: MediaRuntimeStatus) -> dict:
    payload = status.model_dump(mode="json")
    if status.install_plan is not None:
        payload["install_command"] = status.install_plan.display_command
    else:
        payload["install_command"] = None
    return payload


def initialize(
    ctx: typer.Context,
    ffmpeg: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            resolve_path=True,
            help="Use this FFmpeg executable instead of searching PATH.",
        ),
    ] = None,
    ffprobe: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            resolve_path=True,
            help="Use this ffprobe executable instead of searching PATH.",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Confirm the displayed system package-manager command.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Verify and save the system FFmpeg runtime used by VidXP."""

    root_format = ctx.find_root().params.get(
        "output_format",
        OutputFormat.rich,
    )
    output_format = (
        OutputFormat.json
        if json_output or root_format == OutputFormat.json
        else OutputFormat.rich
    )
    status = inspect_media_runtime(ffmpeg=ffmpeg, ffprobe=ffprobe)
    if not status.ready:
        payload = _media_runtime_payload(status)
        if output_format == OutputFormat.rich:
            typer.secho(
                "FFmpeg and ffprobe are not ready.",
                fg=typer.colors.YELLOW,
                bold=True,
            )
            for error in status.errors:
                typer.echo(f"  {error}")
            if status.install_plan is not None:
                typer.echo(
                    f"Package manager: {status.install_plan.manager}"
                )
                typer.secho(
                    f"Command: {status.install_plan.display_command}",
                    fg=typer.colors.YELLOW,
                )
        plan = status.install_plan
        if plan is None or not plan.automatic:
            if output_format == OutputFormat.json:
                emit_json(payload)
            elif plan is None:
                typer.echo(
                    "Install FFmpeg and ffprobe with your operating-system "
                    "package manager, or pass explicit --ffmpeg and "
                    "--ffprobe paths, then rerun `vidxp init`."
                )
            else:
                typer.echo(
                    "Install FFmpeg with the command above, or pass explicit "
                    "--ffmpeg and --ffprobe paths, then rerun `vidxp init`."
                )
            raise typer.Exit(1)
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        if not yes:
            if not interactive:
                if output_format == OutputFormat.json:
                    emit_json(payload)
                else:
                    typer.echo(
                        "No installation was started. Rerun interactively or "
                        "pass --yes after reviewing the command."
                    )
                raise typer.Exit(1)
            typer.confirm(
                f"Install through {plan.manager} using the displayed command?",
                abort=True,
            )
        if output_format == OutputFormat.rich:
            emit_progress(f"Installing FFmpeg through {plan.manager}...")
        install_media_runtime(
            plan,
            output_to_stderr=output_format == OutputFormat.json,
        )
        status = inspect_media_runtime(ffmpeg=ffmpeg, ffprobe=ffprobe)
        if not status.ready:
            if output_format == OutputFormat.json:
                emit_json(_media_runtime_payload(status))
            else:
                for error in status.errors:
                    typer.secho(error, fg=typer.colors.RED)
            raise typer.Exit(1)

    configuration = save_media_runtime_configuration(status)
    payload = {
        **_media_runtime_payload(status),
        "initialized": True,
        "configuration": str(media_runtime_config_path()),
    }
    if output_format == OutputFormat.json:
        emit_json(payload)
    else:
        typer.secho(
            "VidXP media runtime is initialized.",
            fg=typer.colors.GREEN,
            bold=True,
        )
        typer.echo(f"FFmpeg: {configuration.ffmpeg_executable}")
        typer.echo(f"ffprobe: {configuration.ffprobe_executable}")


def doctor(
    ctx: typer.Context,
    modalities: Annotated[
        list[str] | None,
        typer.Option(
            "--modalities",
            "-m",
            help=(
                "Only validate dependencies for these modalities. "
                "Accepts comma-separated values or repeated options."
            ),
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Validate selected indexing dependencies without downloading models."""

    state = state_from_context(ctx)
    capabilities = tuple(state.service.list_capabilities())
    available = tuple(capability.name for capability in capabilities)
    selected = (
        available
        if modalities is None
        else parse_modalities(",".join(modalities), available)
    )
    output_format = effective_output_format(state, json_output)

    def show_check_start(
        capability: str,
        kind: DependencyKind,
        name: str,
    ) -> None:
        label = {
            DependencyKind.distribution: f"package {name}",
            DependencyKind.model: f"model {name}",
        }.get(kind, name)
        emit_progress(
            f"Checking [{capability}] {label}...",
            newline=False,
        )

    def show_check_complete(
        check: CapabilityDependencyCheck,
        elapsed_seconds: float,
    ) -> None:
        if check.ok:
            detail = (
                f"version {check.installed_version}, {elapsed_seconds:.1f}s"
                if check.kind == DependencyKind.distribution
                else f"{elapsed_seconds:.1f}s"
            )
            typer.secho(
                f" OK ({detail})",
                fg=typer.colors.GREEN,
            )
        else:
            typer.secho(
                f" FAILED ({elapsed_seconds:.1f}s): {check.error}",
                fg=typer.colors.RED,
            )

    result = state.service.check_dependencies(
        DependencyCheckCommand(
            modalities=selected,
            include_models=True,
        ),
        on_check_start=(
            show_check_start if output_format == OutputFormat.rich else None
        ),
        on_check_complete=(
            show_check_complete
            if output_format == OutputFormat.rich
            else None
        ),
    )
    payload = result.model_dump(mode="json")
    if output_format == OutputFormat.json:
        emit_json(payload)
    else:
        python_failures = tuple(
            check
            for check in result.checks
            if (
                not check.ok
                and check.capability != "media"
                and check.kind != DependencyKind.model
            )
        )
        if python_failures:
            selected_capabilities = {
                capability.name: capability for capability in capabilities
            }
            builtin_extras = tuple(
                dict.fromkeys(
                    selected_capabilities[name].install_extra
                    for name in selected
                    if selected_capabilities[name].provenance is None
                )
            )
            external_distributions = tuple(
                dict.fromkeys(
                    selected_capabilities[name].provenance.distribution
                    for name in selected
                    if selected_capabilities[name].provenance is not None
                )
            )
            if builtin_extras:
                typer.secho(
                    "REMEDY: In a source checkout, rerun uv sync with all "
                    "profiles you use and include --extra local-worker.",
                    fg=typer.colors.YELLOW,
                )
                typer.secho(
                    'Published package: pip install "vidxp['
                    + ",".join(builtin_extras)
                    + ']"',
                    fg=typer.colors.YELLOW,
                )
            if external_distributions:
                typer.secho(
                    "External capabilities: pip install "
                    + " ".join(external_distributions),
                    fg=typer.colors.YELLOW,
                )
        media_failures = tuple(
            check
            for check in result.checks
            if not check.ok and check.capability == "media"
        )
        if media_failures:
            typer.secho(
                "REMEDY: Run `vidxp init` to verify and configure FFmpeg.",
                fg=typer.colors.YELLOW,
            )
        missing_model_checks = tuple(
            check
            for check in result.checks
            if not check.ok and check.kind == DependencyKind.model
        )
        missing_models = tuple(
            dict.fromkeys(check.capability for check in missing_model_checks)
        )
        if missing_models:
            typer.secho("Missing model downloads:", bold=True)
            for check in missing_model_checks:
                typer.echo(
                    f"  {check.name}: "
                    f"{_format_bytes(check.download_size_bytes or 0)}"
                )
            required_bytes = sum(
                check.download_size_bytes or 0
                for check in missing_model_checks
            )
            typer.secho(
                "Maximum additional download and cache space: "
                f"{_format_bytes(required_bytes)}",
                bold=True,
            )
            typer.echo(f"Model cache: {state.service.model_cache}")
            free_bytes = available_storage_bytes(state.service.model_cache)
            if free_bytes is not None:
                typer.echo(
                    "Free space at model cache: "
                    f"{_format_bytes(free_bytes)}"
                )
            command = "vidxp prepare --modalities " + ",".join(missing_models)
            typer.secho(
                "REMEDY: Download the selected model artifacts explicitly:",
                fg=typer.colors.YELLOW,
            )
            typer.secho(command, fg=typer.colors.YELLOW)
    if not result.ok:
        raise typer.Exit(1)
    if output_format == OutputFormat.rich:
        typer.secho(
            "Selected VidXP dependencies and model artifacts are available.",
            fg=typer.colors.GREEN,
            bold=True,
        )


def prepare(
    ctx: typer.Context,
    modalities: Annotated[
        list[str] | None,
        typer.Option(
            "--modalities",
            "-m",
            help=(
                "Only prepare models for these modalities. "
                "Accepts comma-separated values or repeated options."
            ),
        ),
    ] = None,
    capability_options: Annotated[
        list[str] | None,
        typer.Option(
            "--option",
            help=(
                "Capability setting as CAPABILITY.KEY=VALUE; "
                "repeat for multiple settings."
            ),
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    detach: Annotated[
        bool,
        typer.Option(
            "--detach",
            help="Return after the durable job is queued.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Confirm the displayed model download and cache size.",
        ),
    ] = False,
) -> None:
    """Download and cache selected runtime models before indexing."""

    state = state_from_context(ctx)
    preparable = tuple(
        capability.name
        for capability in state.service.list_capabilities()
        if capability.prepares_models
    )
    selected = (
        preparable
        if modalities is None
        else parse_modalities(",".join(modalities), preparable)
    )
    output_format = effective_output_format(state, json_output)
    readiness = state.service.model_readiness(selected)
    missing = tuple(check for check in readiness.checks if not check.ok)
    required_bytes = sum(
        check.download_size_bytes or 0
        for check in missing
    )
    free_bytes = (
        available_storage_bytes(state.service.model_cache)
        if missing
        else None
    )
    if missing and output_format == OutputFormat.rich:
        typer.secho("Models to download:", bold=True)
        for check in missing:
            typer.echo(
                f"  {check.name}: "
                f"{_format_bytes(check.download_size_bytes or 0)}"
            )
        typer.secho(
            "Maximum additional download and cache space: "
            f"{_format_bytes(required_bytes)}",
            bold=True,
        )
        typer.echo(f"Model cache: {state.service.model_cache}")
        if free_bytes is not None:
            typer.echo(
                "Free space at model cache: "
                f"{_format_bytes(free_bytes)}"
            )
    if (
        missing
        and free_bytes is not None
        and free_bytes < required_bytes
    ):
        raise typer.BadParameter(
            "The model cache does not have enough free space for the "
            f"selected downloads ({_format_bytes(required_bytes)} required, "
            f"{_format_bytes(free_bytes)} free at "
            f"{state.service.model_cache}). Choose another VidXP data "
            "location with the global --data-dir option or VIDXP_DATA_DIR."
        )
    if missing and not yes:
        if output_format == OutputFormat.json:
            raise typer.BadParameter(
                "Model downloads require explicit confirmation; review "
                "`vidxp doctor --modalities ... --json`, then rerun prepare "
                "with --yes."
            )
        typer.confirm("Download these models?", abort=True)
    show_progress = not state.quiet and output_format == OutputFormat.rich
    if show_progress:
        emit_progress(
            "Starting model preparation for " + ", ".join(selected) + "."
        )
    job = state.jobs.submit_prepare_models(
        PrepareModelsCommand(
            modalities=selected,
            capability_options=parse_capability_options(capability_options),
        )
    )
    if not detach:
        job = state.jobs.wait(
            job.job_id,
            progress=emit_job_progress if show_progress else None,
        )
    if output_format == OutputFormat.json:
        emit_json(job.model_dump(mode="json"))
    else:
        typer.secho(
            (
                f"Model preparation job queued: {job.job_id}"
                if detach
                else f"Selected runtime models are prepared: {job.job_id}"
            ),
            fg=typer.colors.GREEN,
            bold=True,
        )


def ui(
    ctx: typer.Context,
    host: Annotated[
        str | None,
        typer.Option("--host", help="Streamlit server address."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(
            "--port",
            min=1,
            max=65535,
            help="Streamlit server port.",
        ),
    ] = None,
) -> None:
    """Launch Streamlit with the selected repository configuration."""

    require_media_runtime()
    state = state_from_context(ctx)
    show_progress = (
        not state.quiet and state.output_format == OutputFormat.rich
    )
    if show_progress:
        emit_progress("Loading the browser interface...")
    try:
        from vidxp import frontend
    except ModuleNotFoundError as exc:
        if exc.name == "streamlit":
            raise ApplicationError(
                "frontend_unavailable",
                ErrorCategory.unavailable,
                "The browser interface is unavailable. "
                "In a source checkout, rerun uv sync with all profiles "
                "you use and include --extra frontend. Published package: "
                'pip install "vidxp[frontend]".',
            ) from exc
        raise

    streamlit_arguments = []
    if host is not None:
        streamlit_arguments.append(f"--server.address={host}")
    if port is not None:
        streamlit_arguments.append(f"--server.port={port}")
    if show_progress:
        emit_progress("Starting the browser interface...")
    try:
        frontend.main(
            streamlit_arguments,
            settings=state.settings,
        )
    finally:
        try:
            state.jobs.stop_worker()
        except ApplicationError as exc:
            typer.secho(
                f"WARNING: The local background worker did not stop: {exc}",
                fg=typer.colors.YELLOW,
                err=True,
            )
