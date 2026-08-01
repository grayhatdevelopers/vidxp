from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from vidxp import __version__
from vidxp.application_models import ApplicationError
from vidxp.benchmarks.cli import app as benchmark_app
from vidxp.cli_commands.actors import app as actor_app
from vidxp.cli_commands.index import app as index_app
from vidxp.cli_commands.jobs import app as jobs_app
from vidxp.cli_commands.mcp import mcp_config
from vidxp.cli_commands.media import app as media_app
from vidxp.cli_commands.artifacts import app as artifacts_app
from vidxp.cli_commands.repositories import app as repositories_app
from vidxp.cli_commands.runtime import doctor, initialize, prepare, ui
from vidxp.cli_commands.search import search
from vidxp.cli_commands.query import query
from vidxp.cli_commands.probe import desktop_probe
from vidxp.cli_support import CLIState, OutputFormat
from vidxp.composition import create_local_application


app = typer.Typer(
    no_args_is_help=True,
    help="Index and search video with installable capabilities.",
)
app.add_typer(index_app, name="index")
app.add_typer(jobs_app, name="jobs")
app.add_typer(media_app, name="media")
app.add_typer(artifacts_app, name="artifacts")
app.command("search")(search)
app.command("query")(query)
app.command("desktop-probe")(desktop_probe)
app.command("mcp-config")(mcp_config)
app.add_typer(repositories_app, name="repositories")
app.add_typer(actor_app, name="actors")


app.add_typer(benchmark_app, name="benchmark")
app.command()(doctor)
app.command("init")(initialize)
app.command()(prepare)
app.command()(ui)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"VidXP {__version__}")
        raise typer.Exit()


@app.callback()
def app_options(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_show_version,
            is_eager=True,
            help="Show the installed VidXP version and exit.",
        ),
    ] = False,
    repository_name: Annotated[
        str | None,
        typer.Option(
            "--repository",
            "-r",
            help="Named repository to use.",
        ),
    ] = None,
    config_file: Annotated[
        Path | None,
        typer.Option(
            "--config",
            dir_okay=False,
            help="Repository configuration file.",
        ),
    ] = None,
    index_directory: Annotated[
        Path | None,
        typer.Option(
            "--index-dir",
            file_okay=False,
            help="Override the selected repository index directory.",
        ),
    ] = None,
    data_directory: Annotated[
        Path | None,
        typer.Option(
            "--data-dir",
            file_okay=False,
            help=(
                "Store VidXP models and the default repository beneath this "
                "directory."
            ),
        ),
    ] = None,
    device: Annotated[
        str | None,
        typer.Option(
            "--device",
            help="Override the selected repository runtime device.",
        ),
    ] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            envvar="VIDXP_OUTPUT_FORMAT",
            help="Default command output format.",
        ),
    ] = OutputFormat.rich,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress progress output."),
    ] = False,
) -> None:
    if ctx.invoked_subcommand in {"desktop-probe", "init", "mcp-config"}:
        return
    local = create_local_application(
        registry_path=config_file,
        repository_name=repository_name,
        index_directory=index_directory,
        data_directory=data_directory,
        device=device,
    )
    ctx.call_on_close(local.close)
    ctx.obj = CLIState(
        local=local,
        registry=local.repositories,
        repository=local.repository,
        output_format=output_format,
        quiet=quiet,
    )


def _wants_json(arguments: list[str] | None = None) -> bool:
    values = list(sys.argv[1:] if arguments is None else arguments)
    if "--json" in values:
        return True
    for index, value in enumerate(values):
        if value == "--format" and index + 1 < len(values):
            return values[index + 1].lower() == "json"
        if value.startswith("--format="):
            return value.split("=", 1)[1].lower() == "json"
    return os.environ.get("VIDXP_OUTPUT_FORMAT", "").lower() == "json"


def _error_message(exc: Exception) -> str:
    formatter = getattr(exc, "format_message", None)
    return str(formatter()) if formatter is not None else str(exc)


def _exit_code(exc: Exception) -> int:
    return int(getattr(exc, "exit_code", 1) or 1)


def _emit_error(exc: Exception, *, json_output: bool) -> None:
    message = _error_message(exc)
    if json_output:
        error = (
            exc.to_dict()
            if isinstance(exc, ApplicationError)
            else {
                "type": type(exc).__name__,
                "message": message,
                "exit_code": _exit_code(exc),
            }
        )
        typer.echo(
            json.dumps(
                {
                    "ok": False,
                    "error": error,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            err=True,
        )
    elif show := getattr(exc, "show", None):
        show(file=sys.stderr)
    else:
        typer.secho(message, fg=typer.colors.RED, err=True)


def main() -> None:
    try:
        exit_code = app(standalone_mode=False)
        if isinstance(exit_code, int) and exit_code:
            raise SystemExit(exit_code)
    except typer.Exit as exc:
        raise SystemExit(exc.exit_code) from None
    except typer.Abort as exc:
        _emit_error(exc, json_output=_wants_json())
        raise SystemExit(1) from exc
    except Exception as exc:
        if isinstance(exc, ApplicationError):
            _emit_error(exc, json_output=_wants_json())
            raise SystemExit(_exit_code(exc)) from exc
        is_command_error = hasattr(exc, "exit_code") and hasattr(
            exc,
            "format_message",
        )
        if not is_command_error:
            raise
        _emit_error(exc, json_output=_wants_json())
        raise SystemExit(_exit_code(exc)) from exc


if __name__ == "__main__":
    main()
