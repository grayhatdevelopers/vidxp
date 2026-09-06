from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Annotated

import typer

from vidxp.application_models import ApplicationError, ErrorCategory
from vidxp.cli_support import LiveProgress, OutputFormat, emit_json
from vidxp.core.manifest import write_json_atomic
from vidxp.local_answers import (
    DEFAULT_OLLAMA_BASE_URL,
    LocalAnswerConfiguration,
    LocalAnswerError,
    inspect_local_answers,
    load_local_answer_configuration,
    local_answer_config_path,
    local_answer_spec,
    prepare_local_answers,
)


app = typer.Typer(
    no_args_is_help=True,
    help="Prepare and inspect VidXP's optional local grounded answers.",
)


def _root_value(ctx: typer.Context, name: str, default):
    value = ctx.find_root().params.get(name)
    return default if value is None else value


def _selection(
    *,
    base_url: str | None,
    model: str | None,
) -> tuple[str, str, LocalAnswerConfiguration | None]:
    try:
        configured = load_local_answer_configuration()
    except (OSError, ValueError) as exc:
        raise ApplicationError(
            "local_answers_configuration_invalid",
            ErrorCategory.validation,
            "The saved local-answer configuration is invalid.",
        ) from exc
    spec = local_answer_spec()
    return (
        base_url or (configured.base_url if configured else DEFAULT_OLLAMA_BASE_URL),
        model or (configured.model if configured else spec.model),
        configured,
    )


def _require_client() -> None:
    if importlib.util.find_spec("pydantic_ai") is not None:
        return
    raise ApplicationError(
        "local_answers_client_unavailable",
        ErrorCategory.unavailable,
        "Local grounded answers require the VidXP 'slm' extra.",
        details={
            "remediation": 'Install `vidxp[slm]`, then rerun this command.',
        },
    )


@app.command("status")
def status(
    ctx: typer.Context,
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="Self-hosted Ollama /v1 endpoint."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Intentional local model override."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Check the configured Ollama service and approved answer model."""

    selected_url, selected_model, configured = _selection(
        base_url=base_url,
        model=model,
    )
    _require_client()
    try:
        result = inspect_local_answers(
            base_url=selected_url,
            model=selected_model,
            configuration=configured,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    output_format = (
        OutputFormat.json
        if json_output
        or _root_value(ctx, "output_format", OutputFormat.rich) == OutputFormat.json
        else OutputFormat.rich
    )
    if output_format == OutputFormat.json:
        emit_json(result.model_dump(mode="json"))
    else:
        typer.secho(
            "Local grounded answers are ready."
            if result.ready
            else "Local grounded answers are not ready.",
            fg=typer.colors.GREEN if result.ready else typer.colors.YELLOW,
            bold=True,
        )
        typer.echo(f"Endpoint: {result.base_url}")
        typer.echo(f"Model: {result.model}")
        for error in result.errors:
            typer.echo(f"  {error}")
    if not result.ready:
        raise typer.Exit(1)


@app.command("prepare")
def prepare(
    ctx: typer.Context,
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="Self-hosted Ollama /v1 endpoint."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Intentional local model override."),
    ] = None,
    ollama: Annotated[
        Path | None,
        typer.Option(
            "--ollama",
            exists=True,
            dir_okay=False,
            resolve_path=True,
            help="Use this Ollama executable instead of discovery or managed setup.",
        ),
    ] = None,
    runtime_root: Annotated[
        Path | None,
        typer.Option("--runtime-root", file_okay=False, hidden=True),
    ] = None,
    progress_file: Annotated[
        Path | None,
        typer.Option("--progress-file", hidden=True),
    ] = None,
    save: Annotated[
        bool,
        typer.Option(
            "--save/--no-save",
            help="Save the provider selection for later VidXP commands.",
        ),
    ] = True,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm the disclosed downloads."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Install or reuse Ollama and download the approved answer model."""

    selected_url, selected_model, configured = _selection(
        base_url=base_url,
        model=model,
    )
    _require_client()
    output_format = (
        OutputFormat.json
        if json_output
        or _root_value(ctx, "output_format", OutputFormat.rich) == OutputFormat.json
        else OutputFormat.rich
    )
    spec = local_answer_spec()
    current = inspect_local_answers(
        base_url=selected_url,
        model=selected_model,
        configuration=configured,
    )
    if not current.ready and not yes:
        if output_format == OutputFormat.rich:
            typer.echo(
                "Managed Ollama runtime download: up to "
                f"{spec.managed_runtime.maximum_download_size_bytes / 1024**3:.2f} GiB"
            )
            typer.echo(
                f"Local answer model download: {spec.download_size_bytes / 1024**3:.2f} GiB"
            )
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        if not interactive:
            raise typer.BadParameter(
                "Local-answer downloads require explicit confirmation; rerun with --yes."
            )
        typer.confirm("Download the required local-answer files?", abort=True)

    show_progress = (
        output_format == OutputFormat.rich
        and not _root_value(ctx, "quiet", False)
    )
    with LiveProgress(show_progress) as live_progress:

        def report(event: dict[str, object]) -> None:
            if progress_file is not None:
                write_json_atomic(progress_file, event)
            live_progress.update(event)

        try:
            configuration = prepare_local_answers(
                data_directory=_root_value(ctx, "data_directory", None),
                runtime_root=runtime_root,
                base_url=selected_url,
                model=selected_model,
                executable=ollama,
                save=save,
                progress=report,
            )
        except (LocalAnswerError, OSError, ValueError) as exc:
            raise ApplicationError(
                "local_answers_unavailable",
                ErrorCategory.unavailable,
                str(exc),
                details={
                    "remediation": "Rerun `vidxp local-answers prepare --yes`.",
                },
            ) from exc
    payload = {
        "ready": True,
        "base_url": configuration.base_url,
        "model": configuration.model,
        "executable": (
            str(configuration.executable)
            if configuration.executable is not None
            else None
        ),
        "model_directory": (
            str(configuration.model_directory)
            if configuration.model_directory is not None
            else None
        ),
        "saved": save,
        "configuration": str(local_answer_config_path()) if save else None,
    }
    if output_format == OutputFormat.json:
        emit_json(payload)
    else:
        typer.secho("Local grounded answers are ready.", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"Model: {configuration.model}")
        if save:
            typer.echo(f"Configuration: {local_answer_config_path()}")
