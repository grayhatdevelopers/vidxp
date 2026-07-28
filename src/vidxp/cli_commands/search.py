from __future__ import annotations

from typing import Annotated

import typer

from vidxp.application_models import SearchCommand
from vidxp.capabilities.schemas import SearchResult
from vidxp.cli_support import (
    CLIState,
    effective_output_format,
    emit_search,
    state_from_context,
)


def run_search(
    state: CLIState,
    capability: str,
    query: str,
    *,
    top_k: int,
    json_output: bool,
) -> SearchResult:
    result = state.service.search(
        SearchCommand(
            modality=capability,
            query=query,
            top_k=top_k,
        )
    )
    emit_search(
        result,
        output_format=effective_output_format(state, json_output),
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
        top_k=top_k,
        json_output=json_output,
    )
