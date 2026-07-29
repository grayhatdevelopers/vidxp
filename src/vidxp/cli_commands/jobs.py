from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from vidxp.application_models import ListJobsCommand
from vidxp.cli_support import (
    OutputFormat,
    effective_output_format,
    emit_json,
    state_from_context,
)


app = typer.Typer(no_args_is_help=True, help="Manage durable background jobs.")


@app.command("list")
def list_jobs(
    ctx: typer.Context,
    page_size: Annotated[
        int,
        typer.Option("--page-size", min=1, max=100),
    ] = 50,
    cursor: Annotated[
        str | None,
        typer.Option("--cursor", help="Cursor returned by the previous page."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """List durable VidXP jobs."""

    state = state_from_context(ctx)
    page = state.jobs.list(
        ListJobsCommand(page_size=page_size, cursor=cursor)
    )
    payload = page.model_dump(mode="json")
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
        return
    table = Table(title="VidXP jobs")
    table.add_column("Job")
    table.add_column("Kind")
    table.add_column("State")
    table.add_column("Queue")
    table.add_column("Progress")
    for job in page.items:
        table.add_row(
            job.job_id,
            job.kind.value,
            job.state.value,
            job.queue.value,
            job.progress.message if job.progress is not None else "—",
        )
    Console().print(table)
    if page.next_cursor is not None:
        typer.echo(f"Next cursor: {page.next_cursor}")


@app.command("show")
def show_job(
    ctx: typer.Context,
    job_id: Annotated[str, typer.Argument(help="Durable job identifier.")],
) -> None:
    """Show one job and its typed result when available."""

    state = state_from_context(ctx)
    emit_json(state.jobs.get(job_id).model_dump(mode="json"))


@app.command("cancel")
def cancel_job(
    ctx: typer.Context,
    job_id: Annotated[str, typer.Argument(help="Durable job identifier.")],
) -> None:
    """Request cancellation of a queued or running job."""

    state = state_from_context(ctx)
    emit_json(state.jobs.cancel(job_id).model_dump(mode="json"))


@app.command("retry")
def retry_job(
    ctx: typer.Context,
    job_id: Annotated[str, typer.Argument(help="Durable job identifier.")],
) -> None:
    """Retry a failed or cancelled job through DBOS recovery."""

    state = state_from_context(ctx)
    emit_json(state.jobs.retry(job_id).model_dump(mode="json"))


@app.command("stop-worker")
def stop_worker(ctx: typer.Context) -> None:
    """Stop the detached local worker; durable jobs remain recoverable."""

    state = state_from_context(ctx)
    stopped = state.jobs.stop_worker()
    emit_json({"stopped": stopped})
