from __future__ import annotations

import os
from typing import Annotated

import typer

from vidxp.application_models import (
    DependencyCheckCommand,
    PrepareModelsCommand,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.cli_support import (
    OutputFormat,
    effective_output_format,
    emit_json,
    parse_capability_options,
    parse_modalities,
    state_from_context,
)

_COMMAND_REGISTRY = create_capability_registry()
ALL_CAPABILITIES = ",".join(_COMMAND_REGISTRY.names())
PREPARABLE_CAPABILITIES = ",".join(
    _COMMAND_REGISTRY.preparable_names()
)


def doctor(
    ctx: typer.Context,
    modalities: Annotated[
        str,
        typer.Option(
            "--modalities",
            "-m",
            help="Only validate dependencies for these modalities.",
        ),
    ] = ALL_CAPABILITIES,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Validate selected indexing dependencies without downloading models."""

    state = state_from_context(ctx)
    selected = parse_modalities(modalities, state.service.registry)
    result = state.service.check_dependencies(
        DependencyCheckCommand(modalities=selected)
    )
    payload = result.model_dump(mode="json")
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
    else:
        for check in payload["checks"]:
            if check["ok"]:
                detail = f": {check['path']}" if check.get("path") else ""
                typer.secho(
                    f"OK {check['name']}{detail}",
                    fg=typer.colors.GREEN,
                )
            else:
                typer.secho(
                    f"FAILED {check['name']}: {check['error']}",
                    fg=typer.colors.RED,
                )
    if not result.ok:
        raise typer.Exit(1)
    if effective_output_format(state, json_output) == OutputFormat.rich:
        typer.secho(
            "Selected VidXP dependencies are available.",
            fg=typer.colors.GREEN,
            bold=True,
        )


def prepare(
    ctx: typer.Context,
    modalities: Annotated[
        str,
        typer.Option(
            "--modalities",
            "-m",
            help="Only prepare models for these modalities.",
        ),
    ] = PREPARABLE_CAPABILITIES,
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
) -> None:
    """Download and cache selected runtime models before indexing."""

    state = state_from_context(ctx)
    selected = parse_modalities(modalities, state.service.registry)
    result = state.service.prepare_models(
        PrepareModelsCommand(
            modalities=selected,
            capability_options=parse_capability_options(capability_options),
        ),
        progress_callback=(
            None
            if state.quiet
            or effective_output_format(state, json_output)
            == OutputFormat.json
            else lambda event: typer.echo(event["message"])
        ),
    )
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(result.model_dump(mode="json"))
    else:
        typer.secho(
            "Selected VidXP runtime models are prepared.",
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
    os.environ["VIDXP_CONFIG_FILE"] = str(state.registry.path)
    os.environ["VIDXP_REPOSITORY"] = state.repository.name
    os.environ["VIDXP_INDEX_DIR"] = str(state.service.layout.root)
    if state.service.device is None:
        os.environ.pop("VIDXP_DEVICE", None)
    else:
        os.environ["VIDXP_DEVICE"] = state.service.device

    try:
        from vidxp import frontend
    except ModuleNotFoundError as exc:
        if exc.name == "streamlit":
            raise RuntimeError(
                "The browser interface requires the frontend extra. "
                "Install vidxp[frontend]."
            ) from exc
        raise

    streamlit_arguments = []
    if host is not None:
        streamlit_arguments.append(f"--server.address={host}")
    if port is not None:
        streamlit_arguments.append(f"--server.port={port}")
    frontend.main(streamlit_arguments)
