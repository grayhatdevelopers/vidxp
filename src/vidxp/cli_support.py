from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table

from vidxp.application import VidXPApplication
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.capabilities.schemas import SearchResult
from vidxp.repositories import RepositoryConfig, RepositoryRegistry


class OutputFormat(str, Enum):
    rich = "rich"
    json = "json"


@dataclass
class CLIState:
    service: VidXPApplication
    registry: RepositoryRegistry
    repository: RepositoryConfig
    output_format: OutputFormat = OutputFormat.rich
    quiet: bool = False


def state_from_context(ctx: typer.Context) -> CLIState:
    state = ctx.ensure_object(CLIState)
    if not isinstance(state, CLIState):
        raise RuntimeError("VidXP CLI state was not initialized.")
    return state


def effective_output_format(
    state: CLIState,
    json_output: bool,
) -> OutputFormat:
    return OutputFormat.json if json_output else state.output_format


def emit_json(payload: Any) -> None:
    typer.echo(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def emit_search(
    result: SearchResult,
    *,
    output_format: OutputFormat,
) -> None:
    if output_format == OutputFormat.json:
        emit_json(result.to_dict())
        return
    if not result.hits:
        typer.echo(f"No {result.modality} matches found.")
        return
    table = Table(title=f"{result.modality.title()} search results")
    table.add_column("Rank", justify="right")
    table.add_column("Start", justify="right")
    table.add_column("End", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Video")
    for hit in result.hits:
        table.add_row(
            str(hit.rank),
            f"{hit.start:.3f}s",
            f"{hit.end:.3f}s",
            f"{hit.score:.6f}",
            hit.video_id,
        )
    Console().print(table)


def emit_status(
    status: dict[str, Any],
    *,
    output_format: OutputFormat,
) -> None:
    if output_format == OutputFormat.json:
        emit_json(status)
        return
    table = Table(title="VidXP index")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("State", str(status.get("state", "unknown")))
    table.add_row("Message", str(status.get("message", "—")))
    if updated_at := status.get("updated_at"):
        table.add_row("Updated", str(updated_at))
    if video := status.get("video"):
        table.add_row(
            "Video",
            str(video.get("source_name") or video.get("path") or "—"),
        )
    summary = status.get("summary") or {}
    if modalities := (summary.get("configuration") or {}).get(
        "enabled_modalities"
    ):
        table.add_row("Modalities", ", ".join(map(str, modalities)))
    Console().print(table)


class IndexProgress:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=Console(stderr=True),
        )
        self.task_id: int | None = None
        self.stage: str | None = None

    def __enter__(self) -> "IndexProgress":
        if self.enabled:
            self.progress.start()
        return self

    def __exit__(self, *_: Any) -> None:
        if self.enabled:
            self.progress.stop()

    def update(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        stage = str(event.get("stage", "indexing"))
        total = event.get("total")
        current = event.get("current")
        if self.task_id is None or stage != self.stage:
            if self.task_id is not None:
                self.progress.update(self.task_id, completed=1, total=1)
            self.task_id = self.progress.add_task(
                str(event.get("message", stage.replace("_", " "))),
                total=float(total) if total else None,
            )
            self.stage = stage
        self.progress.update(
            self.task_id,
            description=str(event.get("message", stage.replace("_", " "))),
            total=float(total) if total else None,
            completed=float(current) if current is not None else None,
        )


def selected_modalities(
    values: Iterable[str] | None,
    registry: CapabilityRegistry,
) -> tuple[str, ...]:
    if values is None:
        return registry.index_names()
    try:
        return registry.validate_names(values)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def parse_modalities(
    value: str,
    registry: CapabilityRegistry,
) -> tuple[str, ...]:
    selected = tuple(
        item.strip().lower()
        for item in value.split(",")
        if item.strip()
    )
    try:
        return registry.validate_names(selected)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def parse_capability_options(
    values: Iterable[str] | None,
) -> dict[str, dict[str, Any]]:
    options: dict[str, dict[str, Any]] = {}
    for value in values or ():
        path, separator, raw = value.partition("=")
        capability, dot, key = path.partition(".")
        if not separator or not dot or not capability or not key:
            raise typer.BadParameter(
                "Capability options must use CAPABILITY.KEY=VALUE."
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        options.setdefault(capability, {})[key] = parsed
    return options
