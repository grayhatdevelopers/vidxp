from __future__ import annotations

from typing import Annotated

import typer

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
    state_from_context,
)

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
        label = f"package {name}" if kind == DependencyKind.distribution else name
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
                f"version {check.installed_version}"
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
        DependencyCheckCommand(modalities=selected),
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
            if not check.ok and check.capability != "media"
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
    if not result.ok:
        raise typer.Exit(1)
    if output_format == OutputFormat.rich:
        typer.secho(
            "Selected VidXP dependencies are available.",
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
    frontend.main(
        streamlit_arguments,
        settings=state.settings,
    )
