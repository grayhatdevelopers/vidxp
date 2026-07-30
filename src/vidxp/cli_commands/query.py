from __future__ import annotations

from typing import Annotated

import typer

from vidxp.application_models import (
    QueryAnswer,
    QueryJobResult,
    QueryVideoCommand,
)
from vidxp.cli_support import (
    CLIState,
    OutputFormat,
    effective_output_format,
    emit_job_progress,
    emit_progress,
    emit_query,
    state_from_context,
)


def run_query(
    state: CLIState,
    question: str,
    *,
    media_id: str | None,
    modalities: tuple[str, ...],
    top_k: int,
    json_output: bool,
) -> QueryAnswer:
    output_format = effective_output_format(state, json_output)
    show_progress = not state.quiet and output_format == OutputFormat.rich
    if show_progress:
        emit_progress("Starting grounded video query...")
    job = state.jobs.submit_query(
        QueryVideoCommand(
            question=question,
            media_id=media_id,
            modalities=modalities,
            top_k=top_k,
        )
    )
    completed = state.jobs.wait(
        job.job_id,
        progress=emit_job_progress if show_progress else None,
    )
    if not isinstance(completed.result, QueryJobResult):
        raise RuntimeError("The completed query job has no query result.")
    result = completed.result.result
    emit_query(
        result,
        output_format=output_format,
    )
    return result


def query(
    ctx: typer.Context,
    question: Annotated[
        str,
        typer.Argument(help="Natural-language question about indexed media."),
    ],
    media_id: Annotated[
        str | None,
        typer.Option(
            "--media-id",
            help=(
                "Use evidence only from this media ID. Omit to query every "
                "media item in the active index snapshot."
            ),
        ),
    ] = None,
    modality: Annotated[
        list[str] | None,
        typer.Option(
            "--modality",
            "-m",
            help="Restrict query evidence; repeat for multiple capabilities.",
        ),
    ] = None,
    top_k: Annotated[
        int,
        typer.Option("--top-k", "-k", min=1, max=50),
    ] = 10,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    run_query(
        state_from_context(ctx),
        question,
        media_id=media_id,
        modalities=tuple(dict.fromkeys(modality or ())),
        top_k=top_k,
        json_output=json_output,
    )
