from __future__ import annotations

import os
import sys
from datetime import datetime


def _wants_json(arguments: list[str]) -> bool:
    if "--json" in arguments:
        return True
    for index, value in enumerate(arguments):
        if value == "--format" and index + 1 < len(arguments):
            return arguments[index + 1].lower() == "json"
        if value.startswith("--format="):
            return value.split("=", 1)[1].lower() == "json"
    return os.environ.get("VIDXP_OUTPUT_FORMAT", "").lower() == "json"


def startup_command(arguments: list[str]) -> str | None:
    if (
        _wants_json(arguments)
        or any(
            value in arguments
            for value in ("--quiet", "-q", "--help", "--version", "-V")
        )
    ):
        return None
    root_values = {
        "--repository",
        "-r",
        "--config",
        "--index-dir",
        "--data-dir",
        "--device",
        "--format",
    }
    command = []
    skip_next = False
    for value in arguments:
        if skip_next:
            skip_next = False
            continue
        if not command and value in root_values:
            skip_next = True
            continue
        if not command and value.startswith("-"):
            continue
        command.append(value)
        if len(command) == 2:
            break
    path = tuple(command)
    if path and path[0] in {
        "benchmark",
        "doctor",
        "init",
        "prepare",
        "query",
        "search",
        "ui",
    }:
        return path[0]
    if path in {
        ("actors", "render"),
        ("artifacts", "snippet"),
        ("index", "create"),
        ("media", "import"),
    }:
        return " ".join(path)
    return None


def main() -> None:
    command = startup_command(sys.argv[1:])
    if command is not None:
        timestamp = datetime.now().astimezone()
        print(
            f"[{timestamp:%H:%M:%S}] Starting VidXP {command}...",
            file=sys.stderr,
            flush=True,
        )
    from vidxp.cli import main as cli_main

    cli_main()
