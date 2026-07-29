from __future__ import annotations

from typing import Annotated

import typer

from vidxp.application_models import SearchCommand, SearchJobResult
from vidxp.application_models import FusedSearchResult
from vidxp.cli_support import (
    CLIState,
    OutputFormat,
    effective_output_format,
    emit_job_progress,
    emit_progress,
    emit_search,
    state_from_context,
)


def run_search(
    state: CLIState,
    capability: str,
    query: str,
    *,
    media_id: str | None,
    top_k: int,
    json_output: bool,
) -> FusedSearchResult:
    output_format = effective_output_format(state, json_output)
    show_progress = not state.quiet and output_format == OutputFormat.rich
    if show_progress:
        emit_progress(f"Starting {capability} search...")
    job = state.jobs.submit_search(
        SearchCommand(
            modalities=(capability,),
            query=query,
            media_id=media_id,
            top_k=top_k,
        )
    )
    completed = state.jobs.wait(
        job.job_id,
        progress=emit_job_progress if show_progress else None,
    )
    if not isinstance(completed.result, SearchJobResult):
        raise RuntimeError("The completed search job has no search result.")
    result = completed.result.result
    emit_search(
        result,
        output_format=output_format,
    )
    return result


def search(
    ctx: typer.Context,
    capability: Annotated[
        str,
        typer.Argument(help="Registered capability to query."),
    ],
    query: Annotated[
        str,
        typer.Argument(help="Text query to find."),
    ],
    media_id: Annotated[
        str | None,
        typer.Option(
            "--media-id",
            help=(
                "Search only this media ID. Omit to rank matches across every "
                "media item in the active index snapshot."
            ),
        ),
    ] = None,
    top_k: Annotated[
        int,
        typer.Option(
            "--top-k",
            "-k",
            min=1,
            max=100,
            help="Maximum ranked hits.",
        ),
    ] = 10,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    state = state_from_context(ctx)
    run_search(
        state,
        capability,
        query,
        media_id=media_id,
        top_k=top_k,
        json_output=json_output,
    )
