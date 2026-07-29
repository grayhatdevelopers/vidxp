from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
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

from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    FusedSearchResult,
    QueryAnswer,
)
from vidxp.media_runtime import media_runtime_is_initialized
from vidxp.repositories import RepositoryConfig, RepositoryRegistry
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vidxp.application import VidXPApplication
    from vidxp.composition import LocalApplicationContext
    from vidxp.job_service import JobService
    from vidxp.settings import VidXPSettings


class OutputFormat(str, Enum):
    rich = "rich"
    json = "json"


@dataclass
class CLIState:
    local: "LocalApplicationContext"
    registry: RepositoryRegistry
    repository: RepositoryConfig
    output_format: OutputFormat = OutputFormat.rich
    quiet: bool = False

    @property
    def service(self) -> "VidXPApplication":
        return self.local.application

    @property
    def jobs(self) -> "JobService":
        return self.local.jobs

    @property
    def settings(self) -> "VidXPSettings":
        return self.local.settings


def state_from_context(ctx: typer.Context) -> CLIState:
    state = ctx.ensure_object(CLIState)
    if not isinstance(state, CLIState):
        raise RuntimeError("VidXP CLI state was not initialized.")
    return state


def require_media_runtime() -> None:
    if media_runtime_is_initialized():
        return
    raise ApplicationError(
        "media_runtime_uninitialized",
        ErrorCategory.unavailable,
        "FFmpeg and ffprobe are not initialized for local media work. "
        "Run `vidxp init`, then retry this command.",
        details={
            "remediation": "vidxp init",
            "install_hint": "Run vidxp init",
        },
    )


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


def emit_progress(
    message: str,
    *,
    updated_at: datetime | None = None,
    newline: bool = True,
) -> None:
    timestamp = (updated_at or datetime.now().astimezone()).astimezone()
    typer.secho(
        f"[{timestamp:%H:%M:%S}] {message}",
        fg=typer.colors.BLUE,
        nl=newline,
    )


def emit_job_progress(job: Any) -> None:
    if job.progress is not None:
        message = job.progress.message
        if (
            job.progress.stage == "downloading_model"
            and job.progress.current is not None
            and job.progress.total
        ):
            gib = 1024**3
            mib = 1024**2
            unit = gib if job.progress.total >= gib else mib
            suffix = "GiB" if unit == gib else "MiB"
            message += (
                f" {job.progress.current / unit:.1f} of "
                f"{job.progress.total / unit:.1f} {suffix}"
            )
        emit_progress(
            message,
            updated_at=job.progress.updated_at,
        )


def emit_search(
    result: FusedSearchResult,
    *,
    output_format: OutputFormat,
) -> None:
    if output_format == OutputFormat.json:
        emit_json(result.model_dump(mode="json"))
        return
    if not result.moments:
        typer.echo("No matching moments found.")
        return
    table = Table(title="Fused search results")
    table.add_column("Rank", justify="right")
    table.add_column("Start", justify="right")
    table.add_column("End", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Video")
    table.add_column("Modalities")
    for moment in result.moments:
        table.add_row(
            str(moment.rank),
            f"{moment.start:.3f}s",
            f"{moment.end:.3f}s",
            f"{moment.score:.6f}",
            moment.media_id,
            ", ".join(moment.modalities),
        )
    Console().print(table)


def emit_query(
    result: QueryAnswer,
    *,
    output_format: OutputFormat,
) -> None:
    if output_format == OutputFormat.json:
        emit_json(result.model_dump(mode="json"))
        return
    console = Console()
    console.print(f"Mode: {result.mode.value}")
    if result.claims:
        for claim in result.claims:
            console.print(f"• {claim.text}")
            console.print(f"  Evidence: {', '.join(claim.evidence_ids)}")
    elif result.evidence:
        console.print(
            f"No generated answer; returning {len(result.evidence)} "
            "evidence item(s)."
        )
    else:
        console.print("No supporting evidence found.")
    if result.fallback_reason:
        console.print(f"Fallback: {result.fallback_reason}")


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
    summary = status.get("summary") or {}
    if modalities := summary.get("modalities"):
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
    available: Iterable[str],
) -> tuple[str, ...]:
    supported = tuple(available)
    if values is None:
        return supported
    selected = tuple(dict.fromkeys(values))
    unknown = sorted(set(selected) - set(supported))
    if unknown:
        raise typer.BadParameter(
            "Unknown or unsupported capabilities: " + ", ".join(unknown)
        )
    return selected


def parse_modalities(
    value: str,
    available: Iterable[str],
) -> tuple[str, ...]:
    selected = tuple(
        item.strip().lower()
        for item in value.split(",")
        if item.strip()
    )
    return selected_modalities(selected, available)


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
